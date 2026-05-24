"""Async HTTP client for communicating with Hermes API Server (OpenAI-compatible)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import httpx


class HermesClient:
    """Async client that talks to a Hermes API Server over OpenAI-compatible endpoints."""

    def __init__(self, base_url: str = "http://localhost:8642", timeout: int = 300, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------
    async def send_message(
        self, text: str, session_id: str | None = None
    ) -> tuple[str, str]:
        """Send a message and return ``(response_text, session_id)``.

        Uses ``POST /v1/chat/completions`` with ``stream: false``.
        If the server returns a ``session_id`` field it is reused; otherwise
        a new UUID is generated.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if session_id is not None:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            resp = await http.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        sid = data.get("session_id") or session_id or str(uuid.uuid4())
        return content, sid

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def send_message_stream(
        self, text: str, session_id: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks from an SSE stream.

        Uses ``POST /v1/chat/completions`` with ``stream: true``.
        Parses ``data: {json}`` lines and yields ``delta.content`` chunks.
        The terminal ``data: [DONE]`` line is skipped.
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if session_id is not None:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            async with http.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[len("data: "):]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    async def health_check(self) -> bool:
        """Return ``True`` if the server health endpoint responds with 200."""
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
