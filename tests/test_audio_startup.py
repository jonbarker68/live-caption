"""Intentional digital silence is valid after the initial input check."""
import asyncio
import queue
import unittest
from unittest.mock import patch
from live_caption import asr


class AudioStartupTests(unittest.TestCase):
    def exercise(self, chunks, mode='microphone'):
        worker = asr.SpeechmaticsWorker(queue.Queue(), queue.Queue(),
            {'speechmatics': {'api_key': 'test-only-key'}, 'audio': {'mode': mode}})
        sent = []
        stopped = []
        remaining = list(chunks)

        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def on(self, kind): return lambda callback: callback
            async def start_session(self, **kwargs): pass
            async def send_audio(self, audio): sent.append(audio)

        class Input:
            def __init__(self, *args, **kwargs): pass
            def start(self): return True
            def stop(self): stopped.append(True)
            async def read(self, **kwargs):
                audio = remaining.pop(0)
                if not remaining: worker.stop()
                return audio

        with patch.object(asr, 'AsyncClient', Client), patch.object(asr, 'Microphone', Input), patch.object(asr, 'MixedAudioInput', Input):
            worker.run()
        events = []
        while not worker.display_events.empty(): events.append(worker.display_events.get_nowait())
        self.assertEqual(stopped, [True])
        self.assertIsNone(worker.raw_events.get_nowait())
        return sent, events

    def test_long_and_repeated_mutes_after_signal_keep_session_alive(self):
        zero = bytes(32000)  # one second of mono 16 kHz PCM16
        signal = b'\x01\x00' + bytes(31998)
        chunks = [signal] + [zero] * 120 + [signal] + [zero] * 10 + [signal]
        for mode in ('microphone', 'mixed'):
            with self.subTest(mode=mode):
                sent, events = self.exercise(chunks, mode)
                self.assertEqual(sent, chunks)
                self.assertFalse(any(kind == 'error' for kind, text in events), events)

    def test_initial_digital_silence_still_fails_startup(self):
        sent, events = self.exercise([bytes(32000)] * 4)
        self.assertEqual(len(sent), 2)
        errors = [text for kind, text in events if kind == 'error']
        self.assertEqual(len(errors), 1)
        self.assertIn('Audio startup check', errors[0])
        self.assertIn('3 seconds before any signal', errors[0])

    def test_signal_before_startup_deadline_disables_later_checks(self):
        zero = bytes(32000)
        signal = b'\x01\x00'
        chunks = [zero, zero, signal] + [zero] * 10
        sent, events = self.exercise(chunks)
        self.assertEqual(sent, chunks)
        self.assertFalse(any(kind == 'error' for kind, text in events), events)
