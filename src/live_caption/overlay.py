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
    NSView,
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

from renderers import make_renderer


DEFAULT_OVERLAY_CONFIG = {
    "mode": "overlay",
    "layout": "rolling",
    "width_fraction": 0.82,
    "max_width": 1200.0,
    "height": 170.0,
    "bottom_margin": 45.0,
    "position": "bottom",
    "font_family": "system",
    "font_size": 30.0,
    "font_weight": "semibold",
    "font_color": "#ffffff",
    "alignment": "center",
    "background_color": "#000000",
    "background_opacity": 0.76,
    "padding": 18.0,
    "shadow": True,
    "max_context_chars": 180,
}

FONT_WEIGHTS = {
    "regular": 0.0,
    "medium": 0.23,
    "semibold": 0.30,
    "bold": 0.40,
}

ALIGNMENTS = {
    "left": 0,
    "center": 1,
    "right": 2,
}


def _overlay_config(config: dict) -> dict:
    values = dict(DEFAULT_OVERLAY_CONFIG)
    values.update(config.get("overlay", {}))

    mode = str(values["mode"]).lower()
    if mode not in {"overlay", "window"}:
        raise ValueError("overlay.mode must be 'overlay' or 'window'")
    values["mode"] = mode

    position = str(values["position"]).lower()
    if position not in {"bottom", "center", "top"}:
        raise ValueError("overlay.position must be 'bottom', 'center', or 'top'")
    values["position"] = position

    alignment = str(values["alignment"]).lower()
    if alignment not in ALIGNMENTS:
        raise ValueError("overlay.alignment must be 'left', 'center', or 'right'")
    values["alignment"] = alignment

    weight = str(values["font_weight"]).lower()
    if weight not in FONT_WEIGHTS:
        raise ValueError(
            "overlay.font_weight must be one of: "
            + ", ".join(FONT_WEIGHTS)
        )
    values["font_weight"] = weight

    opacity = float(values["background_opacity"])
    if not 0.0 <= opacity <= 1.0:
        raise ValueError("overlay.background_opacity must be between 0 and 1")
    values["background_opacity"] = opacity

    if float(values["width_fraction"]) <= 0:
        raise ValueError("overlay.width_fraction must be greater than zero")
    if float(values["max_width"]) <= 0:
        raise ValueError("overlay.max_width must be greater than zero")
    if float(values["height"]) <= 0:
        raise ValueError("overlay.height must be greater than zero")
    if float(values["padding"]) < 0:
        raise ValueError("overlay.padding cannot be negative")
    if int(values["max_context_chars"]) <= 0:
        raise ValueError("overlay.max_context_chars must be greater than zero")

    return values


def _hex_color(value: str, alpha: float = 1.0):
    value = str(value).strip()
    if value.startswith("#"):
        value = value[1:]

    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)

    if len(value) != 6:
        raise ValueError(f"Expected colour in #RRGGBB form, got {value!r}")

    try:
        red = int(value[0:2], 16) / 255.0
        green = int(value[2:4], 16) / 255.0
        blue = int(value[4:6], 16) / 255.0
    except ValueError as exc:
        raise ValueError(f"Invalid colour value #{value}") from exc

    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        red, green, blue, alpha
    )


def _font(family: str, size: float, weight: str):
    size = float(size)
    if family == "system":
        return NSFont.systemFontOfSize_weight_(size, FONT_WEIGHTS[weight])

    font = NSFont.fontWithName_size_(family, size)
    if font is None:
        raise ValueError(f"Font {family!r} is not installed")
    return font


