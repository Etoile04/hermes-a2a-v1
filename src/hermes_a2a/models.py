"""Pydantic v2 models for Hermes A2A Gateway configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """HTTP server configuration."""

    host: str = "0.0.0.0"
    port: int = 18800


class HermesConfig(BaseModel):
    """Hermes Agent API connection settings."""

    api_url: str = "http://localhost:8642"
    api_key: str = ""
    timeout: int = 300


class AgentSkillConfig(BaseModel):
    """A single skill advertised by the agent."""

    id: str = "general"
    name: str = "General"
    description: str = ""


class AgentProviderConfig(BaseModel):
    """Agent provider information."""

    organization: str = "Hermes"
    url: str = "https://github.com/Etoile04/hermes-a2a-v1"


class AgentConfig(BaseModel):
    """Agent identity and capabilities."""

    name: str = "Hermes Agent"
    description: str = "AI Agent powered by Hermes via A2A v1.0"
    url: str = "http://localhost:18800"
    documentation_url: str = "https://github.com/Etoile04/hermes-a2a-v1/blob/main/README.md"
    provider: AgentProviderConfig = Field(default_factory=AgentProviderConfig)
    skills: list[AgentSkillConfig] = Field(default_factory=list)


class AuthConfig(BaseModel):
    """Authentication configuration."""

    enabled: bool = True
    token: str = ""
    admin_token: str = ""  # Falls back to token when empty


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    enabled: bool = True
    requests_per_minute: int = 60
    burst_size: int = 10


class CORSConfig(BaseModel):
    """CORS configuration."""

    origins: list[str] = Field(default_factory=lambda: ["*"])


class TaskStoreConfig(BaseModel):
    """Task persistence configuration."""

    type: str = "sqlite"
    path: str = "~/.hermes/a2a-gateway/tasks.db"


class PeerConfig(BaseModel):
    """Configuration for a remote A2A peer agent."""

    name: str
    agent_card_url: str
    auth_token: str = ""
    enabled: bool = True


class GatewayConfig(BaseModel):
    """Top-level gateway configuration."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    hermes: HermesConfig = Field(default_factory=HermesConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    task_store: TaskStoreConfig = Field(default_factory=TaskStoreConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    logging_level: str = "INFO"
    peers: list[PeerConfig] = Field(default_factory=list)
