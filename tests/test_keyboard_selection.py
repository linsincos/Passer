from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import Passer


class _Widget:
    def __init__(self, widget_class: str, master=None) -> None:
        self.widget_class = widget_class
        self.master = master

    def winfo_class(self) -> str:
        return self.widget_class


def _item(item_id: str, col: int, row: int, *, group_id=None) -> Passer.DockItem:
    return Passer.DockItem(
        item_id,
        "file",
        f"D:/{item_id}.txt",
        f"{item_id}.txt",
        "",
        grid_x=col,
        grid_y=row,
        group_id=group_id,
    )


class KeyboardSelectionTests(unittest.TestCase):
    def make_app(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.items = [
            _item("current", 0, 0),
            _item("right-aligned", 3, 0),
            _item("right-diagonal", 1, 1),
            _item("down-aligned", 0, 2),
            _item("group-member", 0, 1, group_id="some-group"),
        ]
        app.selected_ids = {"current"}
        app.anchor_selected_id = "current"
        app.group_overlay = None
        app.update_selection_styles = mock.Mock()
        app.scroll_item_into_view = mock.Mock()
        return app

    def test_arrow_keys_choose_the_best_aligned_top_level_tile(self):
        app = self.make_app()
        event = SimpleNamespace(widget=_Widget("Canvas"), state=0)

        result = app.navigate_selected_item(event, 1, 0)

        self.assertEqual(result, "break")
        self.assertEqual(app.selected_ids, {"right-aligned"})
        self.assertEqual(app.anchor_selected_id, "right-aligned")
        app.update_selection_styles.assert_called_once_with(check_broken=False)
        app.scroll_item_into_view.assert_called_once_with(app.items[1])

        app.selected_ids = {"current"}
        app.anchor_selected_id = "current"
        app.update_selection_styles.reset_mock()
        app.scroll_item_into_view.reset_mock()

        app.navigate_selected_item(event, 0, 1)

        self.assertEqual(app.selected_ids, {"down-aligned"})
        app.scroll_item_into_view.assert_called_once_with(app.items[3])

    def test_text_controls_and_modified_arrows_keep_native_behavior(self):
        app = self.make_app()
        input_event = SimpleNamespace(widget=_Widget("Entry"), state=0)

        self.assertIsNone(app.navigate_selected_item(input_event, 1, 0))
        self.assertEqual(app.selected_ids, {"current"})

        canvas_event = SimpleNamespace(widget=_Widget("Canvas"), state=Passer.CTRL_MASK)
        self.assertIsNone(app.navigate_selected_item(canvas_event, 1, 0))
        self.assertEqual(app.selected_ids, {"current"})
        app.update_selection_styles.assert_not_called()

    def test_navigation_requires_exactly_one_main_grid_selection(self):
        app = self.make_app()
        event = SimpleNamespace(widget=_Widget("Canvas"), state=0)
        app.selected_ids = {"current", "right-aligned"}

        self.assertIsNone(app.navigate_selected_item(event, 1, 0))
        self.assertEqual(app.selected_ids, {"current", "right-aligned"})

        app.selected_ids = {"group-member"}
        self.assertIsNone(app.navigate_selected_item(event, 0, 1))
        self.assertEqual(app.selected_ids, {"group-member"})


if __name__ == "__main__":
    unittest.main()
