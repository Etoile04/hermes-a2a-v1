"""Admin API route handlers for operational management.

Provides helper functions that return async route-handler closures
following the same pattern as server.py — each closure captures the
app's gateway state via ``request.app.state.gateway``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import Request, Response
from sse_starlette.sse import EventSourceResponse
from starlette.routing import Route

from hermes_a2a.models import PeerConfig


# ------------------------------------------------------------------
# Route handler factories
# ------------------------------------------------------------------


def _make_admin_peers_list() -> Any:
    """GET /admin/peers — list peers with health status."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        pm = gw["peer_manager"]
        peers = await pm.list_peers()
        return Response(
            content=json.dumps({"peers": peers}),
            media_type="application/json",
        )

    return handler


def _make_admin_peers_add() -> Any:
    """POST /admin/peers — add a new peer dynamically."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        pm = gw["peer_manager"]
        data = await request.json()

        name = data.get("name")
        agent_card_url = data.get("agent_card_url")
        if not name or not agent_card_url:
            return Response(
                status_code=400,
                content=json.dumps({"error": "Missing required fields: name, agent_card_url"}),
                media_type="application/json",
            )

        # Check for duplicate
        for p in pm.peers_config:
            if p.name == name:
                return Response(
                    status_code=409,
                    content=json.dumps({"error": f"Peer '{name}' already exists"}),
                    media_type="application/json",
                )

        peer_cfg = PeerConfig(
            name=name,
            agent_card_url=agent_card_url,
            auth_token=data.get("auth_token", ""),
            enabled=data.get("enabled", True),
        )
        pm.peers_config.append(peer_cfg)
        return Response(
            status_code=201,
            content=json.dumps({"message": f"Peer '{name}' added", "peer": {"name": name, "agent_card_url": agent_card_url}}),
            media_type="application/json",
        )

    return handler


def _make_admin_peers_remove() -> Any:
    """DELETE /admin/peers/{name} — remove a peer by name."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        pm = gw["peer_manager"]
        peer_name = request.path_params["name"]

        original_len = len(pm.peers_config)
        pm.peers_config = [p for p in pm.peers_config if p.name != peer_name]
        # Also clear any cached discovery info
        pm._discovered.pop(peer_name, None)

        if len(pm.peers_config) == original_len:
            return Response(
                status_code=404,
                content=json.dumps({"error": f"Peer '{peer_name}' not found"}),
                media_type="application/json",
            )

        return Response(
            content=json.dumps({"message": f"Peer '{peer_name}' removed"}),
            media_type="application/json",
        )

    return handler


def _make_admin_peers_check() -> Any:
    """POST /admin/peers/{name}/check — trigger health check for a peer."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        pm = gw["peer_manager"]
        peer_name = request.path_params["name"]

        peer = next(
            (p for p in pm.peers_config if p.name == peer_name and p.enabled),
            None,
        )
        if not peer:
            return Response(
                status_code=404,
                content=json.dumps({"error": f"Peer '{peer_name}' not found or disabled"}),
                media_type="application/json",
            )

        result = await pm._discover_one(peer)
        if result.get("status") != "error":
            pm._discovered[peer_name] = result

        return Response(
            content=json.dumps(result),
            media_type="application/json",
        )

    return handler


def _make_admin_tasks_list() -> Any:
    """GET /admin/tasks — list tasks with optional filters."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        ts = gw["task_store"]

        status_filter = request.query_params.get("status")
        context_id = request.query_params.get("context_id")
        limit_str = request.query_params.get("limit")

        tasks = await ts.list()

        # Apply status filter
        if status_filter is not None:
            filtered = []
            for t in tasks:
                task_status = t.get("status", {})
                if isinstance(task_status, dict):
                    state = task_status.get("state")
                else:
                    state = task_status
                if str(state) == status_filter:
                    filtered.append(t)
            tasks = filtered

        # Apply context_id filter
        if context_id is not None:
            tasks = [t for t in tasks if t.get("contextId") == context_id]

        # Apply limit
        if limit_str is not None:
            try:
                limit = int(limit_str)
                tasks = tasks[:limit]
            except ValueError:
                pass

        return Response(
            content=json.dumps({"tasks": tasks, "count": len(tasks)}),
            media_type="application/json",
        )

    return handler


