from copy import deepcopy
from pathlib import Path
import math

import yaml

from .vocabulary import load_vocabulary, validate_vocabulary
from .renderers.live import pause_threshold_seconds
from .speakers import DEFAULT_SPEAKERS, SpeakerStyle


DEFAULT_CONFIG = {
    "speechmatics": {
        "model": "enhanced",
        "diarization": "none",
        "language": "en",
        "max_delay": 0.7,
        "enable_partials": True,
    },
    "speakers": DEFAULT_SPEAKERS,
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


def load_config(path, *, vocabulary_file=None, diarization=None, speaker_colours=None) -> dict:
    """Resolve shared config and vocabulary, with an optional CLI file override.

    Configured vocabulary paths are relative to this YAML file. A CLI override
    is relative to the current working directory. No implicit file is loaded.
    """
    path = Path(path).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f)
    if user_config is None:
        user_config = {}

    if not isinstance(user_config, dict):
        raise ValueError("Top-level config must be a YAML mapping")

    if diarization is not None:
        user_config.setdefault("speechmatics", {})["diarization"] = diarization
    if speaker_colours is not None:
        user_config.setdefault("speakers", {})["mode"] = speaker_colours
    config = resolve_config(user_config)
    selected = vocabulary_file if vocabulary_file is not None else config.get("vocabulary_file")
    if selected is not None:
        if not isinstance(selected, (str, Path)) or not str(selected).strip():
            raise ValueError("vocabulary_file must be a non-empty path or null")
        selected = Path(selected).expanduser()
        if vocabulary_file is None and not selected.is_absolute():
            selected = path.parent / selected
        selected = selected.resolve()
        entries = load_vocabulary(selected)
        config["speechmatics"]["additional_vocab"] = validate_vocabulary(
            config["speechmatics"].get("additional_vocab", []) + entries,
            source="Combined speechmatics.additional_vocab and vocabulary file",
        )
        config["vocabulary_file"] = str(selected)
    return config


def resolve_config(user_config: dict | None = None) -> dict:
    """Canonical caption schema for the overlay, replay and external consumers.

    Preserve unknown/presentation settings so existing layouts remain compatible.
    Legacy filter booleans map to off/omit; replacement characters are retired.
    """
    if user_config is None:
        user_config = {}
    if not isinstance(user_config, dict):
        raise ValueError("Top-level config must be a YAML mapping")
    for section in ("speechmatics", "processing", "overlay", "bbc", "audio", "speakers"):
        if section in user_config and not isinstance(user_config[section], dict):
            raise ValueError(f"{section} must be a mapping")
    config = _merge(DEFAULT_CONFIG, user_config)
    SpeakerStyle(config["speakers"])
    if config["speechmatics"]["diarization"] not in ("none", "speaker", "channel", "channel_and_speaker"):
        raise ValueError("speechmatics.diarization must be none, speaker, channel or channel_and_speaker")
    diarization = config["speechmatics"].get("speaker_diarization_config", {})
    if not isinstance(diarization, dict):
        raise ValueError("speechmatics.speaker_diarization_config must be a mapping")
    if "max_speakers" in diarization:
        value = diarization["max_speakers"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise ValueError("speaker_diarization_config.max_speakers must be an integer >= 2")
    if "prefer_current_speaker" in diarization and not isinstance(diarization["prefer_current_speaker"], bool):
        raise ValueError("speaker_diarization_config.prefer_current_speaker must be true or false")
    if "speaker_sensitivity" in diarization:
        value = diarization["speaker_sensitivity"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("speaker_diarization_config.speaker_sensitivity must be between 0 and 1")
    if "additional_vocab" in config["speechmatics"]:
        config["speechmatics"]["additional_vocab"] = validate_vocabulary(
            config["speechmatics"]["additional_vocab"]
        )
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
    pause_threshold_seconds(config.get("bbc", {}))
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
