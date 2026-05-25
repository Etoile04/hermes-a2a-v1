"""Enhanced tests for A2A client and PeerManager (pooling, circuit breaker, health)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_a2a.a2a_client import A2AClientError, HermesA2AClient
from hermes_a2a.models import PeerConfig
from hermes_a2a.peer_manager import PeerManager, _FAILURE_THRESHOLD, _COOLDOWN_SECONDS

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


def _make_pm(peers=None):
    """Create a PeerManager with optional peer list."""
    return PeerManager(peers or [PEER_A])


# ---------------------------------------------------------------------------
# 1. Connection pooling tests
# ---------------------------------------------------------------------------


class TestConnectionPooling:
    """Tests for shared httpx client reuse."""

    @pytest.mark.asyncio
    async def test_shared_httpx_client_injected(self):
        """PeerManager injects its shared httpx client into HermesA2AClient."""
        pm = _make_pm()
        assert pm._a2a_client._httpx_client is pm._httpx_client
        assert pm._a2a_client._httpx_client is not None
        await pm.close()

    @pytest.mark.asyncio
    async def test_same_client_across_multiple_peers(self):
        """All peer operations reuse the same httpx client instance."""
        pm = PeerManager([PEER_A, PEER_B])
        # The shared client is injected into the single _a2a_client
        shared = pm._httpx_client
        assert pm._a2a_client._httpx_client is shared

        # discover_all uses _a2a_client which carries the shared client
        async def _fake_discover(url):
            return {"name": "R", "version": "1.0", "description": "t"}

        with patch.object(pm._a2a_client, "discover_agent", side_effect=_fake_discover):
            await pm.discover_all()

        # Still the same client after operations
        assert pm._a2a_client._httpx_client is shared
        await pm.close()


# ---------------------------------------------------------------------------
# 2. Circuit breaker tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for circuit breaker open / close / skip behaviour."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold_failures(self):
        """Peer is marked DOWN after 3 consecutive failures."""
        pm = _make_pm()

        # Simulate failures
        with patch.object(
            pm._a2a_client,
            "send_message",
            side_effect=A2AClientError("fail"),
        ):
            for _ in range(_FAILURE_THRESHOLD):
                result = await pm.send_to_peer("agent-a", "hello")
                assert result["success"] is False

        # Now the peer should be down
        assert pm.peer_status["agent-a"] == "down"
        await pm.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_after_cooldown(self):
        """Peer auto-resets to UP after cooldown period."""
        pm = _make_pm()

        # Force the peer into DOWN state by recording failures
        for _ in range(_FAILURE_THRESHOLD):
            pm._cb_record_failure("agent-a")

        assert pm._is_peer_down("agent-a") is True
        assert pm.peer_status["agent-a"] == "down"

        # Manipulate down_until to simulate elapsed cooldown
        state = pm._cb_state["agent-a"]
        state["down_until"] = time.monotonic() - 1  # already expired

        # Now it should auto-reset
        assert pm._is_peer_down("agent-a") is False
        assert pm.peer_status["agent-a"] == "up"
        await pm.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_success_resets_failures(self):
        """A successful call resets the failure counter."""
        pm = _make_pm()

        # Record 2 failures (below threshold)
        pm._cb_record_failure("agent-a")
        pm._cb_record_failure("agent-a")
        assert pm._cb_state["agent-a"]["failures"] == 2

        # Success resets
        pm._cb_record_success("agent-a")
        assert "agent-a" not in pm._cb_state
        assert pm.peer_status["agent-a"] == "up"
        await pm.close()


# ---------------------------------------------------------------------------
# 3. Health check tests
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for check_peer_health method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """check_peer_health returns healthy=True on success."""
        pm = _make_pm()

        async def _fake(url):
            return {"name": "Remote", "version": "1.0", "description": "ok"}

        with patch.object(pm._a2a_client, "discover_agent", side_effect=_fake):
            health = await pm.check_peer_health("agent-a")

        assert health["name"] == "agent-a"
        assert health["healthy"] is True
        assert health["latency_ms"] >= 0
        assert "last_check" in health
        await pm.close()

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """check_peer_health returns healthy=False on exception."""
        pm = _make_pm()

        with patch.object(
            pm._a2a_client,
            "discover_agent",
            side_effect=A2AClientError("timeout"),
        ):
            health = await pm.check_peer_health("agent-a")

        assert health["name"] == "agent-a"
        assert health["healthy"] is False
        assert "latency_ms" in health
        await pm.close()

    @pytest.mark.asyncio
    async def test_health_check_unknown_peer(self):
        """check_peer_health returns unhealthy for unknown peer."""
        pm = _make_pm()
        health = await pm.check_peer_health("nonexistent")
        assert health["healthy"] is False
        await pm.close()


# ---------------------------------------------------------------------------
# 4. send_to_peer skips down peer
# ---------------------------------------------------------------------------


class TestSendToPeerSkipsDown:
    """Tests that send_to_peer skips circuit-broken peers."""

    @pytest.mark.asyncio
    async def test_send_to_peer_skips_down_peer(self):
        """send_to_peer returns failure without calling a2a_client when peer is down."""
        pm = _make_pm()

        # Force peer into DOWN state
        for _ in range(_FAILURE_THRESHOLD):
            pm._cb_record_failure("agent-a")

        # Patch send_message — it should NOT be called
        with patch.object(
            pm._a2a_client, "send_message", side_effect=AssertionError("should not be called")
        ) as mock_send:
            result = await pm.send_to_peer("agent-a", "hello")

        mock_send.assert_not_called()
        assert result["success"] is False
        assert "circuit breaker" in result["error"]
        await pm.close()

    @pytest.mark.asyncio
    async def test_send_to_peer_works_after_cooldown_reset(self):
        """send_to_peer succeeds after cooldown expires and circuit resets."""
        pm = _make_pm()

        # Force DOWN then expire cooldown
        for _ in range(_FAILURE_THRESHOLD):
            pm._cb_record_failure("agent-a")
        state = pm._cb_state["agent-a"]
        state["down_until"] = time.monotonic() - 1

        with patch.object(
            pm._a2a_client,
            "send_message",
            return_value={"task_id": "t-1", "state": "COMPLETED"},
        ):
            result = await pm.send_to_peer("agent-a", "hello")

        assert result["success"] is True
        assert result["task_id"] == "t-1"
        await pm.close()
