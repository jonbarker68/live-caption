import asyncio
import dataclasses
import os
import queue
import threading
import time

from speechmatics.rt import (
    AsyncClient,
    AudioEncoding,
    AudioFormat,
    AuthenticationError,
    Microphone,
    ServerMessageType,
    TranscriptResult,
    TranscriptionConfig,
)

from events import RawASREvent
from recording import EventRecorder


AUDIO_SAMPLE_RATE = 16000
AUDIO_CHUNK_SIZE = 4096
SILENT_MIC_TIMEOUT = 3.0
BYTES_PER_SAMPLE = 2  # PCM_S16LE


def _jsonable(value):
    """Best-effort conversion of an SDK message to JSON-compatible data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))

    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _jsonable(method())
            except Exception:
                pass

    if hasattr(value, "__dict__"):
        try:
            return _jsonable(vars(value))
        except Exception:
            pass

    return repr(value)


class SpeechmaticsWorker(threading.Thread):
    """Capture microphone audio and emit raw Speechmatics response events."""

    def __init__(self, raw_events: queue.Queue, display_events: queue.Queue, record_path=None):
        super().__init__(name="speechmatics-worker", daemon=False)
        self.raw_events = raw_events
        self.display_events = display_events
        self.record_path = record_path
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.display_events.put(("error", str(exc)))
        finally:
            self.raw_events.put(None)

    async def _run(self):
        api_key = os.environ["SPEECHMATICS_API_KEY"]

        audio_format = AudioFormat(
            encoding=AudioEncoding.PCM_S16LE,
            sample_rate=AUDIO_SAMPLE_RATE,
            chunk_size=AUDIO_CHUNK_SIZE,
        )

        config_values = {
            "model": "enhanced",
            "language": "en",
            "max_delay": 0.7,
            "enable_partials": True,
        }
        config = TranscriptionConfig(**config_values)

        recorder = None
        if self.record_path:
            recorder = EventRecorder(
                self.record_path,
                metadata={
                    "source": "speechmatics",
                    "transcription_config": config_values,
                    "audio": {
                        "encoding": "pcm_s16le",
                        "sample_rate": AUDIO_SAMPLE_RATE,
                        "chunk_size": AUDIO_CHUNK_SIZE,
                    },
                },
            )

        mic = Microphone(
            sample_rate=audio_format.sample_rate,
            chunk_size=audio_format.chunk_size,
        )

        if not mic.start():
            if recorder:
                recorder.close()
            raise RuntimeError("Microphone failed to start")

        self.display_events.put(("status", "Connecting…"))

        try:
            async with AsyncClient(api_key=api_key) as client:
                live_time = None

                def emit(kind, msg):
                    result = TranscriptResult.from_message(msg)
                    text = result.metadata.transcript
                    if not text:
                        return

                    event = RawASREvent(
                        t=time.monotonic() - live_time,
                        kind=kind,
                        text=text,
                        payload=_jsonable(msg),
                    )

                    if recorder:
                        recorder.write_event(event)

                    self.raw_events.put(event)

                @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
                def handle_partial(msg):
                    emit("partial", msg)

                @client.on(ServerMessageType.ADD_TRANSCRIPT)
                def handle_final(msg):
                    emit("final", msg)

                await client.start_session(
                    transcription_config=config,
                    audio_format=audio_format,
                )

                live_time = time.monotonic()
                self.display_events.put(("status", "Live"))

                silent_seconds = 0.0

                while not self.stop_requested.is_set():
                    audio = await mic.read(chunk_size=audio_format.chunk_size)

                    if any(audio):
                        silent_seconds = 0.0
                    else:
                        silent_seconds += len(audio) / (
                            BYTES_PER_SAMPLE * audio_format.sample_rate
                        )
                        if silent_seconds >= SILENT_MIC_TIMEOUT:
                            raise RuntimeError(
                                "Microphone is returning digital silence for "
                                f"{SILENT_MIC_TIMEOUT:.0f} seconds. Check "
                                "microphone permission for the terminal app "
                                "and restart it if necessary."
                            )

                    await client.send_audio(audio)

        except AuthenticationError as exc:
            self.display_events.put(("error", f"Authentication failed: {exc}"))

        finally:
            self.display_events.put(("status", "Stopping…"))
            mic.stop()
            if recorder:
                recorder.close()
