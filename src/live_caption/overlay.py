import queue
import signal

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


MAX_CONTEXT_CHARS = 180
OVERLAY_WIDTH_FRACTION = 0.82
OVERLAY_MAX_WIDTH = 1200.0
OVERLAY_HEIGHT = 170.0
OVERLAY_BOTTOM_MARGIN = 45.0


class AppDelegate(NSObject):
    panel = objc.ivar()
    label = objc.ivar()
    timer = objc.ivar()
    source_worker = objc.ivar()
    processor_worker = objc.ivar()
    events = objc.ivar()
    final_text = objc.ivar()
    partial_text = objc.ivar()
    shutting_down = objc.ivar(type=objc._C_BOOL)

    def initWithEvents_source_processor_(self, events, source_worker, processor_worker):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.events = events
        self.source_worker = source_worker
        self.processor_worker = processor_worker
        return self

    def applicationDidFinishLaunching_(self, notification):
        self.final_text = ""
        self.partial_text = ""
        self.shutting_down = False

        self._create_overlay()
        self.processor_worker.start()
        self.source_worker.start()

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.03, self, "processEvents:", None, True
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
            NSMakeRect(x, y, width, height), style, NSBackingStoreBuffered, False
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

        self.label = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        self.label.setEditable_(False)
        self.label.setSelectable_(False)
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(True)
        self.label.setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.76)
        )
        self.label.setTextColor_(NSColor.whiteColor())
        self.label.setAlignment_(1)
        self.label.setFont_(NSFont.systemFontOfSize_weight_(30.0, 0.5))
        self.label.setStringValue_("Starting…")
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
                text = text[first_space + 1:]
            text = "… " + text

        self.label.setStringValue_(text or "Listening…")

    @objc.IBAction
    def processEvents_(self, timer):
        while True:
            try:
                kind, text = self.events.get_nowait()
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
                print(f"Live caption error: {text}", flush=True)
                self.label.setStringValue_(f"Live caption error: {text}")
                self.shutting_down = True
                self.source_worker.stop()

            elif kind == "pipeline_stopped":
                NSApplication.sharedApplication().terminate_(None)

    def requestShutdown(self):
        if self.shutting_down:
            return

        self.shutting_down = True
        self.label.setStringValue_("Stopping…")
        print("Stopping live captions…", flush=True)
        self.source_worker.stop()

    def applicationWillTerminate_(self, notification):
        if self.timer is not None:
            self.timer.invalidate()

        if self.source_worker is not None and self.source_worker.is_alive():
            self.source_worker.stop()
            self.source_worker.join(timeout=3.0)

        if self.processor_worker is not None and self.processor_worker.is_alive():
            self.processor_worker.join(timeout=3.0)

        if self.panel is not None:
            self.panel.orderOut_(None)


def run_overlay(events, source_worker, processor_worker):
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().initWithEvents_source_processor_(
        events, source_worker, processor_worker
    )
    app.setDelegate_(delegate)

    def handle_sigint(signum, frame):
        delegate.requestShutdown()

    signal.signal(signal.SIGINT, handle_sigint)
    app.run()
