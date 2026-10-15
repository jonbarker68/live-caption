"""Incremental live captions, independent of any particular UI framework."""
from collections import deque
from dataclasses import dataclass
import math
import re
from typing import Callable


@dataclass(frozen=True)
class TextLayout:
    """Available text width and measurement callback, in the same units.

    The callback must measure the full string in the actual presentation font.
    It is called on the renderer's thread (the UI thread in the native overlay).
    """
    width: float
    measure: Callable[[str], float] = len

    def __post_init__(self):
        if not math.isfinite(self.width) or self.width <= 0:
            raise ValueError('TextLayout.width must be positive and finite')
        if not callable(self.measure):
            raise ValueError('TextLayout.measure must be callable')

    def fits(self, text):
        size = self.measure(text)
        if not math.isfinite(size) or size < 0:
            raise ValueError('Text measurement must be non-negative and finite')
        return size <= self.width


class LiveRenderer:
    """Keep final line breaks stable; allow the current partial to be revised.

    Final events append *new* committed speech; partials replace the complete
    uncommitted hypothesis. Partial previews never modify committed history.
    Only the last ``lines`` lines are displayed. Older committed lines are
    discarded, so memory does not grow with the duration of the session.
    """
    def __init__(self, config, *, layout=None):
        settings = config.get('bbc', {})
        self.lines = int(settings.get('lines', 2))
        if self.lines <= 0:
            raise ValueError('bbc.lines must be greater than zero')
        self.layout = layout or TextLayout(float(settings.get('line_width', 36)))
        self.reset()

    def reset(self):
        """Discard all displayed, committed and provisional text."""
        self._closed = deque(maxlen=max(0, self.lines - 1))
        self._open = ''
        self.partial_text = ''
        self._has_partial = False
        self._committed_breaks = 0
        self._display_top = 0
        self._last_display = ''
        self.scroll_lines = 0

    def set_layout(self, layout):
        """Replace font/width measurements and reset; old speech is not replayed."""
        self.layout = layout
        self.reset()

    def _sentence_end(self, text):
        # Deliberately conservative: only full stops, including before a
        # closing quote/bracket. Avoid common titles, initials and ellipses.
        token = text.split()[-1].rstrip('"\'”’)]}')
        if not token.endswith('.') or token.endswith('..'):
            return False
        if token.lower() in {'mr.', 'mrs.', 'ms.', 'dr.', 'prof.', 'sr.', 'jr.',
                             'st.', 'vs.', 'e.g.', 'i.e.'}:
            return False
        if re.fullmatch(r'(?:[A-Za-z]\.)+', token):
            return False
        return True

    def _append(self, closed, current, text, *, final=False):
        breaks = 0
        for index, word in enumerate(text.split()):
            # The prefix is committed. Punctuation added within a partial
            # is provisional and must not trigger an editorial break yet.
            # Wait for the following word so a completed sentence alone
            # does not create an empty line or premature scroll.
            if (current and (final or index == 0) and self._sentence_end(current)
                    and self.layout.measure(current) >= self.layout.width * 0.5):
                closed.append(current)
                breaks += 1
                current = ''
            candidate = f'{current} {word}' if current else word
            if self.layout.fits(candidate):
                current = candidate
                continue
            if current:
                closed.append(current)
                breaks += 1
                current = ''
            if self.layout.fits(word):
                current = word
                continue
            # Extremely long tokens must also fit at the fixed font size.
            # Split only when a whole token cannot fit on an empty line.
            for char in word:
                if not self.layout.fits(char):
                    raise ValueError('Caption region is too narrow for a character')
                if not self.layout.fits(current + char):
                    closed.append(current)
                    breaks += 1
                    current = char
                else:
                    current += char
        return current, breaks

    def update_partial(self, text):
        closed = deque(self._closed, maxlen=self.lines)
        tail, added = self._append(closed, self._open, text)
        self.partial_text = self._visible(closed, tail)
        self._publish(self.partial_text, self._committed_breaks + added, bool(tail))
        self._has_partial = True
        return self.current_text()

    def _visible(self, closed, tail):
        return '\n'.join([*closed, *([tail] if tail else [])][-self.lines:])

    def add_final(self, text):
        self._open, added = self._append(self._closed, self._open, text, final=True)
        self._committed_breaks += added
        self.partial_text = ''
        self._has_partial = False
        self._publish(self.current_text(), self._committed_breaks, bool(self._open))
        return self.current_text()

    def _publish(self, text, breaks, has_tail):
        # Signal only a one-line forward shift with visual continuity. A
        # correction, a final replacing its partial, or a multi-line burst
        # must not start another upward animation.
        top = max(0, breaks + int(has_tail) - self.lines)
        old, new = self._last_display.splitlines(), text.splitlines()
        retained = bool(old and new and (new[0] == old[-1] or new[0].startswith(old[-1] + ' ')))
        self.scroll_lines = int(self.lines > 1 and len(old) == self.lines
                                and top == self._display_top + 1 and retained)
        self._display_top = top
        self._last_display = text

    def current_text(self):
        if self._has_partial:
            return self.partial_text
        return self._visible(self._closed, self._open)
