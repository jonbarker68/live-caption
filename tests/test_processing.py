import copy
from pathlib import Path
import queue
import tempfile
import unittest

from live_caption.config import DEFAULT_CONFIG, load_config, resolve_config
from live_caption.events import RawASREvent
from live_caption.processor import CaptionProcessor, ProcessorWorker
from live_caption.renderers import make_renderer, TextLayout


class ConfigTests(unittest.TestCase):
    def test_defaults_legacy_and_explicit_choice(self):
        self.assertEqual(resolve_config()["processing"]["profanity"]["mode"], "omit")
        for flag, mode in ((False, "off"), (True, "omit")):
            self.assertEqual(resolve_config({"processing": {"profanity_filter": flag,
                "profanity_replacement": "*"}})["processing"]["profanity"]["mode"], mode)
        self.assertEqual(resolve_config({"processing": {"profanity_filter": False,
            "profanity": {"mode": "omit"}}})["processing"]["profanity"]["mode"], "omit")

    def test_layout_settings_preserved_and_inputs_not_mutated(self):
        config = {"overlay": {"font_size": 42, "layout": "bbc", "width_fraction": .6,
                  "max_width": 900, "alignment": "right"}, "bbc": {"line_width": 29, "lines": 3}}
        original = copy.deepcopy(config)
        resolved = resolve_config(config)
        self.assertEqual(resolved["overlay"], original["overlay"])
        self.assertEqual(resolved["bbc"], original["bbc"])
        resolved["processing"]["profanity"]["additional_words"].append("custom")
        self.assertEqual(config, original)
        self.assertEqual(DEFAULT_CONFIG["processing"]["profanity"]["additional_words"], [])

    def test_yaml_and_invalid_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            path.write_text("processing:\n  profanity:\n    mode: off\n")
            self.assertEqual(load_config(path)["processing"]["profanity"]["mode"], "off")
        for config in ({"processing": None}, {"processing": {"profanity": False}},
                       {"processing": {"profanity": {"mode": "mask"}}},
                       {"processing": {"profanity_filter": "false"}},
                       {"processing": {"profanity": {"additional_words": "bad"}}},
                       {"processing": {"profanity": {"allow_words": [""]}}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                resolve_config(config)


class ProcessingTests(unittest.TestCase):
    def process(self, text, payload=None, config=None, kind="partial"):
        return CaptionProcessor(config or {}).process(RawASREvent(0, kind, text, payload))

    def test_untagged_case_inflections_boundaries_and_punctuation(self):
        for text, expected in [("Hello FUCKING world.", "Hello world."),
            ("Shit, welcome!", "welcome!"), ("Welcome (fuck) everyone.", "Welcome everyone."),
            ("Welcome 'fuck' everyone.", "Welcome everyone."), ("FUCK!!!", ""),
            ("bastard’s music", "music"), ("assessment Scunthorpe Dickens class cocktail hello", "assessment Scunthorpe Dickens class cocktail hello")]:
            with self.subTest(text=text):
                self.assertEqual(self.process(text), ("partial", expected))

    def test_tags_custom_terms_and_allow_list(self):
        payload = {"results": [{"alternatives": [{"content": "unexpected", "tags": ["profanity"]}]}]}
        self.assertEqual(self.process("An unexpected word", payload), ("partial", "An word"))
        config = {"processing": {"profanity": {"additional_words": ["custom", "bad phrase"],
                                                "allow_words": ["bloody", "unexpected"]}}}
        self.assertEqual(self.process("bloody custom bad phrase unexpected", payload, config),
                         ("partial", "bloody unexpected"))

    def test_off_passes_text_verbatim(self):
        config = {"processing": {"profanity": {"mode": "off"}}}
        self.assertEqual(self.process("  FUCK!  ", config=config), ("partial", "  FUCK!  "))
        self.assertEqual(self.process("", config=config), ("partial", ""))

    def test_hypotheses_and_all_omitted_finals_in_every_layout(self):
        processor = CaptionProcessor({})
        for layout in ("bbc_live", "bbc", "rolling"):
            renderer = make_renderer(layout, {"bbc": {"line_width": 20}},
                                     **({"layout": TextLayout(20)} if layout == "bbc_live" else {}))
            def send(kind, text):
                k, clean = processor.process(RawASREvent(0, kind, text))
                return renderer.add_final(clean) if k == "final" else renderer.update_partial(clean)
            self.assertEqual(send("final", "Welcome"), "Welcome")
            self.assertEqual(send("partial", "shit everyone"), "Welcome everyone")
            self.assertEqual(send("partial", "shit"), "Welcome")
            self.assertEqual(send("partial", "dear audience"), "Welcome dear audience" if layout == "rolling" else "Welcome dear\naudience")
            self.assertEqual(send("final", "fuck"), "Welcome")
            self.assertEqual(send("partial", ""), "Welcome")
            self.assertEqual(send("final", "everyone"), "Welcome everyone")
            self.assertNotIn("*", renderer.current_text())

    def test_worker_forwards_empty_events_and_finishes(self):
        raw, display = queue.Queue(), queue.Queue()
        for kind in ("partial", "final"):
            raw.put(RawASREvent(0, kind, "fuck"))
        raw.put(None)
        worker = ProcessorWorker(raw, display, {})
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([display.get_nowait() for _ in range(3)],
                         [("partial", ""), ("final", ""), ("pipeline_stopped", "")])
        self.assertIsNone(self.process("ignored", kind="status"))


class SpeechmaticsEmissionTests(unittest.TestCase):
    def test_empty_hypothesis_and_payload_reach_processing(self):
        import asyncio
        from unittest.mock import patch
        from live_caption import asr

        raw, status = queue.Queue(), queue.Queue()
        worker = asr.SpeechmaticsWorker(raw, status,
            resolve_config({"speechmatics": {"api_key": "test-only-key"}}))
        handlers = {}
        messages = [
            (asr.ServerMessageType.ADD_PARTIAL_TRANSCRIPT, "Welcome"),
            (asr.ServerMessageType.ADD_PARTIAL_TRANSCRIPT, ""),
            (asr.ServerMessageType.ADD_TRANSCRIPT, "unexpected"),
        ]

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def on(self, kind):
                def register(callback):
                    handlers[kind] = callback
                    return callback
                return register

            async def start_session(self, **kwargs):
                pass

            async def send_audio(self, audio):
                pass

        class Microphone:
            def __init__(self, **kwargs):
                pass

            def start(self):
                return True

            def stop(self):
                pass

            async def read(self, **kwargs):
                for kind, text in messages:
                    handlers[kind]({"message": kind, "metadata": {"transcript": text,
                        "start_time": 0, "end_time": 1}, "results": [{"type": "word",
                        "start_time": 0, "end_time": 1, "alternatives": [{"content": text,
                        "confidence": 1, "tags": ["profanity"] if text == "unexpected" else []}]}]})
                worker.stop()
                return b"\x01\x00"

        with patch.object(asr, "AsyncClient", Client), patch.object(asr, "Microphone", Microphone):
            asyncio.run(worker._run())
        processor = CaptionProcessor({})
        self.assertEqual([processor.process(raw.get_nowait()) for _ in messages],
                         [("partial", "Welcome"), ("partial", ""), ("final", "")])
