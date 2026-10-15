import unittest
from live_caption.renderers import make_renderer, TextLayout
from live_caption.renderers.live import LiveRenderer


class LiveRendererTests(unittest.TestCase):
    def renderer(self, width=10, lines=2):
        return LiveRenderer({'bbc': {'lines': lines}}, layout=TextLayout(width))

    def test_partial_corrections_do_not_commit_or_duplicate(self):
        r = self.renderer()
        r.add_final('Hello')
        self.assertEqual(r.update_partial('the cat'), 'Hello the\ncat')
        self.assertEqual(r.update_partial('a dog'), 'Hello a\ndog')
        self.assertEqual(r.add_final('a dog'), 'Hello a\ndog')
        self.assertEqual(r.current_text(), 'Hello a\ndog')

    def test_committed_line_breaks_survive_new_finals(self):
        r = self.renderer()
        self.assertEqual(r.add_final('one two three'), 'one two\nthree')
        self.assertEqual(r.add_final('four'), 'one two\nthree four')
        self.assertEqual(r.add_final('five'), 'three four\nfive')
        self.assertEqual(r.add_final('six'), 'three four\nfive six')

    def test_measurement_uses_actual_string_width(self):
        measure = lambda text: sum(4 if c == 'W' else 1 for c in text)
        r = LiveRenderer({}, layout=TextLayout(10, measure))
        self.assertEqual(r.add_final('WW ii iii'), 'WW\nii iii')
        for line in r.current_text().splitlines():
            self.assertLessEqual(measure(line), 10)

    def test_overlong_word_is_split_without_loss(self):
        r = self.renderer(width=5, lines=4)
        self.assertEqual(r.add_final('abcdefghijk'), 'abcde\nfghij\nk')
        self.assertEqual(r.current_text().replace('\n', ''), 'abcdefghijk')

    def test_empty_partial_restores_committed_view(self):
        r = self.renderer()
        r.add_final('one two')
        r.update_partial('temporary wording')
        self.assertEqual(r.update_partial(''), 'one two')

    def test_long_session_has_bounded_state(self):
        r = self.renderer()
        for _ in range(5000):
            r.add_final('one two three four')
        r.update_partial('long provisional utterance ' * 1000)
        self.assertLessEqual(len(r.current_text().splitlines()), 2)
        self.assertLessEqual(len(r._closed), 1)
        self.assertLessEqual(len(r._open), 10)
        self.assertLessEqual(len(r.partial_text), 21)

    def test_layout_change_and_reset_discard_old_speech(self):
        r = self.renderer()
        r.add_final('old speech')
        r.update_partial('old partial')
        r.set_layout(TextLayout(5))
        self.assertEqual(r.current_text(), '')
        self.assertEqual(r.add_final('new words'), 'new\nwords')
        r.reset()
        self.assertEqual(r.current_text(), '')

    def test_scroll_signal_is_not_repeated_when_partial_becomes_final(self):
        r = self.renderer()
        r.add_final('one two three four')
        self.assertEqual(r.scroll_lines, 0)
        self.assertEqual(r.update_partial('five'), 'three four\nfive')
        self.assertEqual(r.scroll_lines, 1)
        r.add_final('five')
        self.assertEqual(r.scroll_lines, 0)
        r.update_partial('six')
        self.assertEqual(r.scroll_lines, 0)
        r.reset()
        self.assertEqual(r.scroll_lines, 0)

    def test_corrections_and_multi_line_bursts_do_not_animate(self):
        r = self.renderer()
        r.add_final('one two')
        r.update_partial('three four five six')
        r.update_partial('new wording replaces the provisional hypothesis')
        self.assertEqual(r.scroll_lines, 0)
        r.add_final('a large burst of completely new confirmed words arriving at once')
        self.assertEqual(r.scroll_lines, 0)

    def test_single_line_viewport(self):
        r = self.renderer(lines=1)
        self.assertEqual(r.add_final('one two three'), 'three')

    def test_replay_pipeline_and_final_output(self):
        import queue
        from pathlib import Path
        from live_caption.recording import ReplayWorker
        from live_caption.processor import CaptionProcessor
        from live_caption.config import DEFAULT_CONFIG
        raw, status = queue.Queue(), queue.Queue()
        replay = ReplayWorker(Path(__file__).parent / 'fixtures/caption-layout.jsonl', raw, status, speed=10000)
        replay.start()
        replay.join(timeout=5)
        self.assertFalse(replay.is_alive())
        processor = CaptionProcessor(DEFAULT_CONFIG)
        r = self.renderer(width=36)
        partial_seen = final_seen = False
        while True:
            event = raw.get(timeout=1)
            if event is None:
                break
            kind, text = processor.process(event)
            partial_seen |= kind == 'partial'
            final_seen |= kind == 'final'
            output = r.update_partial(text) if kind == 'partial' else r.add_final(text)
            self.assertLessEqual(len(output.splitlines()), 2)
            self.assertTrue(all(len(line) <= 36 for line in output.splitlines()))
        self.assertTrue(partial_seen and final_seen)
        self.assertIn('Thank you for taking part.', r.current_text())

    def test_invalid_measurement_or_width(self):
        for width in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                TextLayout(width)
        with self.assertRaises(ValueError):
            LiveRenderer({}, layout=TextLayout(10, lambda s: float('nan'))).add_final('a')
        with self.assertRaises(ValueError):
            self.renderer(width=0.5).add_final('W')
        with self.assertRaises(ValueError):
            self.renderer(lines=0)

    def test_legacy_modes_and_reset_remain_available(self):
        for name in ('bbc', 'rolling', 'bbc_live'):
            r = make_renderer(name, {'bbc': {'line_width': 10}})
            self.assertTrue(r.add_final('some speech'))
            r.reset()
            self.assertEqual(r.current_text(), '')
        self.assertEqual(make_renderer('bbc', {}).line_width, 36)


if __name__ == '__main__':
    unittest.main()
