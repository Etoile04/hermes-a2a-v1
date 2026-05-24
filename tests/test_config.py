"""Tests for config loading and Pydantic models."""

import os
import tempfile
from pathlib import Path

import pytest

from hermes_a2a.config import load_config
from hermes_a2a.models import (
    AgentConfig,
    AgentSkillConfig,
    AuthConfig,
    GatewayConfig,
    HermesConfig,
    ServerConfig,
    TaskStoreConfig,
)


class TestLoadDefaultConfig:
    """Test loading config from a YAML file with partial overrides."""

    def test_load_from_file_with_overrides(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            """
server:
  host: "127.0.0.1"
  port: 9999
hermes:
  api_url: "http://my-hermes:8642"
"""
        )
        cfg = load_config(str(cfg_file))

        assert isinstance(cfg, GatewayConfig)
        # Overridden values
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9999
        assert cfg.hermes.api_url == "http://my-hermes:8642"
        # Defaults still apply for non-overridden fields
        assert cfg.hermes.timeout == 300
        assert cfg.agent.name == "Hermes Agent"
        assert cfg.agent.url == "http://localhost:18800"
        assert cfg.auth.enabled is True
        assert cfg.task_store.type == "sqlite"
        assert cfg.logging_level == "INFO"

    def test_load_full_config(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            """
server:
  host: "0.0.0.0"
  port: 18800
hermes:
  api_url: "http://localhost:8642"
  timeout: 60
agent:
  name: "Test Agent"
  description: "A test"
  url: "http://localhost:18800"
  skills:
    - id: "coding"
      name: "Coding"
      description: "Write code"
auth:
  enabled: false
  token: "secret"
task_store:
  type: sqlite
  path: "/tmp/tasks.db"
logging:
  level: DEBUG
"""
        )
        cfg = load_config(str(cfg_file))

        assert cfg.server.host == "0.0.0.0"
        assert cfg.hermes.timeout == 60
        assert cfg.agent.name == "Test Agent"
        assert len(cfg.agent.skills) == 1
        assert cfg.agent.skills[0].id == "coding"
        assert cfg.auth.enabled is False
        assert cfg.auth.token == "secret"
        assert cfg.logging_level == "DEBUG"


class TestLoadMissingFileUsesDefaults:
    """Test that a missing config file returns all defaults."""

    def test_nonexistent_path(self):
        cfg = load_config("/no/such/path/config.yaml")
        assert isinstance(cfg, GatewayConfig)
        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 18800
        assert cfg.hermes.api_url == "http://localhost:8642"
        assert cfg.hermes.timeout == 300
        assert cfg.agent.name == "Hermes Agent"
        assert cfg.agent.description == "AI Agent powered by Hermes via A2A v1.0"
        assert cfg.agent.url == "http://localhost:18800"
        assert cfg.agent.skills == []
        assert cfg.auth.enabled is True
        assert cfg.auth.token == ""
        assert cfg.task_store.type == "sqlite"
        assert cfg.task_store.path == "~/.hermes/a2a-gateway/tasks.db"
        assert cfg.logging_level == "INFO"

    def test_none_path(self):
        cfg = load_config(None)
        assert isinstance(cfg, GatewayConfig)
        assert cfg.server.port == 18800


class TestConfigValidation:
    """Test that invalid values raise validation errors."""

    def test_invalid_port_type(self):
        with pytest.raises(Exception):
            ServerConfig(host="0.0.0.0", port="not_a_number")

    def test_invalid_timeout_type(self):
        with pytest.raises(Exception):
            HermesConfig(api_url="http://localhost:8642", timeout="bad")

    def test_missing_required_nested_model(self):
        """GatewayConfig requires server, hermes, agent, auth, task_store."""
        with pytest.raises(Exception):
            GatewayConfig()

    def test_negative_port(self):
        """Pydantic will accept negative int; this just verifies construction works."""
        # Depending on whether we add validators — at minimum construction should not crash
        cfg = ServerConfig(host="0.0.0.0", port=-1)
        assert cfg.port == -1
