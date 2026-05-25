"""A2A client wrapper for peer communication."""
from __future__ import annotations

import logging

import httpx
from a2a.client import ClientFactory
from a2a.client.card_resolver import A2ACardResolver

logger = logging.getLogger(__name__)


class A2AClientError(Exception):
    """Raised when A2A client operations fail."""


class HermesA2AClient:
    """Wrapper around a2a-sdk Client for peer communication."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._httpx_client: httpx.AsyncClient | None = None

    async def _get_httpx(self) -> httpx.AsyncClient:
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(timeout=self.timeout)
        return self._httpx_client

    async def close(self) -> None:
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

    async def discover_agent(self, agent_card_url: str) -> dict:
        """Discover remote agent by fetching its agent card URL.

        Returns dict with agent info (name, version, description).
        """
        try:
            httpx_client = await self._get_httpx()
            base_url = agent_card_url.replace("/.well-known/agent-card.json", "")
            resolver = A2ACardResolver(httpx_client, base_url)
            card = await resolver.get_agent_card()
            return {
                "name": card.name,
                "version": card.version,
                "description": card.description,
            }
        except Exception as exc:
            raise A2AClientError(f"Agent discovery failed: {exc}") from exc

    async def send_message(
        self,
        agent_url: str,
        message: str,
        context_id: str | None = None,
        auth_token: str | None = None,
    ) -> dict:
        """Send message to remote A2A agent. Returns dict with task info."""
        client = None
        try:
            httpx_client = await self._get_httpx()
            resolver = A2ACardResolver(httpx_client, agent_url)
            card = await resolver.get_agent_card()

            factory = ClientFactory()
            client = factory.create(card)

            from a2a.types.a2a_pb2 import Message, Part, SendMessageRequest

            parts = [Part(text=message)]
            msg = Message(role="ROLE_USER", parts=parts)
            if context_id:
                msg.context_id = context_id
            request = SendMessageRequest(message=msg)

            # send_message returns AsyncIterator[StreamResponse]
            task = None
            async for stream_response in client.send_message(request):
                if stream_response.HasField("task"):
                    task = stream_response.task

            if task is None:
                raise A2AClientError("No task returned from remote agent")

            result = {"task_id": task.id, "state": task.status.state}
            return result
        except A2AClientError:
            raise
        except Exception as exc:
            raise A2AClientError(f"Message sending failed: {exc}") from exc
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