def _make_admin_tasks_delete() -> Any:
    """DELETE /admin/tasks/{task_id} — delete a task."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        ts = gw["task_store"]
        task_id = request.path_params["task_id"]

        existing = await ts.get(task_id)
        if existing is None:
            return Response(
                status_code=404,
                content=json.dumps({"error": f"Task '{task_id}' not found"}),
                media_type="application/json",
            )

        await ts.delete(task_id)
        return Response(
            content=json.dumps({"message": f"Task '{task_id}' deleted"}),
            media_type="application/json",
        )

    return handler


def _make_admin_metrics() -> Any:
    """GET /admin/metrics — detailed operational metrics."""

    async def handler(request: Request) -> Response:
        gw = request.app.state.gateway
        m = gw["metrics"]
        handler_ref = gw["handler"]
        ts = gw["task_store"]
        cfg = gw["config"]

        # Uptime
        start = gw.get("start_time")
        uptime_seconds = round(time.time() - start, 1) if start else 0

        # Active sessions
        active_sessions = len(handler_ref._sessions)

        # Peer count
        pm = gw["peer_manager"]
        peer_count = len([p for p in pm.peers_config if p.enabled])

        # Task counts by status
        all_tasks = await ts.list()
        task_counts: dict[str, int] = {}
        for t in all_tasks:
            task_status = t.get("status", {})
            if isinstance(task_status, dict):
                state = str(task_status.get("state", "unknown"))
            else:
                state = str(task_status)
            task_counts[state] = task_counts.get(state, 0) + 1

        result = {
            "uptime_seconds": uptime_seconds,
            "requests_total": m["requests_total"],
            "errors_total": m["errors_total"],
            "active_sessions": active_sessions,
            "peer_count": peer_count,
            "task_count_by_status": task_counts,
            "total_tasks": len(all_tasks),
            "version": "0.1.0",
        }
        return Response(
            content=json.dumps(result),
            media_type="application/json",
        )

    return handler


# ------------------------------------------------------------------
# SSE Metrics Stream
# ------------------------------------------------------------------


async def _collect_stream_metrics(gw: dict) -> dict[str, Any]:
    """Collect metrics payload for SSE stream and WS broadcast."""
    m = gw.get("metrics", {})
    handler_ref = gw.get("handler")
    ts = gw.get("task_store")
    pm = gw.get("peer_manager")

    active_tasks = 0
    if ts is not None:
        try:
            all_tasks = await ts.list()
            active_tasks = len(all_tasks)
        except Exception:
            all_tasks = []
    else:
        all_tasks = []

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


def _make_admin_metrics_stream(interval: float = 5.0) -> Any:
    """GET /admin/metrics/stream — SSE endpoint pushing metrics every *interval* seconds."""

    async def handler(request: Request) -> EventSourceResponse:
        gw = request.app.state.gateway
        # Allow tests to limit the number of events via ?max_events=N
        max_events = request.query_params.get("max_events")
        max_events = int(max_events) if max_events else None

        async def event_generator():
            count = 0
            while True:
                if await request.is_disconnected():
                    break
                metrics = await _collect_stream_metrics(gw)
                yield {
                    "event": "metrics",
                    "data": json.dumps(metrics),
                }
                count += 1
                if max_events is not None and count >= max_events:
                    break
                await asyncio.sleep(interval)

        return EventSourceResponse(event_generator())

    return handler


# ------------------------------------------------------------------
# Public helper: build all admin routes
# ------------------------------------------------------------------


def create_admin_routes() -> list[Route]:
    """Return a list of Starlette Routes for the admin API.

    Called from ``server.py`` during app setup.
    """
    return [
        Route("/admin/peers", _make_admin_peers_list(), methods=["GET"]),
        Route("/admin/peers", _make_admin_peers_add(), methods=["POST"]),
        Route("/admin/peers/{name}", _make_admin_peers_remove(), methods=["DELETE"]),
        Route("/admin/peers/{name}/check", _make_admin_peers_check(), methods=["POST"]),
        Route("/admin/tasks", _make_admin_tasks_list(), methods=["GET"]),
        Route("/admin/tasks/{task_id}", _make_admin_tasks_delete(), methods=["DELETE"]),
        Route("/admin/metrics", _make_admin_metrics(), methods=["GET"]),
        Route("/admin/metrics/stream", _make_admin_metrics_stream(), methods=["GET"]),
    ]
