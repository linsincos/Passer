from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import Passer
from aira_tool import (
    AiraUsageReminder,
    USAGE_IDLE_RESET_SECONDS,
    USAGE_REMIND_AFTER_SECONDS,
)


class _ImmediateRoot:
    def after(self, _delay, callback):
        callback()
        return "after-id"


class _FakeApp:
    def __init__(self, mode: str = "passer") -> None:
        self.root = _ImmediateRoot()
        self.settings = {"aira_usage_notify_mode": mode}
        self.aira_window = None
        self.notifications: list[tuple[str, str, str]] = []
        self.unexpected: list[tuple[BaseException, dict]] = []

    def notify_aira_usage_reminder(self, title: str, message: str, mode: str) -> None:
        self.notifications.append((title, message, mode))

    def _log_unexpected(self, exc: BaseException, **context) -> None:
        self.unexpected.append((exc, context))


class AiraUsageReminderTests(unittest.TestCase):
    def test_reminds_once_after_an_hour_and_resets_after_a_real_break(self):
        app = _FakeApp("passer")
        with tempfile.TemporaryDirectory() as temporary:
            reminder = AiraUsageReminder(app, Path(temporary))
            reminder.record_sample(USAGE_REMIND_AFTER_SECONDS - 1, 0)
            self.assertEqual(app.notifications, [])

            state = reminder.record_sample(2, 0)
            self.assertEqual(len(app.notifications), 1)
            self.assertEqual(app.notifications[0][2], "passer")
            self.assertIn("连续使用电脑", app.notifications[0][1])
            self.assertGreaterEqual(state["continuous_seconds"], 3600)

            reminder.record_sample(1200, 0)
            self.assertEqual(len(app.notifications), 1)
            state = reminder.record_sample(1, USAGE_IDLE_RESET_SECONDS)
            self.assertEqual(state["continuous_seconds"], 0)

            reminder.record_sample(USAGE_REMIND_AFTER_SECONDS, 0)
            self.assertEqual(len(app.notifications), 2)

    def test_passer_and_windows_notification_routes_are_both_supported(self):
        class Chat:
            _visible = True

            def __init__(self):
                self.reminders: list[str] = []

            def show_reminder(self, text: str) -> None:
                self.reminders.append(text)

        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.ai_chat = Chat()
        app.notify_aira_usage_reminder("Aira 使用时长提醒", "请休息。", "passer")
        self.assertEqual(app.ai_chat.reminders, ["Aira 使用时长提醒\n请休息。"])

        windows_notifications: list[tuple[str, str]] = []
        original_notify = Passer.notify_windows
        Passer.notify_windows = lambda title, message: windows_notifications.append((title, message))
        try:
            app.notify_aira_usage_reminder("Aira 使用时长提醒", "请休息。", "windows")
        finally:
            Passer.notify_windows = original_notify
        self.assertEqual(windows_notifications, [("Aira 使用时长提醒", "请休息。")])

    def test_notification_mode_is_independent_and_stats_survive_a_short_restart(self):
        app = _FakeApp("windows")
        now = [datetime(2026, 7, 15, 9, 0, 0)]
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            reminder = AiraUsageReminder(app, data_dir, wall_clock=lambda: now[0])
            reminder.start()
            reminder.record_sample(900, 0)
            reminder.close()

            now[0] += timedelta(minutes=1)
            restored = AiraUsageReminder(app, data_dir, wall_clock=lambda: now[0])
            state = restored.snapshot()
            self.assertGreaterEqual(state["today_seconds"], 900)
            self.assertGreaterEqual(state["continuous_seconds"], 900)
            self.assertIn("不记录应用", state["privacy"])

            now[0] += timedelta(minutes=6)
            after_break = AiraUsageReminder(app, data_dir, wall_clock=lambda: now[0])
            state = after_break.snapshot()
            self.assertGreaterEqual(state["today_seconds"], 900)
            self.assertEqual(state["continuous_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
