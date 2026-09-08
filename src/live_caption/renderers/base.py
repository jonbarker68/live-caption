from abc import ABC, abstractmethod


class CaptionRenderer(ABC):
    @abstractmethod
    def update_partial(self, text: str) -> str:
        """Update the current partial hypothesis and return display text."""
        raise NotImplementedError

    @abstractmethod
    def add_final(self, text: str) -> str:
        """Commit final text and return display text."""
        raise NotImplementedError

    @abstractmethod
    def current_text(self) -> str:
        """Return the current display text."""
        raise NotImplementedError
