# Notes

If necessary, kill the native overlay test process with the following command:

```bash
pkill -f native_overlay_test.py
```

Run with the following command:

```bash
python3 -m live_caption.live_caption --config ~/.config/live-caption/config.yaml
```

or

```bash
uv run live-caption --config ~/.config/live-caption/config.yaml
``` 

## Test the new live caption layout

`bbc_live` is an opt-in renderer. The existing `bbc` and `rolling` modes retain their behaviour. Add `--layout bbc_live` to your usual launch command, for example:

```sh
uv run live-caption --config ~/.config/live-caption/config.yaml --layout bbc_live
```

Test the layout without a microphone or transcription connection using the supplied synthetic recording:

```sh
uv run live-caption --config ./config.yaml --layout bbc_live --replay tests/fixtures/caption-layout.jsonl
```

The recording contains growing partials, a recognition correction, a pause and final speech. The overlay remains open after replay; use the CC menu to quit. To compare the original layout, rerun with `--layout bbc`. CLI selection affects only that run. To make the new layout persistent, use the following in your local YAML (retain your existing speech/audio settings):

```yaml
overlay:
  layout: bbc_live
  alignment: left
  font_size: 36
bbc:
  lines: 2
```

### What changes

- Native AppKit font measurements and the actual caption-region width determine line breaks. Text is wrapped once; the native label does not wrap it again or shrink its font. `bbc.line_width` is only a fallback for callers that do not supply measurements.
- The renderer retains completed final lines and the current final line, rather than rewrapping an entire transcript. Only the most recent configured number of lines are displayed. New final events must contain only newly committed speech, as Speechmatics events do.
- Partials replace the current uncommitted hypothesis. Corrections can reflow provisional lines or cause the viewport to move backwards; finalised line breaks remain stable. The renderer does not invent word timing or add a reading queue. A large incoming event can still scroll several lines at once.
- Unusually long tokens are split to fit, rather than causing automatic font reduction.
- The native panel reserves enough height for the configured number of lines at the chosen font size. It defaults to left alignment in the new mode; an explicit `overlay.alignment` still takes precedence.
- Stop/Start creates a fresh renderer. Shared callers can use `reset()` to discard old text or `set_layout()` to change measurements and reset. No automatic caption expiry or speaker identification is added.

Font sizes here are native macOS points; they are not PowerPoint-equivalent fullscreen slide sizes. Adjust `overlay.font_size` and the caption width together, then check from the intended viewing distance. Screen or font changes require restarting the native overlay to recalculate its measurements.

### Shared renderer interface

```python
from live_caption.renderers import TextLayout, make_renderer

# Width and measure_text must use the same units and actual display font.
renderer = make_renderer(
    "bbc_live", config,
    layout=TextLayout(width=available_width, measure=measure_text),
)
text = renderer.update_partial("provisional speech")
text = renderer.add_final("confirmed speech")
renderer.reset()
```

Measurement callbacks run on the caller's thread; call the renderer from the UI thread when using native font APIs. `set_layout(new_layout)` clears history deliberately so old speech is not replayed following a display change. Without a `TextLayout`, wrapping uses `bbc.line_width` characters (default 36).

Cadenza has not been migrated: it still requests `bbc`, and continues using the original renderer. Switching it to `bbc_live` later will also require supplying its Qt font measurements and disabling its second wrapping pass.

### Checks

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Checks cover partial corrections, finalisation, measured widths, long words, scrolling, bounded history, resets, legacy compatibility, replay, and native macOS font/height integration. Native checks create an offscreen panel without starting microphone or network workers.

### Smooth upward scrolling (`bbc_live`)

New-line transitions animate upward by one line over 180 ms by default. Word additions and recognition corrections update immediately. To adjust or disable animation, add this to your existing overlay settings and restart:

```yaml
overlay:
  layout: bbc_live
  scroll_duration_ms: 180  # 0 disables animation; accepted range 0–1000 ms
```

The outgoing line leaves through the clipped top edge as the incoming line enters from below. Updates arriving during that transition refresh the incoming text without restarting the animation. Corrections without clear line continuity and multi-line bursts snap directly to the latest text; animations are never queued, so they do not accumulate caption delay. The option has no effect on the original `bbc` or `rolling` modes.

The shared renderer exposes `scroll_lines` alongside its unchanged string-returning methods: `1` means a one-line forward shift with retained text; `0` means an ordinary update, correction, or burst. Consumers that only use returned text continue working. Cadenza has no animation changes.
