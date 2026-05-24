"""Tests for Agent Card builder."""

from hermes_a2a.agent_card import build_agent_card
from hermes_a2a.models import (
    AgentConfig,
    AgentSkillConfig,
    AuthConfig,
    GatewayConfig,
)


def _make_config(**overrides) -> GatewayConfig:
    """Helper to build a GatewayConfig with sensible defaults."""
    defaults = {
        "agent": {
            "name": "Hermes Agent - Mac (A)",
            "description": "AI Agent powered by Hermes via A2A v1.0",
            "url": "http://100.64.0.1:18800",
        },
        "auth": {"enabled": True, "token": "secret-token"},
    }
    defaults.update(overrides)
    return GatewayConfig(**defaults)


def test_build_agent_card_basic():
    """Verify all required fields are present and correct."""
    config = _make_config(
        agent={
            "name": "Hermes Agent - Mac (A)",
            "description": "AI Agent powered by Hermes via A2A v1.0",
            "url": "http://100.64.0.1:18800",
            "skills": [
                {"id": "general", "name": "General Q&A", "description": "Answer general questions"},
            ],
        },
        auth={"enabled": True, "token": "secret-token"},
    )
    card = build_agent_card(config)

    assert card["name"] == "Hermes Agent - Mac (A)"
    assert card["description"] == "AI Agent powered by Hermes via A2A v1.0"
    assert card["url"] == "http://100.64.0.1:18800"
    assert card["version"] == "0.1.0"
    assert card["protocolVersion"] == "1.0"

    # capabilities
    assert card["capabilities"]["streaming"] is True
    assert card["capabilities"]["pushNotifications"] is False

    # skills
    assert len(card["skills"]) == 1
    assert card["skills"][0]["id"] == "general"
    assert card["skills"][0]["name"] == "General Q&A"
    assert card["skills"][0]["description"] == "Answer general questions"

    # authentication
    assert "authentication" in card
    assert card["authentication"]["schemes"] == ["bearer"]


def test_build_agent_card_no_skills():
    """When no skills are defined, a default 'general' skill should be added."""
    config = _make_config(
        agent={
            "name": "Test Agent",
            "description": "desc",
            "url": "http://localhost:18800",
            "skills": [],
        },
        auth={"enabled": False},
    )
    card = build_agent_card(config)

    assert len(card["skills"]) == 1
    assert card["skills"][0]["id"] == "general"
    assert card["skills"][0]["name"] == "General"


def test_build_agent_card_no_auth():
    """No authentication section when auth is disabled."""
    config = _make_config(
        auth={"enabled": False},
    )
    card = build_agent_card(config)

    assert "authentication" not in card


def test_build_agent_card_custom_skills():
    """Multiple custom skills from config."""
    config = _make_config(
        agent={
            "name": "Multi-Skill Agent",
            "description": "desc",
            "url": "http://localhost:18800",
            "skills": [
                {"id": "translate", "name": "Translation", "description": "Translate text"},
                {"id": "summarize", "name": "Summarization", "description": "Summarize documents"},
                {"id": "code", "name": "Code Review", "description": "Review source code"},
            ],
        },
        auth={"enabled": True, "token": "tok"},
    )
    card = build_agent_card(config)

    assert len(card["skills"]) == 3
    assert card["skills"][0]["id"] == "translate"
    assert card["skills"][1]["id"] == "summarize"
    assert card["skills"][2]["id"] == "code"


def test_build_agent_card_protocol_version():
    """protocolVersion must be '1.0'."""
    config = _make_config()
    card = build_agent_card(config)

    assert card["protocolVersion"] == "1.0"
