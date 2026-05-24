"""Tests for HermesRequestHandler."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from hermes_a2a.handler import HermesRequestHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_params(text: str, context_id: str | None = None) -> dict:
    """Build a minimal A2A message/send params dict."""
    p = {
        "message": {
            "parts": [{"text": text}],
        },
    }
    if context_id is not None:
        p["contextId"] = context_id
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def hermes_client():
    client = AsyncMock()
    # Default: send_message returns (response_text, session_id)
    client.send_message = AsyncMock(return_value=("Hello back!", "sess-001"))
    return client


@pytest.fixture
def task_store():
    store = AsyncMock()
    store.save = AsyncMock()
    # Default: list returns empty
    store.list = AsyncMock(return_value=[])
    return store


@pytest.fixture
def handler(hermes_client, task_store):
    return HermesRequestHandler(hermes_client, task_store)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_message_send_basic(handler, hermes_client, task_store):
    """Verify message sent to Hermes, task saved and returned."""
    params = _make_params("Hello Hermes")

    result = await handler.on_message_send(params)

    # Hermes client was called with the extracted text
    hermes_client.send_message.assert_awaited_once_with("Hello Hermes", None)

    # Task was saved
    task_store.save.assert_awaited_once()
    saved_task = task_store.save.call_args[0][0]

    # Verify task structure
    assert saved_task["status"]["state"] == "completed"
    assert saved_task["status"]["message"]["role"] == "agent"
    assert "Hello back!" in saved_task["status"]["message"]["parts"][0]["text"]

    # Return value is the task dict
    assert result == saved_task


@pytest.mark.asyncio
async def test_on_message_send_multi_turn(handler, hermes_client, task_store):
    """Second message with same contextId reuses Hermes session."""
    params1 = _make_params("Hi", "ctx-123")
    await handler.on_message_send(params1)

    # After first call, session should be mapped
    assert "ctx-123" in handler.session_map

    # Now send a second message with the same contextId
    hermes_client.send_message.reset_mock()
    hermes_client.send_message.return_value = ("Follow-up reply", "sess-001")
    params2 = _make_params("Follow up", "ctx-123")
    await handler.on_message_send(params2)

    # Should have been called with the existing session_id
    hermes_client.send_message.assert_awaited_once_with("Follow up", "sess-001")


@pytest.mark.asyncio
async def test_on_get_task(handler, task_store):
    """Returns stored task by ID."""
    stored = {"id": "task-42", "status": {"state": "completed"}}
    task_store.get = AsyncMock(return_value=stored)

    result = await handler.on_get_task({"id": "task-42"})

    task_store.get.assert_awaited_once_with("task-42", None)
    assert result == stored


@pytest.mark.asyncio
async def test_on_get_task_missing(handler, task_store):
    """Returns None when task not found."""
    task_store.get = AsyncMock(return_value=None)
    result = await handler.on_get_task({"id": "nonexistent"})
    assert result is None


@pytest.mark.asyncio
async def test_on_cancel_task(handler, task_store):
    """Sets state to canceled and saves."""
    stored = {"id": "task-99", "status": {"state": "working"}}
    task_store.get = AsyncMock(return_value=stored)

    result = await handler.on_cancel_task({"id": "task-99"})

    # Should have saved back with canceled state
    task_store.save.assert_awaited_once()
    saved = task_store.save.call_args[0][0]
    assert saved["status"]["state"] == "canceled"
    assert result == saved


@pytest.mark.asyncio
async def test_on_cancel_task_missing(handler, task_store):
    """Returns None when trying to cancel a nonexistent task."""
    task_store.get = AsyncMock(return_value=None)
    result = await handler.on_cancel_task({"id": "nope"})
    assert result is None
    task_store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_list_tasks(handler, task_store):
    """Returns all tasks from task_store."""
    tasks = [
        {"id": "t1", "status": {"state": "completed"}},
        {"id": "t2", "status": {"state": "working"}},
    ]
    task_store.list = AsyncMock(return_value=tasks)

    result = await handler.on_list_tasks({})

    assert result == {"tasks": tasks}


def test_extract_text_from_dict(handler):
    """Text extraction from dict-style params."""
    params = _make_params("Hello world")
    assert handler._extract_text(params) == "Hello world"


def test_extract_text_from_object(handler):
    """Text extraction from attribute-style params."""
    part = MagicMock()
    part.text = "Attribute text"
    msg = MagicMock()
    msg.parts = [part]
    params = MagicMock()
    params.message = msg

    assert handler._extract_text(params) == "Attribute text"


def test_extract_text_fallback_empty(handler):
    """Returns empty string when no text found."""
    assert handler._extract_text({}) == ""
