import asyncio
import os
import queue
import signal
import threading

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindowCollectionBehaviorCanJoinAllApplications,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSTimer

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


API_KEY = os.environ["SPEECHMATICS_API_KEY"]

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHUNK_SIZE = 4096

MAX_CONTEXT_CHARS = 180
OVERLAY_WIDTH_FRACTION = 0.82
OVERLAY_MAX_WIDTH = 1200.0
OVERLAY_HEIGHT = 170.0
OVERLAY_BOTTOM_MARGIN = 45.0

# Messages passed safely from the Speechmatics worker thread to AppKit.
events: queue.Queue[tuple[str, str]] = queue.Queue()


class SpeechmaticsWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="speechmatics-worker", daemon=False)
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def run(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            events.put(("error", str(exc)))
        finally:
            events.put(("worker_stopped", ""))

    async def _run(self):
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

        events.put(("status", "Connecting…"))

        try:
            async with AsyncClient(api_key=API_KEY) as client:

                @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
                def handle_partial(msg):
                    result = TranscriptResult.from_message(msg)
                    text = result.metadata.transcript
                    if text:
                        events.put(("partial", text))

                @client.on(ServerMessageType.ADD_TRANSCRIPT)
                def handle_final(msg):
                    result = TranscriptResult.from_message(msg)
                    text = result.metadata.transcript
                    if text:
                        events.put(("final", text))

                await client.start_session(
                    transcription_config=config,
                    audio_format=audio_format,
                )

                events.put(("status", "Live"))

                while not self.stop_requested.is_set():
                    audio = await mic.read(
                        chunk_size=audio_format.chunk_size
                    )
                    await client.send_audio(audio)

        except AuthenticationError as exc:
            events.put(("error", f"Authentication failed: {exc}"))

        finally:
            events.put(("status", "Stopping…"))
            mic.stop()


class AppDelegate(NSObject):
    panel = objc.ivar()
    label = objc.ivar()
    timer = objc.ivar()
    worker = objc.ivar()
    final_text = objc.ivar()
    partial_text = objc.ivar()
    shutting_down = objc.ivar(type=objc._C_BOOL)

    def applicationDidFinishLaunching_(self, notification):
        self.final_text = ""
        self.partial_text = ""
        self.shutting_down = False

        self._create_overlay()

        self.worker = SpeechmaticsWorker()
        self.worker.start()

        # Poll the worker -> GUI queue frequently enough that partial
        # transcripts appear effectively immediately.
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.03,
            self,
            "processEvents:",
            None,
            True,
        )

        print("Live captions running. Press Ctrl-C to stop.", flush=True)

    def _create_overlay(self):
        screen = NSScreen.mainScreen().visibleFrame()
        width = min(OVERLAY_MAX_WIDTH, screen.size.width * OVERLAY_WIDTH_FRACTION)
        height = OVERLAY_HEIGHT
        x = screen.origin.x + (screen.size.width - width) / 2.0
        y = screen.origin.y + OVERLAY_BOTTOM_MARGIN

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )

        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setFloatingPanel_(True)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setLevel_(NSScreenSaverWindowLevel)

        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorCanJoinAllApplications
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self.panel.setCollectionBehavior_(behavior)

        self.label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        self.label.setEditable_(False)
        self.label.setSelectable_(False)
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(True)
        self.label.setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.76)
        )
        self.label.setTextColor_(NSColor.whiteColor())
        self.label.setAlignment_(1)  # centred
        self.label.setFont_(NSFont.systemFontOfSize_weight_(30.0, 0.5))
        self.label.setStringValue_("Connecting to Speechmatics…")
        self.label.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        cell = self.label.cell()
        cell.setWraps_(True)
        cell.setScrollable_(False)
        cell.setUsesSingleLineMode_(False)

        self.panel.setContentView_(self.label)
        self.panel.orderFrontRegardless()

    def _render_caption(self):
        text = f"{self.final_text} {self.partial_text}".strip()

        if len(text) > MAX_CONTEXT_CHARS:
            text = text[-MAX_CONTEXT_CHARS:]
            first_space = text.find(" ")
            if first_space != -1:
                text = text[first_space + 1 :]
            text = "… " + text

        self.label.setStringValue_(text or "Listening…")

    @objc.IBAction
    def processEvents_(self, timer):
        while True:
            try:
                kind, text = events.get_nowait()
            except queue.Empty:
                break

            if kind == "partial":
                self.partial_text = text
                self._render_caption()

            elif kind == "final":
                self.final_text = f"{self.final_text} {text}".strip()
                self.partial_text = ""
                self._render_caption()

            elif kind == "status":
                print(text, flush=True)
                if not self.final_text and not self.partial_text:
                    self.label.setStringValue_(text)

            elif kind == "error":
                print(f"Speechmatics error: {text}", flush=True)
                self.label.setStringValue_(f"Speechmatics error: {text}")

            elif kind == "worker_stopped":
                if self.shutting_down:
                    NSApplication.sharedApplication().terminate_(None)

        # If shutdown has been requested, terminate once the worker has had
        # enough time to close the mic and leave AsyncClient cleanly.
        if self.shutting_down and not self.worker.is_alive():
            NSApplication.sharedApplication().terminate_(None)

    def requestShutdown(self):
        if self.shutting_down:
            return

        self.shutting_down = True
        self.label.setStringValue_("Stopping…")
        print("Stopping live captions…", flush=True)
        self.worker.stop()

    def applicationWillTerminate_(self, notification):
        if self.timer is not None:
            self.timer.invalidate()

        if self.worker is not None and self.worker.is_alive():
            self.worker.stop()
            self.worker.join(timeout=3.0)

        if self.panel is not None:
            self.panel.orderOut_(None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    def handle_sigint(signum, frame):
        delegate.requestShutdown()

    signal.signal(signal.SIGINT, handle_sigint)

    app.run()


if __name__ == "__main__":
    main()
