"""Tests for A2A v0.3 backward-compatibility layer.

Validates that enabling ``enable_v0_3_compat=True`` allows the same endpoint
to serve both v1.0 (CamelCase) and v0.3 (slash-case) JSON-RPC method names.

The v0.3 adapter inside a2a-sdk converts v0.3 types ↔ v1.0 proto types, so
the handler stays unchanged.  We only verify end-to-end JSON-RPC behaviour.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

V03_HEADERS = {"A2A-Version": "0.3"}
V10_HEADERS = {"A2A-Version": "1.0"}


def _rpc(method: str, params: dict, rpc_id: str | int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": method,
        "params": params,
    }


def _v03_message_params(text: str) -> dict:
    """Build v0.3 message/send params (user message with text).

    v0.3 Message requires a ``messageId`` field (UUID) and uses camelCase
    ``role`` values (``"user"`` / ``"agent"``).
    """
    import uuid
    return {
        "message": {
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": text}],
        },
    }


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_hermes():
    """Mock HermesClient that returns canned responses."""
    client = AsyncMock()
    client.send_message.return_value = ("Hello from Hermes!", "sess-001")
    client.health_check.return_value = True
    return client


@pytest.fixture
def app_client(mock_hermes):
    """Create TestClient with mocked HermesClient but real TaskStore."""
    with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as client:
            yield client


# ------------------------------------------------------------------
# v0.3 message/send
# ------------------------------------------------------------------

def test_v03_message_send(app_client):
    """v0.3 method name 'message/send' should work with A2A-Version: 0.3."""
    payload = _rpc("message/send", _v03_message_params("Hello v0.3"))
    resp = app_client.post("/", json=payload, headers=V03_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # v0.3 adapter returns {"jsonrpc":"2.0", "id":..., "result": {"kind":"task", ...}}
    assert "result" in data, f"Expected result, got: {data}"
    result = data["result"]
    assert result.get("kind") == "task"
    assert result["status"]["state"] == "completed"


def test_v03_message_send_no_version_header(app_client):
    """Without A2A-Version header, version defaults to 0.3, and v0.3 methods
    should still work (because enable_v0_3_compat is True)."""
    payload = _rpc("message/send", _v03_message_params("No header"))
    resp = app_client.post("/", json=payload)  # no headers
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data, f"Expected result, got: {data}"
    assert data["result"]["kind"] == "task"


# ------------------------------------------------------------------
# v0.3 tasks/get
# ------------------------------------------------------------------

def test_v03_tasks_get_not_found(app_client):
    """v0.3 'tasks/get' for nonexistent task should return error.

    The v0.3 adapter catches the TaskNotFoundError as an internal error
    (-32603) since it propagates as a generic exception in the adapter.
    """
    payload = _rpc("tasks/get", {"id": "nonexistent-task-id"})
    resp = app_client.post("/", json=payload, headers=V03_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    # The v0.3 adapter wraps unhandled exceptions as InternalError (-32603)
    assert data["error"]["code"] == -32603


# ------------------------------------------------------------------
# v0.3 tasks/cancel
# ------------------------------------------------------------------

def test_v03_tasks_cancel_not_found(app_client):
    """v0.3 'tasks/cancel' for nonexistent task should return error."""
    payload = _rpc("tasks/cancel", {"id": "no-such-task"})
    resp = app_client.post("/", json=payload, headers=V03_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


# ------------------------------------------------------------------
# v0.3 tasks/list — not a v0.3 method, should fall through
# ------------------------------------------------------------------

def test_v03_tasks_list_falls_through_to_v10(app_client):
    """'tasks/list' is not a v0.3 method, so it should NOT be handled by
    the v0.3 adapter.  Since it's also not a v1.0 method name (ListTasks),
    we expect a method-not-found error."""
    payload = _rpc("tasks/list", {})
    resp = app_client.post("/", json=payload, headers=V03_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    # -32601 = Method not found
    assert data["error"]["code"] == -32601


# ------------------------------------------------------------------
# v1.0 methods still work (no regression)
# ------------------------------------------------------------------

def test_v10_send_message_still_works(app_client):
    """v1.0 'SendMessage' should continue to work unchanged."""
    payload = _rpc("SendMessage", {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Hello v1.0"}],
        },
    })
    resp = app_client.post("/", json=payload, headers=V10_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data
    result = data["result"]
    assert "task" in result
    assert result["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


# ------------------------------------------------------------------
# Mixed v0.3 and v1.0 requests to the same endpoint
# ------------------------------------------------------------------

def test_mixed_v03_and_v10_on_same_endpoint(app_client):
    """Send v0.3 then v1.0 requests to the same endpoint — both should work."""
    # v0.3 message/send
    v03_payload = _rpc("message/send", _v03_message_params("v0.3 call"), rpc_id=1)
    resp_v03 = app_client.post("/", json=v03_payload, headers=V03_HEADERS)
    assert resp_v03.status_code == 200
    data_v03 = resp_v03.json()
    assert "result" in data_v03
    assert data_v03["result"]["kind"] == "task"

    # v1.0 SendMessage
    v10_payload = _rpc("SendMessage", {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "v1.0 call"}],
        },
    }, rpc_id=2)
    resp_v10 = app_client.post("/", json=v10_payload, headers=V10_HEADERS)
    assert resp_v10.status_code == 200
    data_v10 = resp_v10.json()
    assert "result" in data_v10
    assert "task" in data_v10["result"]

    # Verify both got the mocked Hermes response
    assert "Hello from Hermes!" in resp_v03.text
    assert "Hello from Hermes!" in resp_v10.text


# ------------------------------------------------------------------
# v0.3 tasks/get after creating a task via message/send
# ------------------------------------------------------------------

def test_v03_tasks_get_after_send(app_client):
    """Create a task via v0.3 message/send, then retrieve it with tasks/get."""
    # Step 1: send message to create a task
    send_payload = _rpc("message/send", _v03_message_params("Create task"), rpc_id=10)
    resp_send = app_client.post("/", json=send_payload, headers=V03_HEADERS)
    assert resp_send.status_code == 200
    task_data = resp_send.json()["result"]
    task_id = task_data["id"]

    # Step 2: retrieve the task with tasks/get
    get_payload = _rpc("tasks/get", {"id": task_id}, rpc_id=11)
    resp_get = app_client.post("/", json=get_payload, headers=V03_HEADERS)
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert "result" in data_get, f"Expected result, got: {data_get}"
    assert data_get["result"]["id"] == task_id
    assert data_get["result"]["kind"] == "task"
    assert data_get["result"]["status"]["state"] == "completed"


# ------------------------------------------------------------------
# v0.3 tasks/cancel after creating a task
# ------------------------------------------------------------------

def test_v03_tasks_cancel_after_send(app_client):
    """Create a task via v0.3 message/send, then cancel it with tasks/cancel."""
    # Step 1: send message to create a task
    send_payload = _rpc("message/send", _v03_message_params("To cancel"), rpc_id=20)
    resp_send = app_client.post("/", json=send_payload, headers=V03_HEADERS)
    assert resp_send.status_code == 200
    task_id = resp_send.json()["result"]["id"]

    # Step 2: cancel the task
    cancel_payload = _rpc("tasks/cancel", {"id": task_id}, rpc_id=21)
    resp_cancel = app_client.post("/", json=cancel_payload, headers=V03_HEADERS)
    assert resp_cancel.status_code == 200
    data_cancel = resp_cancel.json()
    assert "result" in data_cancel, f"Expected result, got: {data_cancel}"
    assert data_cancel["result"]["id"] == task_id
    assert data_cancel["result"]["status"]["state"] == "canceled"
