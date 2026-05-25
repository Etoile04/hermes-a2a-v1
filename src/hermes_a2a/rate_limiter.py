"""Token-bucket rate limiter as Starlette middleware."""

from __future__ import annotations

import time
import threading
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class _TokenBucket:
    """Per-client token bucket for rate limiting.

    Thread-safe: all mutations are guarded by a private lock.
    """

    __slots__ = ("tokens", "max_tokens", "refill_rate", "last_refill", "lock")

    def __init__(self, max_tokens: float, refill_rate: float) -> None:
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = max_tokens
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class TokenBucketRateLimiter(BaseHTTPMiddleware):
    """Starlette middleware that rate-limits requests per client IP using a
    token-bucket algorithm.

    Parameters
    ----------
    app:
        The ASGI application to wrap.
    requests_per_minute:
        Sustained request rate (tokens replenished at this rate).
    burst_size:
        Maximum burst — the bucket capacity.  A client can send this many
        requests instantly before being throttled.
    """

    def __init__(
        self,
        app: Any,
        requests_per_minute: int = 60,
        burst_size: int = 10,
    ) -> None:
        super().__init__(app)
        self._refill_rate = requests_per_minute / 60.0  # tokens per second
        self._burst_size = burst_size
        self._buckets: dict[str, _TokenBucket] = {}
        self._buckets_lock = threading.Lock()

    # -- helpers ----------------------------------------------------------

    def _get_bucket(self, client_ip: str) -> _TokenBucket:
        """Get or create a token bucket for *client_ip*."""
        # Fast path: bucket already exists (no lock needed for read-only,
        # but dict is not safe for concurrent read+write so we still lock).
        with self._buckets_lock:
            bucket = self._buckets.get(client_ip)
            if bucket is None:
                bucket = _TokenBucket(
                    max_tokens=float(self._burst_size),
                    refill_rate=self._refill_rate,
                )
                self._buckets[client_ip] = bucket
            return bucket

    @staticmethod
    def _extract_client_ip(request: Request) -> str:
        """Extract the client IP from the request, respecting X-Forwarded-For."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First IP in the list is the original client
            return forwarded.split(",")[0].strip()
        client = request.client
        if client:
            return client.host
        return "unknown"

    # -- middleware entry point -------------------------------------------

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        client_ip = self._extract_client_ip(request)
        bucket = self._get_bucket(client_ip)

        if not bucket.consume():
            return JSONResponse(
                status_code=429,
                content={"error": "Too many requests", "retry_after": "1s"},
            )

        return await call_next(request)
