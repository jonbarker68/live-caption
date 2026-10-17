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

    def test_full_stop_breaks_at_half_width_in_same_or_later_final(self):
        r = self.renderer(width=20)
        self.assertEqual(r.add_final('Hello all. Next'), 'Hello all.\nNext')
        r.reset()
        self.assertEqual(r.add_final('Hello all.'), 'Hello all.')
        self.assertEqual(r.add_final('Next'), 'Hello all.\nNext')

    def test_short_sentence_and_other_punctuation_stay_inline(self):
        r = self.renderer(width=40)
        self.assertEqual(r.add_final('Hi. Welcome'), 'Hi. Welcome')
        for phrase in ('Hello everyone! Next', 'Hello everyone? Next', 'Hello everyone, next'):
            r.reset()
            self.assertEqual(r.add_final(phrase), phrase)

    def test_sentence_threshold_uses_font_measurements(self):
        measure = lambda text: sum(5 if c == 'W' else 1 for c in text)
        r = LiveRenderer({}, layout=TextLayout(30, measure))
        # Eight characters, but 20 units wide: more than half the line.
        self.assertEqual(r.add_final('WWW end. Next'), 'WWW end.\nNext')

    def test_provisional_stop_does_not_break_until_confirmed(self):
        r = self.renderer(width=30)
        phrase = 'Hello everyone. Next'
        self.assertEqual(r.update_partial(phrase), phrase)
        self.assertEqual(r.update_partial('Hello everyone next'), 'Hello everyone next')
        self.assertEqual(r.add_final(phrase), 'Hello everyone.\nNext')

    def test_partial_respects_previously_committed_full_stop(self):
        r = self.renderer(width=30)
        r.add_final('Hello everyone.')
        self.assertEqual(r.update_partial('Next'), 'Hello everyone.\nNext')
        self.assertEqual(r.add_final('Next'), 'Hello everyone.\nNext')
        self.assertEqual(r.scroll_lines, 0)

    def test_abbreviations_decimals_and_ellipses_are_not_sentence_breaks(self):
        r = self.renderer(width=40)
        for phrase in ('The speaker is Dr. Smith', 'Speak to J. Smith',
                       'He lives in the U.K. today', 'The value is 3.14 today',
                       'We are waiting... quietly'):
            r.reset()
            self.assertEqual(r.add_final(phrase), phrase)

    def test_full_stop_before_closing_quote_and_sentence_scroll(self):
        r = self.renderer(width=30)
        self.assertEqual(r.add_final('He said "Hello." Next'), 'He said "Hello."\nNext')
        r.reset()
        r.add_final('Hello everyone. Another sentence.')
        self.assertEqual(r.add_final('Next'), 'Another sentence.\nNext')
        self.assertEqual(r.scroll_lines, 1)
        r.reset()
        self.assertEqual(r.add_final('Fresh words'), 'Fresh words')

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
        self.assertIn('Thank you for taking part.', ' '.join(r.current_text().split()))

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


class PauseBreakTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0

    def renderer(self, *, threshold=2.0, width=40, lines=2):
        return LiveRenderer({'bbc': {'pause_threshold_seconds': threshold, 'lines': lines}},
                            layout=TextLayout(width), clock=lambda: self.now)

    def test_short_line_starts_fresh_at_threshold_without_punctuation(self):
        for ending in ('Hi', 'Hi.'):
            with self.subTest(ending=ending):
                self.now = 0
                r = self.renderer()
                r.add_final(ending)
                self.now = 2.0
                self.assertEqual(r.add_final('Welcome'), ending + '\nWelcome')

    def test_short_gap_and_disabled_setting_extend_line(self):
        for threshold, gap in ((2.0, 1.99), (0, 100)):
            with self.subTest(threshold=threshold):
                self.now = 0
                r = self.renderer(threshold=threshold)
                r.add_final('Hi.')
                self.now = gap
                self.assertEqual(r.add_final('Welcome'), 'Hi. Welcome')

    def test_partial_boundary_survives_corrections_and_final_without_double_scroll(self):
        r = self.renderer()
        r.add_final('Old sentence. Another sentence.')
        # Existing half-width punctuation break fills both line positions.
        self.assertEqual(r.current_text(), 'Old sentence. Another sentence.')
        self.now = 3
        self.assertEqual(r.add_final('Hi.'), 'Old sentence. Another sentence.\nHi.')
        self.now = 6
        self.assertEqual(r.update_partial('Welcome'), 'Hi.\nWelcome')
        self.assertEqual(r.scroll_lines, 1)
        self.assertEqual(r._open, 'Hi.')  # A preview cannot mutate final history.
        self.assertEqual(r.update_partial('Welcome back'), 'Hi.\nWelcome back')
        self.assertEqual(r.scroll_lines, 0)
        self.now = 10  # A delayed confirmation belongs to the same hypothesis.
        self.assertEqual(r.add_final('Welcome back'), 'Hi.\nWelcome back')
        self.assertEqual(r.scroll_lines, 0)
        self.assertEqual(r.add_final('everyone'), 'Hi.\nWelcome back everyone')

    def test_outstanding_partial_does_not_split_due_to_delayed_correction_or_final(self):
        r = self.renderer()
        r.add_final('Hi')
        self.now = .1
        r.update_partial('there')
        self.now = 4
        self.assertEqual(r.update_partial('everyone'), 'Hi everyone')
        self.now = 8
        self.assertEqual(r.add_final('everyone'), 'Hi everyone')
        self.now = 11
        self.assertEqual(r.add_final('Welcome back'), 'Hi everyone\nWelcome back')

    def test_empty_updates_do_not_restart_clock_or_create_blank_lines(self):
        r = self.renderer()
        r.add_final('Hi')
        self.now = 1.5
        self.assertEqual(r.update_partial(''), 'Hi')
        self.assertEqual(r.add_final(''), 'Hi')
        self.now = 2
        self.assertEqual(r.update_partial('Welcome'), 'Hi\nWelcome')
        self.assertEqual(r.update_partial(''), 'Hi')
        self.assertEqual(r.update_partial('Welcome back'), 'Hi\nWelcome back')
        self.assertEqual(r.add_final(''), 'Hi')
        self.assertEqual(r.current_text(), 'Hi')
        self.assertEqual(r.add_final('Again'), 'Hi\nAgain')

    def test_reset_and_layout_change_clear_timing_history(self):
        r = self.renderer()
        for reset in (r.reset, lambda: r.set_layout(TextLayout(40))):
            r.add_final('Old')
            self.now += 10
            reset()
            self.assertEqual(r.add_final('Fresh'), 'Fresh')
            self.assertEqual(r.add_final('words'), 'Fresh words')

    def test_single_line_and_bounded_history_after_many_pauses(self):
        for lines in (1, 2):
            r = self.renderer(lines=lines)
            for i in range(100):
                self.now += 3
                r.add_final('word' + str(i))
            self.assertEqual(r.current_text(), 'word99' if lines == 1 else 'word98\nword99')
            self.assertLessEqual(len(r._closed), lines - 1)

    def test_invalid_pause_setting_rejected_by_schema_and_direct_renderer(self):
        from live_caption.config import resolve_config
        for value in (-1, True, None, 'invalid', float('nan'), float('inf')):
            config = {'bbc': {'pause_threshold_seconds': value}}
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'bbc.pause_threshold_seconds'):
                    resolve_config(config)
                with self.assertRaisesRegex(ValueError, 'bbc.pause_threshold_seconds'):
                    LiveRenderer(config)
        self.assertEqual(LiveRenderer({}).pause_threshold_seconds, 2.0)


if __name__ == '__main__':
    unittest.main()
