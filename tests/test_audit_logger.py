"""Tests for audit logger (audit_logger.py)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_a2a.audit_logger import AuditLogger


@pytest.fixture
def audit_dir():
    """Provide a temporary directory for audit logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def audit_log(audit_dir):
    """Create an AuditLogger writing to a temp file."""
    log_path = os.path.join(audit_dir, "test_audit.log")
    return AuditLogger(log_path=log_path, max_file_size=1024, max_backups=3)


# ---------------------------------------------------------------------------
# C2 Tests: AuditLogger
# ---------------------------------------------------------------------------

class TestAuditLoggerWriting:
    """Tests for basic log writing."""

    def test_log_creates_entry(self, audit_log, audit_dir):
        audit_log.log("admin", "create_task", "task-123", "success")
        log_path = os.path.join(audit_dir, "test_audit.log")
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["actor"] == "admin"
        assert entry["action"] == "create_task"
        assert entry["target"] == "task-123"
        assert entry["result"] == "success"

    def test_log_with_metadata(self, audit_log, audit_dir):
        audit_log.log(
            "agent-1", "send_message", "peer-2", "success",
            metadata={"message_size": 1024, "latency_ms": 50},
        )
        log_path = os.path.join(audit_dir, "test_audit.log")
        with open(log_path) as f:
            entry = json.loads(f.readline())
        assert entry["metadata"]["message_size"] == 1024
        assert entry["metadata"]["latency_ms"] == 50

    def test_log_without_metadata(self, audit_log, audit_dir):
        audit_log.log("user1", "login", "session-abc", "success")
        log_path = os.path.join(audit_dir, "test_audit.log")
        entry = json.loads(open(log_path).readline())
        assert "metadata" not in entry

    def test_log_has_timestamp(self, audit_log, audit_dir):
        audit_log.log("user1", "action", "target", "result")
        log_path = os.path.join(audit_dir, "test_audit.log")
        entry = json.loads(open(log_path).readline())
        assert "timestamp" in entry
        # ISO8601 format check
        assert "T" in entry["timestamp"]


class TestAuditLoggerRotation:
    """Tests for log file rotation."""

    def test_rotation_creates_backup(self, audit_log, audit_dir):
        log_path = os.path.join(audit_dir, "test_audit.log")
        # Write enough data to trigger rotation
        for i in range(100):
            audit_log.log(
                "user", "action", f"target-{i}", "success",
                metadata={"data": "x" * 100},
            )
        # Should have created backup files
        backup_1 = Path(f"{log_path}.1")
        assert backup_1.exists()

    def test_rotation_respects_max_backups(self, audit_dir):
        log_path = os.path.join(audit_dir, "rotate_audit.log")
        audit = AuditLogger(log_path=log_path, max_file_size=256, max_backups=2)
        for i in range(200):
            audit.log("user", "action", f"target-{i}", "success", metadata={"x": "y" * 50})
        # max_backups=2 means only .1 and .2 should exist
        assert not Path(f"{log_path}.3").exists()


class TestAuditLoggerQuery:
    """Tests for querying audit logs."""

    def test_query_by_actor(self, audit_log, audit_dir):
        audit_log.log("alice", "create", "task-1", "success")
        audit_log.log("bob", "create", "task-2", "success")
        audit_log.log("alice", "delete", "task-1", "success")

        results = audit_log.query({"actor": "alice"})
        assert len(results) == 2
        assert all(r["actor"] == "alice" for r in results)

    def test_query_by_action(self, audit_log, audit_dir):
        audit_log.log("alice", "create", "task-1", "success")
        audit_log.log("bob", "create", "task-2", "success")
        audit_log.log("alice", "delete", "task-3", "success")

        results = audit_log.query({"action": "create"})
        assert len(results) == 2

    def test_query_by_target(self, audit_log, audit_dir):
        audit_log.log("alice", "create", "task-1", "success")
        audit_log.log("bob", "create", "task-2", "success")

        results = audit_log.query({"target": "task-1"})
        assert len(results) == 1
        assert results[0]["target"] == "task-1"

    def test_query_by_time_range(self, audit_log, audit_dir):
        audit_log.log("user", "action", "target", "result")
        results = audit_log.query({
            "start_time": "2000-01-01T00:00:00",
            "end_time": "2099-12-31T23:59:59",
        })
        assert len(results) == 1

    def test_query_no_match(self, audit_log, audit_dir):
        audit_log.log("alice", "create", "task-1", "success")
        results = audit_log.query({"actor": "nonexistent"})
        assert len(results) == 0

    def test_query_multiple_filters(self, audit_log, audit_dir):
        audit_log.log("alice", "create", "task-1", "success")
        audit_log.log("alice", "create", "task-2", "failure")
        audit_log.log("bob", "create", "task-3", "success")

        results = audit_log.query({"actor": "alice", "result": "success"})
        assert len(results) == 1
        assert results[0]["target"] == "task-1"


class TestAuditLoggerWebhook:
    """Tests for webhook delivery."""

    def test_webhook_success(self, audit_log):
        event = {"event": "test", "severity": "info"}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = audit_log.webhook_deliver("http://siem.example.com/api", event)
        assert result["status_code"] == 200
        assert result["body"] == "ok"

    def test_webhook_connection_error(self, audit_log):
        import urllib.error
        event = {"event": "test"}
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = audit_log.webhook_deliver("http://unreachable.example.com", event)
        assert "error" in result
