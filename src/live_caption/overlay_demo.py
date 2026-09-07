import platform
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


DEMO_CAPTIONS = [
    "This is a standalone caption overlay test.",
    "The text changes automatically, so Speechmatics is not being used.",
    "Now switch to another application and check whether this remains visible.",
    "Try a normal window first, then put PowerPoint or a PDF viewer full screen.",
    "If you can still read this, the overlay behaviour is working.",
]


class CaptionOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self._caption_index = 0

        self.setWindowTitle("Live Caption Overlay Test")

        # Deliberately do NOT use Qt.Tool here. On macOS, tool windows can be
        # hidden when their application becomes inactive.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.caption = QLabel(DEMO_CAPTIONS[0])
        self.caption.setWordWrap(True)
        self.caption.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )

        font = QFont()
        font.setPointSize(30)
        font.setWeight(QFont.Weight.DemiBold)
        self.caption.setFont(font)

        self.caption.setStyleSheet(
            """
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 190);
                border-radius: 14px;
                padding: 18px 28px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.caption)

        screen = QApplication.primaryScreen().availableGeometry()
        width = min(1200, int(screen.width() * 0.82))
        height = 170
        x = screen.x() + (screen.width() - width) // 2
        y = screen.y() + screen.height() - height - 45
        self.setGeometry(x, y, width, height)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_caption)
        self.timer.start(2500)

    def next_caption(self):
        self._caption_index = (self._caption_index + 1) % len(DEMO_CAPTIONS)
        self.caption.setText(DEMO_CAPTIONS[self._caption_index])

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


def configure_macos_window(overlay):
    if platform.system() != "Darwin":
        return

    try:
        from AppKit import (
            NSFloatingWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )
    except ImportError:
        print(
            "For macOS full-screen support install: "
            "uv add pyobjc-framework-Cocoa",
            file=sys.stderr,
        )
        return

    # winId() forces Qt to create the native NSWindow.
    overlay.winId()

    # PySide exposes the underlying NSView pointer via winId(), but the most
    # robust route here is to find the native window by title after show().
    from AppKit import NSApp

    target = None
    for window in NSApp.windows():
        try:
            if str(window.title()) == "Live Caption Overlay Test":
                target = window
                break
        except Exception:
            continue

    if target is None:
        print("Could not locate the native macOS overlay window.", file=sys.stderr)
        return

    behaviour = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )

    target.setCollectionBehavior_(behaviour)
    target.setLevel_(NSFloatingWindowLevel)
    target.setHidesOnDeactivate_(False)

    # Keep the overlay visible without making the Python app active.
    target.setCanHide_(False)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    overlay = CaptionOverlay()
    overlay.show()

    configure_macos_window(overlay)

    # Do not activate/focus the overlay: the whole point of this test is that
    # it should remain visible while another application is active.
    overlay.raise_()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
