"""Tests for HermesClient retry logic, timeout layering, and structured errors."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from hermes_a2a.exceptions import (
    HermesAuthError,
    HermesConnectionError,
    HermesError,
    HermesServerError,
)
from hermes_a2a.hermes_client import HermesClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(session_id: str | None = None) -> httpx.Response:
    body: dict = {
        "id": "chatcmpl-test",
        "choices": [{"message": {"content": "Hello!"}}],
    }
    if session_id:
        body["session_id"] = session_id
    return httpx.Response(200, json=body)


@pytest.fixture
def client():
    return HermesClient(base_url="http://localhost:8642", timeout=30)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    """Custom exceptions must inherit from HermesError."""

    def test_hermes_connection_error_is_hermes_error(self):
        assert issubclass(HermesConnectionError, HermesError)

    def test_hermes_auth_error_is_hermes_error(self):
        assert issubclass(HermesAuthError, HermesError)

    def test_hermes_server_error_is_hermes_error(self):
        assert issubclass(HermesServerError, HermesError)

    def test_hermes_error_is_exception(self):
        assert issubclass(HermesError, Exception)

    def test_exception_context(self):
        err = HermesConnectionError("boom", url="http://x", status_code=None)
        assert err.url == "http://x"
        assert err.message == "boom"
        assert err.status_code is None


# ---------------------------------------------------------------------------
# ConnectError retries (3 attempts)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_connect_error_retries_3_times_then_raises(client):
    """ConnectError should be retried 3 times then raise HermesConnectionError."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(HermesConnectionError) as exc_info:
            await client.send_message("hi")

    # 3 retries means 4 total attempts (initial + 3 retries)
    assert route.call_count == 4
    # sleep called 3 times (before each retry)
    assert mock_sleep.call_count == 3
    assert "Connection refused" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TimeoutException retries (2 retries)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_timeout_retries_2_times_then_raises(client):
    """TimeoutException should be retried 2 times then raise HermesConnectionError."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(side_effect=httpx.ReadTimeout("Read timed out"))

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(HermesConnectionError) as exc_info:
            await client.send_message("hi")

    # 2 retries means 3 total attempts
    assert route.call_count == 3
    assert "timed out" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 429 Retry-After
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_429_retries_with_retry_after_header(client):
    """HTTP 429 should retry and respect Retry-After header."""
    route = respx.post("http://localhost:8642/v1/chat/completions")

    # First call: 429 with Retry-After; second call: success
    route.mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited"}),
            _ok_response(),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        text, sid = await client.send_message("hi")

    assert text == "Hello!"
    assert mock_sleep.call_count == 1
    # Should have waited 2 seconds (from Retry-After header)
    mock_sleep.assert_called_once_with(2.0)


@pytest.mark.asyncio
@respx.mock
async def test_429_without_retry_after_uses_backoff(client):
    """HTTP 429 without Retry-After should use default backoff."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        side_effect=[
            httpx.Response(429, json={"error": "rate limited"}),
            _ok_response(),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        text, sid = await client.send_message("hi")

    assert text == "Hello!"
    mock_sleep.assert_called_once_with(1)  # first backoff


# ---------------------------------------------------------------------------
# Retry success path: fail then succeed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_connect_error_then_success(client):
    """3 connection failures then success on 4th attempt."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            _ok_response(),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        text, sid = await client.send_message("hi")

    assert text == "Hello!"
    assert route.call_count == 4
    assert mock_sleep.call_count == 3  # slept before each retry


# ---------------------------------------------------------------------------
# 401 — no retry, immediate HermesAuthError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_401_raises_auth_error_no_retry(client):
    """HTTP 401 should immediately raise HermesAuthError without retrying."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}),
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(HermesAuthError) as exc_info:
            await client.send_message("hi")

    assert route.call_count == 1
    assert mock_sleep.call_count == 0
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 500 — retry then HermesServerError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_500_retries_then_raises_server_error(client):
    """HTTP 500 should be retried, then raise HermesServerError."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        return_value=httpx.Response(500, json={"error": "internal"}),
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(HermesServerError) as exc_info:
            await client.send_message("hi")

    # 3 retries means 4 total attempts
    assert route.call_count == 4
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@respx.mock
async def test_500_then_success(client):
    """HTTP 500 once, then success."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        side_effect=[
            httpx.Response(500, json={"error": "internal"}),
            _ok_response(),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        text, sid = await client.send_message("hi")

    assert text == "Hello!"
    assert route.call_count == 2
    assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# 503 — retryable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_503_retries_and_uses_retry_after(client):
    """HTTP 503 should retry and respect Retry-After header."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "3"}, json={"error": "unavailable"}),
            _ok_response(),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        text, sid = await client.send_message("hi")

    assert text == "Hello!"
    mock_sleep.assert_called_once_with(3.0)


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

class TestTimeoutConfiguration:
    """Verify timeout layering is correctly applied."""

    def test_default_timeouts(self):
        c = HermesClient()
        t = c._build_http_timeout()
        assert t.connect == 10
        assert t.read == 60
        assert t.write == 10
        assert t.pool == 300

    def test_custom_read_timeout(self):
        c = HermesClient(read_timeout=120)
        t = c._build_http_timeout()
        assert t.read == 120

    def test_custom_connect_timeout(self):
        c = HermesClient(connect_timeout=5)
        t = c._build_http_timeout()
        assert t.connect == 5


# ---------------------------------------------------------------------------
# Streaming basic error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_stream_connect_error_retries(client):
    """Stream: ConnectError should be retried."""
    route = respx.post("http://localhost:8642/v1/chat/completions")

    # First attempt: ConnectError, second: SSE stream
    sse_body = (
        "data: {\"choices\":[{\"delta\":{\"content\":\"Hi\"}}]}\n\n"
        "data: [DONE]\n\n"
    )
    route.mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.Response(
                200,
                content=sse_body.encode(),
                headers={"content-type": "text/event-stream"},
            ),
        ]
    )

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock):
        chunks = []
        async for chunk in client.send_message_stream("hi"):
            chunks.append(chunk)

    assert chunks == ["Hi"]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_stream_connect_error_exhausted(client):
    """Stream: after max retries, should raise HermesConnectionError."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(side_effect=httpx.ConnectError("refused"))

    with patch("hermes_a2a.hermes_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(HermesConnectionError):
            async for _ in client.send_message_stream("hi"):
                pass

    assert route.call_count == 4  # initial + 3 retries


@pytest.mark.asyncio
@respx.mock
async def test_stream_401_raises_auth_error(client):
    """Stream: HTTP 401 should raise HermesAuthError without retry."""
    route = respx.post("http://localhost:8642/v1/chat/completions")
    route.mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"}),
    )

    with pytest.raises(HermesAuthError) as exc_info:
        async for _ in client.send_message_stream("hi"):
            pass

    assert exc_info.value.status_code == 401
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# Backward compatibility: existing API unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_send_message_new_session_unchanged(client):
    """Existing test: send_message still works as before (backward compat)."""
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=_ok_response(),
    )
    text, sid = await client.send_message("Hello")
    assert text == "Hello!"
    assert sid is not None


@pytest.mark.asyncio
@respx.mock
async def test_send_message_with_session_id(client):
    """send_message passes session_id header correctly."""
    respx.post("http://localhost:8642/v1/chat/completions").mock(
        return_value=_ok_response(session_id="sess-123"),
    )
    text, sid = await client.send_message("Hello", session_id="sess-123")
    assert text == "Hello!"
    assert sid == "sess-123"
