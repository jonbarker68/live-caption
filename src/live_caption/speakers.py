"""Speaker metadata and styling shared by all caption front ends."""
import hashlib
import html
import re

DEFAULT_SPEAKERS = {
    "mode": "white",
    "colours": ["#FFFFFF", "#FFE066", "#77DDF5", "#FFB3D9", "#A9E88B"],
    "unknown_colour": "#FFFFFF",
}


class CaptionText(str):
    """A backwards-compatible string with a speaker label for each character."""
    def __new__(cls, text="", owners=None):
        obj = super().__new__(cls, text)
        obj.owners = tuple(owners if owners is not None else
                           getattr(text, "owners", [None] * len(text)))
        if len(obj.owners) != len(obj):
            raise ValueError("Speaker metadata must match caption text")
        return obj

    def __getitem__(self, key):
        text = super().__getitem__(key)
        owners = self.owners[key]
        return CaptionText(text, owners if isinstance(key, slice) else [owners])

    def __add__(self, other):
        other = CaptionText(other)
        return CaptionText(str(self) + str(other), self.owners + other.owners)

    def __radd__(self, other):
        return CaptionText(other) + self

    def strip(self, chars=None):
        start = len(self) - len(str(self).lstrip(chars))
        end = len(str(self).rstrip(chars))
        return self[start:max(start, end)]

    def split(self, sep=None, maxsplit=-1):
        if sep is None:
            parts = []
            for match in re.finditer(r"\S+", self):
                if maxsplit >= 0 and len(parts) == maxsplit:
                    parts.append(self[match.start():])
                    break
                parts.append(self[match.start():match.end()])
            return parts
        if not sep:
            raise ValueError("empty separator")
        parts, start = [], 0
        while maxsplit < 0 or len(parts) < maxsplit:
            index = self.find(sep, start)
            if index < 0:
                break
            parts.append(self[start:index])
            start = index + len(sep)
        return parts + [self[start:]]

    def runs(self):
        result = []
        for index, owner in enumerate(self.owners):
            if result and result[-1]["speaker"] == owner:
                result[-1]["text"] += str(self[index])
            else:
                result.append({"text": str(self[index]), "speaker": owner})
        return result

    @classmethod
    def from_runs(cls, text, runs):
        if not isinstance(text, str) or not isinstance(runs, list):
            raise ValueError("Invalid caption runs")
        pieces = []
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get("text"), str):
                raise ValueError("Invalid caption run")
            speaker = run.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                raise ValueError("Invalid speaker label")
            pieces.append(cls(run["text"], [speaker] * len(run["text"])))
        result = join_captions("", pieces)
        if result != text:
            raise ValueError("Caption runs do not match text")
        return result


def join_captions(separator, pieces):
    pieces = list(pieces)
    return CaptionText(str(separator).join(map(str, pieces)),
        tuple(owner for index, piece in enumerate(pieces)
              for owner in (([None] * len(separator) if index else []) + list(CaptionText(piece).owners))))


def caption_sub(pattern, replacement, text, *, flags=0):
    """Regex substitution that retains ownership of all surviving characters."""
    text = CaptionText(text)
    pieces, start = [], 0
    for match in re.finditer(pattern, text, flags):
        pieces.append(text[start:match.start()])
        pieces.append(text[match.start(1):match.end(1)] if replacement == r"\1" else CaptionText(replacement))
        start = match.end()
    pieces.append(text[start:])
    return join_captions("", pieces)


def caption_from_event(event):
    owners = [None] * len(event.text)
    # Final labels are used for attribution. Partials remain neutral so a
    # tentative speaker assignment cannot flash another person's colour.
    if event.kind == "final" and isinstance(event.payload, dict):
        cursor = 0
        previous = None
        for result in event.payload.get("results", []) or []:
            if not isinstance(result, dict):
                continue
            alternatives = result.get("alternatives") or []
            if not alternatives or not isinstance(alternatives[0], dict):
                continue
            alternative = alternatives[0]
            content = alternative.get("content")
            if not isinstance(content, str) or not content:
                continue
            match = re.search(re.escape(content), event.text[cursor:], re.IGNORECASE)
            if match is None:
                continue
            start, end = cursor + match.start(), cursor + match.end()
            speaker = alternative.get("speaker")
            if not isinstance(speaker, str) or speaker == "":
                speaker = previous if result.get("type") == "punctuation" else None
            elif speaker == "UU":
                speaker = None
            owners[start:end] = [speaker] * (end - start)
            cursor, previous = end, speaker
    return CaptionText(event.text, owners)


class SpeakerStyle:
    def __init__(self, settings=None):
        self.settings = dict(DEFAULT_SPEAKERS, **(settings or {}))
        if self.settings["mode"] not in ("white", "speakers"):
            raise ValueError("speakers.mode must be 'white' or 'speakers'")
        colours = self.settings["colours"]
        if not isinstance(colours, list) or not colours:
            raise ValueError("speakers.colours must be a non-empty list of #RRGGBB colours")
        for colour in [*colours, self.settings["unknown_colour"]]:
            if not isinstance(colour, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
                raise ValueError("Speaker colours must use #RRGGBB format")

    def colour(self, speaker):
        if not speaker or speaker == "UU":
            return self.settings["unknown_colour"]
        match = re.fullmatch(r"S([1-9][0-9]*)", speaker)
        index = int(match[1]) - 1 if match else int.from_bytes(hashlib.sha256(speaker.encode()).digest()[:4], "big")
        return self.settings["colours"][index % len(self.settings["colours"])]

    def html_lines(self, text):
        return ["".join(f'<font color="{self.colour(run["speaker"])}">{html.escape(run["text"])}</font>'
                        for run in line.runs())
                for line in CaptionText(text).split("\n")]
