"""Configuration loader for Hermes A2A Gateway."""

from __future__ import annotations

from pathlib import Path

import yaml

from hermes_a2a.models import GatewayConfig

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "a2a-gateway" / "config.yaml"


def load_config(path: str | None = None) -> GatewayConfig:
    """Load gateway configuration from a YAML file.

    If *path* is given and the file exists, it is loaded and merged with
    Pydantic defaults (missing keys get their default values).

    If *path* is ``None``, the default location
    ``~/.hermes/a2a-gateway/config.yaml`` is tried.  When the file does not
    exist, a fully-defaulted :class:`GatewayConfig` is returned.

    Parameters
    ----------
    path:
        Explicit path to a YAML config file, or ``None`` to use the default.

    Returns
    -------
    GatewayConfig
        Validated configuration object.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH

    if config_path.is_file():
        text = config_path.read_text()
        data = yaml.safe_load(text) or {}
        # The YAML uses a nested `logging.level` key; map it to the
        # flat `logging_level` field on GatewayConfig.
        if "logging" in data and isinstance(data["logging"], dict):
            data.setdefault("logging_level", data["logging"].get("level", "INFO"))
            del data["logging"]
        return GatewayConfig.model_validate(data)

    return GatewayConfig()
