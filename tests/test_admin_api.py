"""Tests for Admin API endpoints."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_a2a.models import AuthConfig, GatewayConfig, PeerConfig, TaskStoreConfig


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


def _make_cfg(tmp_path, *, auth_enabled=False, token="", admin_token=""):
    """Build a GatewayConfig that uses a temp DB."""
    cfg = GatewayConfig()
    cfg.task_store = TaskStoreConfig(path=str(tmp_path / "test.db"))
    cfg.auth.enabled = auth_enabled
    cfg.auth.token = token
    cfg.auth.admin_token = admin_token
    return cfg


@pytest.fixture
def app_client(mock_hermes_ok, tmp_path):
    """TestClient with no auth and a fresh temp DB — admin routes accessible."""
    cfg = _make_cfg(tmp_path)

    with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok), \
         patch("hermes_a2a.server.load_config", return_value=cfg):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as client:
            yield client


# ------------------------------------------------------------------
# GET /admin/peers
# ------------------------------------------------------------------

class TestAdminPeersList:
    """GET /admin/peers"""

    def test_returns_peers_list(self, app_client):
        resp = app_client.get("/admin/peers")
        assert resp.status_code == 200
        data = resp.json()
        assert "peers" in data
        assert isinstance(data["peers"], list)

    def test_empty_peers_when_none_configured(self, app_client):
        resp = app_client.get("/admin/peers")
        data = resp.json()
        assert data["peers"] == []


# ------------------------------------------------------------------
# POST /admin/peers
# ------------------------------------------------------------------

class TestAdminPeersAdd:
    """POST /admin/peers"""

    def test_add_peer_success(self, app_client):
        payload = {
            "name": "new-agent",
            "agent_card_url": "http://new-agent/.well-known/agent-card.json",
        }
        resp = app_client.post("/admin/peers", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert "added" in data["message"]
        assert data["peer"]["name"] == "new-agent"

    def test_add_peer_missing_fields(self, app_client):
        resp = app_client.post("/admin/peers", json={"name": "only-name"})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_add_duplicate_peer_returns_409(self, app_client):
        payload = {
            "name": "dup-agent",
            "agent_card_url": "http://dup/.well-known/agent-card.json",
        }
        resp1 = app_client.post("/admin/peers", json=payload)
        assert resp1.status_code == 201

        resp2 = app_client.post("/admin/peers", json=payload)
        assert resp2.status_code == 409


# ------------------------------------------------------------------
# DELETE /admin/peers/{name}
# ------------------------------------------------------------------

class TestAdminPeersRemove:
    """DELETE /admin/peers/{name}"""

    def test_remove_existing_peer(self, app_client):
        # Add first
        app_client.post("/admin/peers", json={
            "name": "to-remove",
            "agent_card_url": "http://remove/.well-known/agent-card.json",
        })
        resp = app_client.delete("/admin/peers/to-remove")
        assert resp.status_code == 200
        data = resp.json()
        assert "removed" in data["message"]

    def test_remove_nonexistent_peer_returns_404(self, app_client):
        resp = app_client.delete("/admin/peers/no-such-peer")
        assert resp.status_code == 404


# ------------------------------------------------------------------
# POST /admin/peers/{name}/check
# ------------------------------------------------------------------

class TestAdminPeersCheck:
    """POST /admin/peers/{name}/check"""

    def test_check_existing_peer(self, app_client):
        # Add a peer first
        app_client.post("/admin/peers", json={
            "name": "check-agent",
            "agent_card_url": "http://check/.well-known/agent-card.json",
        })
        # Mock the discover_one method on the peer manager
        gw = app_client.app.state.gateway
        pm = gw["peer_manager"]

        async def _fake_discover(peer_cfg):
            return {"name": peer_cfg.name, "status": "available", "version": "1.0"}

        pm._discover_one = _fake_discover

        resp = app_client.post("/admin/peers/check-agent/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "available"

    def test_check_nonexistent_peer_returns_404(self, app_client):
        resp = app_client.post("/admin/peers/nonexistent/check")
        assert resp.status_code == 404


# ------------------------------------------------------------------
# GET /admin/tasks
# ------------------------------------------------------------------

class TestAdminTasksList:
    """GET /admin/tasks"""

    def test_returns_empty_tasks(self, app_client):
        resp = app_client.get("/admin/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert "count" in data
        assert data["count"] == 0

    def test_returns_tasks_after_creating(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]

        asyncio.get_event_loop().run_until_complete(ts.save({
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": 1},
        }))

        resp = app_client.get("/admin/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "task-1"

    def test_filter_by_status(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]
        loop = asyncio.get_event_loop()

        loop.run_until_complete(ts.save({"id": "t1", "status": {"state": 1}}))
        loop.run_until_complete(ts.save({"id": "t2", "status": {"state": 2}}))
        loop.run_until_complete(ts.save({"id": "t3", "status": {"state": 1}}))

        resp = app_client.get("/admin/tasks?status=1")
        data = resp.json()
        assert data["count"] == 2

    def test_filter_by_context_id(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]
        loop = asyncio.get_event_loop()

        loop.run_until_complete(ts.save({"id": "t1", "contextId": "ctx-a"}))
        loop.run_until_complete(ts.save({"id": "t2", "contextId": "ctx-b"}))

        resp = app_client.get("/admin/tasks?context_id=ctx-a")
        data = resp.json()
        assert data["count"] == 1
        assert data["tasks"][0]["id"] == "t1"

    def test_limit_param(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]
        loop = asyncio.get_event_loop()

        for i in range(5):
            loop.run_until_complete(ts.save({"id": f"t-{i}", "status": {"state": 1}}))

        resp = app_client.get("/admin/tasks?limit=2")
        data = resp.json()
        assert data["count"] == 2


# ------------------------------------------------------------------
# DELETE /admin/tasks/{task_id}
# ------------------------------------------------------------------

class TestAdminTasksDelete:
    """DELETE /admin/tasks/{task_id}"""

    def test_delete_existing_task(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]
        loop = asyncio.get_event_loop()
        loop.run_until_complete(ts.save({"id": "del-me", "status": {"state": 1}}))

        resp = app_client.delete("/admin/tasks/del-me")
        assert resp.status_code == 200
        data = resp.json()
        assert "deleted" in data["message"]

    def test_delete_nonexistent_task_returns_404(self, app_client):
        resp = app_client.delete("/admin/tasks/no-task")
        assert resp.status_code == 404


# ------------------------------------------------------------------
# GET /admin/metrics
# ------------------------------------------------------------------

class TestAdminMetrics:
    """GET /admin/metrics"""

    def test_returns_detailed_metrics(self, app_client):
        resp = app_client.get("/admin/metrics")
        assert resp.status_code == 200
        data = resp.json()

        assert "uptime_seconds" in data
        assert "requests_total" in data
        assert "errors_total" in data
        assert "active_sessions" in data
        assert "peer_count" in data
        assert "task_count_by_status" in data
        assert "total_tasks" in data
        assert "version" in data

        assert isinstance(data["uptime_seconds"], (int, float))
        assert isinstance(data["requests_total"], int)
        assert isinstance(data["errors_total"], int)
        assert isinstance(data["active_sessions"], int)
        assert isinstance(data["peer_count"], int)
        assert isinstance(data["task_count_by_status"], dict)
        assert isinstance(data["total_tasks"], int)

    def test_metrics_reflects_added_peers(self, app_client):
        # Add a peer
        app_client.post("/admin/peers", json={
            "name": "metrics-peer",
            "agent_card_url": "http://metrics/.well-known/agent-card.json",
        })

        resp = app_client.get("/admin/metrics")
        data = resp.json()
        assert data["peer_count"] >= 1

    def test_metrics_shows_task_counts_by_status(self, app_client):
        gw = app_client.app.state.gateway
        ts = gw["task_store"]
        loop = asyncio.get_event_loop()

        loop.run_until_complete(ts.save({"id": "m1", "status": {"state": 1}}))
        loop.run_until_complete(ts.save({"id": "m2", "status": {"state": 2}}))

        resp = app_client.get("/admin/metrics")
        data = resp.json()
        assert data["task_count_by_status"].get("1", 0) >= 1
        assert data["task_count_by_status"].get("2", 0) >= 1


# ------------------------------------------------------------------
# Auth integration
# ------------------------------------------------------------------

class TestAdminAuth:
    """Verify admin routes respect auth middleware with admin_token."""

    def test_admin_requires_admin_token(self, mock_hermes_ok, tmp_path):
        """Admin routes should accept admin_token, reject regular token."""
        cfg = _make_cfg(tmp_path, auth_enabled=True, token="user-token", admin_token="admin-token")

        with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok), \
             patch("hermes_a2a.server.load_config", return_value=cfg):
            from hermes_a2a.server import create_app
            app = create_app()
            with TestClient(app) as client:
                # Regular token should be rejected for admin
                resp = client.get("/admin/peers", headers={"Authorization": "Bearer user-token"})
                assert resp.status_code == 401

                # Admin token should work
                resp = client.get("/admin/peers", headers={"Authorization": "Bearer admin-token"})
                assert resp.status_code == 200

    def test_admin_falls_back_to_regular_token(self, mock_hermes_ok, tmp_path):
        """When admin_token is empty, regular token should work for admin."""
        cfg = _make_cfg(tmp_path, auth_enabled=True, token="shared-token", admin_token="")

        with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok), \
             patch("hermes_a2a.server.load_config", return_value=cfg):
            from hermes_a2a.server import create_app
            app = create_app()
            with TestClient(app) as client:
                resp = client.get("/admin/peers", headers={"Authorization": "Bearer shared-token"})
                assert resp.status_code == 200

    def test_admin_rejects_no_token(self, mock_hermes_ok, tmp_path):
        """Admin routes should reject requests without any auth."""
        cfg = _make_cfg(tmp_path, auth_enabled=True, token="some-token", admin_token="admin-token")

        with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok), \
             patch("hermes_a2a.server.load_config", return_value=cfg):
            from hermes_a2a.server import create_app
            app = create_app()
            with TestClient(app) as client:
                resp = client.get("/admin/peers")
                assert resp.status_code == 401
