import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from live_caption.config import load_config, resolve_config
from live_caption.events import RawASREvent
from live_caption.processor import CaptionProcessor
from live_caption.renderers import make_renderer, TextLayout
from live_caption.speakers import CaptionText, SpeakerStyle, join_captions


def speech(words, *, kind="final"):
    # A single event may contain several speakers, not just a single ID.
    text = " ".join(word for word, speaker in words)
    payload = {"results": [{"type": "word", "alternatives": [{"content": word,
                               "speaker": speaker}]} for word, speaker in words]}
    return CaptionProcessor({}).process(RawASREvent(0, kind, text, payload))[1]


class SpeakerProcessingTests(unittest.TestCase):
    def test_word_labels_survive_omission_and_whitespace_cleanup(self):
        text = speech([("Hello", "S1"), ("shit", "S1"), ("Hello", "S2"), ("everyone", "S2")])
        self.assertEqual(text, "Hello Hello everyone")
        self.assertEqual([run for run in text.runs() if run["text"].strip()],
                         [{"text": "Hello", "speaker": "S1"}, {"text": "Hello", "speaker": "S2"},
                          {"text": "everyone", "speaker": "S2"}])

    def test_tagged_profanity_omitted_without_losing_surrounding_speakers(self):
        event = RawASREvent(0, 'final', 'One rare Two', {"results": [
            {"alternatives": [{"content": "One", "speaker": "S1"}]},
            {"alternatives": [{"content": "rare", "speaker": "S3", "tags": ["profanity"]}]},
            {"alternatives": [{"content": "Two", "speaker": "S2"}]}]})
        text = CaptionProcessor({}).process(event)[1]
        self.assertEqual(text, 'One Two')
        self.assertEqual(text.owners, ('S1', 'S1', 'S1', None, 'S2', 'S2', 'S2'))

    def test_partial_unknown_and_unmatched_tokens_are_neutral(self):
        text = speech([("Tentative", "S2")], kind='partial')
        self.assertEqual(set(text.owners), {None})
        self.assertEqual(set(speech([('Unknown', 'UU')]).owners), {None})
        text = CaptionProcessor({}).process(RawASREvent(0, 'final', 'Different text',
            {"results": [{"alternatives": [{"content": "missing", "speaker": "S2"}]}]}))[1]
        self.assertEqual(set(text.owners), {None})

    def test_unicode_slicing_serialization_and_html_escaping(self):
        text = speech([('🎶<hello>&', 'S2')])
        restored = CaptionText.from_runs(str(text), json.loads(json.dumps(text.runs())))
        self.assertEqual(restored.owners, text.owners)
        self.assertEqual(text[1:].owners, ('S2',) * (len(text) - 1))
        self.assertIsInstance(join_captions(' ', [text, 'neutral']), str)
        html = SpeakerStyle().html_lines(text)[0]
        self.assertIn('&lt;hello&gt;&amp;', html)
        self.assertNotIn('<hello>', html)
        with self.assertRaises(ValueError):
            CaptionText.from_runs('hello', [{'text': 'other', 'speaker': 'S1'}])


