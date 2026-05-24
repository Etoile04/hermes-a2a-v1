"""FastAPI server for the Hermes A2A v1.0 Gateway.

Wires together:
  - a2a-sdk routes (JSON-RPC + agent-card)
  - HermesA2AHandler (RequestHandler → HermesClient)
  - SQLiteTaskStore
  - Optional bearer-token auth middleware
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from hermes_a2a.a2a_handler import HermesA2AHandler
from hermes_a2a.config import load_config
from hermes_a2a.hermes_client import HermesClient
from hermes_a2a.task_store import SQLiteTaskStore

logger = logging.getLogger(__name__)


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

    return AgentCard(
        name=cfg.agent.name,
        description=cfg.agent.description,
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        skills=skills,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
    )


# ------------------------------------------------------------------
# Auth middleware (optional)
# ------------------------------------------------------------------

def _make_auth_middleware(token: str):
    """Return a Starlette middleware that checks Bearer token."""
    async def auth_middleware(request: Request, call_next):
        # Allow unauthenticated access to agent card and health
        if request.url.path in ("/.well-known/agent-card.json", "/health"):
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

    handler = HermesA2AHandler(hermes_client, task_store)
    agent_card = _build_agent_card(cfg)

    # Store references on the app state for external access (e.g. tests)
    app_state = {
        "hermes_client": hermes_client,
        "task_store": task_store,
        "handler": handler,
        "config": cfg,
    }

    # -- lifespan (init/cleanup async resources) ---------------------
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await task_store.init()
        logger.info(
            "Hermes A2A Gateway started — agent=%r, hermes=%s",
            cfg.agent.name,
            cfg.hermes.api_url,
        )
        yield
        await task_store.close()
        logger.info("Hermes A2A Gateway shut down")

    app = FastAPI(
        title="Hermes A2A Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Expose state
    app.state.gateway = app_state

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
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
    )

    # Health check
    async def health(request: Request):
        return Response(content='{"status":"ok"}', media_type="application/json")

    app.routes.extend(card_routes)
    app.routes.extend(rpc_routes)
    app.routes.append(Route("/health", health, methods=["GET"]))

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
