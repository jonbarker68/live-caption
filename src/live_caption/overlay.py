import math
import queue
import signal
import time

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSLayoutManager,
    NSLineBreakByClipping,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
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
from Foundation import NSObject, NSTimer, NSString

from .renderers import make_renderer, TextLayout

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
    "scroll_duration_ms": 180,
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
    # Renderer settings live beside overlay in the full configuration.
    values["bbc"] = dict(config.get("bbc", {}))
    if values["layout"] == "bbc_live" and "alignment" not in config.get("overlay", {}):
        values["alignment"] = "left"

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
            "overlay.font_weight must be one of: " + ", ".join(FONT_WEIGHTS)
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

    duration = values["scroll_duration_ms"]
    if isinstance(duration, bool):
        raise ValueError("overlay.scroll_duration_ms must be a number from 0 to 1000")
    duration = float(duration)
    if not math.isfinite(duration) or not 0 <= duration <= 1000:
        raise ValueError("overlay.scroll_duration_ms must be a number from 0 to 1000")
    values["scroll_duration_ms"] = duration
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

    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, alpha)


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
    status_item = objc.ivar()
    toggle_item = objc.ivar()

    session_factory = objc.ivar()
    source_worker = objc.ivar()
    processor_worker = objc.ivar()
    events = objc.ivar()
    config = objc.ivar()
    renderer = objc.ivar()
    scroll_view = objc.ivar()
    scroll_state = objc.ivar()
    last_caption = objc.ivar()
    line_height = objc.ivar(type=objc._C_DBL)

    captioning = objc.ivar(type=objc._C_BOOL)
    stopping = objc.ivar(type=objc._C_BOOL)
    quitting = objc.ivar(type=objc._C_BOOL)
    hide_on_stop = objc.ivar(type=objc._C_BOOL)
    settle_until = objc.ivar(type=objc._C_DBL)
    quit_deadline = objc.ivar(type=objc._C_DBL)

    def initWithSessionFactory_config_(self, session_factory, config):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None

        self.session_factory = session_factory
        self.config = _overlay_config(config)
        self.source_worker = None
        self.processor_worker = None
        self.events = None
        self.renderer = None
        self.scroll_view = None
        self.scroll_state = None
        self.last_caption = ''
        self.line_height = 0.0
        self.captioning = False
        self.stopping = False
        self.quitting = False
        self.hide_on_stop = True
        self.settle_until = 0.0
        self.quit_deadline = 0.0
        return self

    def applicationDidFinishLaunching_(self, notification):
        self._create_overlay()
        self._create_status_menu()

        self.timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 60.0, self, "processEvents:", None, True
            )
        )

        # Starting the app means starting captioning.
        self.start_captioning()

    def _create_status_menu(self):
        status_bar = NSStatusBar.systemStatusBar()
        self.status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        self.status_item.button().setTitle_("CC")

        menu = NSMenu.alloc().init()

        self.toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Stop Captioning", "toggleCaptioning:", ""
        )
        self.toggle_item.setTarget_(self)
        menu.addItem_(self.toggle_item)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Live Caption", "quitApplication:", "q"
        )
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)

        self.status_item.setMenu_(menu)

    def _set_toggle_state(self):
        if self.stopping:
            self.toggle_item.setTitle_("Stopping…")
            self.toggle_item.setEnabled_(False)
        elif self.captioning:
            self.toggle_item.setTitle_("Stop Captioning")
            self.toggle_item.setEnabled_(True)
        else:
            self.toggle_item.setTitle_("Start Captioning")
            self.toggle_item.setEnabled_(True)

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
        if self.config["layout"] == "bbc_live":
            font = _font(str(self.config["font_family"]), self.config["font_size"], self.config["font_weight"])
            line_height = NSLayoutManager.alloc().init().defaultLineHeightForFont_(font)
            needed = line_height * int(self.config["bbc"].get("lines", 2)) + 2 * float(self.config["padding"])
            if needed > screen.size.height:
                raise ValueError("Caption lines do not fit on this screen at the configured font size")
            height = max(height, needed)
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

        self.content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

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
        self.background.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

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
        self.label.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        cell = self.label.cell()
        cell.setWraps_(self.config["layout"] != "bbc_live")
        if self.config["layout"] == "bbc_live":
            cell.setLineBreakMode_(NSLineBreakByClipping)
            self.label.setMaximumNumberOfLines_(int(self.config["bbc"].get("lines", 2)))
        cell.setScrollable_(False)
        cell.setUsesSingleLineMode_(False)

        self.content.addSubview_(self.background)
        if self.config["layout"] == "bbc_live":
            self.line_height = NSLayoutManager.alloc().init().defaultLineHeightForFont_(self.label.font())
            viewport_height = self.line_height * int(self.config["bbc"].get("lines", 2)) + 4
            self.scroll_view = NSView.alloc().initWithFrame_(
                NSMakeRect(padding, padding + text_height - viewport_height, text_width, viewport_height))
            self.scroll_view.setWantsLayer_(True)
            self.scroll_view.layer().setMasksToBounds_(True)
            self.label.setFrame_(NSMakeRect(0, 0, text_width, viewport_height))
            self.scroll_view.addSubview_(self.label)
            self.content.addSubview_(self.scroll_view)
        else:
            self.content.addSubview_(self.label)
        self.panel.setContentView_(self.content)

    @objc.python_method
    def _show_caption(self, text: str, scroll_lines=0):
        text = text or "Listening…"
        old_lines = self.last_caption.splitlines()
        new_lines = text.splitlines()
        duration = self.config["scroll_duration_ms"] / 1000.0
        if self.scroll_state is not None:
            # Words arriving during a scroll update its incoming lines in
            # place. A second scroll/correction snaps to the latest state,
            # rather than queueing animations and falling behind speech.
            state = self.scroll_state
            if not scroll_lines and new_lines and state['target'].splitlines()[0] == new_lines[0]:
                state['target'] = text
                self.label.setStringValue_(state['outgoing'] + '\n' + text)
                self.last_caption = text
                return
            self._finish_scroll()
            self.label.setStringValue_(text)
            self.last_caption = text
            return
        if self.scroll_view is not None and duration and scroll_lines == 1 and old_lines:
            self.scroll_state = dict(start=time.monotonic(), duration=duration,
                                     outgoing=old_lines[0], target=text)
            self.label.setMaximumNumberOfLines_(int(self.config['bbc'].get('lines', 2)) + 1)
            self.label.setStringValue_(old_lines[0] + '\n' + text)
            self.last_caption = text
            self._advance_scroll(self.scroll_state['start'])
        else:
            self.label.setStringValue_(text)
            self.last_caption = text

    @objc.python_method
    def _advance_scroll(self, now):
        if self.scroll_state is None:
            return
        state = self.scroll_state
        progress = min(1.0, max(0.0, (now - state['start']) / state['duration']))
        if progress >= 1.0:
            self._finish_scroll()
            return
        # Smoothstep eases both ends while keeping the transition brief.
        eased = progress * progress * (3 - 2 * progress)
        bounds = self.scroll_view.bounds()
        self.label.setFrame_(NSMakeRect(0, -self.line_height * (1 - eased),
                                       bounds.size.width, bounds.size.height + self.line_height))

    def _finish_scroll(self):
        if self.scroll_state is None:
            return
        text = self.scroll_state['target']
        self.scroll_state = None
        self.label.setFrame_(self.scroll_view.bounds())
        self.label.setMaximumNumberOfLines_(int(self.config['bbc'].get('lines', 2)))
        self.label.setStringValue_(text)

    def _create_renderer(self):
        layout = None
        if self.config["layout"] == "bbc_live":
            font = self.label.font()
            def measure(text):
                return NSString.stringWithString_(text).sizeWithAttributes_({NSFontAttributeName: font}).width
            # NSTextFieldCell reserves a small inset beyond the label frame.
            layout = TextLayout(max(1, self.label.bounds().size.width - 8), measure)
        return make_renderer(
            self.config["layout"],
            self.config,
            layout=layout,
        )

    def start_captioning(self):
        if self.captioning or self.stopping or self.quitting:
            return

        self._finish_scroll()
        self.last_caption = ''
        self.renderer = self._create_renderer()
        self.events, self.source_worker, self.processor_worker = self.session_factory()

        self.captioning = True
        self.stopping = False
        self.hide_on_stop = True
        self.settle_until = 0.0
        self.label.setStringValue_("Starting…")
        self.panel.orderFrontRegardless()
        self._set_toggle_state()

        self.processor_worker.start()
        self.source_worker.start()

        print(
            f"Live captions running (layout={self.config['layout']}).",
            flush=True,
        )

    def stop_captioning(self, hide_overlay=True):
        self._finish_scroll()
        if not self.captioning:
            return

        if self.stopping:
            if hide_overlay:
                self.hide_on_stop = True
                self.panel.orderOut_(None)
            return

        self.stopping = True
        self.hide_on_stop = hide_overlay
        self.settle_until = 0.0
        self._set_toggle_state()

        if hide_overlay:
            self.panel.orderOut_(None)

        print("Stopping live captions…", flush=True)
        if self.source_worker is not None:
            self.source_worker.stop()

    @objc.IBAction
    def toggleCaptioning_(self, sender):
        if self.captioning:
            self.stop_captioning(hide_overlay=True)
        else:
            self.start_captioning()

    @objc.IBAction
    def quitApplication_(self, sender):
        self.requestShutdown()

    @objc.IBAction
    def processEvents_(self, timer):
        # Drain only the event queue owned by the current pipeline. A new
        # session is never created until these workers have fully exited.
        if self.events is not None:
            while True:
                try:
                    kind, text = self.events.get_nowait()
                except queue.Empty:
                    break

                if kind == "partial":
                    if not self.stopping:
                        self._show_caption(self.renderer.update_partial(text), getattr(self.renderer, "scroll_lines", 0))

                elif kind == "final":
                    if not self.stopping:
                        self._show_caption(self.renderer.add_final(text), getattr(self.renderer, "scroll_lines", 0))

                elif kind == "status":
                    print(text, flush=True)
                    if (
                        not self.stopping
                        and self.renderer is not None
                        and not self.renderer.current_text()
                    ):
                        self.label.setStringValue_(text)

                elif kind == "error":
                    print(f"Live caption error: {text}", flush=True)
                    self._finish_scroll()
                    self.label.setStringValue_(f"Live caption error: {text}")
                    self.stop_captioning(hide_overlay=False)

                elif kind == "pipeline_stopped":
                    # Do not clear worker references here. This only tells us
                    # the processor consumed the source sentinel; the source
                    # thread itself may still be unwinding CoreAudio/PyAudio.
                    pass

        now = time.monotonic()
        self._advance_scroll(now)

        if self.stopping:
            source_done = (
                self.source_worker is None or not self.source_worker.is_alive()
            )
            processor_done = (
                self.processor_worker is None or not self.processor_worker.is_alive()
            )

            if source_done and processor_done:
                if self.settle_until == 0.0:
                    # Give the microphone backend a brief chance to release
                    # the device before Start Captioning is enabled again.
                    self.settle_until = now + 0.25

                if now >= self.settle_until:
                    self.source_worker = None
                    self.processor_worker = None
                    self.events = None
                    self.renderer = None
                    self.captioning = False
                    self.stopping = False
                    self.settle_until = 0.0

                    if self.quitting:
                        NSApplication.sharedApplication().terminate_(None)
                        return

                    self._set_toggle_state()

        # A stuck external library must never make the menu-bar app
        # impossible to quit. After three seconds, terminate regardless.
        if self.quitting and self.quit_deadline and now >= self.quit_deadline:
            print(
                "Caption worker did not stop promptly; forcing application quit.",
                flush=True,
            )
            NSApplication.sharedApplication().terminate_(None)

    def requestShutdown(self):
        if self.quitting:
            return

        self.quitting = True
        self.quit_deadline = time.monotonic() + 3.0

        if self.captioning:
            self.stop_captioning(hide_overlay=True)
        else:
            NSApplication.sharedApplication().terminate_(None)

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


def run_overlay(session_factory, config):
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().initWithSessionFactory_config_(
        session_factory, config
    )
    app.setDelegate_(delegate)

    def handle_sigint(signum, frame):
        delegate.requestShutdown()

    signal.signal(signal.SIGINT, handle_sigint)
    app.run()
