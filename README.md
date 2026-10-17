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

Cadenza uses the shared renderer with its own Qt font measurements.

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

The shared renderer exposes `scroll_lines` alongside its unchanged string-returning methods: `1` means a one-line forward shift with retained text; `0` means an ordinary update, correction, or burst. Consumers that only use returned text continue working. Cadenza uses the same scroll signal with its own presentation animation.

### Full-stop-aware line breaks

The shared `bbc_live` renderer starts the next sentence on a new line when a confirmed full stop ends a line that is at least 50% of the available text width. The threshold uses the display's font measurements (or character width for callers without measurements). Short sentences can still share a line. This applies within a final event and across subsequent events. A sentence ending alone does not create a blank line: the break happens when more words arrive.

Provisional punctuation inside partial recognition results does not trigger these breaks until confirmed. Partials following an already confirmed sentence ending respect that boundary. Only full stops trigger the rule; commas, question marks and exclamation marks do not. Common titles, initials, initialisms and ellipses are excluded using a conservative heuristic, not a complete linguistic sentence detector. Full stops before closing quotes or brackets are recognised.

Cadenza imports this shared renderer, so restarting Cadenza (in its default `bbc_live` mode) picks up the new behaviour automatically. No Cadenza code or configuration changes are needed. Original `bbc` and `rolling` modes retain their old wrapping.

## Shared caption configuration and profanity handling

Both apps use `live_caption.config.load_config` / `resolve_config` and
`live_caption.processor.CaptionProcessor` from the live-caption checkout.
Speechmatics and replay events are processed before text measurement/wrapping.
Cadenza applies this in its bridge for both `--raw` (processed hypotheses) and
legacy rendered output; the presentation does not implement a second filter.
Use the same YAML file in both apps to share audio, Speechmatics, processing,
`overlay`, and `bbc` settings. Unknown/existing settings are preserved.

```yaml
processing:
  profanity:
    mode: omit
    additional_words: []
    allow_words: []
```

`omit` is the default when no profanity settings are supplied. It removes whole
words tagged as profanity by Speechmatics, plus a built-in English list including
common inflections even when tags are absent (e.g. a spurious ASR hypothesis).
Matching is case-insensitive and respects word boundaries, so `assessment` and
`Scunthorpe` are retained. `additional_words` adds whole words or phrases;
`allow_words` permits exact terms, including tagged terms. The English list lives
in `live_caption/processor.py` as `DEFAULT_PROFANITIES`; it is not an exhaustive
multilingual or obfuscated-profanity detector. Partial word fragments that do not
yet match a complete term are not guessed or delayed.

Set `mode: off` to show ASR output verbatim. The old
`processing.profanity_filter: false` remains an explicit opt-out; `true` now
means omission. An explicit `processing.profanity` mapping takes precedence over
the old flag. `profanity_replacement` is accepted for compatibility but ignored;
there is no masking/bleeping mode. Remove the legacy false flag to adopt the new
default, or explicitly select `mode: omit`.

Each partial replaces the preceding provisional hypothesis; every final commits
only its newly recognised clean text. Empty/fully omitted updates still clear
provisional text while retaining earlier committed speech. Omission removes
attached punctuation and empty quote/bracket pairs and closes whitespace gaps.
Filtering precedes all three renderers (`bbc_live`, `bbc`, `rolling`), so omitted
words consume no layout width. Font, alignment, line count, line width, measured
wrapping and animation settings keep their existing meanings. Cadenza retains
its presentation font and CLI layout override.

Raw `--record` JSONL files intentionally retain the original ASR text/payload,
including any profanity; replay applies the current processing settings. The
filter changes captions, not audio or recognition accuracy. Restart the app
after changing settings.

See `config.example.yaml` for a credential-free example. To share one local file:

```sh
uv run live-caption --config ~/.config/live-caption/config.yaml
```

## Shared ASR vocabulary file

The shared loader in `live_caption.vocabulary` accepts a UTF-8 YAML list:

```yaml
- Cadenza
- content: Stoller Hall
- content: Camerata
# Optional pronunciation hints (Speechmatics' example):
- content: gnocchi
  sounds_like: [nyohki, nokey]
```

Use `content` for the spelling to display. Omit `sounds_like` to let Speechmatics
infer pronunciation. An empty file or `[]` supplies no extra words.

Set the top-level `vocabulary_file` in your existing caption configuration:

```yaml
vocabulary_file: /Users/jon/projects/live-caption/vocabulary.yaml
```

Config paths resolve relative to the containing config file; `~` is expanded.
A CLI `--vocabulary FILE` (alias `--vocabulary-file`) overrides this path and
resolves relative to the launch directory. No file is loaded implicitly: omit
the setting or set it to `null` to use only inline vocabulary/default ASR.
Both apps use the same live-caption loader, validator and Speechmatics worker.
Cadenza passes the CLI override through to its caption subprocess.

Existing `speechmatics.additional_vocab` entries are combined with the file's
entries (inline first), with exact duplicates removed. Different pronunciation
entries are preserved. Files are read at application startup; restart after
editing. The list is sent as `additional_vocab` when live recognition starts;
it does not rewrite existing transcripts or affect replayed recognition results,
caption layout or the profanity filter. Replay still validates configured files.

Missing/malformed files, invalid entries and unsupported entry keys produce a
startup error with the source filename/entry. Entries and pronunciation hints
must contain at most six words, each at most 4000 characters; the combined list
cannot exceed 20,000 entries. Speechmatics recommends no more than 1,000 entries
for best performance. Large dictionaries can increase session startup time;
reuse of an identical dictionary benefits from server-side caching. See the
[Speechmatics custom dictionary documentation](https://docs.speechmatics.com/speech-to-text/features/custom-dictionary).

`vocabulary.yaml` contains a small starter list. For example:

```sh
uv run live-caption --config ./config.yaml --vocabulary ./vocabulary.yaml
```
