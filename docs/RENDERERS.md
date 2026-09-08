Caption layout strategies
=========================

Files to add/replace under src/live_caption:

    overlay.py                 replace existing file
    renderers/__init__.py      new
    renderers/factory.py       new
    renderers/rolling.py       new
    renderers/bbc.py           new
    renderers/base.py          new (interface/documentation)

Config
------

Select the caption-flow strategy with:

    overlay:
      layout: rolling

or:

    overlay:
      layout: bbc

`rolling`
---------

Preserves the previous behaviour: final text plus the current partial text is
shown as a rolling character window. Configure it with:

    max_context_chars: 180

`bbc`
-----

A first BBC-inspired live-caption policy:

- final text is committed and is never revised;
- the current Speechmatics partial hypothesis may change;
- text wraps at a target character width;
- only the most recent N lines remain visible.

Configure it with:

    bbc:
      lines: 2
      line_width: 36

For this layout, left alignment is recommended:

    alignment: left

The BBC renderer is intentionally an approximation. It gives us a clean
strategy boundary so we can now improve phrase breaking, scrolling and
partial-text handling independently of the macOS overlay code.
