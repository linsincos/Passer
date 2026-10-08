from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

import map_tool


class _Canvas:
    def __init__(self, width=500, height=300):
        self.width = width
        self.height = height

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height


class MapControlTests(unittest.TestCase):
    def test_map_controls_only_expose_search_measure_location_and_zoom(self):
        chrome_source = inspect.getsource(map_tool.MapWindow.build_chrome)
        control_source = inspect.getsource(map_tool.MapWindow.build_map_controls)
        self.assertIn("relx=0.5", chrome_source)
        self.assertIn("搜索地图", chrome_source)
        self.assertIn("justify=tk.LEFT", chrome_source)
        self.assertNotIn("self.canvas", chrome_source)
        self.assertIn("zoom_slider", control_source)
        self.assertIn("measure_button", control_source)
        self.assertIn("_build_measure_button", control_source)
        self.assertIn("_build_location_button", control_source)
        self.assertNotIn("controls=tk.Frame", control_source)
        self.assertNotIn('self.button(self.canvas,"测距"', control_source)
        for removed in ("保存地点", "加载地点", "route_button", "路线"):
            self.assertNotIn(removed, control_source)

    def test_map_search_suggestions_use_passer_panel_style(self):
        source = inspect.getsource(map_tool.MapWindow._ensure_suggest_box)
        render_source = inspect.getsource(map_tool.MapWindow.show_suggestions)
        chrome_source = inspect.getsource(map_tool.MapWindow.build_chrome)
        self.assertIn('bg="#111827"', source)
        self.assertIn('highlightbackground="#334155"', source)
        self.assertIn('activebackground="#1e3a5f"', render_source)
        self.assertNotIn("Listbox", source)
        self.assertIn('text="×"', chrome_source)
        self.assertIn("self._clear_search", chrome_source)
        self.assertIn("self.search_frame.winfo_rootx()", render_source)
        self.assertIn("self.search_frame.winfo_width()", render_source)
        self.assertNotIn("self.search_entry.winfo_width()", render_source)

    def test_empty_search_shows_up_to_ten_history_records(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.search_history = [
            {"name": f"地点 {index}", "lat": float(index), "lon": float(index)}
            for index in range(12)
        ]
        window._suggest_seq = 0
        shown = []
        window.show_suggestions = lambda rows, history=False: shown.append((list(rows), history))
        window.hide_suggestions = lambda: None

        window._show_search_history()

        self.assertEqual(len(shown[0][0]), 10)
        self.assertTrue(shown[0][1])

    def test_remember_search_deduplicates_and_caps_history(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.search_history = [
            {"name": f"旧地点 {index}", "lat": float(index), "lon": float(index)}
            for index in range(10)
        ]

        window._remember_search("更新后的地点", 5.0, 5.0)

        self.assertEqual(len(window.search_history), 10)
        self.assertEqual(window.search_history[0]["name"], "更新后的地点")
        self.assertEqual(sum(row["lat"] == 5.0 for row in window.search_history), 1)

    def test_clear_search_shows_history_and_keeps_focus(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window._suggest_after = None
        window.query = SimpleNamespace(set=lambda value: setattr(window, "cleared_to", value))
        window.search_entry = SimpleNamespace(focus_set=lambda: setattr(window, "focused", True))
        window._show_search_history = lambda: setattr(window, "history_shown", True)

        result = window._clear_search()

        self.assertEqual(result, "break")
        self.assertEqual(window.cleared_to, "")
        self.assertTrue(window.focused)
        self.assertTrue(window.history_shown)

    def test_zoom_slider_maps_full_track_to_supported_zoom_range(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.canvas = _Canvas()
        _width, start, end = window._slider_geometry()
        self.assertEqual(window._zoom_level_from_slider_x(start - 100), 2)
        self.assertEqual(window._zoom_level_from_slider_x(end + 100), 19)
        self.assertIn(window._zoom_level_from_slider_x((start + end) / 2), (10, 11))

    def test_measure_button_can_finish_an_active_measurement(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.mode = "measure"
        window.measure_points = [(0.0, 0.0), (0.0, 1.0)]
        window.status = SimpleNamespace(set=lambda value: setattr(window, "shown_status", value))
        window._update_control_states = lambda: setattr(window, "state_updated", True)
        window.schedule = lambda *args: setattr(window, "scheduled", True)

        window.toggle_measure()

        self.assertIsNone(window.mode)
        self.assertEqual(window.measure_points, [])
        self.assertIn("测距完成", window.notice)
        self.assertTrue(window.state_updated)
        self.assertTrue(window.scheduled)

    def test_right_click_finish_also_clears_measurement(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.mode = "measure"
        window.measure_points = [(0.0, 0.0), (0.0, 1.0)]
        window.status = SimpleNamespace(set=lambda value: None)
        window._update_control_states = lambda: None
        window.schedule = lambda *args: None

        window.show_map_menu(SimpleNamespace())

        self.assertIsNone(window.mode)
        self.assertEqual(window.measure_points, [])

    def test_measure_mode_invalidates_pending_reverse_geocode(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.inspect_seq = 7
        window.mode = None
        window.action_points = [(1.0, 1.0)]
        window.route_points = [(1.0, 1.0)]
        window.measure_points = [(1.0, 1.0)]
        window.info_data = {"title": "旧地点"}
        window.status = SimpleNamespace(set=lambda value: None)
        window.hide_suggestions = lambda: None
        window._push_undo = lambda: None
        window._update_control_states = lambda: None
        window.schedule = lambda *args: None

        window.set_mode("measure")

        self.assertEqual(window.inspect_seq, 8)
        self.assertIsNone(window.info_data)
        # The callback from the discarded selection must be harmless even if
        # it arrives after the mode switch.
        window.closed = False
        window.inspect_done(7, 1.0, 1.0, ("旧地点", "旧地址"))
        window.inspect_done(8, 1.0, 1.0, ("旧地点", "旧地址"))

    def test_place_card_uses_unlabelled_name_address_coordinates_in_that_order(self):
        lines = map_tool.MapWindow._info_lines({
            "title": "外滩",
            "address": "上海市黄浦区中山东一路",
            "lat": 31.24001,
            "lon": 121.49002,
            "note": "不应显示在地点卡中",
        })
        self.assertEqual(lines, [
            "外滩",
            "上海市黄浦区中山东一路",
            "31.240010, 121.490020",
        ])
        source = inspect.getsource(map_tool.MapWindow.draw_info_bubble)
        self.assertIn("anchor=tk.NW", source)
        self.assertNotIn("rely=1.0", source)
        self.assertIn('"place_title"', source)
        self.assertIn('"place_detail"', source)

    def test_place_card_counts_the_final_wrapped_display_line(self):
        # Tk Text.count(displaylines) reports the span between indices.  The
        # current final display line must be added explicitly.
        self.assertEqual(map_tool.MapWindow._display_line_total((3,),3),4)
        self.assertEqual(map_tool.MapWindow._display_line_total((2,),3),3)

    def test_completed_address_query_forces_card_to_remeasure(self):
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.closed = False
        window.inspect_seq = 4
        window.info_data = {"title": "选中位置", "address": "正在查询地址…"}
        window.marker = None
        window.notice = ""
        window.status = SimpleNamespace(set=lambda value: None)
        window.save_state = lambda: None
        window.schedule = lambda *args: None
        redraws = []
        window.draw_info_bubble = lambda: redraws.append(window.info_data["address"])
        window.window = SimpleNamespace(after_idle=lambda callback: callback())

        long_address = "东京都台东区上野公园一段很长的最终地址"
        window.inspect_done(4, 35.0, 139.0, ("上野动物园", long_address))

        self.assertEqual(redraws, [long_address, long_address])

    def test_copy_selected_place_text_to_clipboard(self):
        copied = []
        window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.info_bubble = SimpleNamespace(get=lambda start, end: "上野动物园\n台东区")
        window.window = SimpleNamespace(
            clipboard_clear=lambda: copied.clear(),
            clipboard_append=lambda value: copied.append(value),
            update_idletasks=lambda: None,
        )

        result = window.copy_info_selection()

        self.assertEqual(result, "break")
        self.assertEqual(copied, ["上野动物园\n台东区"])


if __name__ == "__main__":
    unittest.main()
