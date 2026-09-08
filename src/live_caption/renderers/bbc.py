import textwrap


class BBCRenderer:
    """
    Simple BBC-inspired live caption layout.

    Behaviour:
    - final text is committed and never revised;
    - the current partial hypothesis may change;
    - captions are wrapped into a fixed-width, fixed-line viewport;
    - the most recent lines are shown.

    This is intentionally a first approximation rather than an attempt
    to reproduce the BBC's production subtitling system exactly.
    """

    def __init__(self, config: dict):
        bbc = config.get("bbc", {})
        self.lines = int(bbc.get("lines", 2))
        self.line_width = int(bbc.get("line_width", 36))

        if self.lines <= 0:
            raise ValueError("overlay.bbc.lines must be greater than zero")
        if self.line_width <= 0:
            raise ValueError("overlay.bbc.line_width must be greater than zero")

        self.final_text = ""
        self.partial_text = ""

    def update_partial(self, text: str) -> str:
        self.partial_text = text
        return self.current_text()

    def add_final(self, text: str) -> str:
        self.final_text = f"{self.final_text} {text}".strip()
        self.partial_text = ""
        return self.current_text()

    def _wrapped_lines(self, text: str) -> list[str]:
        if not text:
            return []

        return textwrap.wrap(
            text,
            width=self.line_width,
            break_long_words=False,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        )

    def current_text(self) -> str:
        combined = f"{self.final_text} {self.partial_text}".strip()
        wrapped = self._wrapped_lines(combined)

        if not wrapped:
            return ""

        return "\n".join(wrapped[-self.lines:])
