"""Tests for A2A client wrapper."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.types.a2a_pb2 import AgentCard, StreamResponse, Task, TaskState, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_card(name: str = "RemoteAgent", version: str = "1.0", description: str = "A test agent"):
    return AgentCard(name=name, version=version, description=description)


def _make_task_response(task_id: str = "task-1", state=TaskState.TASK_STATE_COMPLETED):
    task = Task(id=task_id)
    task.status.CopyFrom(TaskStatus(state=state))
    sr = StreamResponse()
    sr.task.CopyFrom(task)
    return sr


# ---------------------------------------------------------------------------
# discover_agent tests
# ---------------------------------------------------------------------------

class TestDiscoverAgent:
    """Tests for HermesA2AClient.discover_agent."""

    @pytest.mark.asyncio
    async def test_discover_agent_success(self):
        """discover_agent returns dict with agent card info on success."""
        from hermes_a2a.a2a_client import HermesA2AClient

        card = _make_card()
        mock_resolver = AsyncMock()
        mock_resolver.get_agent_card.return_value = card

        with patch("hermes_a2a.a2a_client.A2ACardResolver", return_value=mock_resolver):
            client = HermesA2AClient()
            result = await client.discover_agent("http://remote/.well-known/agent-card.json")
            await client.close()

        assert result == {"name": "RemoteAgent", "version": "1.0", "description": "A test agent"}

    @pytest.mark.asyncio
    async def test_discover_agent_failure(self):
        """discover_agent raises A2AClientError when resolver fails."""
        from hermes_a2a.a2a_client import A2AClientError, HermesA2AClient

        mock_resolver = AsyncMock()
        mock_resolver.get_agent_card.side_effect = ConnectionError("host unreachable")

        with patch("hermes_a2a.a2a_client.A2ACardResolver", return_value=mock_resolver):
            client = HermesA2AClient()
            with pytest.raises(A2AClientError, match="Agent discovery failed"):
                await client.discover_agent("http://bad-host/.well-known/agent-card.json")
            await client.close()


# ---------------------------------------------------------------------------
# send_message tests
# ---------------------------------------------------------------------------

class TestSendMessage:
    """Tests for HermesA2AClient.send_message."""

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """send_message returns dict with task info on success."""
        from hermes_a2a.a2a_client import HermesA2AClient

        card = _make_card()
        task_resp = _make_task_response("task-42", TaskState.TASK_STATE_COMPLETED)

        # Mock resolver
        mock_resolver = AsyncMock()
        mock_resolver.get_agent_card.return_value = card

        # Mock client (send_message returns async iterator of StreamResponse)
        mock_a2a_client = AsyncMock()
        async def _stream(*args, **kwargs):
            yield task_resp
        mock_a2a_client.send_message = _stream
        mock_a2a_client.close = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.create.return_value = mock_a2a_client

        with patch("hermes_a2a.a2a_client.A2ACardResolver", return_value=mock_resolver), \
             patch("hermes_a2a.a2a_client.ClientFactory", return_value=mock_factory):
            client = HermesA2AClient()
            result = await client.send_message("http://remote", "Hello!")
            await client.close()

        assert result["task_id"] == "task-42"
        assert result["state"] == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_send_message_failure(self):
        """send_message raises A2AClientError when the call fails."""
        from hermes_a2a.a2a_client import A2AClientError, HermesA2AClient

        mock_resolver = AsyncMock()
        mock_resolver.get_agent_card.side_effect = RuntimeError("boom")

        with patch("hermes_a2a.a2a_client.A2ACardResolver", return_value=mock_resolver):
            client = HermesA2AClient()
            with pytest.raises(A2AClientError, match="Message sending failed"):
                await client.send_message("http://remote", "Hello!")
            await client.close()


# ---------------------------------------------------------------------------
# close tests
# ---------------------------------------------------------------------------

class TestClose:
    """Tests for HermesA2AClient.close."""

    @pytest.mark.asyncio
    async def test_close_cleans_up_httpx_client(self):
        """close() should clean up the internal httpx client."""
        from hermes_a2a.a2a_client import HermesA2AClient

        client = HermesA2AClient()
        # Trigger lazy creation
        httpx_client = await client._get_httpx()
        assert client._httpx_client is not None

        await client.close()
        assert client._httpx_client is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        """close() should be safe to call multiple times."""
        from hermes_a2a.a2a_client import HermesA2AClient

        client = HermesA2AClient()
        await client.close()
        await client.close()  # Should not raise
