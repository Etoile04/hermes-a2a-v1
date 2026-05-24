"""Async HTTP client for communicating with Hermes API Server (OpenAI-compatible)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

import httpx

from hermes_a2a.exceptions import (
    HermesAuthError,
    HermesConnectionError,
    HermesServerError,
)

logger = logging.getLogger(__name__)

# Retry back-off schedules (in seconds) indexed by attempt number.
_CONNECT_RETRY_DELAYS = [1, 2, 4]  # 3 retries
_TIMEOUT_RETRY_DELAYS = [1, 1]  # 2 retries
_HTTP_RETRYABLE_DELAYS = [1, 2, 4]  # up to 3 retries for 429/503/5xx


class HermesClient:
    """Async client that talks to a Hermes API Server over OpenAI-compatible endpoints."""

    def __init__(
        self,
        base_url: str = "http://localhost:8642",
        timeout: int = 300,
        api_key: str | None = None,
        read_timeout: int = 60,
        connect_timeout: int = 10,
        write_timeout: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self._read_timeout = read_timeout
        self._connect_timeout = connect_timeout
        self._write_timeout = write_timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_http_timeout(self) -> httpx.Timeout:
        """Create a layered ``httpx.Timeout`` object."""
        return httpx.Timeout(
            connect=self._connect_timeout,
            read=self._read_timeout,
            write=self._write_timeout,
            pool=self.timeout,
        )

    def _build_headers(self, *, streaming: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if streaming:
            headers["Accept"] = "text/event-stream"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _classify_and_raise(
        self,
        exc: Exception,
        *,
        url: str,
        status_code: int | None = None,
    ) -> HermesConnectionError | HermesServerError:
        """Wrap a low-level exception in the appropriate HermesError subclass."""
        if isinstance(exc, httpx.HTTPStatusError):
            sc = exc.response.status_code
            if 500 <= sc < 600:
                return HermesServerError(
                    f"Server error {sc} from {url}",
                    url=url,
                    status_code=sc,
                )
            return HermesConnectionError(
                f"HTTP error {sc} from {url}",
                url=url,
                status_code=sc,
            )
        return HermesConnectionError(
            str(exc),
            url=url,
            status_code=status_code,
        )

    # ------------------------------------------------------------------
    # Retry engine (non-streaming)
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        *,
        url: str,
        payload: dict,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Execute a POST with exponential-back-off retry logic."""

        timeout_cfg = self._build_http_timeout()

        connect_attempts = 0
        timeout_attempts = 0
        http_retryable_attempts = 0

        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout_cfg) as http:
                    resp = await http.post(url, json=payload, headers=headers)

                # --- Handle HTTP-level status codes ---
                if resp.status_code == 401:
                    raise HermesAuthError(
                        "Authentication failed (HTTP 401)",
                        url=url,
                        status_code=401,
                    )

                if resp.status_code == 429 or resp.status_code == 503:
                    http_retryable_attempts += 1
                    if http_retryable_attempts > len(_HTTP_RETRYABLE_DELAYS):
                        raise HermesServerError(
                            f"Server returned {resp.status_code} after {http_retryable_attempts} attempts",
                            url=url,
                            status_code=resp.status_code,
                        )
                    # Respect Retry-After header
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            wait = _HTTP_RETRYABLE_DELAYS[http_retryable_attempts - 1]
                    else:
                        wait = _HTTP_RETRYABLE_DELAYS[http_retryable_attempts - 1]
                    logger.warning(
                        "HTTP %d from %s, retrying in %.1fs (attempt %d)",
                        resp.status_code, url, wait, http_retryable_attempts,
                    )
                    await asyncio.sleep(wait)
                    continue

                if 500 <= resp.status_code < 600:
                    # Generic 5xx — retry with back-off
                    http_retryable_attempts += 1
                    if http_retryable_attempts > len(_HTTP_RETRYABLE_DELAYS):
                        raise HermesServerError(
                            f"Server error {resp.status_code} after {http_retryable_attempts} attempts",
                            url=url,
                            status_code=resp.status_code,
                        )
                    wait = _HTTP_RETRYABLE_DELAYS[http_retryable_attempts - 1]
                    logger.warning(
                        "HTTP %d from %s, retrying in %.1fs (attempt %d)",
                        resp.status_code, url, wait, http_retryable_attempts,
                    )
                    await asyncio.sleep(wait)
                    continue

                # Any other non-2xx — raise immediately, no retry
                resp.raise_for_status()

                # Success!
                return resp

            except HermesAuthError:
                raise
            except HermesServerError:
                raise
            except HermesConnectionError:
                raise
            except httpx.ConnectError as exc:
                connect_attempts += 1
                if connect_attempts > len(_CONNECT_RETRY_DELAYS):
                    raise HermesConnectionError(
                        f"Connection to {url} failed after {connect_attempts} attempts: {exc}",
                        url=url,
                    ) from exc
                wait = _CONNECT_RETRY_DELAYS[connect_attempts - 1]
                logger.warning(
                    "ConnectError for %s, retrying in %.1fs (attempt %d): %s",
                    url, wait, connect_attempts, exc,
                )
                await asyncio.sleep(wait)
                continue

            except httpx.TimeoutException as exc:
                timeout_attempts += 1
                if timeout_attempts > len(_TIMEOUT_RETRY_DELAYS):
                    raise HermesConnectionError(
                        f"Request to {url} timed out after {timeout_attempts} attempts: {exc}",
                        url=url,
                    ) from exc
                wait = _TIMEOUT_RETRY_DELAYS[timeout_attempts - 1]
                logger.warning(
                    "Timeout for %s, retrying in %.1fs (attempt %d): %s",
                    url, wait, timeout_attempts, exc,
                )
                await asyncio.sleep(wait)
                continue

            except httpx.HTTPStatusError as exc:
                # Non-retryable HTTP error (not 429/503/5xx — those handled above)
                raise self._classify_and_raise(exc, url=url) from exc

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
        headers = self._build_headers()
        if session_id is not None:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": False,
        }

        url = f"{self.base_url}/v1/chat/completions"
        t0 = asyncio.get_event_loop().time()
        try:
            resp = await self._request_with_retry(url=url, payload=payload, headers=headers)
        except Exception as exc:
            duration_ms = round((asyncio.get_event_loop().time() - t0) * 1000, 1)
            logger.error(
                "POST %s failed: duration_ms=%.1f error_type=%s error=%s",
                url, duration_ms, type(exc).__name__, exc,
                exc_info=True,
            )
            raise
        data = resp.json()
        duration_ms = round((asyncio.get_event_loop().time() - t0) * 1000, 1)
        logger.info(
            "POST %s completed: duration_ms=%.1f status_code=%d",
            url, duration_ms, resp.status_code,
        )

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

        Retries only on initial connection errors — once the stream starts,
        mid-stream failures are **not** retried.
        """
        headers = self._build_headers(streaming=True)
        if session_id is not None:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

        url = f"{self.base_url}/v1/chat/completions"
        timeout_cfg = self._build_http_timeout()

        logger.info("POST %s stream started", url)

        # Retry connection-level errors only (not mid-stream failures)
        connect_attempts = 0
        timeout_attempts = 0

        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout_cfg) as http:
                    async with http.stream(
                        "POST", url, json=payload, headers=headers,
                    ) as resp:
                        # Handle HTTP status before streaming
                        if resp.status_code == 401:
                            raise HermesAuthError(
                                "Authentication failed (HTTP 401)",
                                url=url,
                                status_code=401,
                            )
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
                        # Stream completed successfully
                        logger.info("POST %s stream completed", url)
                        return

            except HermesAuthError:
                raise
            except httpx.ConnectError as exc:
                connect_attempts += 1
                if connect_attempts > len(_CONNECT_RETRY_DELAYS):
                    raise HermesConnectionError(
                        f"Connection to {url} failed after {connect_attempts} attempts: {exc}",
                        url=url,
                    ) from exc
                wait = _CONNECT_RETRY_DELAYS[connect_attempts - 1]
                logger.warning(
                    "ConnectError for %s, retrying in %.1fs (attempt %d): %s",
                    url, wait, connect_attempts, exc,
                )
                await asyncio.sleep(wait)
                continue

            except httpx.TimeoutException as exc:
                timeout_attempts += 1
                if timeout_attempts > len(_TIMEOUT_RETRY_DELAYS):
                    raise HermesConnectionError(
                        f"Request to {url} timed out after {timeout_attempts} attempts: {exc}",
                        url=url,
                    ) from exc
                wait = _TIMEOUT_RETRY_DELAYS[timeout_attempts - 1]
                logger.warning(
                    "Timeout for %s, retrying in %.1fs (attempt %d): %s",
                    url, wait, timeout_attempts, exc,
                )
                await asyncio.sleep(wait)
                continue

            except httpx.HTTPStatusError as exc:
                sc = exc.response.status_code
                if 500 <= sc < 600:
                    raise HermesServerError(
                        f"Server error {sc}",
                        url=url,
                        status_code=sc,
                    ) from exc
                raise

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