class SpeakerRendererTests(unittest.TestCase):
    def renderer(self, mode='speakers', width=40, lines=2):
        return make_renderer('bbc_live', {'speakers': {'mode': mode}, 'bbc': {'lines': lines}},
                             layout=TextLayout(width))

    def test_white_mode_preserves_plain_layout_and_speaker_mode_breaks_turns(self):
        words = speech([('Hi', 'S1'), ('Welcome', 'S2')])
        self.assertEqual(self.renderer('white').add_final(words), 'Hi Welcome')
        r = self.renderer()
        self.assertEqual(r.add_final(words), 'Hi\nWelcome')
        self.assertEqual(r.current_text().owners, ('S1', 'S1', None) + ('S2',) * 7)

    def test_colours_stay_with_passages_during_scroll_and_finalisation(self):
        r = self.renderer()
        r.add_final(speech([('One', 'S1')]))
        r.add_final(speech([('Two', 'S2')]))
        r.update_partial(speech([('Three', 'S3')], kind='partial'))
        self.assertIn('Two Three', r.current_text())
        result = r.add_final(speech([('Three', 'S3')]))
        self.assertEqual(result, 'Two\nThree')
        # A neutral partial is reflowed on speaker confirmation: snap instead of animating a correction.
        self.assertEqual(r.scroll_lines, 0)
        self.assertEqual(set(result[:3].owners), {'S2'})
        self.assertEqual(set(result[4:].owners), {'S3'})
        colours = r.style.html_lines(result)
        self.assertIn('#FFE066', colours[0])
        self.assertIn('#77DDF5', colours[1])
        r.add_final(speech([('again', 'S3')]))
        self.assertEqual(r.scroll_lines, 0)

    def test_long_words_retain_labels_when_split(self):
        r = self.renderer(width=5, lines=4)
        result = r.add_final(speech([('abcdefghijk', 'S2')]))
        self.assertEqual(result, 'abcde\nfghij\nk')
        for i, char in enumerate(result):
            if char != '\n':
                self.assertEqual(result.owners[i], 'S2')

    def test_mode_change_resets_layout_but_colour_mapping_stays_stable(self):
        r = self.renderer()
        r.add_final(speech([('Welcome', 'S3')]))
        colour = r.style.colour('S3')
        r.set_speaker_mode(False)
        self.assertEqual(r.current_text(), '')
        self.assertEqual(r.add_final(speech([('One', 'S1'), ('Two', 'S2')])), 'One Two')
        r.set_speaker_mode(True)
        self.assertEqual(r.current_text(), '')
        self.assertEqual(r.style.colour('S3'), colour)
        r.reset()
        self.assertEqual(r.style.colour('S3'), colour)

    def test_old_recordings_and_legacy_renderers_stay_plain(self):
        for name in ('bbc_live', 'bbc', 'rolling'):
            r = make_renderer(name, {'speakers': {'mode': 'white'}})
            self.assertEqual(r.add_final('Plain old recording'), 'Plain old recording')


class SpeakerConfigTests(unittest.TestCase):
    def test_default_off_and_independent_config_cli_overrides(self):
        self.assertEqual(resolve_config()['speechmatics']['diarization'], 'none')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yaml'
            config.write_text('speechmatics: {diarization: speaker}\nspeakers: {mode: speakers}\n')
            loaded = load_config(config, diarization='none', speaker_colours='white')
            self.assertEqual(loaded['speechmatics']['diarization'], 'none')
            self.assertEqual(loaded['speakers']['mode'], 'white')

    def test_invalid_config(self):
        for config in ({'speechmatics': {'diarization': 'bad'}}, {'speakers': {'mode': 'bad'}},
                       {'speakers': {'colours': []}}, {'speakers': {'colours': ['red']}},
                       {'speakers': {'unknown_colour': '<html>'}},
                       {'speechmatics': {'speaker_diarization_config': {'max_speakers': 1}}},
                       {'speechmatics': {'speaker_diarization_config': {'prefer_current_speaker': 'yes'}}},
                       {'speechmatics': {'speaker_diarization_config': {'speaker_sensitivity': 2}}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                resolve_config(config)

    def test_sdk_receives_only_recognition_settings(self):
        from live_caption import asr, live_caption as cli
        for choice in ('none', 'speaker'):
            with tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / 'config.yaml'
                config.write_text('speechmatics: {api_key: test-only-key, speaker_diarization_config: {prefer_current_speaker: true}}\n')
                with patch('sys.argv', ['live-caption', '--config', str(config), '--diarization', choice,
                                       '--speaker-colours', 'speakers', '--layout', 'bbc_live']), \
                     patch.object(cli, 'run_overlay') as overlay:
                    cli.main()
                factory, resolved = overlay.call_args.args
                worker = factory()[1]
                captured = {}
                class Client:
                    def __init__(self, **kwargs): pass
                    async def __aenter__(self): return self
                    async def __aexit__(self, *args): pass
                    def on(self, kind): return lambda fn: fn
                    async def start_session(self, *, transcription_config, audio_format):
                        captured.update(transcription_config.to_dict())
                        worker.stop()
                with patch.object(asr, 'AsyncClient', Client), patch.object(asr, 'Microphone'):
                    asyncio.run(worker._run())
                self.assertEqual(captured['diarization'], choice)
                self.assertNotIn('speakers', captured)
                self.assertEqual('speaker_diarization_config' in captured, choice == 'speaker')
                self.assertEqual(resolved['speaker_available'], choice == 'speaker')
