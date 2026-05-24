"""Tests for enhanced /health and /metrics endpoints."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_hermes_ok():
    """Mock HermesClient that reports healthy."""
    client = AsyncMock()
    client.send_message.return_value = ("Hello!", "sess-001")
    client.health_check.return_value = True
    return client


@pytest.fixture
def mock_hermes_down():
    """Mock HermesClient that reports unreachable."""
    client = AsyncMock()
    client.send_message.return_value = ("Hello!", "sess-001")
    client.health_check.return_value = False
    return client


@pytest.fixture
def app_client_ok(mock_hermes_ok):
    """TestClient with a healthy mock HermesClient."""
    with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as client:
            yield client


@pytest.fixture
def app_client_down(mock_hermes_down):
    """TestClient with an unreachable mock HermesClient."""
    with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_down):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as client:
            yield client


# ------------------------------------------------------------------
# /health — Hermes reachable
# ------------------------------------------------------------------

def test_health_hermes_reachable(app_client_ok):
    """Health endpoint returns correct structure when Hermes is reachable."""
    resp = app_client_ok.get("/health")
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "ok"
    assert data["hermes_api"]["reachable"] is True
    assert isinstance(data["hermes_api"]["latency_ms"], (int, float))
    assert data["hermes_api"]["latency_ms"] >= 0

    assert data["task_store"]["type"] == "sqlite"
    assert isinstance(data["task_store"]["db_path"], str)

    assert isinstance(data["sessions"]["active"], int)
    assert data["sessions"]["active"] >= 0

    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["uptime_seconds"] >= 0

    assert data["version"] == "0.1.0"


# ------------------------------------------------------------------
# /health — Hermes unreachable (degraded)
# ------------------------------------------------------------------

def test_health_hermes_unreachable_returns_degraded(app_client_down):
    """Health endpoint returns status=degraded when Hermes is unreachable."""
    resp = app_client_down.get("/health")
    assert resp.status_code == 200

    data = resp.json()
    assert data["status"] == "degraded"
    assert data["hermes_api"]["reachable"] is False
    assert isinstance(data["hermes_api"]["latency_ms"], (int, float))


# ------------------------------------------------------------------
# /metrics — basic structure
# ------------------------------------------------------------------

def test_metrics_endpoint_returns_counters(app_client_ok):
    """Metrics endpoint returns counter fields."""
    resp = app_client_ok.get("/metrics")
    assert resp.status_code == 200

    data = resp.json()
    assert "requests_total" in data
    assert "errors_total" in data
    assert "active_tasks" in data
    assert "sessions_active" in data

    assert isinstance(data["requests_total"], int)
    assert isinstance(data["errors_total"], int)
    assert isinstance(data["active_tasks"], int)
    assert isinstance(data["sessions_active"], int)


def test_metrics_counters_increment_on_requests(app_client_ok):
    """Metrics counters increment after processing a request."""
    # Get baseline
    resp0 = app_client_ok.get("/metrics")
    baseline = resp0.json()

    # Send a message
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": "Hello"}],
            },
        },
    }
    resp = app_client_ok.post("/", json=payload, headers={"A2A-Version": "1.0"})
    assert resp.status_code == 200

    # Check metrics incremented
    resp1 = app_client_ok.get("/metrics")
    updated = resp1.json()
    assert updated["requests_total"] > baseline["requests_total"]
    assert updated["active_tasks"] > baseline["active_tasks"]


# ------------------------------------------------------------------
# /health — Hermes health_check raises exception
# ------------------------------------------------------------------

def test_health_hermes_exception_returns_degraded():
    """Health endpoint returns degraded when health_check raises."""
    client = AsyncMock()
    client.send_message.return_value = ("Hello!", "sess-001")
    client.health_check.side_effect = Exception("connection refused")

    with patch("hermes_a2a.server.HermesClient", return_value=client):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as tc:
            resp = tc.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["hermes_api"]["reachable"] is False
