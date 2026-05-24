"""HermesRequestHandler — routes A2A requests to Hermes Agent via HermesClient."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any


class HermesRequestHandler:
    """Handles A2A requests by routing to Hermes Agent via HermesClient."""

    def __init__(
        self,
        hermes_client: Any,
        task_store: Any,
        session_map: dict[str, str] | None = None,
    ) -> None:
        self.hermes_client = hermes_client
        self.task_store = task_store
        # context_id -> hermes session_id
        self.session_map: dict[str, str] = session_map or {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text(self, params: Any) -> str:
        """Extract text content from A2A message params.

        ``params`` may be a dict-like or object with
        ``.message.parts[].text``.  Handles both dict and attribute access
        patterns.
        """
        try:
            # Dict-style access
            if isinstance(params, dict):
                message = params.get("message", {})
                parts = message.get("parts", [])
                texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
                return "".join(texts)
            # Attribute-style access
            message = params.message
            parts = message.parts
            return "".join(part.text for part in parts)
        except (AttributeError, TypeError, IndexError):
            return ""

    # ------------------------------------------------------------------
    # Non-streaming message/send
    # ------------------------------------------------------------------

    async def on_message_send(self, params: Any, context: Any = None) -> dict:
        """Handle A2A ``message/send``.

        1. Extract text from *params*.
        2. Get/create ``contextId``.
        3. Look up Hermes ``session_id`` from ``session_map``.
        4. Call ``hermes_client.send_message(text, session_id)``.
        5. Store session mapping.
        6. Build task dict with *id*, *contextId*, *status*: completed, artifacts.
        7. Save to ``task_store``.
        8. Return task dict.
        """
        text = self._extract_text(params)

        # Determine or create contextId
        if isinstance(params, dict):
            context_id = params.get("contextId")
        else:
            context_id = getattr(params, "contextId", None)
        if not context_id:
            context_id = str(uuid.uuid4())

        # Look up existing Hermes session for this context
        session_id = self.session_map.get(context_id)

        # Call Hermes
        response_text, new_session_id = await self.hermes_client.send_message(
            text, session_id
        )

        # Persist session mapping
        self.session_map[context_id] = new_session_id

        # Build task
        task_id = str(uuid.uuid4())
        task: dict = {
            "id": task_id,
            "contextId": context_id,
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"text": response_text}],
                },
            },
            "artifacts": [],
        }

        await self.task_store.save(task, context)
        return task

    # ------------------------------------------------------------------
    # Streaming message/send
    # ------------------------------------------------------------------

    async def on_message_send_stream(
        self, params: Any, context: Any = None
    ) -> AsyncGenerator[dict, None]:
        """Handle A2A ``message/send`` with SSE streaming.

        Yields:
          1. A ``status_update`` event (state: working).
          2. ``artifact_update`` events for each text chunk.
          3. Saves the final task and yields it.
        """
        text = self._extract_text(params)

        # Determine or create contextId
        if isinstance(params, dict):
            context_id = params.get("contextId")
        else:
            context_id = getattr(params, "contextId", None)
        if not context_id:
            context_id = str(uuid.uuid4())

        session_id = self.session_map.get(context_id)
        task_id = str(uuid.uuid4())

        # 1. Yield working status
        yield {
            "type": "status_update",
            "task": {
                "id": task_id,
                "contextId": context_id,
                "status": {"state": "working"},
            },
        }

        # 2. Stream chunks as artifact updates
        collected_parts: list[str] = []
        async for chunk in self.hermes_client.send_message_stream(text, session_id):
            collected_parts.append(chunk)
            yield {
                "type": "artifact_update",
                "task": {
                    "id": task_id,
                    "contextId": context_id,
                    "status": {"state": "working"},
                    "artifacts": [{"parts": [{"text": chunk}]}],
                },
            }

        # 3. Finalise
        full_text = "".join(collected_parts)
        final_task: dict = {
            "id": task_id,
            "contextId": context_id,
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"text": full_text}],
                },
            },
            "artifacts": [],
        }

        # Store session mapping
        self.session_map[context_id] = session_id or str(uuid.uuid4())

        await self.task_store.save(final_task, context)
        yield {
            "type": "status_update",
            "task": final_task,
        }

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def on_get_task(self, params: Any, context: Any = None) -> dict | None:
        """Get task by ID from ``task_store``.  *params* should have an ``id`` key."""
        if isinstance(params, dict):
            task_id = params.get("id")
        else:
            task_id = getattr(params, "id", None)
        return await self.task_store.get(task_id, context)

    async def on_cancel_task(self, params: Any, context: Any = None) -> dict | None:
        """Cancel task: get from store, set state to *canceled*, save back."""
        if isinstance(params, dict):
            task_id = params.get("id")
        else:
            task_id = getattr(params, "id", None)

        task = await self.task_store.get(task_id, context)
        if task is None:
            return None

        task["status"]["state"] = "canceled"
        await self.task_store.save(task, context)
        return task

    async def on_list_tasks(self, params: Any = None, context: Any = None) -> dict:
        """Return all tasks from ``task_store`` as ``{"tasks": [...]}``."""
        tasks = await self.task_store.list(params, context)
        return {"tasks": tasks}
