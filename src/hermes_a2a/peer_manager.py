"""Peer discovery and management with connection pooling and circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from hermes_a2a.a2a_client import A2AClientError, HermesA2AClient
from hermes_a2a.models import PeerConfig

logger = logging.getLogger(__name__)

# Circuit breaker defaults
_FAILURE_THRESHOLD = 3
_COOLDOWN_SECONDS = 60.0


class PeerManager:
    """Manages configured A2A peer agents with connection pooling and circuit breaker."""

    def __init__(self, peers_config: list[PeerConfig]):
        self.peers_config = peers_config
        # Shared connection pool
        self._httpx_client = httpx.AsyncClient(timeout=30)
        self._a2a_client = HermesA2AClient()
        # Inject shared client so all peer calls reuse the pool
        self._a2a_client._httpx_client = self._httpx_client
        self._discovered: dict[str, dict] = {}

        # Circuit breaker state: {peer_name: {"failures": int, "down_until": float|None}}
        self._cb_state: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        """Close shared HTTP client and A2A client."""
        await self._a2a_client.close()
        # _a2a_client.close() already closes the shared _httpx_client,
        # but guard against double-close.
        if self._httpx_client is not None:
            try:
                await self._httpx_client.aclose()
            except Exception:
                pass
            self._httpx_client = None

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _cb_record_success(self, name: str) -> None:
        """Reset failure count on success."""
        self._cb_state.pop(name, None)

    def _cb_record_failure(self, name: str) -> None:
        """Increment failure count; mark peer down if threshold reached."""
        state = self._cb_state.setdefault(name, {"failures": 0, "down_until": None})
        state["failures"] += 1
        if state["failures"] >= _FAILURE_THRESHOLD:
            state["down_until"] = time.monotonic() + _COOLDOWN_SECONDS
            logger.warning("Peer %s marked DOWN (circuit breaker) for %.0fs", name, _COOLDOWN_SECONDS)

    def _is_peer_down(self, name: str) -> bool:
        """Check if peer is circuit-broken. Auto-resets after cooldown."""
        state = self._cb_state.get(name)
        if state is None:
            return False
        down_until = state.get("down_until")
        if down_until is None:
            return False
        if time.monotonic() >= down_until:
            # Cooldown elapsed — auto-reset
            self._cb_state.pop(name, None)
            logger.info("Peer %s circuit breaker auto-reset after cooldown", name)
            return False
        return True

    # ------------------------------------------------------------------
    # Public properties / helpers
    # ------------------------------------------------------------------

    @property
    def peer_status(self) -> dict[str, str]:
        """Return a dict mapping peer name → circuit breaker status ('up'|'down')."""
        result: dict[str, str] = {}
        for p in self.peers_config:
            if not p.enabled:
                continue
            result[p.name] = "down" if self._is_peer_down(p.name) else "up"
        return result

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    async def check_peer_health(self, name: str) -> dict[str, Any]:
        """Fetch agent card for a peer and return health info.

        Returns dict with keys: name, healthy, latency_ms, last_check.
        """
        peer = next(
            (p for p in self.peers_config if p.name == name and p.enabled),
            None,
        )
        if peer is None:
            return {"name": name, "healthy": False, "latency_ms": 0, "last_check": time.time()}

        t0 = time.monotonic()
        try:
            await self._a2a_client.discover_agent(peer.agent_card_url)
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "name": name,
                "healthy": True,
                "latency_ms": round(latency_ms, 1),
                "last_check": time.time(),
            }
        except Exception:
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "name": name,
                "healthy": False,
                "latency_ms": round(latency_ms, 1),
                "last_check": time.time(),
            }

    # ------------------------------------------------------------------
    # List / discover
    # ------------------------------------------------------------------

    async def list_peers(self) -> list[dict]:
        """List all configured peers with their status."""
        result = []
        for p in self.peers_config:
            if not p.enabled:
                continue
            info = {
                "name": p.name,
                "agent_card_url": p.agent_card_url,
                "status": "unknown",
            }
            if p.name in self._discovered:
                info.update(self._discovered[p.name])
            result.append(info)
        return result

    async def discover_all(self) -> list[dict]:
        """Discover all enabled peers concurrently."""
        enabled = [p for p in self.peers_config if p.enabled]
        tasks = [self._discover_one(p) for p in enabled]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        discovered = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                discovered.append(
                    {"name": enabled[i].name, "status": "error", "error": str(r)}
                )
            else:
                self._discovered[enabled[i].name] = r
                discovered.append(r)
        return discovered

    async def _discover_one(self, peer: PeerConfig) -> dict:
        try:
            card = await self._a2a_client.discover_agent(peer.agent_card_url)
            return {**card, "name": peer.name, "status": "available"}
        except A2AClientError as exc:
            return {"name": peer.name, "status": "unreachable", "error": str(exc)}

    # ------------------------------------------------------------------
    # Send (with circuit breaker integration)
    # ------------------------------------------------------------------

    async def send_to_peer(
        self,
        peer_name: str,
        message: str,
        context_id: str | None = None,
    ) -> dict:
        """Send message to a named peer. Skips peers that are circuit-broken."""
        peer = next(
            (p for p in self.peers_config if p.name == peer_name and p.enabled),
            None,
        )
        if not peer:
            raise ValueError(f"Peer '{peer_name}' not found or disabled")

        if self._is_peer_down(peer_name):
            return {
                "success": False,
                "error": f"Peer '{peer_name}' is marked down (circuit breaker)",
                "peer_name": peer_name,
            }

        base_url = peer.agent_card_url.replace("/.well-known/agent-card.json", "")
        try:
            result = await self._a2a_client.send_message(
                base_url, message, context_id, peer.auth_token or None
            )
            self._cb_record_success(peer_name)
            return {"success": True, **result, "peer_name": peer_name}
        except A2AClientError as exc:
            self._cb_record_failure(peer_name)
            return {"success": False, "error": str(exc), "peer_name": peer_name}
