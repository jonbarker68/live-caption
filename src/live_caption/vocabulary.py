"""Shared Speechmatics custom-dictionary parsing and validation."""
from pathlib import Path

import yaml


def validate_vocabulary(value, *, source="speechmatics.additional_vocab"):
    """Return canonical object entries, preserving spelling and pronunciation.

    Match Speechmatics' dictionary limits locally rather than silently dropping
    entries at recognition startup. Exact duplicates are removed in order.
    """
    if not isinstance(value, list):
        raise ValueError(f"{source} must be a YAML list")
    result = []
    seen = set()

    def phrase(text, label):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{label} must be a non-empty string")
        if len(text.split()) > 6 or any(len(word) > 4000 for word in text.split()):
            raise ValueError(f"{label} must contain at most 6 words, each at most 4000 characters")
        return text.strip()

    for index, item in enumerate(value):
        label = f"{source}[{index + 1}]"
        if isinstance(item, str):
            item = {"content": item}
        if not isinstance(item, dict) or set(item) - {"content", "sounds_like"}:
            raise ValueError(f"{label} must be a word or mapping with content and optional sounds_like")
        entry = {"content": phrase(item.get("content"), f"{label}.content")}
        if "sounds_like" in item:
            hints = item["sounds_like"]
            if not isinstance(hints, list) or not hints:
                raise ValueError(f"{label}.sounds_like must be a non-empty list of strings")
            entry["sounds_like"] = [phrase(hint, f"{label}.sounds_like") for hint in hints]
        key = (entry["content"], tuple(entry.get("sounds_like", [])))
        if key not in seen:
            seen.add(key)
            result.append(entry)
    if len(result) > 20000:
        raise ValueError(f"{source} must contain at most 20000 entries")
    return result


def load_vocabulary(path):
    """Read a UTF-8 YAML list; an empty file means an empty dictionary."""
    path = Path(path).expanduser()
    try:
        with path.open(encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot load vocabulary file {path}: {exc}") from exc
    return validate_vocabulary([] if value is None else value, source=f"Vocabulary file {path}")
