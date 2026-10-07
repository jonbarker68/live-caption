import array
import asyncio
import dataclasses
import os
import queue
import threading
import time

import pyaudio
from speechmatics.rt import (
    AsyncClient,
    AudioEncoding,
    AudioFormat,
    AuthenticationError,
    Microphone,
    ServerMessageType,
    TranscriptionConfig,
    TranscriptResult,
)

from .events import RawASREvent
from .recording import EventRecorder

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHUNK_SIZE = 4096
SILENT_MIC_TIMEOUT = 3.0
BYTES_PER_SAMPLE = 2  # PCM_S16LE
MIXED_INPUT_SAMPLE_RATE = 48000
MIXED_INPUT_FRAMES = 3072  # 64 ms at 48 kHz; becomes 1024 samples at 16 kHz


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


def _find_input_device(pyaudio_instance, name):
    """Return the PyAudio input-device index matching *name* exactly."""
    matches = []

    for index in range(pyaudio_instance.get_device_count()):
        info = pyaudio_instance.get_device_info_by_index(index)
        if info.get("maxInputChannels", 0) <= 0:
            continue
        if info.get("name") == name:
            matches.append(index)

    if not matches:
        available = [
            pyaudio_instance.get_device_info_by_index(i)["name"]
            for i in range(pyaudio_instance.get_device_count())
            if pyaudio_instance.get_device_info_by_index(i).get("maxInputChannels", 0)
            > 0
        ]
        raise RuntimeError(
            f"Audio input device {name!r} was not found. "
            f"Available inputs: {', '.join(available)}"
        )

    return matches[0]


def _pcm16_from_bytes(data):
    samples = array.array("h")
    samples.frombytes(data)
    if os.sys.byteorder != "little":
        samples.byteswap()
    return samples


def _pcm16_to_bytes(samples):
    samples = array.array("h", samples)
    if os.sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _downsample_48k_mono_to_16k(samples):
    """Downsample 48-kHz mono PCM16 to 16 kHz using a simple 3-sample box filter."""
    usable = len(samples) - (len(samples) % 3)
    out = array.array("h")

    for i in range(0, usable, 3):
        out.append(int((samples[i] + samples[i + 1] + samples[i + 2]) / 3))

    return out


def _downmix_stereo(samples):
    """Downmix interleaved stereo PCM16 samples to mono PCM16."""
    usable = len(samples) - (len(samples) % 2)
    out = array.array("h")

    for i in range(0, usable, 2):
        out.append(int((samples[i] + samples[i + 1]) / 2))

    return out


def _mix_mono_streams(a, b):
    """Mix two PCM16 mono streams with headroom to avoid clipping."""
    length = min(len(a), len(b))
    out = array.array("h")

    for i in range(length):
        # Averaging preserves ample headroom when both parties speak.
        out.append(int((a[i] + b[i]) / 2))

    return out


