"""Tests for enhanced AgentCard with provider, documentation_url, and security schemes."""

import pytest
from unittest.mock import MagicMock

from hermes_a2a.models import AgentConfig, AgentProviderConfig, GatewayConfig
from hermes_a2a.server import _build_agent_card, VERSION


class TestAgentProviderConfig:
    """Test AgentProviderConfig model defaults and customization."""

    def test_default_organization(self):
        cfg = AgentProviderConfig()
        assert cfg.organization == "Hermes"

    def test_default_url(self):
        cfg = AgentProviderConfig()
        assert "hermes" in cfg.url.lower()

    def test_custom_values(self):
        cfg = AgentProviderConfig(organization="Acme Corp", url="https://acme.example.com")
        assert cfg.organization == "Acme Corp"
        assert cfg.url == "https://acme.example.com"


class TestAgentConfigEnhanced:
    """Test that AgentConfig now includes provider and documentation_url."""

    def test_default_provider_exists(self):
        cfg = AgentConfig()
        assert isinstance(cfg.provider, AgentProviderConfig)
        assert cfg.provider.organization == "Hermes"

    def test_default_documentation_url(self):
        cfg = AgentConfig()
        assert cfg.documentation_url != ""
        assert "github" in cfg.documentation_url.lower() or "hermes" in cfg.documentation_url.lower()

    def test_custom_provider(self):
        cfg = AgentConfig(
            provider=AgentProviderConfig(organization="TestOrg", url="https://test.example.com")
        )
        assert cfg.provider.organization == "TestOrg"

    def test_custom_documentation_url(self):
        cfg = AgentConfig(documentation_url="https://docs.example.com/agent")
        assert cfg.documentation_url == "https://docs.example.com/agent"


class TestBuildAgentCard:
    """Test that _build_agent_card populates provider, security, and doc URL."""

    @pytest.fixture
    def default_config(self):
        return GatewayConfig()

    @pytest.fixture
    def custom_config(self):
        return GatewayConfig(
            agent=AgentConfig(
                name="Custom Agent",
                description="A custom agent",
                documentation_url="https://docs.custom.com",
                provider=AgentProviderConfig(
                    organization="CustomOrg",
                    url="https://custom.org",
                ),
            )
        )

    def test_card_has_provider(self, default_config):
        card = _build_agent_card(default_config)
        assert card.HasField("provider")
        assert card.provider.organization == "Hermes"

    def test_card_has_documentation_url(self, default_config):
        card = _build_agent_card(default_config)
        assert card.documentation_url != ""

    def test_card_has_security_schemes(self, default_config):
        card = _build_agent_card(default_config)
        assert len(card.security_schemes) > 0
        # Should have a 'bearer' scheme
        assert "bearer" in card.security_schemes

    def test_card_has_security_requirements(self, default_config):
        card = _build_agent_card(default_config)
        assert len(card.security_requirements) > 0

    def test_card_security_scheme_is_bearer(self, default_config):
        card = _build_agent_card(default_config)
        scheme = card.security_schemes["bearer"]
        assert scheme.HasField("http_auth_security_scheme")
        assert scheme.http_auth_security_scheme.scheme == "bearer"

    def test_card_custom_provider(self, custom_config):
        card = _build_agent_card(custom_config)
        assert card.provider.organization == "CustomOrg"
        assert card.provider.url == "https://custom.org"

    def test_card_custom_documentation_url(self, custom_config):
        card = _build_agent_card(custom_config)
        assert card.documentation_url == "https://docs.custom.com"

    def test_card_still_has_basic_fields(self, default_config):
        """Ensure original fields are still present after enhancement."""
        card = _build_agent_card(default_config)
        assert card.name == default_config.agent.name
        assert card.description == default_config.agent.description
        assert card.version == VERSION
        assert len(card.skills) > 0
        assert "text/plain" in card.default_input_modes

    def test_gateway_config_default_round_trip(self):
        """Full GatewayConfig → AgentCard without errors."""
        cfg = GatewayConfig()
        card = _build_agent_card(cfg)
        assert card.name != ""
        assert card.provider.organization != ""
        assert card.documentation_url != ""
        assert len(card.security_schemes) > 0
