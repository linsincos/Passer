from __future__ import annotations

import tempfile
import unittest
import stat
from datetime import datetime
from pathlib import Path
from unittest import mock

import Passer


class VersionNamingTests(unittest.TestCase):
    def test_time_version_and_custom_names(self):
        source = Path("报告.txt")
        stamp = datetime(2026, 8, 13, 9, 30, 45)

        filename, label = Passer.build_history_version_name(
            source, "time", "", 1, now=stamp
        )
        self.assertEqual(filename, "报告_20260813_093045.txt")
        self.assertEqual(label, "2026-08-13 09:30:45")

        filename, label = Passer.build_history_version_name(
            source, "version", "", 3, now=stamp
        )
        self.assertEqual(filename, "报告_v3.txt")
        self.assertEqual(label, "v3")

        filename, label = Passer.build_history_version_name(
            source, "custom", "项目_{date}_第{version}版{ext}", 4, now=stamp
        )
        self.assertEqual(filename, "项目_20260813_第4版.txt")
        self.assertEqual(label, "项目_20260813_第4版")

    def test_custom_name_rejects_unknown_placeholder(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            Passer.validate_history_naming_pattern("{stem}_{secret}{ext}")

    def test_settings_history_naming_uses_passer_select_style(self):
        source = Path(Passer.__file__).read_text(encoding="utf-8")
        start = source.index("history_row = tk.Frame(gen")
        end = source.index("# ================= Aira 模型", start)
        history_block = source[start:end]

        self.assertIn("history_mode_select = make_select(", history_block)
        self.assertNotIn("ttk.Combobox(", history_block)
        self.assertIn("history_row.pack(fill=tk.X, pady=(14, 0))", history_block)
        self.assertIn("width=210,", history_block)
        self.assertIn("height=42,", history_block)
        self.assertIn('history_mode_local.trace_add("write"', history_block)

    def test_history_submenu_places_version_manager_first(self):
        class FakeMenu:
            def __init__(self):
                self.entries = []
                self.states = {}

            def delete(self, *_args):
                self.entries.clear()

            def add_command(self, **kwargs):
                self.entries.append(("command", kwargs))

            def add_separator(self):
                self.entries.append(("separator", {}))

            def entryconfig(self, label, **kwargs):
                self.states[label] = kwargs

        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.history_menu = FakeMenu()
        app.menu = FakeMenu()
        app.history_menu_label = "历史版本"
        app.open_version_manager = mock.Mock()
        app.version_history_records = mock.Mock(return_value=[])
        item = Passer.DockItem("item", "file", "D:/demo.txt", "demo.txt", "")

        app.rebuild_history_menu(item, [item])

        self.assertEqual(app.history_menu.entries[0][1]["label"], "版本管理")
        self.assertEqual(app.history_menu.entries[1][0], "separator")
        self.assertEqual(app.history_menu.entries[2][1]["label"], "暂无历史版本")
        self.assertEqual(app.menu.states["历史版本"]["state"], Passer.tk.NORMAL)
        app.history_menu.entries[0][1]["command"]()
        app.open_version_manager.assert_called_once_with(item)

    def test_version_manager_uses_passer_frameless_window(self):
        source = Path(Passer.__file__).with_name("passer_viewers.py").read_text(encoding="utf-8")
        start = source.index("class VersionHistoryManagerWindow(")
        end = source.index("class ShellPreviewHandlerViewer(", start)
        manager_block = source[start:end]

        self.assertIn("VersionHistoryManagerWindow(_FramelessViewer)", manager_block)
        self.assertIn("self._build_frame(app", manager_block)
        self.assertIn('"保存当前为新版本"', manager_block)
        self.assertNotIn('"显示位置"', manager_block)
        self.assertIn('"删除"', manager_block)

    def test_frameless_window_title_text_is_a_drag_handle(self):
        source = Path(Passer.__file__).with_name("passer_viewers.py").read_text(encoding="utf-8")
        start = source.index("class _FramelessViewer:")
        end = source.index("class VersionHistoryManagerWindow(", start)
        frame_block = source[start:end]

        self.assertIn("self.info_label = tk.Label(", frame_block)
        self.assertIn(
            "for widget in (self.toolbar, self.toolbar_left, self.info_label):",
            frame_block,
        )
        self.assertIn('widget.bind("<B1-Motion>", self.do_move)', frame_block)


class VersionHistoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_data_dir = Passer.DATA_DIR
        Passer.DATA_DIR = Path(self.temporary.name) / "PasserData"

    def tearDown(self):
        Passer.DATA_DIR = self.old_data_dir
        self.temporary.cleanup()

    def test_history_index_round_trip_and_path_guard(self):
        version = Passer.history_item_directory("item-1") / "文档_v1.txt"
        version.parent.mkdir(parents=True)
        version.write_text("one", encoding="utf-8")
        history = {
            "item-1": [{
                "id": "record-1",
                "label": "v1",
                "created_at": "2026-08-13T09:30:00",
                "path": Passer.history_relative_path(version),
                "source_target": "D:/文档.txt",
                "version": 1,
            }]
        }
        Passer.save_version_history(history)
        loaded = Passer.load_version_history()
        self.assertEqual(loaded["item-1"][0]["label"], "v1")
        self.assertEqual(Passer.history_record_path(loaded["item-1"][0]), version.resolve())
        self.assertIsNone(Passer.history_record_path({"path": "../outside.txt"}))

    def test_app_creates_incrementing_snapshots_without_overwriting_source(self):
        source = Path(self.temporary.name) / "source.txt"
        source.write_text("original", encoding="utf-8")
        source.chmod(stat.S_IREAD)
        item = Passer.DockItem("item-2", "text", str(source), source.name, "")
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.items = [item]
        app.version_history = {}
        app.history_naming_mode = Passer.HISTORY_NAMING_VERSION
        app.history_naming_pattern = Passer.DEFAULT_HISTORY_NAMING_PATTERN
        app.tile_signatures = {}
        app.root = None
        app.schedule_render = lambda: None
        app.write_status = lambda _message: None

        try:
            first = app.save_item_as_new_version(item)
            self.assertIsNotNone(first)
            self.assertEqual(first.read_text(encoding="utf-8"), "original")
            self.assertTrue(first.stat().st_mode & stat.S_IWRITE)
            self.assertFalse(source.stat().st_mode & stat.S_IWRITE)
            self.assertEqual(source.read_text(encoding="utf-8"), "original")

            second = app.save_item_as_new_version(
                item,
                writer=lambda destination: destination.write_text("edited", encoding="utf-8"),
            )
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(second.read_text(encoding="utf-8"), "edited")
            self.assertEqual([record["version"] for record in app.version_history[item.id]], [1, 2])
        finally:
            source.chmod(stat.S_IWRITE)

    def test_version_manager_deletes_only_the_chosen_history_version(self):
        item = Passer.DockItem("item-3", "text", "D:/source.txt", "source.txt", "")
        first = Passer.history_item_directory(item.id) / "source_v1.txt"
        second = Passer.history_item_directory(item.id) / "source_v2.txt"
        first.parent.mkdir(parents=True)
        first.write_text("one", encoding="utf-8")
        first.chmod(stat.S_IREAD)
        second.write_text("two", encoding="utf-8")
        records = [
            {
                "id": "v1",
                "label": "v1",
                "path": Passer.history_relative_path(first),
                "version": 1,
            },
            {
                "id": "v2",
                "label": "v2",
                "path": Passer.history_relative_path(second),
                "version": 2,
            },
        ]
        app = mock.Mock()
        app.version_history = {item.id: list(records)}
        app.tile_signatures = {item.id: "cached"}
        manager = Passer.VersionHistoryManagerWindow.__new__(Passer.VersionHistoryManagerWindow)
        manager.app = app
        manager.item = item
        manager.window = object()
        manager.refresh = mock.Mock()

        with mock.patch.object(Passer.messagebox, "askyesno", return_value=True):
            manager.delete_record(records[0])

        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual([entry["id"] for entry in app.version_history[item.id]], ["v2"])
        self.assertNotIn(item.id, app.tile_signatures)
        manager.refresh.assert_called_once_with()


class ClipboardToolRegistrationTests(unittest.TestCase):
    def test_clipboard_is_registered_and_packaged(self):
        tool = Passer.BUILTIN_TOOL_BY_TARGET[Passer.BUILTIN_CLIPBOARD_TARGET]
        self.assertEqual(tool["title"], "剪贴板 Clipboard")
        spec = Path(Passer.__file__).with_name("Passer.spec").read_text(encoding="utf-8")
        self.assertIn('"clipboard_tool"', spec)

    def test_file_writeback_uses_passer_native_clipboard_bridge(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        with mock.patch.object(Passer, "copy_paths_to_clipboard", return_value=True) as writer:
            self.assertTrue(app.write_files_to_clipboard([Path("D:/one.txt")]))
        writer.assert_called_once_with(["D:\\one.txt"])


if __name__ == "__main__":
    unittest.main()
