from __future__ import annotations

import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

import Passer


def _item(
    item_id: str,
    filename: str,
    added_at: str,
    *,
    group_id: str = "group",
    group_order: int | None = None,
) -> Passer.DockItem:
    return Passer.DockItem(
        id=item_id,
        kind="file",
        target=rf"C:\Files\{filename}",
        title=filename,
        added_at=added_at,
        group_id=group_id,
        group_order=group_order,
    )


class GroupOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        self.group = Passer.DockItem("group", "group", "", "组", "2026-01-01T00:00:00")

    def test_legacy_group_keeps_joined_time_order(self) -> None:
        later = _item("later", "later.txt", "2026-01-03T00:00:00")
        earlier = _item("earlier", "earlier.txt", "2026-01-02T00:00:00")
        self.app.items = [self.group, later, earlier]

        self.assertEqual(
            [item.id for item in self.app.group_members("group")],
            ["earlier", "later"],
        )

    def test_drag_move_persists_gap_free_member_order(self) -> None:
        members = [
            _item("a", "a.txt", "2026-01-01T00:00:01"),
            _item("b", "b.txt", "2026-01-01T00:00:02"),
            _item("c", "c.txt", "2026-01-01T00:00:03"),
            _item("d", "d.txt", "2026-01-01T00:00:04"),
        ]
        self.app.items = [self.group, *members]

        self.assertTrue(self.app.move_group_member("group", "a", "c"))
        ordered = self.app.group_members("group")

        self.assertEqual([item.id for item in ordered], ["b", "c", "a", "d"])
        self.assertEqual([item.group_order for item in ordered], [0, 1, 2, 3])
        restored = Passer.normalize_item(asdict(ordered[2]))
        self.assertIsNotNone(restored)
        self.assertEqual(restored.group_order, 2)

    def test_filename_sort_is_case_insensitive_and_natural(self) -> None:
        members = [
            _item("ten", "File10.txt", "2026-01-01T00:00:01", group_order=0),
            _item("two", "file2.txt", "2026-01-01T00:00:02", group_order=1),
            _item("one", "FILE1.txt", "2026-01-01T00:00:03", group_order=2),
        ]
        members[0].passer_name = "最前面"
        self.app.items = [self.group, *members]

        self.assertTrue(self.app.sort_group_members_by_filename("group"))
        self.assertEqual(
            [item.id for item in self.app.group_members("group")],
            ["one", "two", "ten"],
        )

    def test_member_context_menu_has_copy_directly_below_open(self) -> None:
        overlay = Passer.GroupOverlay.__new__(Passer.GroupOverlay)
        overlay.win = object()
        overlay.app = mock.Mock()
        overlay._open_member = mock.Mock()
        overlay._eject = mock.Mock()
        overlay._remove = mock.Mock()
        member = _item("member", "member.txt", "2026-01-01T00:00:01")
        menus = []

        class FakeMenu:
            def __init__(self, *_args, **_kwargs):
                self.commands = []
                menus.append(self)

            def add_command(self, **kwargs):
                self.commands.append(kwargs)

            def tk_popup(self, _x, _y):
                return None

        with mock.patch.object(Passer.tk, "Menu", FakeMenu):
            overlay._popup_member_menu(SimpleNamespace(x_root=10, y_root=20), member)

        self.assertEqual([item["label"] for item in menus[0].commands[:2]], ["打开", "复制"])
        menus[0].commands[1]["command"]()
        overlay.app.copy_items_default.assert_called_once_with([member])


if __name__ == "__main__":
    unittest.main()
