import argparse
import queue

from asr import SpeechmaticsWorker
from config import load_config
from overlay import run_overlay
from processor import ProcessorWorker
from recording import ReplayWorker


def parse_args():
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
        default="config.yaml",
        help="local YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="replay speed multiplier (default: 1.0)",
    )

    args = parser.parse_args()

    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if not args.replay and args.speed != 1.0:
        parser.error("--speed is only meaningful with --replay")

    return args


def main():
    args = parse_args()
    config = load_config(args.config)

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

    run_overlay(display_events, source, processor, config)


if __name__ == "__main__":
    main()
