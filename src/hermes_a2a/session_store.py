"""SQLite-backed session store for persisting contextId → sessionId mappings."""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    context_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


class SessionStore:
    """SQLite-backed store for contextId → sessionId mapping.

    Can share the same SQLite database file as TaskStore (different table).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.expanduser(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open the database connection and create the sessions table."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE_SQL)
        await self._db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def save(self, context_id: str, session_id: str) -> None:
        """Insert or update a contextId → sessionId mapping."""
        assert self._db is not None, "Store not initialised – call init() first"
        await self._db.execute(
            """
            INSERT INTO sessions (context_id, session_id, created_at, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(context_id) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (context_id, session_id),
        )
        await self._db.commit()

    async def get(self, context_id: str) -> str | None:
        """Look up sessionId by contextId. Returns None if not found."""
        assert self._db is not None, "Store not initialised – call init() first"
        cursor = await self._db.execute(
            "SELECT session_id FROM sessions WHERE context_id = ?",
            (context_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["session_id"]

    async def delete(self, context_id: str) -> None:
        """Remove a mapping."""
        assert self._db is not None, "Store not initialised – call init() first"
        await self._db.execute(
            "DELETE FROM sessions WHERE context_id = ?",
            (context_id,),
        )
        await self._db.commit()

    async def cleanup(self, max_age_hours: int = 24) -> int:
        """Delete sessions older than max_age_hours. Return count deleted."""
        assert self._db is not None, "Store not initialised – call init() first"
        cursor = await self._db.execute(
            """
            DELETE FROM sessions
            WHERE updated_at < datetime('now', ? || ' hours')
            """,
            (f"-{max_age_hours}",),
        )
        await self._db.commit()
        return cursor.rowcount

    async def load_all(self) -> dict[str, str]:
        """Load all contextId → sessionId mappings. Used for restoring on startup."""
        assert self._db is not None, "Store not initialised – call init() first"
        cursor = await self._db.execute(
            "SELECT context_id, session_id FROM sessions"
        )
        rows = await cursor.fetchall()
        return {row["context_id"]: row["session_id"] for row in rows}
