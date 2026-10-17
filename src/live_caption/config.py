from copy import deepcopy
from pathlib import Path
import math

import yaml


DEFAULT_CONFIG = {
    "speechmatics": {
        "model": "enhanced",
        "language": "en",
        "max_delay": 0.7,
        "enable_partials": True,
    },
    "processing": {
        "profanity": {
            "mode": "omit",
            "additional_words": [],
            "allow_words": [],
        },
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
        user_config = yaml.safe_load(f)
    if user_config is None:
        user_config = {}

    if not isinstance(user_config, dict):
        raise ValueError("Top-level config must be a YAML mapping")

    return resolve_config(user_config)


def resolve_config(user_config: dict | None = None) -> dict:
    """Canonical caption schema for the overlay, replay and external consumers.

    Preserve unknown/presentation settings so existing layouts remain compatible.
    Legacy filter booleans map to off/omit; replacement characters are retired.
    """
    if user_config is None:
        user_config = {}
    if not isinstance(user_config, dict):
        raise ValueError("Top-level config must be a YAML mapping")
    for section in ("speechmatics", "processing", "overlay", "bbc", "audio"):
        if section in user_config and not isinstance(user_config[section], dict):
            raise ValueError(f"{section} must be a mapping")
    config = _merge(DEFAULT_CONFIG, user_config)
    processing = config["processing"]
    if "profanity_filter" in processing:
        if not isinstance(processing["profanity_filter"], bool):
            raise ValueError("processing.profanity_filter must be true or false")
        if "profanity" not in user_config.get("processing", {}):
            processing["profanity"]["mode"] = "omit" if processing["profanity_filter"] else "off"
    profanity = processing["profanity"]
    if not isinstance(profanity, dict):
        raise ValueError("processing.profanity must be a mapping")
    # PyYAML's YAML 1.1 resolver reads an unquoted `off` as False.
    if profanity["mode"] is False:
        profanity["mode"] = "off"
    if profanity["mode"] not in ("omit", "off"):
        raise ValueError("processing.profanity.mode must be 'omit' or 'off'")
    for key in ("additional_words", "allow_words"):
        words = profanity[key]
        if not isinstance(words, list) or any(not isinstance(w, str) or not w.strip() for w in words):
            raise ValueError(f"processing.profanity.{key} must be a list of non-empty strings")
    overlay = config.get("overlay", {})
    for key in ("width_fraction", "max_width"):
        if key in overlay:
            value = float(overlay[key])
            if not math.isfinite(value) or value <= 0 or (key == "width_fraction" and value > 1):
                raise ValueError(f"overlay.{key} must be positive and finite (width_fraction at most 1)")
    duration = float(overlay.get("scroll_duration_ms", 180))
    if not math.isfinite(duration) or not 0 <= duration <= 1000:
        raise ValueError("overlay.scroll_duration_ms must be between 0 and 1000")
    return config
