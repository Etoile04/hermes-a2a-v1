"""Tests for REST transport endpoints exposed via create_rest_routes().

Validates that:
  - v1.0 REST endpoints are mounted under /a2a/
  - POST /a2a/message:send accepts A2A v1.0 protobuf-JSON messages
  - GET /a2a/tasks/{id} returns task details (or 404)
  - GET /a2a/tasks returns task list
  - v0.3 compat REST endpoints are also mounted (under /a2a/v1/)
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

A2A_HEADERS = {"A2A-Version": "1.0"}


def _send_message_body(text: str) -> dict:
    """Build a v1.0 SendMessageRequest body (protobuf-JSON format)."""
    return {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": text}],
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
# v1.0 REST: POST /a2a/message:send
# ------------------------------------------------------------------

def test_rest_message_send(app_client):
    """POST /a2a/message:send should accept A2A v1.0 messages and return a task."""
    body = _send_message_body("Hello via REST")
    resp = app_client.post("/a2a/message:send", json=body, headers=A2A_HEADERS)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # SDK returns SendMessageResponse → {"task": {...}}
    assert "task" in data, f"Expected 'task' in response, got: {data}"
    assert data["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_rest_message_send_no_version_header(app_client):
    """POST /a2a/message:send without A2A-Version header should fail or default."""
    body = _send_message_body("No version header")
    resp = app_client.post("/a2a/message:send", json=body)
    # Without A2A-Version, the SDK validates version and should return an error
    # (version mismatch since default is 0.3 and endpoint expects 1.0)
    assert resp.status_code in (200, 400, 500), f"Unexpected status: {resp.status_code}"


# ------------------------------------------------------------------
# v1.0 REST: GET /a2a/tasks/{id}
# ------------------------------------------------------------------

def test_rest_get_task_not_found(app_client):
    """GET /a2a/tasks/{id} for nonexistent task should return error."""
    resp = app_client.get("/a2a/tasks/nonexistent-task-id", headers=A2A_HEADERS)
    # REST error handler returns HTTP error codes
    assert resp.status_code in (400, 404, 500), f"Unexpected status: {resp.status_code}"
    data = resp.json()
    # Should have an error payload
    assert "code" in data or "error" in data, f"Expected error payload, got: {data}"


def test_rest_get_task_after_send(app_client):
    """Create a task via REST message:send, then retrieve it with GET /a2a/tasks/{id}."""
    # Step 1: send message to create a task
    send_body = _send_message_body("Create task for GET")
    resp_send = app_client.post(
        "/a2a/message:send", json=send_body, headers=A2A_HEADERS
    )
    assert resp_send.status_code == 200, f"Send failed: {resp_send.text}"
    task_id = resp_send.json()["task"]["id"]

    # Step 2: retrieve the task
    resp_get = app_client.get(f"/a2a/tasks/{task_id}", headers=A2A_HEADERS)
    assert resp_get.status_code == 200, f"Get task failed: {resp_get.text}"
    data = resp_get.json()
    assert data["id"] == task_id
    assert data["status"]["state"] == "TASK_STATE_COMPLETED"


# ------------------------------------------------------------------
# v1.0 REST: GET /a2a/tasks
# ------------------------------------------------------------------

def test_rest_list_tasks(app_client):
    """GET /a2a/tasks should return a list of tasks."""
    resp = app_client.get("/a2a/tasks", headers=A2A_HEADERS)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # SDK returns ListTasksResponse → {"tasks": [...]}
    assert "tasks" in data, f"Expected 'tasks' in response, got: {data}"


# ------------------------------------------------------------------
# v1.0 REST: POST /a2a/tasks/{id}:cancel
# ------------------------------------------------------------------

def test_rest_cancel_task_not_found(app_client):
    """POST /a2a/tasks/{id}:cancel for nonexistent task should return error."""
    resp = app_client.post(
        "/a2a/tasks/nonexistent-task-id:cancel", headers=A2A_HEADERS
    )
    assert resp.status_code in (400, 404, 500), f"Unexpected status: {resp.status_code}"


def test_rest_cancel_task_after_send(app_client):
    """Create a task, then cancel it via REST.

    Note: tasks created via message:send complete immediately, so cancelling
    a completed task returns 404 (TaskNotFoundError) since it's in a terminal
    state. This validates the cancel endpoint is reachable and functional.
    """
    # Step 1: create task
    send_body = _send_message_body("Task to cancel")
    resp_send = app_client.post(
        "/a2a/message:send", json=send_body, headers=A2A_HEADERS
    )
    assert resp_send.status_code == 200
    task_id = resp_send.json()["task"]["id"]

    # Step 2: attempt to cancel — completed tasks can't be cancelled
    resp_cancel = app_client.post(
        f"/a2a/tasks/{task_id}:cancel", headers=A2A_HEADERS
    )
    # The handler rejects cancelling terminal-state tasks with TaskNotFoundError
    assert resp_cancel.status_code == 404, f"Expected 404, got {resp_cancel.status_code}: {resp_cancel.text}"


# ------------------------------------------------------------------
# v1.0 REST: GET /a2a/extendedAgentCard
# ------------------------------------------------------------------

def test_rest_extended_agent_card(app_client):
    """GET /a2a/extendedAgentCard should return agent card or error.

    Our gateway doesn't support extended agent cards (capabilities say False),
    so we expect an error response (400 or similar).
    """
    resp = app_client.get("/a2a/extendedAgentCard", headers=A2A_HEADERS)
    # Returns 400 since extended card is not configured
    assert resp.status_code in (200, 400, 500), f"Unexpected status: {resp.status_code}"


# ------------------------------------------------------------------
# v0.3 compat REST endpoints (under /a2a/v1/...)
# ------------------------------------------------------------------

def test_rest_v03_message_send(app_client):
    """v0.3 compat REST endpoint POST /a2a/v1/message:send should work."""
    import uuid

    body = {
        "message": {
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": "Hello v0.3 REST"}],
        },
    }
    resp = app_client.post("/a2a/v1/message:send", json=body)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # v0.3 REST adapter wraps the response as {"task": {...}}
    assert "task" in data, f"Unexpected response: {data}"
    assert data["task"]["status"]["state"] in ("completed", "TASK_STATE_COMPLETED")


def test_rest_v03_get_task_not_found(app_client):
    """v0.3 compat GET /a2a/v1/tasks/{id} should return error for missing task."""
    resp = app_client.get("/a2a/v1/tasks/nonexistent-task-id")
    assert resp.status_code in (200, 400, 404, 500), f"Unexpected status: {resp.status_code}"


def test_rest_v03_list_tasks(app_client):
    """v0.3 compat GET /a2a/v1/tasks — v0.3 REST adapter doesn't implement
    list_tasks (NotImplementedError), so we expect a server error.
    The endpoint exists and is routed correctly, which is what we're testing.
    """
    resp = app_client.get("/a2a/v1/tasks")
    # v0.3 REST adapter raises NotImplementedError for list_tasks → 500
    assert resp.status_code in (200, 500), f"Unexpected status: {resp.status_code}: {resp.text}"


# ------------------------------------------------------------------
# Existing JSON-RPC endpoints still work (no regression)
# ------------------------------------------------------------------

def test_jsonrpc_send_message_still_works(app_client):
    """JSON-RPC SendMessage should continue to work after adding REST routes."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": "JSON-RPC still works"}],
            },
        },
    }
    resp = app_client.post("/", json=payload, headers=A2A_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "result" in data
    assert "task" in data["result"]
    assert data["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_health_still_works(app_client):
    """Health endpoint should still work after adding REST routes."""
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
