import sys
import unittest


@unittest.skipUnless(sys.platform == 'darwin', 'Native overlay requires macOS')
class NativeLayoutTests(unittest.TestCase):
    def test_native_font_width_and_two_line_height(self):
        from AppKit import NSApplication, NSLayoutManager
        from live_caption.overlay import AppDelegate
        NSApplication.sharedApplication()
        config = {'overlay': {'layout': 'bbc_live', 'max_width': 650, 'height': 60,
                              'font_size': 36}, 'bbc': {'lines': 2, 'line_width': 5}}
        delegate = AppDelegate.alloc().initWithSessionFactory_config_(lambda: None, config)
        delegate._create_overlay()
        try:
            renderer = delegate._create_renderer()
            # A measured overlay does not fall back to the five-character limit.
            text = renderer.add_final('Welcome to the Cadenza concert. ' * 4)
            delegate._show_caption(text)
            self.assertEqual(delegate.label.stringValue(), text)
            self.assertEqual(len(text.splitlines()), 2)
            for line in text.splitlines():
                self.assertLessEqual(renderer.layout.measure(line), renderer.layout.width)
            self.assertFalse(delegate.label.cell().wraps())
            self.assertEqual(delegate.label.maximumNumberOfLines(), 2)
            self.assertEqual(delegate.label.alignment(), 0)
            line_height = NSLayoutManager.alloc().init().defaultLineHeightForFont_(delegate.label.font())
            self.assertGreaterEqual(delegate.label.bounds().size.height, 2 * line_height)
            self.assertEqual(delegate.label.font().pointSize(), 36)
        finally:
            delegate.panel.orderOut_(None)

    def test_legacy_configuration_keeps_its_alignment(self):
        from live_caption.overlay import _overlay_config
        self.assertEqual(_overlay_config({'overlay': {'layout': 'bbc'}})['alignment'], 'center')
        self.assertEqual(_overlay_config({'overlay': {'layout': 'bbc_live'}})['alignment'], 'left')


    def test_animation_moves_up_and_finishes_at_exact_target(self):
        from AppKit import NSApplication
        from live_caption.overlay import AppDelegate
        NSApplication.sharedApplication()
        d = AppDelegate.alloc().initWithSessionFactory_config_(lambda: None, {'overlay': {'layout': 'bbc_live'}})
        d._create_overlay()
        try:
            d._show_caption('Old first line\nRetained line')
            d._show_caption('Retained line\nNew line', 1)
            state = d.scroll_state
            self.assertIsNotNone(state)
            self.assertAlmostEqual(d.label.frame().origin.y, -d.line_height)
            d._advance_scroll(state['start'] + state['duration'] / 2)
            self.assertAlmostEqual(d.label.frame().origin.y, -d.line_height / 2, places=4)
            # Words added mid-scroll update the target without restarting time.
            d._show_caption('Retained line\nNew line grows')
            self.assertIs(d.scroll_state, state)
            d._advance_scroll(state['start'] + state['duration'] + .001)
            self.assertIsNone(d.scroll_state)
            self.assertEqual(d.label.stringValue(), 'Retained line\nNew line grows')
            self.assertEqual(d.label.frame(), d.scroll_view.bounds())
            self.assertEqual(d.label.maximumNumberOfLines(), 2)
            self.assertTrue(d.scroll_view.layer().masksToBounds())
        finally:
            d.panel.orderOut_(None)

    def test_disabled_animation_and_bursts_are_immediate(self):
        from AppKit import NSApplication
        from live_caption.overlay import AppDelegate
        NSApplication.sharedApplication()
        for duration in (0, 180):
            d = AppDelegate.alloc().initWithSessionFactory_config_(lambda: None,
                {'overlay': {'layout': 'bbc_live', 'scroll_duration_ms': duration}})
            d._create_overlay()
            try:
                d._show_caption('First\nSecond')
                d._show_caption('Second\nThird', 1)
                if not duration:
                    self.assertIsNone(d.scroll_state)
                    self.assertEqual(d.label.stringValue(), 'Second\nThird')
                else:
                    self.assertIsNotNone(d.scroll_state)
                    # A second scroll snaps to newest text, never queues it.
                    d._show_caption('Third\nFourth', 1)
                    self.assertIsNone(d.scroll_state)
                    self.assertEqual(d.label.stringValue(), 'Third\nFourth')
                    d._show_caption('Fourth\nFifth', 1)
                    d._finish_scroll()
                    self.assertIsNone(d.scroll_state)
                    self.assertEqual(d.label.stringValue(), 'Fourth\nFifth')
            finally:
                d.panel.orderOut_(None)

    def test_invalid_animation_duration_is_rejected(self):
        from live_caption.overlay import _overlay_config
        for value in (-1, 1001, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                _overlay_config({'overlay': {'scroll_duration_ms': value}})


@unittest.skipUnless(sys.platform == 'darwin', 'Native overlay requires macOS')
class NativeSpeakerTests(unittest.TestCase):
    def test_attributed_colours_scrolling_unicode_and_mode_switch(self):
        from unittest.mock import Mock
        from AppKit import NSApplication, NSForegroundColorAttributeName
        from live_caption.overlay import AppDelegate, _hex_color
        from live_caption.speakers import CaptionText
        NSApplication.sharedApplication()
        d = AppDelegate.alloc().initWithSessionFactory_config_(lambda: None, {
            'speechmatics': {'diarization': 'speaker'}, 'speakers': {'mode': 'speakers'},
            'overlay': {'layout': 'bbc_live'}})
        d._create_overlay()
        d.renderer = d._create_renderer()
        d.speaker_item = Mock()
        try:
            text = CaptionText('🎶Hello\nWorld', ['S1'] * 6 + [None] + ['S2'] * 5)
            d._show_caption(text)
            value = d.label.attributedStringValue()
            self.assertEqual(value.string(), text)
            # Foundation offsets use UTF-16, including the two-unit emoji.
            colour, span = value.attribute_atIndex_effectiveRange_(NSForegroundColorAttributeName, 8, None)
            self.assertEqual(colour, _hex_color('#FFE066'))
            incoming = CaptionText('World\nNext', ['S2'] * 5 + [None] + ['S3'] * 4)
            d._show_caption(incoming, 1)
            d._finish_scroll()
            colour, span = d.label.attributedStringValue().attribute_atIndex_effectiveRange_(
                NSForegroundColorAttributeName, 0, None)
            self.assertEqual(colour, _hex_color('#FFE066'))
            d.toggleSpeakerColours_(None)
            self.assertEqual(d.config['speakers']['mode'], 'white')
            self.assertFalse(d.renderer.speaker_mode)
            self.assertEqual(d.renderer.current_text(), '')
        finally:
            d.panel.orderOut_(None)
