from setuptools import setup

APP = ["src/live_caption/live_caption.py"]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Live Caption",
        "CFBundleDisplayName": "Live Caption",
        "CFBundleIdentifier": "uk.ac.sheffield.livecaption",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": (
            "Live Caption uses the microphone to provide real-time captions."
        ),
    },
}


setup(
    name="Live Caption",
    version="0.1.0",
    app=APP,
    options={"py2app": OPTIONS},
)