class MixedAudioInput:
    """Capture a real microphone plus BlackHole and emit 16-kHz mono PCM."""

    def __init__(self, config):
        audio_config = config.get("audio", {})
        mic_config = audio_config.get("microphone", {})
        system_config = audio_config.get("system_audio", {})

        self.microphone_name = mic_config.get("device", "MacBook Pro Microphone")
        self.system_name = system_config.get("device", "BlackHole 2ch")

        self.pa = None
        self.mic_stream = None
        self.system_stream = None

    def start(self):
        self.pa = pyaudio.PyAudio()

        mic_index = _find_input_device(self.pa, self.microphone_name)
        system_index = _find_input_device(self.pa, self.system_name)

        mic_info = self.pa.get_device_info_by_index(mic_index)
        system_info = self.pa.get_device_info_by_index(system_index)

        if int(mic_info.get("defaultSampleRate", 0)) != MIXED_INPUT_SAMPLE_RATE:
            raise RuntimeError(
                f"{self.microphone_name!r} is not running at "
                f"{MIXED_INPUT_SAMPLE_RATE} Hz."
            )
        if int(system_info.get("defaultSampleRate", 0)) != MIXED_INPUT_SAMPLE_RATE:
            raise RuntimeError(
                f"{self.system_name!r} is not running at {MIXED_INPUT_SAMPLE_RATE} Hz."
            )
        if system_info.get("maxInputChannels", 0) < 2:
            raise RuntimeError(
                f"{self.system_name!r} must provide at least two input channels."
            )

        try:
            self.mic_stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=MIXED_INPUT_SAMPLE_RATE,
                input=True,
                input_device_index=mic_index,
                frames_per_buffer=MIXED_INPUT_FRAMES,
            )

            self.system_stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=2,
                rate=MIXED_INPUT_SAMPLE_RATE,
                input=True,
                input_device_index=system_index,
                frames_per_buffer=MIXED_INPUT_FRAMES,
            )
        except Exception:
            self.stop()
            raise

        return True

    async def read(self, chunk_size=None):
        # Both input streams are filling concurrently. Reading the microphone
        # blocks until the next block is ready; BlackHole should then have the
        # corresponding block buffered and ready immediately afterwards.
        mic_data = await asyncio.to_thread(
            self.mic_stream.read,
            MIXED_INPUT_FRAMES,
            exception_on_overflow=False,
        )
        system_data = await asyncio.to_thread(
            self.system_stream.read,
            MIXED_INPUT_FRAMES,
            exception_on_overflow=False,
        )

        mic_48k = _pcm16_from_bytes(mic_data)
        system_stereo_48k = _pcm16_from_bytes(system_data)
        system_48k = _downmix_stereo(system_stereo_48k)

        mic_16k = _downsample_48k_mono_to_16k(mic_48k)
        system_16k = _downsample_48k_mono_to_16k(system_48k)

        mixed = _mix_mono_streams(mic_16k, system_16k)
        return _pcm16_to_bytes(mixed)

    def stop(self):
        for stream_name in ("mic_stream", "system_stream"):
            stream = getattr(self, stream_name, None)
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                setattr(self, stream_name, None)

        if self.pa is not None:
            try:
                self.pa.terminate()
            finally:
                self.pa = None


class SpeechmaticsWorker(threading.Thread):
    """Capture microphone audio and emit raw Speechmatics response events."""

    def __init__(
        self,
        raw_events: queue.Queue,
        display_events: queue.Queue,
        config: dict,
        record_path=None,
    ):
        super().__init__(name="speechmatics-worker", daemon=False)
        self.raw_events = raw_events
        self.display_events = display_events
        self.config = config
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
        config_values = dict(self.config["speechmatics"])
        api_key = config_values.pop("api_key", None) or os.environ.get(
            "SPEECHMATICS_API_KEY"
        )
        if not api_key:
            raise RuntimeError(
                "No Speechmatics API key configured. Set speechmatics.api_key "
                "in the local config file or SPEECHMATICS_API_KEY in the "
                "environment."
            )

        audio_format = AudioFormat(
            encoding=AudioEncoding.PCM_S16LE,
            sample_rate=AUDIO_SAMPLE_RATE,
            chunk_size=AUDIO_CHUNK_SIZE,
        )

        transcription_config = TranscriptionConfig(**config_values)

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
                        "mode": self.config.get("audio", {}).get("mode", "microphone"),
                    },
                },
            )

        audio_config = self.config.get("audio", {})
        audio_mode = str(audio_config.get("mode", "microphone")).lower()

        if audio_mode == "microphone":
            audio_source = Microphone(
                sample_rate=audio_format.sample_rate,
                chunk_size=audio_format.chunk_size,
            )
        elif audio_mode == "mixed":
            audio_source = MixedAudioInput(self.config)
        else:
            if recorder:
                recorder.close()
            raise RuntimeError("audio.mode must be either 'microphone' or 'mixed'")

        if not audio_source.start():
            if recorder:
                recorder.close()
            raise RuntimeError("Audio input failed to start")

        if audio_mode == "mixed":
            self.display_events.put(("status", "Connecting… mic + system audio"))
        else:
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
                    transcription_config=transcription_config,
                    audio_format=audio_format,
                )

                live_time = time.monotonic()
                self.display_events.put(("status", "Live"))

                silent_seconds = 0.0

                while not self.stop_requested.is_set():
                    audio = await audio_source.read(chunk_size=audio_format.chunk_size)

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
                                "microphone permission for Live Caption "
                                "and restart the app if necessary."
                            )

                    await client.send_audio(audio)

        except AuthenticationError as exc:
            self.display_events.put(("error", f"Authentication failed: {exc}"))

        finally:
            self.display_events.put(("status", "Stopping…"))
            audio_source.stop()
            if recorder:
                recorder.close()
