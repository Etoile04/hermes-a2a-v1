"""Custom exceptions for Hermes A2A client."""


class HermesError(Exception):
    """Base exception for all Hermes client errors."""

    def __init__(self, message: str, *, url: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.url = url
        self.status_code = status_code

    def __repr__(self) -> str:
        parts = [self.message]
        if self.url:
            parts.append(f"url={self.url!r}")
        if self.status_code:
            parts.append(f"status_code={self.status_code}")
        return f"{type(self).__name__}({', '.join(parts)})"


class HermesConnectionError(HermesError):
    """Raised when the Hermes server is unreachable or the connection fails after retries."""


class HermesAuthError(HermesError):
    """Raised on authentication failures (HTTP 401)."""


class HermesServerError(HermesError):
    """Raised when the Hermes server returns a 5xx error after all retries are exhausted."""
