import argparse
import queue
import yaml
from pathlib import Path

from .asr import SpeechmaticsWorker
from .config import load_config
from .overlay import run_overlay
from .processor import ProcessorWorker
from .recording import ReplayWorker


def _default_config_path():
    candidates = [
        Path.home() / ".config" / "live-caption" / "config.yaml",
        Path.cwd() / "config.yaml",
        Path(__file__).resolve().parents[2] / "config.yaml",
    ]

    for path in candidates:
        if path.exists():
            return str(path)

    # Give load_config() a useful path to mention if none exists.
    return str(candidates[0])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Live caption overlay")

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--record",
        metavar="FILE",
        help="record raw Speechmatics response events to JSONL while captioning",
    )
    source.add_argument(
        "--replay",
        metavar="FILE",
        help="replay a previously recorded JSONL session",
    )

    parser.add_argument(
        "--config",
        default=None,
        help=(
            "local YAML configuration file "
            "(default: ~/.config/live-caption/config.yaml, "
            "falling back to ./config.yaml)"
        ),
    )
    parser.add_argument(
        "--vocabulary", "--vocabulary-file", metavar="FILE",
        help="YAML vocabulary file; overrides vocabulary_file in config (relative to current directory)",
    )
    parser.add_argument("--diarization", choices=("none", "speaker"),
                        help="Speaker estimation at session startup (default: config, otherwise none)")
    parser.add_argument("--speaker-colours", choices=("white", "speakers"),
                        help="Caption display policy (default: config, otherwise white)")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="replay speed multiplier (default: 1.0)",
    )

    parser.add_argument("--layout", choices=("rolling", "bbc", "bbc_live"),
                        help="override overlay.layout for this run")

    args = parser.parse_args(argv)

    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if not args.replay and args.speed != 1.0:
        parser.error("--speed is only meaningful with --replay")

    return args


def main():
    args = parse_args()
    config_path = args.config or _default_config_path()
    try:
        config = load_config(config_path, vocabulary_file=args.vocabulary,
                             diarization=args.diarization, speaker_colours=args.speaker_colours)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise SystemExit(f"Invalid caption configuration: {exc}") from exc
    config["speaker_available"] = bool(args.replay) or config["speechmatics"]["diarization"] in ("speaker", "channel_and_speaker")
    if args.layout:
        config.setdefault("overlay", {})["layout"] = args.layout

    def make_session():
        raw_events = queue.Queue()
        display_events = queue.Queue()

        processor = ProcessorWorker(raw_events, display_events, config)

        if args.replay:
            source = ReplayWorker(
                args.replay,
                raw_events,
                display_events,
                speed=args.speed,
            )
        else:
            source = SpeechmaticsWorker(
                raw_events,
                display_events,
                config,
                record_path=args.record,
            )

        return display_events, source, processor

    run_overlay(make_session, config)


if __name__ == "__main__":
    main()
