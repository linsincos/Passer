from __future__ import annotations

import types
import unittest
from pathlib import Path
from unittest import mock

import Passer


class _Canvas:
    def __init__(self):
        self.cursor = ""
        self.focused = False
        self.rectangles: list[tuple] = []

    def configure(self, **kwargs):
        if "cursor" in kwargs:
            self.cursor = kwargs["cursor"]

    def focus_set(self):
        self.focused = True

    def create_rectangle(self, *coords, **kwargs):
        self.rectangles.append((coords, kwargs))


def _word(text: str, x: float, *, line: int = 0):
    return types.SimpleNamespace(text=text, x=x, y=10.0, w=30.0, h=20.0, line=line)


def _viewer():
    viewer = Passer.ImageViewer.__new__(Passer.ImageViewer)
    viewer.edit_mode = False
    viewer.crop_mode = False
    viewer.ocr_busy = False
    viewer.ocr_words = [_word("你好", 10.0), _word("Passer", 50.0)]
    viewer.ocr_sel_anchor = None
    viewer.ocr_sel_focus = None
    viewer.ocr_selecting = False
    viewer.ocr_hover_index = None
    viewer.pan_start = None
    viewer.image_center = (100.0, 80.0)
    viewer.canvas = _Canvas()
    viewer.window = mock.Mock()
    viewer.app = types.SimpleNamespace(focus_manager=mock.Mock())
    viewer.annotator = mock.Mock()
    viewer.render = mock.Mock()
    viewer._image_transform = lambda: (1.0, 0.0, 0.0)
    return viewer


class ImageViewerDirectOcrTests(unittest.TestCase):
    def test_text_can_be_drag_selected_without_entering_a_separate_mode(self):
        viewer = _viewer()

        viewer.on_press(types.SimpleNamespace(x=15, y=15))
        self.assertTrue(viewer.ocr_selecting)
        self.assertIsNone(viewer.pan_start)
        self.assertTrue(viewer.canvas.focused)
        viewer.app.focus_manager.claim.assert_called_once_with(viewer.window, viewer.canvas)

        viewer.on_drag(types.SimpleNamespace(x=60, y=15))
        viewer.on_release(types.SimpleNamespace(x=60, y=15))

        self.assertFalse(viewer.ocr_selecting)
        self.assertEqual(viewer._ocr_selected_range(), (0, 1))

    def test_dragging_blank_image_area_still_pans_and_clears_text_selection(self):
        viewer = _viewer()
        viewer.ocr_sel_anchor = 0
        viewer.ocr_sel_focus = 1

        viewer.on_press(types.SimpleNamespace(x=200, y=200))

        self.assertFalse(viewer.ocr_selecting)
        self.assertEqual(viewer.pan_start, (200, 200, 100.0, 80.0))
        self.assertIsNone(viewer._ocr_selected_range())

    def test_only_selected_words_are_drawn_over_the_original_image(self):
        viewer = _viewer()
        viewer.ocr_words.append(_word("第三个", 100.0))
        viewer.ocr_sel_anchor = 0
        viewer.ocr_sel_focus = 1

        viewer._draw_ocr_overlay()

        self.assertEqual(len(viewer.canvas.rectangles), 2)
        self.assertTrue(all(rect[1]["tags"] == "ocrov" for rect in viewer.canvas.rectangles))

    def test_copy_shortcut_requires_a_real_selection(self):
        viewer = _viewer()
        viewer._ocr_copy = mock.Mock()

        self.assertIsNone(viewer._ocr_copy_shortcut())
        viewer._ocr_copy.assert_not_called()

        viewer.ocr_sel_anchor = 0
        viewer.ocr_sel_focus = 0
        self.assertEqual(viewer._ocr_copy_shortcut(), "break")
        viewer._ocr_copy.assert_called_once_with(all_text=False)

    def test_viewer_starts_ocr_automatically_and_has_no_take_text_mode_button(self):
        source = Path(Passer.__file__).with_name("passer_viewers.py").read_text(encoding="utf-8")
        load_start = source.index("    def load_current(")
        load_end = source.index("    def _size_window_to_image(", load_start)
        load_block = source[load_start:load_end]

        self.assertIn("self._ensure_ocr_for_current_image()", load_block)
        self.assertIn('self.canvas.bind("<Control-c>", self._ocr_copy_shortcut)', source)
        self.assertIn('self.canvas.bind("<Control-C>", self._ocr_copy_shortcut)', source)
        self.assertNotIn("self.ocr_button", source)
        self.assertNotIn("self.ocr_mode", source)


if __name__ == "__main__":
    unittest.main()
