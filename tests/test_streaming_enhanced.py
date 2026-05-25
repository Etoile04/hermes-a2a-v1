"""Tests for enhanced streaming: heartbeat keepalive, error handling, session save."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    SendMessageRequest,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from hermes_a2a.a2a_handler import HermesA2AHandler
from hermes_a2a.hermes_client import HermesClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_request(text: str, context_id: str = "") -> SendMessageRequest:
    """Build a SendMessageRequest with a single text part."""
    return SendMessageRequest(
        message=Message(
            role="ROLE_USER",
            parts=[Part(text=text)],
            context_id=context_id,
        ),
    )


def _make_handler(
    mock_hermes: AsyncMock,
    task_store,
    session_store=None,
) -> HermesA2AHandler:
    return HermesA2AHandler(mock_hermes, task_store, session_store)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_hermes():
    """Mock HermesClient."""
    client = AsyncMock(spec=HermesClient)
    client.send_message = AsyncMock(return_value=("Hello!", "sess-1"))
    client.health_check = AsyncMock(return_value=True)
    return client


@pytest.fixture
async def task_store(tmp_path):
    from hermes_a2a.task_store import SQLiteTaskStore
    store = SQLiteTaskStore(str(tmp_path / "test_stream.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def session_store(tmp_path):
    from hermes_a2a.session_store import SessionStore
    store = SessionStore(str(tmp_path / "test_session.db"))
    await store.init()
    yield store
    await store.close()


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestHeartbeatOnSlowResponse:
    """Heartbeat fires when no chunk arrives within the timeout window."""

    @pytest.mark.asyncio
    async def test_heartbeat_fires_on_slow_chunk(self, mock_hermes, task_store):
        """A slow-producing stream should emit WORKING heartbeat events.

        We use a stream that blocks between chunks long enough to trigger
        the heartbeat timeout.  An asyncio.Queue buffers items so that
        chunks aren't lost during the heartbeat cycle.
        """
        q: asyncio.Queue[str | None] = asyncio.Queue()

        async def queued_stream(*a, **kw):
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: queued_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("slow test")

        # Patch the heartbeat timeout to 0.1s for fast testing.
        # We monkey-patch asyncio.wait to inject a short timeout.
        original_wait = asyncio.wait

        async def patched_wait(fs, timeout=None):
            if timeout is not None and timeout == 15.0:
                return await original_wait(fs, timeout=0.1)
            return await original_wait(fs, timeout=timeout)

        import unittest.mock as _mock
        with _mock.patch("hermes_a2a.a2a_handler.asyncio.wait", side_effect=patched_wait):
            # Start collection in a background task
            events: list = []

            async def collect():
                async for event in handler.on_message_send_stream(req, ctx):
                    events.append(event)

            task = asyncio.create_task(collect())

            # Feed chunk1 immediately
            await q.put("chunk1")
            # Wait long enough for heartbeat to fire (0.1s timeout + margin)
            await asyncio.sleep(0.35)
            # Feed chunk2
            await q.put("chunk2")
            # Signal end
            await asyncio.sleep(0.05)
            await q.put(None)

            await asyncio.wait_for(task, timeout=2.0)

        # Analyze events
        status_events = [
            e for e in events if isinstance(e, TaskStatusUpdateEvent)
        ]
        artifact_events = [
            e for e in events if isinstance(e, TaskArtifactUpdateEvent)
        ]

        # Should have at least: initial WORKING + 1+ heartbeat WORKING + COMPLETED
        working_count = sum(
            1 for e in status_events
            if e.status.state == TaskState.TASK_STATE_WORKING
        )
        assert working_count >= 2, (
            f"Expected >= 2 WORKING events (initial + heartbeat), got {working_count}. "
            f"Total status events: {len(status_events)}"
        )
        # Both chunks should have been received as artifacts
        assert len(artifact_events) == 2, (
            f"Expected 2 artifact events, got {len(artifact_events)}"
        )


class TestMultipleChunksStream:
    """Multiple chunks should stream through correctly as artifact events."""

    @pytest.mark.asyncio
    async def test_multiple_chunks_stream_correctly(
        self, mock_hermes, task_store
    ):
        """All chunks from the stream should appear as artifact events."""
        chunks = ["Hello", " from", " Hermes", "!"]

        async def multi_stream(*a, **kw):
            for c in chunks:
                yield c

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: multi_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("multi chunk test")

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        artifact_events = [
            e for e in events if isinstance(e, TaskArtifactUpdateEvent)
        ]
        assert len(artifact_events) == len(chunks)

        # Verify each chunk's text content
        for i, art_event in enumerate(artifact_events):
            part = art_event.artifact.parts[0]
            assert part.text == chunks[i]

        # Last event should be COMPLETED
        final = events[-1]
        assert isinstance(final, TaskStatusUpdateEvent)
        assert final.status.state == TaskState.TASK_STATE_COMPLETED
        assert final.status.message.parts[0].text == "".join(chunks)


class TestErrorDuringStreaming:
    """Errors during streaming should yield a FAILED event and return."""

    @pytest.mark.asyncio
    async def test_error_yields_failed_event(self, mock_hermes, task_store):
        """If the stream raises, a FAILED TaskStatusUpdateEvent is yielded."""
        async def failing_stream(*a, **kw):
            yield "partial"
            raise RuntimeError("upstream exploded")

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: failing_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("error test")

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        # Should have: WORKING(initial) + artifact("partial") + FAILED
        assert len(events) == 3

        # First: initial WORKING
        assert events[0].status.state == TaskState.TASK_STATE_WORKING

        # Second: artifact with "partial"
        assert isinstance(events[1], TaskArtifactUpdateEvent)
        assert events[1].artifact.parts[0].text == "partial"

        # Third: FAILED status
        final = events[2]
        assert isinstance(final, TaskStatusUpdateEvent)
        assert final.status.state == TaskState.TASK_STATE_FAILED
        assert "upstream exploded" in final.status.message.parts[0].text

    @pytest.mark.asyncio
    async def test_connection_error_yields_failed(self, mock_hermes, task_store):
        """Connection errors before any chunk also yield FAILED."""
        async def conn_err_stream(*a, **kw):
            raise ConnectionError("refused")
            yield  # noqa: unreachable — makes this an async generator

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: conn_err_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("conn error test")

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        # WORKING + FAILED
        assert len(events) == 2
        assert events[0].status.state == TaskState.TASK_STATE_WORKING
        assert events[1].status.state == TaskState.TASK_STATE_FAILED


class TestSessionSavedAfterStreaming:
    """Session ID should be saved after successful streaming."""

    @pytest.mark.asyncio
    async def test_session_saved(self, mock_hermes, task_store):
        """After streaming, the session should be persisted in the handler."""
        async def simple_stream(*a, **kw):
            yield "Hello!"

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: simple_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("session test", context_id="ctx-save-test")

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        # Session should be stored in the handler's _sessions dict
        assert "ctx-save-test" in handler._sessions
        assert handler._sessions["ctx-save-test"] is not None

    @pytest.mark.asyncio
    async def test_session_persisted_to_store(
        self, mock_hermes, task_store, session_store
    ):
        """Session should be saved to SessionStore when provided."""
        async def simple_stream(*a, **kw):
            yield "Persisted!"

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: simple_stream()
        )

        handler = _make_handler(mock_hermes, task_store, session_store)
        ctx = ServerCallContext()
        req = _make_request("persist test", context_id="ctx-persist")

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        # Verify in session store
        saved = await session_store.get("ctx-persist")
        assert saved is not None


class TestRapidChunksNoHeartbeat:
    """Rapid chunks should not trigger heartbeat events."""

    @pytest.mark.asyncio
    async def test_rapid_chunks_no_heartbeat(
        self, mock_hermes, task_store, monkeypatch
    ):
        """When chunks arrive quickly, no heartbeat WORKING events appear."""
        async def fast_stream(*a, **kw):
            for c in ["a", "b", "c", "d", "e"]:
                yield c

        mock_hermes.send_message_stream.side_effect = (
            lambda *a, **kw: fast_stream()
        )

        handler = _make_handler(mock_hermes, task_store)
        ctx = ServerCallContext()
        req = _make_request("fast test")

        # Make asyncio.wait never time out so no heartbeat fires
        original_wait = asyncio.wait

        async def no_timeout_wait(fs, timeout=None):
            return await original_wait(fs, timeout=None)

        monkeypatch.setattr(
            "hermes_a2a.a2a_handler.asyncio.wait", no_timeout_wait
        )

        events = []
        async for event in handler.on_message_send_stream(req, ctx):
            events.append(event)

        status_events = [
            e for e in events if isinstance(e, TaskStatusUpdateEvent)
        ]
        artifact_events = [
            e for e in events if isinstance(e, TaskArtifactUpdateEvent)
        ]

        # Should only have: initial WORKING + final COMPLETED (no heartbeat)
        assert len(status_events) == 2, (
            f"Expected exactly 2 status events (no heartbeat), got {len(status_events)}"
        )
        assert status_events[0].status.state == TaskState.TASK_STATE_WORKING
        assert status_events[1].status.state == TaskState.TASK_STATE_COMPLETED
        assert len(artifact_events) == 5
