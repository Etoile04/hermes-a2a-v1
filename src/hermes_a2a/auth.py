"""JWT authentication and Starlette middleware for Hermes A2A Gateway.

Zero-dependency JWT implementation using only stdlib (hmac, hashlib, json, base64).
Supports HS256 (symmetric) and RS256 (asymmetric) algorithms.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthenticationError(Exception):
    """Raised when JWT validation fails."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with padding restoration."""
    # Add padding
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# JWT Auth
# ---------------------------------------------------------------------------

class JWTAuth:
    """JSON Web Token authentication.

    Parameters
    ----------
    secret:
        For HS256 this is the shared secret string.
        For RS256 this is the path to the PEM private key file.
    algorithm:
        ``"HS256"`` (default) or ``"RS256"``.
    expiry_seconds:
        Token lifetime in seconds (default 3600 = 1 hour).
    grace_seconds:
        How many seconds past expiry a token can still be refreshed.
    """

    def __init__(
        self,
        secret: str,
        algorithm: str = "HS256",
        expiry_seconds: int = 3600,
        grace_seconds: int = 300,
    ) -> None:
        if algorithm not in ("HS256", "RS256"):
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        self._secret = secret
        self._algorithm = algorithm
        self._expiry_seconds = expiry_seconds
        self._grace_seconds = grace_seconds

        # For RS256, load keys from file paths
        self._private_key_pem: bytes | None = None
        self._public_key_pem: bytes | None = None
        if algorithm == "RS256":
            key_path = Path(secret)
            if key_path.exists():
                self._private_key_pem = key_path.read_bytes()
                # Try to find public key: same name with .pub or -public suffix
                pub_path = key_path.with_suffix(".pub.pem")
                if not pub_path.exists():
                    pub_path = key_path.with_name(key_path.stem + "-public" + key_path.suffix)
                if pub_path.exists():
                    self._public_key_pem = pub_path.read_bytes()

    # -- signing / verifying ------------------------------------------------

    def _sign(self, message: str) -> str:
        """Sign *message* and return base64url-encoded signature."""
        if self._algorithm == "HS256":
            sig = hmac.new(
                self._secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            return _b64url_encode(sig)
        else:
            # RS256 — use openssl subprocess (zero external deps)
            import subprocess
            if self._private_key_pem is None:
                raise AuthenticationError("RS256 requires a private key file")
            result = subprocess.run(
                ["openssl", "dgst", "-binary", "-sha256", "-sign", self._secret],
                input=message.encode("utf-8"),
                capture_output=True,
            )
            if result.returncode != 0:
                raise AuthenticationError("RS256 signing failed")
            return _b64url_encode(result.stdout)

    def _verify(self, message: str, signature_b64: str) -> bool:
        """Verify *signature_b64* against *message*."""
        if self._algorithm == "HS256":
            expected = self._sign(message)
            return hmac.compare_digest(expected, signature_b64)
        else:
            # RS256 — verify with public key using openssl subprocess
            import subprocess
            sig_bytes = _b64url_decode(signature_b64)
            pub_key = self._public_key_pem
            if pub_key is None:
                # Try extracting public key from private key
                result = subprocess.run(
                    ["openssl", "rsa", "-in", self._secret, "-pubout"],
                    capture_output=True,
                )
                if result.returncode != 0:
                    return False
                pub_key = result.stdout

            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", "/dev/stdin", "-signature", "/dev/stdin"],
                input=pub_key + sig_bytes,
                capture_output=True,
            )
            return result.returncode == 0

    # -- public API ---------------------------------------------------------

    def generate_token(self, subject: str, scopes: list[str] | None = None) -> str:
        """Generate a JWT for *subject* with optional *scopes*.

        Returns the encoded JWT string.
        """
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": subject,
            "iat": now,
            "exp": now + self._expiry_seconds,
        }
        if scopes:
            payload["scopes"] = scopes

        header = {"alg": self._algorithm, "typ": "JWT"}
        header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        message = f"{header_b64}.{payload_b64}"
        signature = self._sign(message)
        return f"{message}.{signature}"

    def validate_token(self, token: str) -> dict:
        """Validate *token* and return its payload dict.

        Raises :class:`AuthenticationError` if the token is invalid or expired.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise AuthenticationError("Invalid token format")
            header_b64, payload_b64, signature_b64 = parts

            # Verify signature
            message = f"{header_b64}.{payload_b64}"
            if not self._verify(message, signature_b64):
                raise AuthenticationError("Invalid signature")

            # Decode payload
            payload_bytes = _b64url_decode(payload_b64)
            payload = json.loads(payload_bytes)

            # Check expiry
            exp = payload.get("exp")
            if exp is not None and time.time() > exp:
                raise AuthenticationError("Token has expired")

            return payload
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(f"Token validation error: {exc}") from exc

    def refresh_token(self, token: str) -> str:
        """Refresh an existing token.

        Generates a new token if the current one is valid or expired within the
        grace period.  Raises :class:`AuthenticationError` if the token is
        completely invalid or beyond the grace period.
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise AuthenticationError("Invalid token format")
            header_b64, payload_b64, signature_b64 = parts

            # Verify signature first
            message = f"{header_b64}.{payload_b64}"
            if not self._verify(message, signature_b64):
                raise AuthenticationError("Invalid signature — cannot refresh")

            # Decode payload
            payload = json.loads(_b64url_decode(payload_b64))

            # Check grace period
            exp = payload.get("exp")
            if exp is not None and time.time() > exp + self._grace_seconds:
                raise AuthenticationError("Token is beyond refresh grace period")

            # Generate new token with same subject and scopes
            subject = payload.get("sub", "")
            scopes = payload.get("scopes")
            return self.generate_token(subject, scopes)

        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(f"Token refresh error: {exc}") from exc


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------

# Paths that do NOT require authentication
_PUBLIC_PATHS: tuple[str, ...] = (
    "/health",
)
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/.well-known/",
    "/admin/dashboard/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates JWT bearer tokens.

    Skips authentication for:
    - ``/health``
    - ``/.well-known/*``
    - ``/admin/dashboard/*``
    """

    def __init__(self, app: Any, jwt_auth: JWTAuth) -> None:
        super().__init__(app)
        self._jwt_auth = jwt_auth

    @staticmethod
    def _is_public_path(path: str) -> bool:
        """Return True if *path* should skip auth."""
        if path in _PUBLIC_PATHS:
            return True
        for prefix in _PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return True
        return False

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if self._is_public_path(request.url.path):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]  # strip "Bearer "
        try:
            self._jwt_auth.validate_token(token)
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=401,
                content={"error": str(exc)},
            )

        return await call_next(request)
