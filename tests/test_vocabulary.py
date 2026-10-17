import asyncio
import copy
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch


from live_caption.config import load_config, resolve_config
from live_caption.vocabulary import load_vocabulary, validate_vocabulary


class VocabularyTests(unittest.TestCase):
    def test_strings_objects_pronunciations_and_exact_duplicates(self):
        entries = ["Cadenza", {"content": "Stoller Hall"},
                   {"content": "Cadenza"}, {"content": "Cadenza", "sounds_like": ["kah den zah"]}]
        before = copy.deepcopy(entries)
        expected = [{"content": "Cadenza"}, {"content": "Stoller Hall"},
                    {"content": "Cadenza", "sounds_like": ["kah den zah"]}]
        self.assertEqual(validate_vocabulary(entries), expected)
        self.assertEqual(entries, before)
        self.assertEqual(validate_vocabulary(expected), expected)

    def test_bad_entries_and_api_limits(self):
        invalid = [None, {}, "Cadenza", [None], [False], [""], [{}],
                   [{"content": ""}], [{"content": "name", "extra": True}],
                   [{"content": "name", "sounds_like": []}],
                   [{"content": "name", "sounds_like": "hint"}],
                   [{"content": "name", "sounds_like": [False]}],
                   ["one two three four five six seven"], ["x" * 4001],
                   [{"content": "name", "sounds_like": ["a b c d e f g"]}]]
        for entries in invalid:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                validate_vocabulary(entries)
        self.assertEqual(validate_vocabulary(["a b c d e f"]), [{"content": "a b c d e f"}])
        with self.assertRaisesRegex(ValueError, "20000"):
            validate_vocabulary([f"word{i}" for i in range(20001)])

    def test_config_relative_paths_merge_and_no_reloading_on_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "words with spaces.yaml").write_text('- Cadenza\n- content: Camerata\n  sounds_like: [kammerata]\n', encoding="utf-8")
            config = folder / "config.yaml"
            config.write_text('vocabulary_file: words with spaces.yaml\nspeechmatics:\n  additional_vocab: [Cadenza, Stoller Hall]\noverlay: {font_size: 42}\n')
            loaded = load_config(config)
            expected = [{"content": "Cadenza"}, {"content": "Stoller Hall"},
                        {"content": "Camerata", "sounds_like": ["kammerata"]}]
            self.assertEqual(loaded["speechmatics"]["additional_vocab"], expected)
            self.assertEqual(loaded["overlay"]["font_size"], 42)
            self.assertEqual(loaded["vocabulary_file"], str((folder / "words with spaces.yaml").resolve()))
            self.assertEqual(resolve_config(loaded)["speechmatics"]["additional_vocab"], expected)

    def test_cli_path_is_relative_to_cwd_and_replaces_configured_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "settings"
            config_dir.mkdir()
            config = config_dir / "config.yaml"
            config.write_text('vocabulary_file: missing.yaml\n')
            override = root / "override.yaml"
            override.write_text('- naïve\n', encoding="utf-8")
            relative = Path(os.path.relpath(override, Path.cwd()))
            loaded = load_config(config, vocabulary_file=str(relative))
            self.assertEqual(loaded["speechmatics"]["additional_vocab"], [{"content": "naïve"}])
            with self.assertRaisesRegex(ValueError, 'missing.yaml'):
                load_config(config)
            home_relative = os.path.relpath(override, Path.home())
            self.assertEqual(load_vocabulary(str(Path('~') / home_relative)), [{"content": "naïve"}])

    def test_empty_optional_missing_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.yaml"
            for setting in ('{}', 'vocabulary_file: null'):
                config.write_text(setting)
                self.assertNotIn("additional_vocab", load_config(config)["speechmatics"])
            vocabulary = root / "vocabulary.yaml"
            config.write_text('vocabulary_file: vocabulary.yaml')
            for content in ('', '[]'):
                vocabulary.write_text(content)
                self.assertEqual(load_config(config)["speechmatics"]["additional_vocab"], [])
            for content in ('[broken', 'words: [Cadenza]', '- content: false'):
                vocabulary.write_text(content)
                with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'vocabulary.yaml'):
                    load_config(config)
            for value in ('false', '42', '\"\"'):
                config.write_text('vocabulary_file: ' + value)
                with self.assertRaisesRegex(ValueError, 'vocabulary_file'):
                    load_config(config)

    def test_both_tracked_examples_resolve_same_starter_list(self):
        project = Path(__file__).resolve().parents[1]
        live_settings = load_config(project / "config.example.yaml")
        cadenza_example = project.parent / "cadenza-stoller-hall/config/caption-config.example.yaml"
        if cadenza_example.exists():
            self.assertEqual(load_config(cadenza_example)["speechmatics"]["additional_vocab"],
                             live_settings["speechmatics"]["additional_vocab"])


class VocabularyWiringTests(unittest.TestCase):
    def test_live_cli_override_reaches_speechmatics_worker_and_sdk(self):
        from live_caption import live_caption as cli, asr
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.yaml"
            config_path.write_text('vocabulary_file: missing.yaml\nspeechmatics: {api_key: test-only-key}\n')
            vocabulary = root / "words.yaml"
            vocabulary.write_text('- content: Cadenza\n  sounds_like: [kahdenzah]\n')
            with patch('sys.argv', ['live-caption', '--config', str(config_path), '--vocabulary', str(vocabulary)]), \
                 patch.object(cli, 'run_overlay') as overlay:
                cli.main()
            factory, config = overlay.call_args.args
            display, worker, processor = factory()
            expected = [{"content": "Cadenza", "sounds_like": ["kahdenzah"]}]
            self.assertEqual(worker.config["speechmatics"]["additional_vocab"], expected)
            captured = {}

            class Client:
                def __init__(self, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def on(self, kind): return lambda callback: callback
                async def start_session(self, *, transcription_config, audio_format):
                    captured.update(transcription_config.to_dict())
                    worker.stop()

            with patch.object(asr, 'AsyncClient', Client), patch.object(asr, 'Microphone'):
                asyncio.run(worker._run())
            self.assertEqual(captured["additional_vocab"], expected)
            self.assertNotIn('vocabulary_file', captured)
            self.assertEqual(cli.parse_args(['--vocabulary-file', str(vocabulary)]).vocabulary, str(vocabulary))
