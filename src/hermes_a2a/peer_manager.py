"""Peer discovery and management."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from hermes_a2a.a2a_client import A2AClientError, HermesA2AClient
from hermes_a2a.models import PeerConfig

logger = logging.getLogger(__name__)


class PeerManager:
    """Manages configured A2A peer agents."""

    def __init__(self, peers_config: list[PeerConfig]):
        self.peers_config = peers_config
        self._a2a_client = HermesA2AClient()
        self._discovered: dict[str, dict] = {}

    async def close(self) -> None:
        await self._a2a_client.close()

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

    async def send_to_peer(
        self,
        peer_name: str,
        message: str,
        context_id: str | None = None,
    ) -> dict:
        """Send message to a named peer."""
        peer = next(
            (p for p in self.peers_config if p.name == peer_name and p.enabled), None
        )
        if not peer:
            raise ValueError(f"Peer '{peer_name}' not found or disabled")
        base_url = peer.agent_card_url.replace("/.well-known/agent-card.json", "")
        try:
            result = await self._a2a_client.send_message(
                base_url, message, context_id, peer.auth_token or None
            )
            return {"success": True, **result, "peer_name": peer_name}
        except A2AClientError as exc:
            return {"success": False, "error": str(exc), "peer_name": peer_name}
