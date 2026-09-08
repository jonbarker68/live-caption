import queue

from asr import SpeechmaticsWorker
from overlay import run_overlay


def main():
    events: queue.Queue[tuple[str, str]] = queue.Queue()
    worker = SpeechmaticsWorker(events)
    run_overlay(events, worker)


if __name__ == "__main__":
    main()