class AppDelegate(NSObject):
    panel = objc.ivar()
    content = objc.ivar()
    background = objc.ivar()
    label = objc.ivar()
    timer = objc.ivar()
    source_worker = objc.ivar()
    processor_worker = objc.ivar()
    events = objc.ivar()
    config = objc.ivar()
    renderer = objc.ivar()
    shutting_down = objc.ivar(type=objc._C_BOOL)

    def initWithEvents_source_processor_config_(
        self, events, source_worker, processor_worker, config
    ):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None

        self.events = events
        self.source_worker = source_worker
        self.processor_worker = processor_worker
        self.config = _overlay_config(config)
        self.renderer = make_renderer(
            self.config["layout"],
            self.config,
        )
        return self

    def applicationDidFinishLaunching_(self, notification):
        self.shutting_down = False

        self._create_overlay()
        self.processor_worker.start()
        self.source_worker.start()

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.03, self, "processEvents:", None, True
        )

        print(
            f"Live captions running "
            f"(layout={self.config['layout']}). Press Ctrl-C to stop.",
            flush=True,
        )

    def _window_rect(self):
        screen = NSScreen.mainScreen().visibleFrame()

        if self.config["mode"] == "window":
            return NSMakeRect(
                screen.origin.x,
                screen.origin.y,
                screen.size.width,
                screen.size.height,
            )

        width = min(
            float(self.config["max_width"]),
            screen.size.width * float(self.config["width_fraction"]),
        )
        height = min(float(self.config["height"]), screen.size.height)
        x = screen.origin.x + (screen.size.width - width) / 2.0

        position = self.config["position"]
        margin = float(self.config["bottom_margin"])

        if position == "bottom":
            y = screen.origin.y + margin
        elif position == "top":
            y = screen.origin.y + screen.size.height - height - margin
        else:
            y = screen.origin.y + (screen.size.height - height) / 2.0

        return NSMakeRect(x, y, width, height)

    def _create_overlay(self):
        rect = self._window_rect()
        width = rect.size.width
        height = rect.size.height

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )

        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(bool(self.config["shadow"]))
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

        self.content = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )

        self.background = NSTextField.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        self.background.setEditable_(False)
        self.background.setSelectable_(False)
        self.background.setBezeled_(False)
        self.background.setDrawsBackground_(True)
        self.background.setBackgroundColor_(
            _hex_color(
                self.config["background_color"],
                float(self.config["background_opacity"]),
            )
        )
        self.background.setStringValue_("")
        self.background.setAutoresizingMask_(
            NSViewWidthSizable | NSViewHeightSizable
        )

        padding = float(self.config["padding"])
        text_width = max(1.0, width - 2 * padding)
        text_height = max(1.0, height - 2 * padding)

        self.label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(padding, padding, text_width, text_height)
        )
        self.label.setEditable_(False)
        self.label.setSelectable_(False)
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(False)
        self.label.setTextColor_(_hex_color(self.config["font_color"]))
        self.label.setAlignment_(ALIGNMENTS[self.config["alignment"]])
        self.label.setFont_(
            _font(
                str(self.config["font_family"]),
                float(self.config["font_size"]),
                self.config["font_weight"],
            )
        )
        self.label.setStringValue_("Starting…")
        self.label.setAutoresizingMask_(
            NSViewWidthSizable | NSViewHeightSizable
        )

        cell = self.label.cell()
        cell.setWraps_(True)
        cell.setScrollable_(False)
        cell.setUsesSingleLineMode_(False)

        self.content.addSubview_(self.background)
        self.content.addSubview_(self.label)
        self.panel.setContentView_(self.content)
        self.panel.orderFrontRegardless()

    def _show_caption(self, text: str):
        self.label.setStringValue_(text or "Listening…")

    @objc.IBAction
    def processEvents_(self, timer):
        while True:
            try:
                kind, text = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "partial":
                self._show_caption(self.renderer.update_partial(text))

            elif kind == "final":
                self._show_caption(self.renderer.add_final(text))

            elif kind == "status":
                print(text, flush=True)
                if not self.renderer.current_text():
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


def run_overlay(events, source_worker, processor_worker, config):
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().initWithEvents_source_processor_config_(
        events, source_worker, processor_worker, config
    )
    app.setDelegate_(delegate)

    def handle_sigint(signum, frame):
        delegate.requestShutdown()

    signal.signal(signal.SIGINT, handle_sigint)
    app.run()
