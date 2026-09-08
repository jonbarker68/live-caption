from copy import deepcopy
from pathlib import Path

import yaml


DEFAULT_CONFIG = {
    "speechmatics": {
        "model": "enhanced",
        "language": "en",
        "max_delay": 0.7,
        "enable_partials": True,
    },
    "processing": {
        "profanity_filter": False,
        "profanity_replacement": "*",
    },
}


def _merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path) -> dict:
    """Load YAML config, applying defaults for omitted settings."""
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    if not isinstance(user_config, dict):
        raise ValueError("Top-level config must be a YAML mapping")

    config = _merge(DEFAULT_CONFIG, user_config)

    if not isinstance(config["processing"]["profanity_filter"], bool):
        raise ValueError("processing.profanity_filter must be true or false")

    replacement = config["processing"]["profanity_replacement"]
    if not isinstance(replacement, str) or not replacement:
        raise ValueError("processing.profanity_replacement must be a non-empty string")

    return config
