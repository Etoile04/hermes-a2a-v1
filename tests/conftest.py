"""Shared pytest fixtures for Hermes A2A Gateway tests."""

import pytest
from unittest.mock import AsyncMock

from hermes_a2a.a2a_handler import HermesA2AHandler
from hermes_a2a.hermes_client import HermesClient
from hermes_a2a.task_store import SQLiteTaskStore
from hermes_a2a.session_store import SessionStore


@pytest.fixture
def mock_hermes_client():
    """Mocked HermesClient for unit tests."""
    client = AsyncMock(spec=HermesClient)
    client.send_message = AsyncMock(return_value=("Test response", "sess-1"))
    client.health_check = AsyncMock(return_value=True)
    return client


@pytest.fixture
async def task_store(tmp_path):
    """In-memory SQLiteTaskStore for tests."""
    store = SQLiteTaskStore(str(tmp_path / "test.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def session_store(tmp_path):
    """In-memory SessionStore for tests."""
    store = SessionStore(str(tmp_path / "test.db"))
    await store.init()
    yield store
    await store.close()
