"""HermesA2AHandler — adapts HermesClient to a2a-sdk's RequestHandler interface.

This is a lean implementation that bypasses the SDK's AgentExecutor/QueueManager
complexity and directly routes A2A requests to the Hermes API Server.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator

from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    Artifact,
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from hermes_a2a.hermes_client import HermesClient
from hermes_a2a.session_store import SessionStore
from hermes_a2a.task_state_machine import TaskStateMachine
from hermes_a2a.task_store import SQLiteTaskStore

logger = logging.getLogger(__name__)


def _text_part(text: str) -> Part:
    """Build a protobuf Part with text content."""
    return Part(text=text)


def _make_task(
    task_id: str,
    context_id: str,
    state: "TaskState.V",
    response_text: str | None = None,
) -> Task:
    """Build a protobuf Task object."""
    status = TaskStatus(state=state)
    if response_text:
        status.message.CopyFrom(
            Message(
                role="ROLE_AGENT",
                parts=[_text_part(response_text)],
            )
        )
    return Task(id=task_id, context_id=context_id, status=status)


class HermesA2AHandler(RequestHandler):
    """A2A RequestHandler that delegates to HermesClient + SQLiteTaskStore.

    This handler directly bridges A2A v1.0 requests to the Hermes Agent API
    without using the SDK's AgentExecutor pipeline — keeping things simple and
    predictable.
    """

    def __init__(
        self,
        hermes_client: HermesClient,
        task_store: SQLiteTaskStore,
        session_store: SessionStore | None = None,
    ) -> None:
        self._hermes = hermes_client
        self._store = task_store
        self._session_store = session_store
        self._state_machine = TaskStateMachine()
        # context_id → hermes session_id mapping for multi-turn
        # If session_store is provided, it will be used instead of this dict
        self._sessions: dict[str, str] = {}

    async def _get_session(self, context_id: str) -> str | None:
        """Look up session_id by context_id."""
        if self._session_store is not None:
            return await self._session_store.get(context_id)
        return self._sessions.get(context_id)

    async def _save_session(self, context_id: str, session_id: str) -> None:
        """Save a context_id → session_id mapping."""
        if self._session_store is not None:
            await self._session_store.save(context_id, session_id)
        else:
            self._sessions[context_id] = session_id

    async def restore_sessions(self) -> None:
        """Restore sessions from persistent store (call after session_store.init())."""
        if self._session_store is not None:
            self._sessions = await self._session_store.load_all()
            logger.info("Restored %d sessions from persistent store", len(self._sessions))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_text(self, params: SendMessageRequest) -> str:
        """Extract user text from a SendMessageRequest proto."""
        parts = list(params.message.parts)
        return "".join(p.text for p in parts if p.text)

    def _context_id(self, params: SendMessageRequest) -> str:
        """Get or create a contextId for the conversation."""
        cid = params.message.context_id or ""
        return cid if cid else str(uuid.uuid4())

    def _increment_metric(self, key: str) -> None:
        """Increment a metric counter on the app state metrics dict, if available."""
        try:
            # Access metrics through the handler's reference chain
            # The handler doesn't have direct access to app.state, so we
            # use an optional _metrics dict that can be set externally
            if hasattr(self, "_metrics") and self._metrics is not None:
                self._metrics[key] = self._metrics.get(key, 0) + 1
        except Exception:
            pass

    # ------------------------------------------------------------------
    # message/send (non-streaming)
    # ------------------------------------------------------------------

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Message | Task:
        t0 = time.monotonic()
        text = self._extract_text(params)
        context_id = self._context_id(params)
        session_id = await self._get_session(context_id)

        logger.info("message/send: context=%s session=%s", context_id, session_id)

        try:
            response_text, new_session_id = await self._hermes.send_message(
                text, session_id
            )
        except Exception as exc:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.error(
                "message/send failed: duration_ms=%.1f context_id=%s error=%s",
                duration_ms, context_id, exc,
                exc_info=True,
            )
            self._increment_metric("errors_total")
            raise

        await self._save_session(context_id, new_session_id)

        task_id = str(uuid.uuid4())
        task = _make_task(task_id, context_id, TaskState.TASK_STATE_COMPLETED, response_text)

        # Persist as JSON dict for our SQLite store
        task_dict = {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "response": response_text,
        }
        await self._store.save(task_dict, context)

        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "message/send completed: duration_ms=%.1f task_id=%s context_id=%s",
            duration_ms, task_id, context_id,
        )
        self._increment_metric("requests_total")
        return task

    # ------------------------------------------------------------------
    # message/stream (SSE)
    # ------------------------------------------------------------------

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        t0 = time.monotonic()
        text = self._extract_text(params)
        context_id = self._context_id(params)
        session_id = await self._get_session(context_id)
        task_id = str(uuid.uuid4())

        logger.info("message/stream: context=%s task=%s", context_id, task_id)

        # 1. Working status event
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

        # 2. Stream chunks as artifact events
        collected: list[str] = []
        async for chunk in self._hermes.send_message_stream(text, session_id):
            collected.append(chunk)
            yield TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(parts=[_text_part(chunk)]),
                append=True,
                last_chunk=False,
            )

        # 3. Final completed status event
        full_text = "".join(collected)
        await self._save_session(context_id, session_id or str(uuid.uuid4()))

        final_task = _make_task(
            task_id, context_id, TaskState.TASK_STATE_COMPLETED, full_text
        )
        # Persist
        task_dict = {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "response": full_text,
        }
        await self._store.save(task_dict, context)

        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_COMPLETED,
                message=Message(
                    role="ROLE_AGENT",
                    parts=[_text_part(full_text)],
                ),
            ),
        )

        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "message/stream completed: duration_ms=%.1f task_id=%s context_id=%s",
            duration_ms, task_id, context_id,
        )
        self._increment_metric("requests_total")

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task_dict = await self._store.get(params.id, context)
        if task_dict is None:
            return None
        state_map = {
            "completed": TaskState.TASK_STATE_COMPLETED,
            "working": TaskState.TASK_STATE_WORKING,
            "canceled": TaskState.TASK_STATE_CANCELED,
            "failed": TaskState.TASK_STATE_FAILED,
        }
        state = state_map.get(
            task_dict.get("status", {}).get("state", ""),
            TaskState.TASK_STATE_COMPLETED,
        )
        return _make_task(
            task_dict["id"],
            task_dict.get("contextId", ""),
            state,
            task_dict.get("response"),
        )

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task_dict = await self._store.get(params.id, context)
        if task_dict is None:
            return None

        # Validate current state is cancelable
        current_state_str = task_dict.get("status", {}).get("state", "")
        current_state = TaskStateMachine.state_from_str(current_state_str)

        if self._state_machine.is_terminal(current_state):
            logger.warning(
                "Cannot cancel task %s in terminal state '%s'",
                params.id, current_state_str,
            )
            return None

        task_dict["status"]["state"] = "canceled"
        await self._store.save(task_dict, context)
        return _make_task(
            task_dict["id"],
            task_dict.get("contextId", ""),
            TaskState.TASK_STATE_CANCELED,
        )

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        tasks_dicts = await self._store.list(params, context)
        state_map = {
            "completed": TaskState.TASK_STATE_COMPLETED,
            "working": TaskState.TASK_STATE_WORKING,
            "canceled": TaskState.TASK_STATE_CANCELED,
            "failed": TaskState.TASK_STATE_FAILED,
        }
        tasks = []
        for td in tasks_dicts:
            state = state_map.get(
                td.get("status", {}).get("state", ""),
                TaskState.TASK_STATE_COMPLETED,
            )
            tasks.append(
                _make_task(td["id"], td.get("contextId", ""), state, td.get("response"))
            )
        return ListTasksResponse(tasks=tasks)

    # ------------------------------------------------------------------
    # Push notifications — not supported, raise cleanly
    # ------------------------------------------------------------------

    async def on_create_task_push_notification_config(
        self, params, context
    ):
        from a2a.utils.errors import PushNotificationNotSupportedError
        raise PushNotificationNotSupportedError

    async def on_get_task_push_notification_config(
        self, params, context
    ):
        from a2a.utils.errors import PushNotificationNotSupportedError
        raise PushNotificationNotSupportedError

    async def on_list_task_push_notification_configs(
        self, params, context
    ):
        from a2a.utils.errors import PushNotificationNotSupportedError
        raise PushNotificationNotSupportedError

    async def on_delete_task_push_notification_config(
        self, params, context
    ):
        from a2a.utils.errors import PushNotificationNotSupportedError
        raise PushNotificationNotSupportedError

    async def on_subscribe_to_task(
        self, params, context
    ) -> AsyncGenerator[Event]:
        """Not supported — yields nothing."""
        return
        yield  # make this an async generator

    async def on_get_extended_agent_card(self, params, context):
        from a2a.utils.errors import ExtendedAgentCardNotConfiguredError
        raise ExtendedAgentCardNotConfiguredError
