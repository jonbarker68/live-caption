import asyncio
import os
import queue
import threading

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


AUDIO_SAMPLE_RATE = 16000
AUDIO_CHUNK_SIZE = 4096
SILENT_MIC_TIMEOUT = 3.0
BYTES_PER_SAMPLE = 2  # PCM_S16LE


class SpeechmaticsWorker(threading.Thread):
    """Run Speechmatics real-time transcription in a worker thread.

    Results and status changes are emitted onto ``events`` as ``(kind, text)``
    tuples. The worker has no knowledge of the GUI or caption rendering.
    """

    def __init__(self, events: queue.Queue[tuple[str, str]]):
        super().__init__(name="speechmatics-worker", daemon=False)
        self.events = events
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("worker_stopped", ""))

    async def _run(self):
        api_key = os.environ["SPEECHMATICS_API_KEY"]

        audio_format = AudioFormat(
            encoding=AudioEncoding.PCM_S16LE,
            sample_rate=AUDIO_SAMPLE_RATE,
            chunk_size=AUDIO_CHUNK_SIZE,
        )

        config = TranscriptionConfig(
            model="enhanced",
            language="en",
            max_delay=0.7,
            enable_partials=True,
        )

        mic = Microphone(
            sample_rate=audio_format.sample_rate,
            chunk_size=audio_format.chunk_size,
        )

        if not mic.start():
            raise RuntimeError("Microphone failed to start")

        self.events.put(("status", "Connecting…"))

        try:
            async with AsyncClient(api_key=api_key) as client:

                @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
                def handle_partial(msg):
                    result = TranscriptResult.from_message(msg)
                    text = result.metadata.transcript
                    if text:
                        self.events.put(("partial", text))

                @client.on(ServerMessageType.ADD_TRANSCRIPT)
                def handle_final(msg):
                    result = TranscriptResult.from_message(msg)
                    text = result.metadata.transcript
                    if text:
                        self.events.put(("final", text))

                await client.start_session(
                    transcription_config=config,
                    audio_format=audio_format,
                )

                self.events.put(("status", "Live"))

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
                                f"{SILENT_MIC_TIMEOUT:.0f} seconds. Check macOS "
                                "microphone permission for the terminal app and "
                                "restart it if necessary."
                            )

                    await client.send_audio(audio)

        except AuthenticationError as exc:
            self.events.put(("error", f"Authentication failed: {exc}"))

        finally:
            self.events.put(("status", "Stopping…"))
            mic.stop()
