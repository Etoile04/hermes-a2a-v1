"""Structured audit logger for Hermes A2A Gateway.

Writes JSON-line audit logs with file rotation and query/filter support.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Structured audit logger with rotation and query support.

    Parameters
    ----------
    log_path:
        Path to the audit log file.
    max_file_size:
        Maximum size (bytes) of each log file before rotation.
        Default is 10 MB.
    max_backups:
        Maximum number of rotated backup files to keep.
    """

    def __init__(
        self,
        log_path: str = "audit.log",
        max_file_size: int = 10 * 1024 * 1024,
        max_backups: int = 5,
    ) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_file_size = max_file_size
        self._max_backups = max_backups

        # Set up rotating file handler with a dedicated logger
        self._logger = logging.getLogger(f"hermes.audit.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        handler = logging.handlers.RotatingFileHandler(
            str(self._log_path),
            maxBytes=max_file_size,
            backupCount=max_backups,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler

    # -- public API ---------------------------------------------------------

    def log(
        self,
        actor: str,
        action: str,
        target: str,
        result: str,
        metadata: dict | None = None,
    ) -> None:
        """Write a structured JSON audit log entry.

        Parameters
        ----------
        actor:
            Who performed the action (user, service, agent name).
        action:
            What action was performed (e.g. ``"create_task"``).
        target:
            What the action was performed on (e.g. task ID, peer name).
        result:
            Outcome — ``"success"`` or ``"failure"``.
        metadata:
            Optional additional key-value pairs.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "result": result,
        }
        if metadata:
            entry["metadata"] = metadata

        self._logger.info(json.dumps(entry, default=str))

    def query(self, filters: dict) -> list[dict]:
        """Search audit logs by filters.

        Supported filter keys:
        - ``actor``: exact match on actor field
        - ``action``: exact match on action field
        - ``target``: exact match on target field
        - ``result``: exact match on result field
        - ``start_time``: ISO8601 string, inclusive lower bound
        - ``end_time``: ISO8601 string, inclusive upper bound

        Searches the current log file and all backup files.
        """
        results: list[dict] = []
        log_files = self._get_log_files()

        for log_file in log_files:
            if not log_file.exists():
                continue
            try:
                with open(log_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if self._matches(entry, filters):
                            results.append(entry)
            except OSError:
                continue

        # Sort by timestamp descending (newest first)
        results.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return results

    def webhook_deliver(self, url: str, event: dict) -> dict[str, Any]:
        """Deliver an audit event to a SIEM endpoint via HTTP POST.

        Uses :mod:`urllib.request` — no external dependencies.

        Parameters
        ----------
        url:
            Target URL (SIEM / webhook endpoint).
        event:
            Event payload to send as JSON body.

        Returns
        -------
        dict
            ``{"status_code": int, "body": str}`` on success, or
            ``{"error": str}`` on failure.
        """
        payload = json.dumps(event, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {"status_code": resp.status, "body": body}
        except urllib.error.HTTPError as exc:
            return {"error": f"HTTP {exc.code}: {exc.reason}"}
        except urllib.error.URLError as exc:
            return {"error": f"URL error: {exc.reason}"}
        except Exception as exc:
            return {"error": str(exc)}

    # -- helpers ------------------------------------------------------------

    def _get_log_files(self) -> list[Path]:
        """Return list of log files (current + backups) sorted newest first."""
        files: list[Path] = []
        if self._log_path.exists():
            files.append(self._log_path)
        # RotatingFileHandler creates backups as file.1, file.2, ...
        for i in range(1, self._max_backups + 1):
            backup = Path(f"{self._log_path}.{i}")
            if backup.exists():
                files.append(backup)
        return files

    @staticmethod
    def _matches(entry: dict, filters: dict) -> bool:
        """Return True if *entry* matches all *filters*."""
        for key, value in filters.items():
            if key == "start_time":
                ts = entry.get("timestamp", "")
                if ts < value:
                    return False
            elif key == "end_time":
                ts = entry.get("timestamp", "")
                if ts > value:
                    return False
            else:
                if entry.get(key) != value:
                    return False
        return True
