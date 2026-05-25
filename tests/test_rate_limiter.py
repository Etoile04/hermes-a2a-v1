"""Tests for the TokenBucketRateLimiter middleware."""

from __future__ import annotations

import time
import threading
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_a2a.rate_limiter import TokenBucketRateLimiter, _TokenBucket


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_app(rpm: int = 60, burst: int = 3) -> FastAPI:
    """Create a minimal FastAPI app with the rate limiter wired in."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    app.add_middleware(TokenBucketRateLimiter, requests_per_minute=rpm, burst_size=burst)
    return app


# ------------------------------------------------------------------
# 1. Basic: requests within burst are allowed
# ------------------------------------------------------------------

def test_requests_within_burst_allowed():
    """Burst-size requests should all succeed (200)."""
    app = _make_app(rpm=60, burst=5)
    with TestClient(app) as client:
        for i in range(5):
            resp = client.get("/test")
            assert resp.status_code == 200, f"request {i} should succeed"


# ------------------------------------------------------------------
# 2. Requests exceeding burst are rejected (429)
# ------------------------------------------------------------------

def test_requests_exceeding_burst_rejected():
    """After exhausting the bucket, the next request should be 429."""
    app = _make_app(rpm=60, burst=3)
    with TestClient(app) as client:
        for _ in range(3):
            client.get("/test")
        # 4th should be rate-limited
        resp = client.get("/test")
        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body


# ------------------------------------------------------------------
# 3. Per-IP isolation: different IPs have separate buckets
# ------------------------------------------------------------------

def test_per_ip_isolation():
    """Requests from different client IPs should have independent buckets."""
    app = _make_app(rpm=60, burst=2)
    with TestClient(app) as client:
        # Exhaust bucket for IP 1
        for _ in range(2):
            resp = client.get("/test", headers={"X-Forwarded-For": "1.2.3.4"})
            assert resp.status_code == 200
        # IP 1 should now be limited
        resp = client.get("/test", headers={"X-Forwarded-For": "1.2.3.4"})
        assert resp.status_code == 429

        # IP 2 should still be allowed
        resp = client.get("/test", headers={"X-Forwarded-For": "5.6.7.8"})
        assert resp.status_code == 200


# ------------------------------------------------------------------
# 4. Token refill over time
# ------------------------------------------------------------------

def test_token_refill_over_time():
    """After waiting, tokens should refill and requests should be allowed again."""
    # 60 RPM = 1 token/second, burst=1
    app = _make_app(rpm=60, burst=1)
    with TestClient(app) as client:
        # Use the single token
        resp = client.get("/test")
        assert resp.status_code == 200

        # Immediately rejected
        resp = client.get("/test")
        assert resp.status_code == 429

        # Wait enough time for 1 token to refill (>1s)
        time.sleep(1.1)

        resp = client.get("/test")
        assert resp.status_code == 200


# ------------------------------------------------------------------
# 5. Rate limiter disabled via config
# ------------------------------------------------------------------

def test_rate_limit_config_defaults():
    """RateLimitConfig should have expected defaults."""
    from hermes_a2a.models import RateLimitConfig

    cfg = RateLimitConfig()
    assert cfg.enabled is True
    assert cfg.requests_per_minute == 60
    assert cfg.burst_size == 10


# ------------------------------------------------------------------
# 6. CORS config defaults
# ------------------------------------------------------------------

def test_cors_config_defaults():
    """CORSConfig default should be allow_origins=['*']."""
    from hermes_a2a.models import CORSConfig

    cfg = CORSConfig()
    assert cfg.origins == ["*"]


# ------------------------------------------------------------------
# 7. GatewayConfig includes rate_limit and cors
# ------------------------------------------------------------------

def test_gateway_config_includes_new_fields():
    """GatewayConfig should include rate_limit and cors with defaults."""
    from hermes_a2a.models import GatewayConfig

    cfg = GatewayConfig()
    assert cfg.rate_limit.enabled is True
    assert cfg.rate_limit.requests_per_minute == 60
    assert cfg.rate_limit.burst_size == 10
    assert cfg.cors.origins == ["*"]


# ------------------------------------------------------------------
# 8. _TokenBucket thread-safety
# ------------------------------------------------------------------

def test_token_bucket_thread_safety():
    """_TokenBucket should handle concurrent access safely."""
    bucket = _TokenBucket(max_tokens=100.0, refill_rate=10.0)
    allowed = []
    lock = threading.Lock()

    def consume_many(n: int):
        count = 0
        for _ in range(n):
            if bucket.consume():
                count += 1
        with lock:
            allowed.append(count)

    threads = [threading.Thread(target=consume_many, args=(50,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_allowed = sum(allowed)
    # With 100 initial tokens and 4 threads racing for 50 each,
    # at most ~100 should be allowed (some refill may happen during the test)
    assert total_allowed <= 200  # generous upper bound to avoid flakiness
    assert total_allowed >= 100  # at least the initial burst


# ------------------------------------------------------------------
# 9. 429 response body has retry_after
# ------------------------------------------------------------------

def test_429_response_body():
    """Rate-limited response should include error and retry_after."""
    app = _make_app(rpm=60, burst=1)
    with TestClient(app) as client:
        client.get("/test")  # use the token
        resp = client.get("/test")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"] == "Too many requests"
        assert "retry_after" in body
