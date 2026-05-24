"""Shared loader for apps_config.toml, used by the per-app cooks (desktop,
chromium_flags, settings). Not a cook itself — just data access."""

import tomllib

from harness import SRC_DIR

APPS_CONFIG_TOML = SRC_DIR / "apps_config.toml"


def load() -> dict:
    with APPS_CONFIG_TOML.open("rb") as f:
        return tomllib.load(f)


def apps_with(config: dict, *markers: str) -> dict[str, dict]:
    """App sections (dict-valued, carrying at least one of `markers`). Filters
    out the shared scalar tables like [chromium] / [env]."""
    return {
        name: cfg
        for name, cfg in config.items()
        if isinstance(cfg, dict) and any(m in cfg for m in markers)
    }
