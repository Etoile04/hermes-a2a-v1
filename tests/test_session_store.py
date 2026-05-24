"""Tests for SessionStore — SQLite-backed contextId → sessionId persistence."""

import asyncio

import pytest

from hermes_a2a.session_store import SessionStore

# `session_store` fixture is provided by conftest.py


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_get(session_store):
    """save → get round-trip."""
    await session_store.save("ctx-1", "sess-abc")
    result = await session_store.get("ctx-1")
    assert result == "sess-abc"


@pytest.mark.asyncio
async def test_get_missing_returns_none(session_store):
    """get on non-existent context_id returns None."""
    assert await session_store.get("no-such-context") is None


@pytest.mark.asyncio
async def test_save_updates_existing(session_store):
    """Saving the same context_id updates the session_id."""
    await session_store.save("ctx-1", "sess-old")
    await session_store.save("ctx-1", "sess-new")
    assert await session_store.get("ctx-1") == "sess-new"


@pytest.mark.asyncio
async def test_delete(session_store):
    """delete removes the mapping."""
    await session_store.save("ctx-del", "sess-del")
    await session_store.delete("ctx-del")
    assert await session_store.get("ctx-del") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_is_noop(session_store):
    """Deleting a non-existent key should not raise."""
    await session_store.delete("ghost")  # should not raise


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_removes_expired(session_store):
    """cleanup deletes sessions older than max_age_hours."""
    # Insert a session and manually backdate updated_at
    await session_store.save("ctx-old", "sess-old")
    assert session_store._db is not None
    await session_store._db.execute(
        "UPDATE sessions SET updated_at = datetime('now', '-48 hours') "
        "WHERE context_id = ?",
        ("ctx-old",),
    )
    await session_store._db.commit()

    # Insert a fresh session
    await session_store.save("ctx-fresh", "sess-fresh")

    deleted = await session_store.cleanup(max_age_hours=24)
    assert deleted == 1
    assert await session_store.get("ctx-old") is None
    assert await session_store.get("ctx-fresh") == "sess-fresh"


@pytest.mark.asyncio
async def test_cleanup_nothing_to_delete(session_store):
    """cleanup returns 0 when all sessions are fresh."""
    await session_store.save("ctx-1", "sess-1")
    deleted = await session_store.cleanup(max_age_hours=24)
    assert deleted == 0


# ---------------------------------------------------------------------------
# Persistence / restart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restart_preserves_data(tmp_path):
    """Data survives close → re-open."""
    db_path = str(tmp_path / "restart.db")

    # Phase 1: write data
    store1 = SessionStore(db_path)
    await store1.init()
    await store1.save("ctx-1", "sess-111")
    await store1.save("ctx-2", "sess-222")
    await store1.close()

    # Phase 2: re-open and read
    store2 = SessionStore(db_path)
    await store2.init()
    assert await store2.get("ctx-1") == "sess-111"
    assert await store2.get("ctx-2") == "sess-222"
    await store2.close()


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_all(session_store):
    """load_all returns all mappings as a dict."""
    await session_store.save("ctx-a", "sess-a")
    await session_store.save("ctx-b", "sess-b")
    result = await session_store.load_all()
    assert result == {"ctx-a": "sess-a", "ctx-b": "sess-b"}


@pytest.mark.asyncio
async def test_load_all_empty(session_store):
    """load_all returns empty dict when no sessions exist."""
    assert await session_store.load_all() == {}


# ---------------------------------------------------------------------------
# Concurrent access (basic)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_saves(session_store):
    """Multiple concurrent saves should not corrupt data."""
    n = 20

    async def _save(i):
        await session_store.save(f"ctx-{i}", f"sess-{i}")

    await asyncio.gather(*[_save(i) for i in range(n)])

    for i in range(n):
        assert await session_store.get(f"ctx-{i}") == f"sess-{i}"


# ---------------------------------------------------------------------------
# Shared DB with TaskStore (same file, different tables)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shares_db_with_task_store(tmp_path):
    """SessionStore and TaskStore can coexist in the same SQLite file."""
    from hermes_a2a.task_store import SQLiteTaskStore

    db_path = str(tmp_path / "shared.db")

    ts = SQLiteTaskStore(db_path)
    ss = SessionStore(db_path)

    await ts.init()
    await ss.init()

    # Write to both stores
    await ss.save("ctx-1", "sess-1")
    await ts.save({"id": "t1", "status": {"state": "COMPLETED"}}, None)

    # Both should be readable
    assert await ss.get("ctx-1") == "sess-1"
    task = await ts.get("t1", None)
    assert task is not None
    assert task["id"] == "t1"

    await ts.close()
    await ss.close()
