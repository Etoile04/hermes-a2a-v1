"""Build an A2A v1.0 compliant Agent Card from GatewayConfig."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_a2a.models import GatewayConfig

_VERSION = "0.1.0"
_PROTOCOL_VERSION = "1.0"


def build_agent_card(config: GatewayConfig) -> dict:
    """Build A2A v1.0 Agent Card from config.

    Returns a dict that will be served as JSON at /.well-known/agent-card.json
    """
    card: dict = {
        "name": config.agent.name,
        "description": config.agent.description,
        "url": config.agent.url,
        "version": _VERSION,
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
        },
        "skills": _build_skills(config),
    }

    if config.auth.enabled:
        card["authentication"] = {"schemes": ["bearer"]}

    return card


def _build_skills(config: GatewayConfig) -> list[dict]:
    """Build the skills list, falling back to a default 'general' skill."""
    if config.agent.skills:
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
            }
            for skill in config.agent.skills
        ]

    return [
        {
            "id": "general",
            "name": "General",
            "description": "General-purpose assistance",
        }
    ]
