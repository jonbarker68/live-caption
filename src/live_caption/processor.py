import threading

from events import RawASREvent


class CaptionProcessor:
    """Convert raw ASR events into display events.

    For now this is intentionally identity-like. Profanity masking, speaker
    colouring, confidence styling, etc. belong here later.
    """

    def process(self, event: RawASREvent):
        if event.kind in {"partial", "final"} and event.text:
            return (event.kind, event.text)
        return None


class ProcessorWorker(threading.Thread):
    def __init__(self, raw_events, display_events, processor=None):
        super().__init__(name="caption-processor", daemon=False)
        self.raw_events = raw_events
        self.display_events = display_events
        self.processor = processor or CaptionProcessor()

    def run(self):
        try:
            while True:
                event = self.raw_events.get()
                if event is None:
                    break

                output = self.processor.process(event)
                if output is not None:
                    self.display_events.put(output)

        except Exception as exc:
            self.display_events.put(("error", f"Caption processing failed: {exc}"))
        finally:
            self.display_events.put(("pipeline_stopped", ""))
