from __future__ import annotations

import unittest

from popup_manager import PopupManager, tk_geometry


class FakeRoot:
    def __init__(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.updated = False

    def update_idletasks(self) -> None:
        self.updated = True

    def winfo_rootx(self) -> int:
        return self.x

    def winfo_rooty(self) -> int:
        return self.y

    def winfo_width(self) -> int:
        return self.width

    def winfo_height(self) -> int:
        return self.height

    def winfo_screenwidth(self) -> int:
        return self.screen_width

    def winfo_screenheight(self) -> int:
        return self.screen_height


class PopupManagerMultiMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitors = [
            (True, 0, 0, 1920, 1040),
            (False, -1280, 0, 0, 1024),
            (False, 1920, -200, 3840, 880),
        ]
        self.manager = PopupManager(lambda: self.monitors)

    def test_center_over_root_keeps_popup_on_negative_secondary(self) -> None:
        root = FakeRoot(-1000, 100, 300, 300)

        self.assertEqual(
            self.manager.center_over_root(root, 600, 400, min_width=1, min_height=1),
            (-1150, 50),
        )

    def test_root_outside_uses_nearest_monitor(self) -> None:
        root = FakeRoot(-2500, 200, 200, 200)

        self.assertEqual(self.manager.root_work_area(root), (-1280, 0, 0, 1024))

    def test_point_outside_uses_nearest_monitor(self) -> None:
        self.assertEqual(self.manager.point_work_area(4500, 200), (1920, -200, 3840, 880))
        self.assertEqual(self.manager.point_work_area(-1400, 500), (-1280, 0, 0, 1024))

    def test_restore_saved_negative_secondary_position_when_visible(self) -> None:
        root = FakeRoot(100, 100, 600, 400)

        self.assertEqual(
            self.manager.restored_window_position(700, 500, root, -1200, 100),
            (-1200, 100),
        )

    def test_restore_offscreen_position_to_preferred_secondary(self) -> None:
        root = FakeRoot(100, 100, 600, 400)

        self.assertEqual(
            self.manager.restored_window_position(640, 480, root, 8000, 8000),
            (-960, 272),
        )

    def test_first_run_geometry_uses_secondary_half_size(self) -> None:
        root = FakeRoot(100, 100, 600, 400)

        self.assertEqual(
            self.manager.first_run_geometry(root, min_width=420, min_height=220),
            (640, 512, -960, 256),
        )

    def test_rect_intersection_across_monitors(self) -> None:
        self.assertTrue(self.manager.rect_intersects_any_work_area(-20, 20, 80, 80))
        self.assertFalse(self.manager.rect_intersects_any_work_area(5000, 5000, 120, 120))

    def test_available_size_uses_root_monitor(self) -> None:
        root = FakeRoot(2100, 100, 600, 400)

        self.assertEqual(
            self.manager.available_size_for_root(
                root,
                margin_x=48,
                margin_y=96,
                fallback_width=480,
                fallback_height=420,
            ),
            (1872, 984),
        )

    def test_empty_monitor_fallback_uses_root_screen(self) -> None:
        manager = PopupManager(lambda: [])
        root = FakeRoot(0, 0, 500, 300, screen_width=1366, screen_height=768)

        self.assertEqual(
            manager.available_size_for_root(root, margin_x=50, margin_y=80),
            (1316, 688),
        )

    def test_tk_geometry_formats_negative_coordinates(self) -> None:
        self.assertEqual(tk_geometry(800, 600, -1280, 40), "800x600-1280+40")


if __name__ == "__main__":
    unittest.main()

