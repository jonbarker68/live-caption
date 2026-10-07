import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .events import RawASREvent


class EventRecorder:
    """Append raw ASR events to a JSON Lines recording."""

    def __init__(self, path, metadata=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

        self._write(
            {
                "type": "session",
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            }
        )

    def _write(self, record: dict):
        with self._lock:
            json.dump(record, self._file, ensure_ascii=False)
            self._file.write("\n")
            self._file.flush()

    def write_event(self, event: RawASREvent):
        self._write(event.to_record())

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()


class ReplayWorker(threading.Thread):
    """Replay a recording using the original ASR response timings."""

    def __init__(self, path, raw_events, display_events, speed=1.0):
        super().__init__(name="replay-worker", daemon=False)
        self.path = Path(path)
        self.raw_events = raw_events
        self.display_events = display_events
        self.speed = speed
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            self.display_events.put(("status", "Replay"))
            previous_t = 0.0

            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    if self.stop_requested.is_set():
                        break

                    record = json.loads(line)
                    if record.get("type") != "asr":
                        continue

                    event = RawASREvent.from_record(record)
                    delay = max(0.0, event.t - previous_t) / self.speed

                    if self.stop_requested.wait(delay):
                        break

                    self.raw_events.put(event)
                    previous_t = event.t

        except Exception as exc:
            self.display_events.put(("error", f"Replay failed: {exc}"))
        finally:
            self.raw_events.put(None)
