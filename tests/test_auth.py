"""Tests for JWT authentication (auth.py)."""

from __future__ import annotations

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from hermes_a2a.auth import JWTAuth, AuthenticationError, AuthMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(jwt_auth: JWTAuth, public_endpoints: bool = True) -> Starlette:
    """Build a minimal Starlette app with AuthMiddleware."""

    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"status": "ok"})

    async def well_known(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"agent": "hermes"})

    async def dashboard(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"dashboard": True})

    async def protected(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"data": "secret"})

    routes = [
        Route("/health", health),
        Route("/.well-known/agent.json", well_known),
        Route("/admin/dashboard/index", dashboard),
        Route("/api/tasks", protected),
        Route("/admin/peers", protected),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(AuthMiddleware, jwt_auth=jwt_auth)
    return app


# ---------------------------------------------------------------------------
# C1 Tests: JWTAuth
# ---------------------------------------------------------------------------

class TestJWTAuthHS256:
    """Tests for HS256 JWT authentication."""

    def setup_method(self):
        self.jwt = JWTAuth(secret="test-secret-key", algorithm="HS256", expiry_seconds=3600)

    def test_generate_token_returns_string(self):
        token = self.jwt.generate_token("user1")
        assert isinstance(token, str)
        assert token.count(".") == 2  # header.payload.signature

    def test_validate_valid_token(self):
        token = self.jwt.generate_token("user1")
        payload = self.jwt.validate_token(token)
        assert payload["sub"] == "user1"
        assert "iat" in payload
        assert "exp" in payload

    def test_validate_token_with_scopes(self):
        token = self.jwt.generate_token("admin", scopes=["read", "write"])
        payload = self.jwt.validate_token(token)
        assert payload["sub"] == "admin"
        assert payload["scopes"] == ["read", "write"]

    def test_validate_token_without_scopes_has_no_scopes_key(self):
        token = self.jwt.generate_token("user1")
        payload = self.jwt.validate_token(token)
        assert "scopes" not in payload

    def test_validate_expired_token_raises(self):
        jwt_short = JWTAuth(secret="test-secret-key", algorithm="HS256", expiry_seconds=-1)
        token = jwt_short.generate_token("user1")
        with pytest.raises(AuthenticationError, match="expired"):
            jwt_short.validate_token(token)

    def test_validate_wrong_secret_raises(self):
        jwt_a = JWTAuth(secret="secret-a", algorithm="HS256")
        jwt_b = JWTAuth(secret="secret-b", algorithm="HS256")
        token = jwt_a.generate_token("user1")
        with pytest.raises(AuthenticationError, match="Invalid signature"):
            jwt_b.validate_token(token)

    def test_validate_tampered_payload_raises(self):
        token = self.jwt.generate_token("user1")
        # Tamper with the payload part
        parts = token.split(".")
        import base64
        payload_bytes = base64.urlsafe_b64decode(parts[1] + "==")
        payload_dict = json.loads(payload_bytes)
        payload_dict["sub"] = "attacker"
        tampered_payload = base64.urlsafe_b64encode(
            json.dumps(payload_dict).encode()
        ).rstrip(b"=").decode("ascii")
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
        with pytest.raises(AuthenticationError):
            self.jwt.validate_token(tampered_token)

    def test_validate_malformed_token_raises(self):
        with pytest.raises(AuthenticationError, match="Invalid token format"):
            self.jwt.validate_token("not.a.valid.token.format")
        with pytest.raises(AuthenticationError):
            self.jwt.validate_token("onlyonepart")

    def test_refresh_valid_token(self):
        token = self.jwt.generate_token("user1", scopes=["read"])
        new_token = self.jwt.refresh_token(token)
        payload = self.jwt.validate_token(new_token)
        assert payload["sub"] == "user1"
        assert payload["scopes"] == ["read"]
        # New token should have a later expiry
        old_payload = json.loads(
            __import__("base64").urlsafe_b64decode(token.split(".")[1] + "==")
        )
        assert payload["exp"] >= old_payload["exp"]

    def test_refresh_expired_token_within_grace(self):
        jwt_short = JWTAuth(
            secret="test-secret-key",
            algorithm="HS256",
            expiry_seconds=-1,
            grace_seconds=300,
        )
        token = jwt_short.generate_token("user1")
        # Token is expired but within grace period
        new_token = jwt_short.refresh_token(token)
        assert isinstance(new_token, str)

    def test_refresh_expired_token_beyond_grace_raises(self):
        jwt_short = JWTAuth(
            secret="test-secret-key",
            algorithm="HS256",
            expiry_seconds=-1000,
            grace_seconds=1,
        )
        token = jwt_short.generate_token("user1")
        with pytest.raises(AuthenticationError, match="grace period"):
            jwt_short.refresh_token(token)

    def test_unsupported_algorithm_raises(self):
        with pytest.raises(ValueError, match="Unsupported algorithm"):
            JWTAuth(secret="x", algorithm="ES256")


class TestAuthMiddleware:
    """Tests for AuthMiddleware (Starlette integration)."""

    def setup_method(self):
        self.jwt = JWTAuth(secret="middleware-secret", algorithm="HS256")
        self.app = _make_app(self.jwt)
        self.client = TestClient(self.app)

    def test_health_endpoint_no_auth_required(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200

    def test_well_known_no_auth_required(self):
        resp = self.client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_dashboard_no_auth_required(self):
        resp = self.client.get("/admin/dashboard/index")
        assert resp.status_code == 200

    def test_protected_endpoint_requires_auth(self):
        resp = self.client.get("/api/tasks")
        assert resp.status_code == 401

    def test_protected_endpoint_with_valid_token(self):
        token = self.jwt.generate_token("user1")
        resp = self.client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_protected_endpoint_with_expired_token(self):
        jwt_short = JWTAuth(secret="middleware-secret", algorithm="HS256", expiry_seconds=-1)
        token = jwt_short.generate_token("user1")
        resp = self.client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_protected_endpoint_with_wrong_secret_token(self):
        jwt_wrong = JWTAuth(secret="wrong-secret", algorithm="HS256")
        token = jwt_wrong.generate_token("user1")
        resp = self.client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_bearer_prefix_required(self):
        token = self.jwt.generate_token("user1")
        resp = self.client.get("/api/tasks", headers={"Authorization": f"Token {token}"})
        assert resp.status_code == 401

    def test_admin_endpoint_requires_auth(self):
        resp = self.client.get("/admin/peers")
        assert resp.status_code == 401
