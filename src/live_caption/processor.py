import re
import threading

from .events import RawASREvent


def _find_profanities(value):
    """Yield word contents whose Speechmatics alternative is tagged profanity."""
    if isinstance(value, dict):
        alternatives = value.get("alternatives")
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if not isinstance(alternative, dict):
                    continue
                tags = alternative.get("tags") or []
                content = alternative.get("content")
                if content and "profanity" in tags:
                    yield str(content)

        for child in value.values():
            yield from _find_profanities(child)

    elif isinstance(value, list):
        for child in value:
            yield from _find_profanities(child)


def _mask_profanity(text: str, payload, replacement: str) -> str:
    """Mask words explicitly tagged as profanity by Speechmatics."""
    profanities = list(dict.fromkeys(_find_profanities(payload)))

    for word in profanities:
        mask = replacement * max(1, len(word))
        pattern = rf"(?<!\w){re.escape(word)}(?!\w)"
        text = re.sub(pattern, mask, text, flags=re.IGNORECASE)

    return text


class CaptionProcessor:
    """Convert raw ASR events into display events."""

    def __init__(self, config: dict):
        processing = config["processing"]
        self.profanity_filter = processing["profanity_filter"]
        self.profanity_replacement = processing["profanity_replacement"]

    def process(self, event: RawASREvent):
        if event.kind not in {"partial", "final"} or not event.text:
            return None

        text = event.text

        if self.profanity_filter:
            text = _mask_profanity(
                text,
                event.payload,
                self.profanity_replacement,
            )

        return (event.kind, text)


class ProcessorWorker(threading.Thread):
    def __init__(self, raw_events, display_events, config):
        super().__init__(name="caption-processor", daemon=False)
        self.raw_events = raw_events
        self.display_events = display_events
        self.processor = CaptionProcessor(config)

    def run(self):
        try:
            while True:
                event = self.raw_events.get()
                if event is None:
                    break

                output = self.processor.process(event)
                if output is not None:
                    self.display_events.put(output)

        except Exception as exc:
            self.display_events.put(("error", f"Caption processing failed: {exc}"))
        finally:
            self.display_events.put(("pipeline_stopped", ""))
