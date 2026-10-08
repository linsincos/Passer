from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import map_tool


class _Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class MapAsyncSelectionTests(unittest.TestCase):
    """Run workers and queued UI callbacks in a chosen order, without Tk/network."""

    def setUp(self):
        self.workers = []
        self.callbacks = []
        self.window = window = map_tool.MapWindow.__new__(map_tool.MapWindow)
        window.closed = False
        window.inspect_seq = 0
        window.center_lat = window.center_lon = 0.0
        window.zoom = 10
        window.marker = window.info_data = None
        window.search_history = []
        window.query = _Value()
        window.status = _Value()
        window.notice = ""
        window.window = SimpleNamespace(after=lambda _delay, callback: self.callbacks.append(callback))
        window.search_entry = SimpleNamespace(focus_set=mock.Mock())
        window.note_at = mock.Mock(return_value=None)
        for name in ("save_state", "schedule", "_refresh_info_layout", "hide_suggestions",
                     "_show_search_history", "_push_undo", "_update_control_states"):
            setattr(window, name, mock.Mock())
        patcher = mock.patch.object(map_tool.threading, "Thread", side_effect=self.queue_worker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def queue_worker(self, *, target, **_kwargs):
        self.workers.append(target)
        return SimpleNamespace(start=lambda: None)

    def finish_worker(self, index, response):
        with mock.patch.object(map_tool.urllib.request, "urlopen",
                               return_value=io.BytesIO(json.dumps(response).encode("utf-8"))):
            self.workers[index]()
        self.callbacks.pop(0)()

    def begin_search(self, query):
        self.window.query.set(query)
        self.window.search()

    @staticmethod
    def search_result(name, lat, lon):
        return [{"lat": str(lat), "lon": str(lon), "display_name": name}]

    def assert_place(self, name, lat, lon):
        window = self.window
        self.assertEqual(window.marker, (lat, lon, name))
        self.assertEqual(window.info_data["title"], name)
        self.assertEqual((window.info_data["lat"], window.info_data["lon"]), (lat, lon))

    def test_old_reverse_lookup_cannot_overwrite_a_search_result(self):
        self.window.inspect_point(1.0, 2.0)
        self.begin_search("地点 B")
        self.finish_worker(1, self.search_result("地点 B", 3.0, 4.0))
        self.finish_worker(0, {"name": "地点 A", "display_name": "地点 A 的地址"})

        self.assert_place("地点 B", 3.0, 4.0)
        self.assertEqual(self.window.info_data["address"], "地点 B")

    def test_latest_search_wins_for_both_old_success_and_old_failure(self):
        for old_response in (self.search_result("地点 A", 1.0, 2.0), []):
            with self.subTest(old_response=old_response):
                start = len(self.workers)
                self.begin_search("地点 A")
                self.begin_search("地点 B")
                self.finish_worker(start + 1, self.search_result("地点 B", 3.0, 4.0))
                self.window.status.set("地点 B 已显示")
                self.finish_worker(start, old_response)

                self.assert_place("地点 B", 3.0, 4.0)
                self.assertEqual(self.window.status.get(), "地点 B 已显示")
                self.assertEqual([row["name"] for row in self.window.search_history], ["地点 B"])

    def test_point_selection_supersedes_pending_search(self):
        self.begin_search("地点 A")
        self.window.inspect_point(3.0, 4.0)
        self.finish_worker(0, self.search_result("地点 A", 1.0, 2.0))
        self.finish_worker(1, {"name": "地点 B", "display_name": "地点 B 的地址"})

        self.assert_place("地点 B", 3.0, 4.0)
        self.assertEqual(self.window.info_data["address"], "地点 B 的地址")
        self.assertEqual(self.window.search_history, [])

    def test_coordinate_history_and_saved_place_supersede_pending_lookups(self):
        actions = (
            lambda: self.begin_search("3, 4"),
            lambda: self.window._use_suggestion(0),
            lambda: self.window.open_location(3.0, 4.0, title="地点 B"),
        )
        for action in actions:
            for pending in ("inspect", "search"):
                with self.subTest(action=action, pending=pending):
                    start = len(self.workers)
                    if pending == "inspect":
                        self.window.inspect_point(1.0, 2.0)
                        response = {"name": "地点 A", "display_name": "地点 A 的地址"}
                    else:
                        self.begin_search("地点 A")
                        response = self.search_result("地点 A", 1.0, 2.0)
                    self.window.suggest_rows = [("地点 B", 3.0, 4.0)]
                    action()
                    expected = dict(self.window.info_data)
                    self.finish_worker(start, response)

                    self.assertEqual(self.window.info_data, expected)
                    self.assertEqual(self.window.marker[:2], (3.0, 4.0))
                    self.assertEqual((self.window.center_lat, self.window.center_lon), (3.0, 4.0))

    def test_clear_and_measure_ignore_pending_search(self):
        for action in (self.window._clear_search, lambda: self.window.set_mode("measure")):
            with self.subTest(action=action):
                start = len(self.workers)
                self.begin_search("地点 A")
                action()
                self.window.status.set("当前操作")
                self.finish_worker(start, self.search_result("地点 A", 1.0, 2.0))

                self.assertIsNone(self.window.info_data)
                self.assertEqual(self.window.search_history, [])
                self.assertEqual(self.window.status.get(), "当前操作")

    def test_search_supersedes_pending_location_request(self):
        self.window.locate()
        self.begin_search("地点 B")
        self.finish_worker(1, self.search_result("地点 B", 3.0, 4.0))
        self.window.status.set("地点 B 已显示")
        with mock.patch.object(map_tool.subprocess, "run", return_value=SimpleNamespace(stdout="1,2")):
            self.workers[0]()
        self.callbacks.pop(0)()

        self.assert_place("地点 B", 3.0, 4.0)
        self.assertEqual(self.window.status.get(), "地点 B 已显示")

    def test_close_before_completion_does_not_remember_or_navigate(self):
        self.begin_search("地点 A")
        self.window.closed = True
        self.finish_worker(0, self.search_result("地点 A", 1.0, 2.0))

        self.assertIsNone(self.window.info_data)
        self.assertEqual(self.window.search_history, [])
        self.window.save_state.assert_not_called()
        self.window.schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
