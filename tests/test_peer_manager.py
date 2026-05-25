"""Tests for PeerManager."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hermes_a2a.models import PeerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PEER_A = PeerConfig(
    name="agent-a",
    agent_card_url="http://agent-a/.well-known/agent-card.json",
    auth_token="tok-a",
    enabled=True,
)
PEER_B = PeerConfig(
    name="agent-b",
    agent_card_url="http://agent-b/.well-known/agent-card.json",
    enabled=True,
)
PEER_DISABLED = PeerConfig(
    name="disabled-peer",
    agent_card_url="http://disabled/.well-known/agent-card.json",
    enabled=False,
)


# ---------------------------------------------------------------------------
# list_peers tests
# ---------------------------------------------------------------------------

class TestListPeers:
    """Tests for PeerManager.list_peers."""

    @pytest.mark.asyncio
    async def test_empty_config(self):
        """list_peers returns [] when no peers configured."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([])
        result = await pm.list_peers()
        assert result == []
        await pm.close()

    @pytest.mark.asyncio
    async def test_returns_enabled_peers(self):
        """list_peers returns info for all enabled peers."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A, PEER_B, PEER_DISABLED])
        result = await pm.list_peers()
        await pm.close()

        assert len(result) == 2
        names = [p["name"] for p in result]
        assert "agent-a" in names
        assert "agent-b" in names
        assert "disabled-peer" not in names

    @pytest.mark.asyncio
    async def test_undiscovered_status_is_unknown(self):
        """Peers that haven't been discovered yet show status='unknown'."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])
        result = await pm.list_peers()
        await pm.close()

        assert result[0]["status"] == "unknown"


# ---------------------------------------------------------------------------
# discover_all tests
# ---------------------------------------------------------------------------

class TestDiscoverAll:
    """Tests for PeerManager.discover_all."""

    @pytest.mark.asyncio
    async def test_discovers_available_peers(self):
        """discover_all returns 'available' for reachable peers."""
        from hermes_a2a.a2a_client import A2AClientError
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])

        async def _fake_discover(url):
            return {"name": "RemoteAgent", "version": "1.0", "description": "test"}

        with patch.object(pm._a2a_client, "discover_agent", side_effect=_fake_discover):
            result = await pm.discover_all()

        await pm.close()
        assert len(result) == 1
        assert result[0]["name"] == "agent-a"
        assert result[0]["status"] == "available"

    @pytest.mark.asyncio
    async def test_handles_unreachable_peers(self):
        """discover_all returns 'unreachable' for failed peers."""
        from hermes_a2a.a2a_client import A2AClientError
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])

        with patch.object(
            pm._a2a_client,
            "discover_agent",
            side_effect=A2AClientError("connection refused"),
        ):
            result = await pm.discover_all()

        await pm.close()
        assert len(result) == 1
        assert result[0]["status"] == "unreachable"

    @pytest.mark.asyncio
    async def test_caches_discovered_info(self):
        """After discover_all, list_peers reflects discovered info."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])

        async def _fake_discover(url):
            return {"name": "RemoteAgent", "version": "2.0", "description": "cached"}

        with patch.object(pm._a2a_client, "discover_agent", side_effect=_fake_discover):
            await pm.discover_all()

        peers = await pm.list_peers()
        await pm.close()
        assert peers[0]["version"] == "2.0"
        assert peers[0]["status"] == "available"


# ---------------------------------------------------------------------------
# send_to_peer tests
# ---------------------------------------------------------------------------

class TestSendToPeer:
    """Tests for PeerManager.send_to_peer."""

    @pytest.mark.asyncio
    async def test_success_for_valid_peer(self):
        """send_to_peer returns success dict for a known peer."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])

        with patch.object(
            pm._a2a_client,
            "send_message",
            return_value={"task_id": "t-1", "state": "COMPLETED"},
        ):
            result = await pm.send_to_peer("agent-a", "hello")

        await pm.close()
        assert result["success"] is True
        assert result["peer_name"] == "agent-a"
        assert result["task_id"] == "t-1"

    @pytest.mark.asyncio
    async def test_raises_for_unknown_peer(self):
        """send_to_peer raises ValueError for an unknown peer name."""
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])
        with pytest.raises(ValueError, match="not found or disabled"):
            await pm.send_to_peer("nonexistent", "hello")
        await pm.close()

    @pytest.mark.asyncio
    async def test_returns_failure_on_client_error(self):
        """send_to_peer returns failure dict when A2AClientError is raised."""
        from hermes_a2a.a2a_client import A2AClientError
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A])

        with patch.object(
            pm._a2a_client,
            "send_message",
            side_effect=A2AClientError("network error"),
        ):
            result = await pm.send_to_peer("agent-a", "hello")

        await pm.close()
        assert result["success"] is False
        assert "network error" in result["error"]


# ---------------------------------------------------------------------------
# Integration-style flow test
# ---------------------------------------------------------------------------

class TestIntegrationFlow:
    """Full list → discover → relay flow with mocked A2A client."""

    @pytest.mark.asyncio
    async def test_full_flow(self):
        from hermes_a2a.a2a_client import A2AClientError
        from hermes_a2a.peer_manager import PeerManager

        pm = PeerManager([PEER_A, PEER_B])

        # 1. list — both unknown
        peers = await pm.list_peers()
        assert len(peers) == 2
        assert all(p["status"] == "unknown" for p in peers)

        # 2. discover — one available, one unreachable
        call_count = 0
        async def _discover(url):
            nonlocal call_count
            call_count += 1
            if "agent-a" in url:
                return {"name": "AgentA", "version": "1.0", "description": "Peer A"}
            raise A2AClientError("unreachable")

        with patch.object(pm._a2a_client, "discover_agent", side_effect=_discover):
            discovered = await pm.discover_all()

        assert len(discovered) == 2
        statuses = {d["name"]: d["status"] for d in discovered}
        assert statuses["agent-a"] == "available"
        assert statuses["agent-b"] == "unreachable"

        # 3. list now reflects discovery
        peers = await pm.list_peers()
        peer_map = {p["name"]: p for p in peers}
        assert peer_map["agent-a"]["status"] == "available"
        assert peer_map["agent-b"]["status"] == "unreachable"  # unreachable is cached

        # 4. relay to available peer
        with patch.object(
            pm._a2a_client,
            "send_message",
            return_value={"task_id": "t-99", "state": "COMPLETED"},
        ):
            result = await pm.send_to_peer("agent-a", "ping")
        assert result["success"] is True

        await pm.close()
