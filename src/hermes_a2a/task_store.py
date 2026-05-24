"""SQLite-backed task store for persisting A2A tasks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiosqlite

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    context_id TEXT,
    state INTEGER DEFAULT 1,
    data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_tasks_context ON tasks(context_id)
"""


class SQLiteTaskStore:
    """Async SQLite task store with basic CRUD operations."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.expanduser(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open the database connection and create tables."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE_SQL)
        await self._db.execute(_CREATE_INDEX_SQL)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def save(self, task: dict, context: Any = None) -> None:
        """Upsert a task. Uses INSERT OR REPLACE keyed on task['id']."""
        assert self._db is not None, "Store not initialised – call init() first"
        task_id = task["id"]
        context_id = task.get("contextId")
        # Extract state from nested status dict; fall back to 1
        state = 1
        status = task.get("status")
        if isinstance(status, dict):
            raw_state = status.get("state")
            if isinstance(raw_state, int):
                state = raw_state
            elif isinstance(raw_state, str):
                try:
                    state = int(raw_state)
                except ValueError:
                    pass
        data = json.dumps(task)
        await self._db.execute(
            """
            INSERT OR REPLACE INTO tasks (task_id, context_id, state, data, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (task_id, context_id, state, data),
        )
        await self._db.commit()

    async def get(self, task_id: str, context: Any = None) -> dict | None:
        """Retrieve a single task by ID. Returns None if not found."""
        assert self._db is not None, "Store not initialised – call init() first"
        cursor = await self._db.execute(
            "SELECT data FROM tasks WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    async def delete(self, task_id: str, context: Any = None) -> None:
        """Delete a task by ID."""
        assert self._db is not None, "Store not initialised – call init() first"
        await self._db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        await self._db.commit()

    async def list(self, params: Any = None, context: Any = None) -> list[dict]:
        """Return all tasks ordered by most-recently-updated first."""
        assert self._db is not None, "Store not initialised – call init() first"
        cursor = await self._db.execute(
            "SELECT data FROM tasks ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [json.loads(row["data"]) for row in rows]
