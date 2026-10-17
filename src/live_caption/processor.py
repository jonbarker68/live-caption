import re
import threading

from .events import RawASREvent
from .config import resolve_config
from .speakers import caption_from_event, caption_sub, join_captions


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


# Whole words only: do not block innocent substrings (e.g. Scunthorpe,
# assessment). Tags cover other languages/terms; this English fallback also
# handles untagged partials, recordings and ASR mistakes. It is not exhaustive.
DEFAULT_PROFANITIES = frozenset("""
ass asses arse arses arsehole arseholes asshole assholes bastard bastards bitch bitches
bitching bloody bollock bollocks bugger buggers buggery bullshit bullshitting
cock cocks cocksucker cocksuckers crap crappy cunt cunts damn
damned goddamn goddamned hell horseshit dick dicks dickhead dickheads fuck fucks fucked fucking fucker fuckers
motherfucker motherfuckers motherfucking piss pissed pissing prick pricks
shit shits shitty shitting shite shithead shitheads twat twats wank wanker wankers wanking
""".split())


def _normalise_word(word):
    return word.casefold().replace("’", "'")


class CaptionProcessor:
    """Shared, stateless processing before layout for every ASR hypothesis."""

    def __init__(self, config: dict):
        settings = resolve_config(config)["processing"]["profanity"]
        self.mode = settings["mode"]
        self.words = DEFAULT_PROFANITIES | {_normalise_word(w.strip()) for w in settings["additional_words"]}
        self.allowed = {_normalise_word(w.strip()) for w in settings["allow_words"]}

    def process(self, event: RawASREvent):
        if event.kind not in {"partial", "final"}:
            return None
        text = caption_from_event(event)
        if self.mode == "omit" and text:
            words = self.words | {_normalise_word(w) for w in _find_profanities(event.payload)}
            words -= self.allowed
            if words:
                # Longest phrases first. Include possessives and immediately
                # attached punctuation so omitted-only utterances leave no marks.
                terms = "|".join(re.escape(w).replace("'", "['’]") for w in sorted(words, key=len, reverse=True))
                pattern = rf"(?<!\w)(?:{terms})(?:['’]s)?(?!\w)[,;:.!?]*"
                filtered = caption_sub(pattern, "", text, flags=re.IGNORECASE)
                if filtered != text:
                    text = join_captions(" ", filtered.split())
                    text = caption_sub(r"\s+([,;:.!?])", r"\1", text)
                    text = caption_sub(r"[\(\[\{\"'“‘]\s*[\)\]\}\"'”’]", "", text)
                    text = join_captions(" ", text.split())
                    if not any(c.isalnum() for c in text):
                        text = ""
        # Empty updates are meaningful: clear a provisional hypothesis even
        # when the new partial/final consists entirely of omitted words.
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
