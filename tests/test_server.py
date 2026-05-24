"""Tests for the FastAPI server and A2A handler integration."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

A2A_HEADERS = {"A2A-Version": "1.0"}


def _rpc(method: str, params: dict, rpc_id: str | int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": method,
        "params": params,
    }


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_hermes():
    """Mock HermesClient that returns canned responses."""
    client = AsyncMock()
    client.send_message.return_value = ("Hello from Hermes!", "sess-001")

    async def _stream(*a, **kw):
        for chunk in ["Hello", " from", " Hermes!"]:
            yield chunk

    client.send_message_stream.side_effect = lambda *a, **kw: _stream()
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
# Health check
# ------------------------------------------------------------------

def test_health_endpoint(app_client):
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ------------------------------------------------------------------
# Agent card
# ------------------------------------------------------------------

def test_agent_card_endpoint(app_client):
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Hermes Agent"
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False
    assert len(card["skills"]) >= 1


# ------------------------------------------------------------------
# JSON-RPC: SendMessage
# ------------------------------------------------------------------

def test_jsonrpc_send_message(app_client):
    """Test A2A SendMessage via JSON-RPC."""
    payload = _rpc("SendMessage", {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
        },
    })
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data, f"Expected result, got: {data}"
    result = data["result"]
    # SDK wraps as SendMessageResponse → {"task": {...}}
    assert "task" in result
    assert result["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


# ------------------------------------------------------------------
# JSON-RPC: GetTask (not found)
# ------------------------------------------------------------------

def test_jsonrpc_get_task_not_found(app_client):
    payload = _rpc("GetTask", {"id": "nonexistent-task-id"})
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    # TaskNotFoundError code from a2a-sdk
    assert data["error"]["code"] == -32001


# ------------------------------------------------------------------
# JSON-RPC: ListTasks
# ------------------------------------------------------------------

def test_jsonrpc_list_tasks(app_client):
    payload = _rpc("ListTasks", {})
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data, f"Expected result, got: {data}"
    assert "tasks" in data["result"]


# ------------------------------------------------------------------
# JSON-RPC: CancelTask (not found)
# ------------------------------------------------------------------

def test_jsonrpc_cancel_task_not_found(app_client):
    payload = _rpc("CancelTask", {"id": "no-such-task"})
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


# ------------------------------------------------------------------
# JSON-RPC: invalid method
# ------------------------------------------------------------------

def test_jsonrpc_invalid_method(app_client):
    payload = _rpc("nonexistent/method", {})
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32601


# ------------------------------------------------------------------
# JSON-RPC: SendStreamingMessage (SSE)
# ------------------------------------------------------------------

def test_jsonrpc_send_streaming_message(app_client):
    """Test SendStreamingMessage returns SSE stream."""
    payload = _rpc("SendStreamingMessage", {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Stream test"}],
        },
    })
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200
    # SSE response
    ct = resp.headers.get("content-type", "")
    assert "text/event-stream" in ct, f"Expected SSE, got: {ct}"
    body = resp.text
    assert "data:" in body


# ------------------------------------------------------------------
# JSON-RPC: version mismatch
# ------------------------------------------------------------------

def test_jsonrpc_version_mismatch(app_client):
    """Wrong A2A-Version header should return error."""
    payload = _rpc("SendMessage", {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "test"}],
        },
    })
    # No A2A-Version header → defaults to 0.3 → mismatch with 1.0 handler
    resp = app_client.post("/", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # VersionNotSupportedError or just error
    assert "error" in data
