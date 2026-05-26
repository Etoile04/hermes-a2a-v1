"""WebSocket broadcaster for live dashboard updates.

Provides:
  - A WebSocket endpoint at /admin/ws
  - A registry of connected clients
  - Broadcast methods for task events, peer status changes, and metrics ticks
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from starlette.routing import Route
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketBroadcaster:
    """Manages WebSocket connections and broadcasts events to all clients."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
        self._background_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.debug("WS client connected (total=%d)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass
        logger.debug("WS client disconnected (total=%d)", len(self._connections))

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Send an event to all connected clients.

        Payload format: {"type": <event_type>, "data": {...}, "timestamp": ...}
        """
        payload = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        })
        stale: list[WebSocket] = []
        async with self._lock:
            for ws in self._connections:
                try:
                    await ws.send_text(payload)
                except Exception:
                    stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

    # Convenience wrappers

    async def broadcast_task_created(self, task_id: str, context_id: str | None = None) -> None:
        await self.broadcast("task_created", {"task_id": task_id, "context_id": context_id})

    async def broadcast_task_completed(self, task_id: str) -> None:
        await self.broadcast("task_completed", {"task_id": task_id})

    async def broadcast_task_failed(self, task_id: str, error: str = "") -> None:
        await self.broadcast("task_failed", {"task_id": task_id, "error": error})

    async def broadcast_peer_status(self, peer_name: str, status: str) -> None:
        await self.broadcast("peer_status", {"peer_name": peer_name, "status": status})

    async def broadcast_metrics_tick(self, metrics: dict[str, Any]) -> None:
        await self.broadcast("metrics_tick", metrics)

    # ------------------------------------------------------------------
    # Periodic metrics broadcast
    # ------------------------------------------------------------------

    async def start_metrics_loop(self, gateway: dict, interval: float = 5.0) -> None:
        """Background task: broadcast metrics every *interval* seconds."""
        self._background_task = asyncio.create_task(
            self._metrics_loop(gateway, interval)
        )

    async def stop_metrics_loop(self) -> None:
        if self._background_task is not None:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None

    async def _metrics_loop(self, gateway: dict, interval: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                metrics = await _collect_metrics(gateway)
                await self.broadcast_metrics_tick(metrics)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in metrics broadcast loop")


async def _collect_metrics(gateway: dict) -> dict[str, Any]:
    """Collect current metrics from the gateway state."""
    m = gateway.get("metrics", {})
    handler_ref = gateway.get("handler")
    ts = gateway.get("task_store")
    pm = gateway.get("peer_manager")

    active_tasks = 0
    if ts is not None:
        try:
            all_tasks = await ts.list()
            active_tasks = len(all_tasks)
        except Exception:
            pass

    peer_health: dict[str, str] = {}
    if pm is not None:
        try:
            peer_health = pm.peer_status
        except Exception:
            pass

    return {
        "request_count": m.get("requests_total", 0),
        "errors_total": m.get("errors_total", 0),
        "latency_p50": 0,
        "latency_p95": 0,
        "latency_p99": 0,
        "active_tasks": active_tasks,
        "peer_health": peer_health,
    }


# ------------------------------------------------------------------
# Route handler factory
# ------------------------------------------------------------------

def _make_ws_endpoint(broadcaster: WebSocketBroadcaster) -> Any:
    """Create the WebSocket handler for /admin/ws."""

    async def handler(ws: WebSocket) -> None:
        await broadcaster.connect(ws)
        try:
            while True:
                # Keep connection alive; client messages are ignored
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await broadcaster.disconnect(ws)

    return handler


def create_ws_routes(broadcaster: WebSocketBroadcaster | None = None) -> list[Route]:
    """Return a list of Starlette routes for WebSocket endpoints."""
    if broadcaster is None:
        broadcaster = WebSocketBroadcaster()
    from starlette.routing import WebSocketRoute
    return [
        WebSocketRoute("/admin/ws", _make_ws_endpoint(broadcaster)),
    ]
