from __future__ import annotations

import unittest

from phone_mirror_interaction_tool import PhoneMirrorAdvancedWindow, PhoneMirrorWindow


class _Value:
    def __init__(self, events: list, name: str) -> None:
        self.events = events
        self.name = name

    def set(self, value) -> None:
        self.events.append((self.name, value))


class _Stopper:
    def __init__(self, events: list, name: str) -> None:
        self.events = events
        self.name = name

    def stop(self) -> None:
        self.events.append(self.name)


class _Window:
    def __init__(self, events: list, name: str) -> None:
        self.events = events
        self.name = name

    def destroy(self) -> None:
        self.events.append(self.name)


class _Advanced:
    closed = False

    def __init__(self, events: list) -> None:
        self.events = events

    def shutdown(self, notify_parent: bool = False) -> None:
        self.events.append(("advanced-shutdown", notify_parent))
        self.closed = True


class PhoneMirrorCloseTests(unittest.TestCase):
    def test_main_close_stops_all_projection_and_destroys_controller(self):
        events = []
        controller = PhoneMirrorWindow.__new__(PhoneMirrorWindow)
        controller.closed = False
        controller.advanced_window = _Advanced(events)
        controller.window = _Window(events, "main-destroy")
        controller._save_projection_settings = lambda: events.append("save")
        controller.stop_projection = lambda: events.append("projection-stop")
        controller._stop_projection_dock_tracking = (
            lambda destroy=False: events.append(("dock-stop", destroy))
        )

        controller.close()
        controller.close()

        self.assertTrue(controller.closed)
        self.assertIsNone(controller.advanced_window)
        self.assertEqual(events, [
            "save",
            "projection-stop",
            ("dock-stop", True),
            ("advanced-shutdown", False),
            "main-destroy",
        ])

    def test_advanced_shutdown_releases_services_and_destroys_window(self):
        events = []
        controller = PhoneMirrorAdvancedWindow.__new__(PhoneMirrorAdvancedWindow)
        controller.closed = False
        controller.bridge_var = _Value(events, "bridge")
        controller.tap_bridge = _Stopper(events, "tap-stop")
        controller.fast_shell = _Stopper(events, "shell-stop")
        controller.stop_scrcpy = lambda: events.append("scrcpy-stop")
        controller.window = _Window(events, "advanced-destroy")
        controller.on_close = lambda: events.append("parent-callback")

        controller.shutdown(notify_parent=False)
        controller.shutdown(notify_parent=True)

        self.assertTrue(controller.closed)
        self.assertEqual(events, [
            ("bridge", False),
            "tap-stop",
            "shell-stop",
            "scrcpy-stop",
            "advanced-destroy",
        ])


if __name__ == "__main__":
    unittest.main()
