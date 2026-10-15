class RollingRenderer:
    def __init__(self, config: dict):
        self.max_context_chars = int(config.get("max_context_chars", 180))
        self.final_text = ""
        self.partial_text = ""

    def reset(self):
        """Discard all committed and provisional caption text."""
        self.final_text = ""
        self.partial_text = ""

    def update_partial(self, text: str) -> str:
        self.partial_text = text
        return self.current_text()

    def add_final(self, text: str) -> str:
        self.final_text = f"{self.final_text} {text}".strip()
        self.partial_text = ""
        return self.current_text()

    def current_text(self) -> str:
        text = f"{self.final_text} {self.partial_text}".strip()

        if len(text) > self.max_context_chars:
            text = text[-self.max_context_chars:]
            first_space = text.find(" ")
            if first_space != -1:
                text = text[first_space + 1:]
            text = "… " + text

        return text
