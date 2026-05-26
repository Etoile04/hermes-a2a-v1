"""Tests for the monitoring dashboard UI."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_a2a.models import GatewayConfig, TaskStoreConfig


def _make_cfg(tmp_path):
    cfg = GatewayConfig()
    cfg.task_store = TaskStoreConfig(path=str(tmp_path / "test.db"))
    cfg.auth.enabled = False
    return cfg


@pytest.fixture
def mock_hermes_ok():
    client = AsyncMock()
    client.send_message.return_value = ("Hello!", "sess-001")
    client.health_check.return_value = True
    return client


@pytest.fixture
def app_client(mock_hermes_ok, tmp_path):
    cfg = _make_cfg(tmp_path)
    with patch("hermes_a2a.server.HermesClient", return_value=mock_hermes_ok), \
         patch("hermes_a2a.server.load_config", return_value=cfg):
        from hermes_a2a.server import create_app
        app = create_app()
        with TestClient(app) as client:
            yield client


class TestDashboard:
    """Verify the dashboard UI is served correctly."""

    def test_dashboard_returns_html(self, app_client):
        """GET /admin/dashboard/ should return HTML."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/html" in content_type

    def test_dashboard_contains_title(self, app_client):
        """Dashboard HTML should contain the gateway title."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        text = resp.text
        assert "Hermes A2A" in text

    def test_dashboard_contains_chart_js(self, app_client):
        """Dashboard should reference Chart.js from CDN."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        text = resp.text
        assert "chart.js" in text.lower() or "Chart" in text

    def test_dashboard_has_dark_theme(self, app_client):
        """Dashboard should use dark theme styles."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        text = resp.text
        # Check for dark background color
        assert "#0f1117" in text or "dark" in text.lower()

    def test_dashboard_has_sse_connection(self, app_client):
        """Dashboard should set up SSE connection."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        text = resp.text
        assert "EventSource" in text
        assert "/admin/metrics/stream" in text

    def test_dashboard_has_websocket(self, app_client):
        """Dashboard should set up WebSocket connection."""
        resp = app_client.get("/admin/dashboard/", follow_redirects=True)
        text = resp.text
        assert "WebSocket" in text
        assert "/admin/ws" in text
