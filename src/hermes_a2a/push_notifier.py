"""Push notification support for A2A tasks.

Provides:
  - PushNotificationStore: in-memory dict store for push configs per task_id.
  - PushNotifier: async webhook delivery with retry.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# PushNotificationStore
# ------------------------------------------------------------------

class PushNotificationStore:
    """In-memory store for TaskPushNotificationConfig protos.

    Keyed by (task_id, config_id).  Each task can have multiple configs.
    """

    def __init__(self) -> None:
        # {(task_id, config_id): config_proto}
        self._configs: dict[tuple[str, str], Any] = {}

    # -- CRUD --------------------------------------------------------

    def create(self, config: Any) -> Any:
        """Store a push notification config.  Returns the stored config."""
        task_id: str = config.task_id
        config_id: str = config.id or str(uuid.uuid4())
        # Ensure the proto has an id set
        if not config.id:
            config.id = config_id
        self._configs[(task_id, config_id)] = config
        return config

    def get(self, task_id: str, config_id: str) -> Any | None:
        """Get a specific config by task_id and config_id."""
        return self._configs.get((task_id, config_id))

    def list_configs(self, task_id: str) -> list[Any]:
        """List all configs for a given task_id."""
        return [c for (tid, _), c in self._configs.items() if tid == task_id]

    def delete(self, task_id: str, config_id: str) -> bool:
        """Delete a config. Returns True if found and deleted."""
        key = (task_id, config_id)
        if key in self._configs:
            del self._configs[key]
            return True
        return False


# ------------------------------------------------------------------
# PushNotifier
# ------------------------------------------------------------------

class PushNotifier:
    """Delivers push notifications via HTTP POST with retry."""

    def __init__(
        self,
        store: PushNotificationStore,
        *,
        max_retries: int = 3,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
    ) -> None:
        self._store = store
        self._max_retries = max_retries
        self._retry_delays = retry_delays
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def deliver(self, task_id: str, status: str, message: str) -> None:
        """POST push notifications to all configured URLs for *task_id*.

        Runs as a best-effort background fire-and-forget — errors are logged
        but never raised.
        """
        configs = self._store.list_configs(task_id)
        if not configs:
            return

        payload = {
            "task_id": task_id,
            "status": status,
            "message": message,
        }

        for cfg in configs:
            url: str = cfg.url
            if not url:
                continue

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if cfg.token:
                headers["Authorization"] = f"Bearer {cfg.token}"

            await self._post_with_retry(url, payload, headers)

    async def _post_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> bool:
        """Attempt POST with retries.  Returns True on success."""
        client = await self._ensure_client()
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code < 400:
                    logger.info(
                        "Push notification delivered to %s (status=%d attempt=%d)",
                        url, resp.status_code, attempt + 1,
                    )
                    return True
                logger.warning(
                    "Push notification to %s returned %d (attempt=%d)",
                    url, resp.status_code, attempt + 1,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Push notification to %s failed: %s (attempt=%d)",
                    url, exc, attempt + 1,
                )

            if attempt < self._max_retries - 1:
                delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                await asyncio.sleep(delay)

        logger.error(
            "Push notification to %s failed after %d attempts: %s",
            url, self._max_retries, last_exc,
        )
        return False
