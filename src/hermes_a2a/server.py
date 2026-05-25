"""FastAPI server for the Hermes A2A v1.0 Gateway.

Wires together:
  - a2a-sdk routes (JSON-RPC + agent-card)
  - HermesA2AHandler (RequestHandler → HermesClient)
  - SQLiteTaskStore
  - Optional bearer-token auth middleware
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
)

from hermes_a2a.a2a_handler import HermesA2AHandler
from hermes_a2a.config import load_config
from hermes_a2a.hermes_client import HermesClient
from hermes_a2a.peer_manager import PeerManager
from hermes_a2a.rate_limiter import TokenBucketRateLimiter
from hermes_a2a.session_store import SessionStore
from hermes_a2a.task_store import SQLiteTaskStore

logger = logging.getLogger(__name__)

VERSION = "0.1.0"


# ------------------------------------------------------------------
# Build AgentCard proto from config
# ------------------------------------------------------------------

def _build_agent_card(cfg: Any) -> AgentCard:
    """Create an a2a-sdk AgentCard protobuf from our GatewayConfig."""
    skills = []
    for s in cfg.agent.skills:
        skills.append(AgentSkill(id=s.id, name=s.name, description=s.description))
    if not skills:
        skills.append(
            AgentSkill(id="general", name="General Q&A", description="General conversation")
        )

    # Build provider
    provider = AgentProvider(
        organization=cfg.agent.provider.organization,
        url=cfg.agent.provider.url,
    )

    # Build security schemes
    bearer_auth = HTTPAuthSecurityScheme(
        scheme="bearer",
        description="Bearer token authentication",
    )
    security_scheme = SecurityScheme()
    security_scheme.http_auth_security_scheme.CopyFrom(bearer_auth)

    # Build security requirement
    security_requirement = SecurityRequirement()
    security_requirement.schemes["bearer"].list.append("bearer")

    card = AgentCard(
        name=cfg.agent.name,
        description=cfg.agent.description,
        provider=provider,
        version=VERSION,
        documentation_url=cfg.agent.documentation_url,
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        skills=skills,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )
    card.security_schemes["bearer"].CopyFrom(security_scheme)
    card.security_requirements.append(security_requirement)

    return card


# ------------------------------------------------------------------
# Auth middleware (optional)
# ------------------------------------------------------------------

def _make_auth_middleware(token: str):
    """Return a Starlette middleware that checks Bearer token."""
    async def auth_middleware(request: Request, call_next):
        # Allow unauthenticated access to agent card, health, and metrics
        if request.url.path in ("/.well-known/agent-card.json", "/health", "/metrics"):
            return await call_next(request)
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {token}":
            return Response(status_code=401, content="Unauthorized")
        return await call_next(request)
    return auth_middleware


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def create_app(config_path: str | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    # Fallback to env var when called as uvicorn factory (no args)
    if config_path is None:
        config_path = os.environ.get("HERMES_A2A_CONFIG")
    cfg = load_config(config_path)
    logging.basicConfig(level=getattr(logging, cfg.logging_level, logging.INFO))

    # -- dependencies ------------------------------------------------
    hermes_client = HermesClient(
        base_url=cfg.hermes.api_url,
        timeout=cfg.hermes.timeout,
        api_key=cfg.hermes.api_key or None,
    )
    task_store = SQLiteTaskStore(cfg.task_store.path)
    session_store = SessionStore(cfg.task_store.path)  # share same SQLite DB

    handler = HermesA2AHandler(hermes_client, task_store, session_store)
    agent_card = _build_agent_card(cfg)

    peer_manager = PeerManager(cfg.peers)

    # Metrics counters (shared between handler and endpoints)
    _metrics_counters = {
        "requests_total": 0,
        "errors_total": 0,
    }
    handler._metrics = _metrics_counters  # wire up metrics counter

    # Store references on the app state for external access (e.g. tests)
    app_state = {
        "hermes_client": hermes_client,
        "task_store": task_store,
        "session_store": session_store,
        "handler": handler,
        "config": cfg,
        "start_time": None,  # set in lifespan
        "metrics": _metrics_counters,
        "peer_manager": peer_manager,
    }

    # -- lifespan (init/cleanup async resources) ---------------------
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_state["start_time"] = time.time()
        await task_store.init()
        await session_store.init()
        await handler.restore_sessions()
        # Clean up stale sessions on startup
        deleted = await session_store.cleanup(max_age_hours=24)
        if deleted:
            logger.info("Cleaned up %d stale sessions", deleted)

        # Log startup configuration (hide sensitive fields)
        safe_cfg = {
            "server": {"host": cfg.server.host, "port": cfg.server.port},
            "hermes": {
                "api_url": cfg.hermes.api_url,
                "timeout": cfg.hermes.timeout,
                "api_key": "***" if cfg.hermes.api_key else "(none)",
            },
            "agent": {"name": cfg.agent.name, "description": cfg.agent.description},
            "auth": {"enabled": cfg.auth.enabled, "token": "***" if cfg.auth.token else "(none)"},
            "task_store": {"type": cfg.task_store.type, "path": cfg.task_store.path},
            "logging_level": cfg.logging_level,
            "version": VERSION,
        }
        logger.info("Hermes A2A Gateway starting — config=%s", json.dumps(safe_cfg))
        yield
        await session_store.close()
        await task_store.close()
        await peer_manager.close()
        logger.info("Hermes A2A Gateway shut down")

    app = FastAPI(
        title="Hermes A2A Gateway",
        version=VERSION,
        lifespan=lifespan,
    )

    # Expose state
    app.state.gateway = app_state

    # CORS (configurable origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors.origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting middleware (before auth so unauthenticated requests are also limited)
    if cfg.rate_limit.enabled:
        app.add_middleware(
            TokenBucketRateLimiter,
            requests_per_minute=cfg.rate_limit.requests_per_minute,
            burst_size=cfg.rate_limit.burst_size,
        )

    # Auth middleware (after CORS)
    if cfg.auth.enabled and cfg.auth.token:
        app.add_middleware(BaseHTTPMiddleware, dispatch=_make_auth_middleware(cfg.auth.token))

    # -- A2A routes --------------------------------------------------
    # Agent card at /.well-known/agent-card.json
    card_routes = create_agent_card_routes(agent_card)

    # JSON-RPC endpoint at / (A2A spec default)
    rpc_routes = create_jsonrpc_routes(
        request_handler=handler,
        rpc_url="/",
        enable_v0_3_compat=True,
    )

    # Health check (enhanced)
    async def health(request: Request):
        gw = request.app.state.gateway
        hermes_client: HermesClient = gw["hermes_client"]

        # Check Hermes API reachability with latency measurement
        t0 = time.monotonic()
        try:
            reachable = await hermes_client.health_check()
        except Exception:
            reachable = False
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        status = "ok" if reachable else "degraded"

        # Calculate uptime
        start = gw.get("start_time")
        uptime_seconds = round(time.time() - start, 1) if start else 0

        # Active sessions count
        handler_ref: HermesA2AHandler = gw["handler"]
        active_sessions = len(handler_ref._sessions)

        result = {
            "status": status,
            "hermes_api": {
                "reachable": reachable,
                "latency_ms": latency_ms,
            },
            "task_store": {
                "type": cfg.task_store.type,
                "db_path": cfg.task_store.path,
            },
            "sessions": {
                "active": active_sessions,
            },
            "uptime_seconds": uptime_seconds,
            "version": VERSION,
        }
        return Response(content=json.dumps(result), media_type="application/json")

    # Metrics endpoint
    async def metrics(request: Request):
        gw = request.app.state.gateway
        m = gw["metrics"]
        handler_ref: HermesA2AHandler = gw["handler"]
        result = {
            "requests_total": m["requests_total"],
            "errors_total": m["errors_total"],
            "active_tasks": len(await gw["task_store"].list()),
            "sessions_active": len(handler_ref._sessions),
        }
        return Response(content=json.dumps(result), media_type="application/json")

    # REST endpoints at /a2a/ (A2A v1.0 + v0.3 compat)
    rest_routes = create_rest_routes(
        request_handler=handler,
        path_prefix="/a2a",
        enable_v0_3_compat=True,
    )

    # -- Peer management route handlers ----------------------------
    async def list_peers_endpoint(request: Request):
        gw = request.app.state.gateway
        pm: PeerManager = gw["peer_manager"]
        peers = await pm.list_peers()
        return Response(
            content=json.dumps({"peers": peers}), media_type="application/json"
        )

    async def discover_peers_endpoint(request: Request):
        gw = request.app.state.gateway
        pm: PeerManager = gw["peer_manager"]
        discovered = await pm.discover_all()
        return Response(
            content=json.dumps({"discovered": discovered}),
            media_type="application/json",
        )

    async def relay_message_endpoint(request: Request):
        gw = request.app.state.gateway
        pm: PeerManager = gw["peer_manager"]
        data = await request.json()
        peer_name = data.get("peer_name")
        message = data.get("message")
        if not peer_name or not message:
            return Response(
                status_code=400,
                content=json.dumps({"error": "Missing peer_name or message"}),
            )
        result = await pm.send_to_peer(peer_name, message, data.get("context_id"))
        return Response(content=json.dumps(result), media_type="application/json")

    # Register custom routes BEFORE rest_routes to avoid being shadowed
    # by the SDK's catch-all /{tenant} route.
    peer_routes = [
        Route("/a2a/peers", list_peers_endpoint, methods=["GET"]),
        Route("/a2a/peers/discover", discover_peers_endpoint, methods=["POST"]),
        Route("/a2a/relay", relay_message_endpoint, methods=["POST"]),
    ]

    app.routes.extend(card_routes)
    app.routes.extend(rpc_routes)
    app.routes.extend(peer_routes)  # custom routes before REST catch-all
    app.routes.extend(rest_routes)
    app.routes.append(Route("/health", health, methods=["GET"]))
    app.routes.append(Route("/metrics", metrics, methods=["GET"]))

    return app


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------

def main():
    """Run the gateway with uvicorn."""
    import uvicorn

    config_path = os.environ.get("HERMES_A2A_CONFIG")
    cfg = load_config(config_path)

    # Build the app string for uvicorn import
    if config_path:
        os.environ["HERMES_A2A_CONFIG"] = config_path

    uvicorn.run(
        "hermes_a2a.server:create_app",
        factory=True,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.logging_level.lower(),
    )


if __name__ == "__main__":
    main()
