from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import Passer
import file_share_tool


class _RecordingSocket:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.sent_json: list[dict] = []

    def sendall(self, payload: bytes) -> None:
        if payload.endswith(b"\n"):
            self.sent_json.append(json.loads(payload.decode("utf-8")))
        self.sock.sendall(payload)

    def __getattr__(self, name):
        return getattr(self.sock, name)


class _DummyRoot:
    def __init__(self):
        self.destroyed = False
        self.cancelled: list[object] = []

    def after(self, _delay, callback):
        callback()

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def destroy(self):
        self.destroyed = True


class _QueuedRoot:
    def __init__(self):
        self.callbacks: list[object] = []

    def after(self, _delay, callback):
        self.callbacks.append(callback)
        return f"after-{len(self.callbacks)}"

    def after_idle(self, callback):
        self.callbacks.append(callback)
        return f"idle-{len(self.callbacks)}"


class _DummyApp:
    def __init__(self, store_dir: Path):
        self.root = _DummyRoot()
        self.store_dir = store_dir
        self.file_share_window = None
        self.status: list[str] = []

    def write_status(self, message: str) -> None:
        self.status.append(message)


class AutomationPersistenceTests(unittest.TestCase):
    def test_runtime_progress_is_never_persisted_and_stale_flags_are_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "automations.json"
            task = {
                "id": "task-1",
                "title": "测试任务",
                "prompt": "完成测试",
                "mode": "daily",
                "_running": True,
                "_run_stage": "正在执行",
                "_run_preview": "临时进度",
            }
            with mock.patch.object(Passer, "AUTOMATIONS_FILE", path):
                Passer.save_automations([task])
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("_running", saved[0])
                self.assertNotIn("_run_preview", saved[0])

                saved[0]["_running"] = True
                saved[0]["_run_stage"] = "异常退出前状态"
                path.write_text(
                    json.dumps(saved, ensure_ascii=False),
                    encoding="utf-8",
                )
                loaded = Passer.load_automations()
                self.assertNotIn("_running", loaded[0])
                self.assertNotIn("_run_stage", loaded[0])


class FileShareHardeningTests(unittest.TestCase):
    def test_v3_challenge_auth_and_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = b"Passer secure transfer\x00\x01"
            source = root / "sample.bin"
            source.write_bytes(payload)

            service = file_share_tool.FileShareService(_DummyApp(root))
            service.code = "123456"
            service.shares = [str(source)]
            service._serving = True

            server_sock, client_sock = socket.socketpair()
            worker = threading.Thread(
                target=service._handle_client,
                args=(server_sock, ("127.0.0.2", 12345)),
                daemon=True,
            )
            worker.start()
            recorded = _RecordingSocket(client_sock)
            try:
                self.assertTrue(file_share_tool._authenticate_client_v3(recorded, "123456"))
                self.assertTrue(all("code" not in message for message in recorded.sent_json))
                header = json.loads(file_share_tool._recv_line(recorded).decode("utf-8"))
                self.assertTrue(header["ok"])
                self.assertEqual(header["protocol"], file_share_tool.TRANSFER_PROTOCOL)
                file_share_tool._send_json(recorded, {"offset": 0})
                ack = json.loads(file_share_tool._recv_line(recorded).decode("utf-8"))
                self.assertTrue(ack["ok"])
                received = bytearray()
                while len(received) < len(payload):
                    chunk = recorded.recv(len(payload) - len(received))
                    self.assertTrue(chunk)
                    received.extend(chunk)
                self.assertEqual(bytes(received), payload)
            finally:
                client_sock.close()
                worker.join(timeout=3)
            self.assertFalse(worker.is_alive())

    def test_auth_failures_are_rate_limited_and_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            service = file_share_tool.FileShareService(_DummyApp(Path(folder)))
            blocked_for = 0
            for _ in range(file_share_tool.AUTH_MAX_FAILURES):
                blocked_for = service._record_auth_failure("192.0.2.10")
            self.assertGreaterEqual(blocked_for, 1)
            self.assertGreaterEqual(service._auth_retry_after("192.0.2.10"), 1)
            service._clear_auth_failures("192.0.2.10")
            self.assertEqual(service._auth_retry_after("192.0.2.10"), 0)

    def test_transfer_code_accepts_legacy_and_stronger_lengths(self):
        self.assertTrue(file_share_tool._valid_transfer_code("1234"))
        self.assertTrue(file_share_tool._valid_transfer_code("12345678"))
        self.assertFalse(file_share_tool._valid_transfer_code("123"))
        self.assertFalse(file_share_tool._valid_transfer_code("12ab56"))


class SettingsHardeningTests(unittest.TestCase):
    def setUp(self):
        self.old_settings_file = Passer.SETTINGS_FILE
        self.old_data_dir = Passer.DATA_DIR

    def tearDown(self):
        Passer.SETTINGS_FILE = self.old_settings_file
        Passer.DATA_DIR = self.old_data_dir

    def test_malformed_values_fall_back_without_startup_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Passer.DATA_DIR = root
            Passer.SETTINGS_FILE = root / "settings.json"
            Passer.SETTINGS_FILE.write_text(json.dumps({
                "width": "not-a-number",
                "height": -100,
                "transparent_alpha": "NaN",
                "recent_search_items": None,
                "disabled_builtin_tools": None,
            }), encoding="utf-8")
            settings = Passer.load_settings()
            self.assertEqual(settings["width"], Passer.DEFAULT_WIDTH)
            self.assertEqual(settings["height"], Passer.MIN_HEIGHT)
            self.assertEqual(settings["transparent_alpha"], Passer.TRANSPARENT_ALPHA)
            self.assertEqual(settings["recent_search_items"], [])
            self.assertEqual(settings["disabled_builtin_tools"], [])

    def test_corrupt_settings_are_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Passer.DATA_DIR = root
            Passer.SETTINGS_FILE = root / "settings.json"
            Passer.SETTINGS_FILE.write_text("{not-json", encoding="utf-8")
            settings = Passer.load_settings()
            self.assertEqual(settings["width"], Passer.DEFAULT_WIDTH)
            self.assertFalse(Passer.SETTINGS_FILE.exists())
            self.assertEqual(len(list(root.glob("settings.broken_*.json"))), 1)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_transfer_code_is_persisted_with_dpapi(self):
        class Root:
            def winfo_width(self): return 900
            def winfo_height(self): return 600
            def winfo_x(self): return 10
            def winfo_y(self): return 20

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Passer.DATA_DIR = root
            Passer.SETTINGS_FILE = root / "settings.json"
            Passer.save_settings(Root(), True, file_share_code="123456")
            raw = json.loads(Passer.SETTINGS_FILE.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], Passer.SETTINGS_SCHEMA_VERSION)
            self.assertTrue(raw["file_share_code"].startswith("dpapi:"))
            self.assertEqual(Passer.load_settings()["file_share_code"], "123456")

    def test_aira_usage_reminder_settings_round_trip(self):
        class Root:
            def winfo_width(self): return 900
            def winfo_height(self): return 600
            def winfo_x(self): return 10
            def winfo_y(self): return 20

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Passer.DATA_DIR = root
            Passer.SETTINGS_FILE = root / "settings.json"
            Passer.save_settings(
                Root(),
                True,
                aira_usage_reminder_enabled=True,
                aira_usage_notify_mode="passer",
                aira_font_size="特大",
                aira_line_spacing="宽松",
                aira_mobile_enabled=True,
                openclaw_enabled=True,
            )
            raw = json.loads(Passer.SETTINGS_FILE.read_text(encoding="utf-8"))
            self.assertTrue(raw["aira_usage_reminder_enabled"])
            self.assertEqual(raw["aira_usage_notify_mode"], "passer")
            self.assertEqual(raw["aira_font_size"], "特大")
            self.assertEqual(raw["aira_line_spacing"], "宽松")
            self.assertTrue(raw["aira_mobile_enabled"])
            self.assertTrue(raw["openclaw_enabled"])
            settings = Passer.load_settings()
            self.assertTrue(settings["aira_usage_reminder_enabled"])
            self.assertEqual(settings["aira_usage_notify_mode"], "passer")
            self.assertEqual(settings["aira_font_size"], "特大")
            self.assertEqual(settings["aira_line_spacing"], "宽松")
            self.assertTrue(settings["aira_mobile_enabled"])
            self.assertTrue(settings["openclaw_enabled"])

    def test_external_interface_defaults_on_and_requires_aira(self):
        class Root:
            def winfo_width(self): return 900
            def winfo_height(self): return 600
            def winfo_x(self): return 10
            def winfo_y(self): return 20

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Passer.DATA_DIR = root
            Passer.SETTINGS_FILE = root / "settings.json"

            Passer.SETTINGS_FILE.write_text(
                json.dumps({"ai_enabled": True}), encoding="utf-8"
            )
            self.assertTrue(
                Passer.load_settings()["ai_external_interface_enabled"]
            )

            Passer.SETTINGS_FILE.write_text(
                json.dumps({
                    "ai_enabled": False,
                    "ai_external_interface_enabled": True,
                }),
                encoding="utf-8",
            )
            self.assertFalse(
                Passer.load_settings()["ai_external_interface_enabled"]
            )

            Passer.save_settings(
                Root(),
                True,
                ai_enabled=True,
                ai_external_interface_enabled=False,
            )
            self.assertFalse(
                Passer.load_settings()["ai_external_interface_enabled"]
            )

            Passer.save_settings(
                Root(),
                True,
                ai_enabled=False,
                ai_external_interface_enabled=True,
            )
            raw = json.loads(Passer.SETTINGS_FILE.read_text(encoding="utf-8"))
            self.assertFalse(raw["ai_external_interface_enabled"])

    def test_external_interface_row_is_directly_below_aira(self):
        source = Path(Passer.__file__).read_text(encoding="utf-8")
        aira_row = source.index('text="启用 Aira"')
        external_row = source.index('text="启用外置接口"')
        prompt_cache_row = source.index('text="提示缓存"')
        self.assertLess(aira_row, external_row)
        self.assertLess(external_row, prompt_cache_row)

    def test_json_transaction_rolls_back_every_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text('{"old": 1}', encoding="utf-8")
            second.write_text('{"old": 2}', encoding="utf-8")
            real_replace = Passer.os.replace

            def failing_replace(source, destination):
                if str(source).endswith(".stage") and Path(destination) == second:
                    raise OSError("simulated second install failure")
                return real_replace(source, destination)

            Passer.os.replace = failing_replace
            try:
                with self.assertRaises(OSError):
                    Passer.write_json_transaction({
                        first: {"new": 1},
                        second: {"new": 2},
                    })
            finally:
                Passer.os.replace = real_replace
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), {"old": 1})
            self.assertEqual(json.loads(second.read_text(encoding="utf-8")), {"old": 2})


class ExceptionLoggingTests(unittest.TestCase):
    def test_expected_errors_stay_out_of_crash_log_and_unexpected_errors_include_context(self):
        old_data_dir = Passer.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as folder:
                Passer.DATA_DIR = Path(folder)
                expected = OSError("file is temporarily unavailable")
                written = Passer.log_unexpected_exception(
                    expected,
                    module="items.open",
                    action="open:file",
                    target_path=r"C:\missing\demo.txt",
                    expected=(OSError,),
                )
                self.assertFalse(written)
                self.assertFalse(Passer.crash_log_path().exists())

                try:
                    _ = 1 / 0
                except ZeroDivisionError as unexpected:
                    written = Passer.log_unexpected_exception(
                        unexpected,
                        module="items.open",
                        action="open:file",
                        target_path=r"C:\work\demo.txt",
                        expected=(OSError,),
                    )
                self.assertTrue(written)
                log = Passer.crash_log_path().read_text(encoding="utf-8")
                self.assertIn("module: items.open", log)
                self.assertIn("action: open:file", log)
                self.assertIn(r"target_path: C:\work\demo.txt", log)
                self.assertIn("exception: ZeroDivisionError", log)
        finally:
            Passer.DATA_DIR = old_data_dir


class DeferredStartupTests(unittest.TestCase):
    def test_registry_discovery_is_published_after_startup(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._closing = False
        app.zotero_path = None
        app.zotero_menu_label = None

        class _Var:
            value = False

            def set(self, value):
                self.value = bool(value)

        app.autostart_var = _Var()
        app._publish_deferred_system_state(r"C:\Apps\Zotero\zotero.exe", True)

        self.assertEqual(app.zotero_path, r"C:\Apps\Zotero\zotero.exe")
        self.assertEqual(app.zotero_menu_label, "Zotero 打开")
        self.assertTrue(app.autostart_var.value)

    def test_expensive_ui_startup_steps_are_split_across_event_loop_turns(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._closing = False
        app.root = _QueuedRoot()
        app.data_dir = Path("startup-data")
        app._deferred_startup_completed = False
        events: list[str] = []
        app.apply_ai_settings = lambda force_show=False: events.append("aira")
        app.reload_mods_runtime = lambda render=True: events.append("mods")
        app._install_deferred_startup_integrations = lambda: events.append("integrations")
        app.emit_mod_event = lambda event, payload=None: events.append(event)

        app._run_deferred_startup_ui_step(0)
        self.assertEqual(events, ["aira"])
        self.assertEqual(len(app.root.callbacks), 1)
        app.root.callbacks.pop(0)()
        self.assertEqual(events, ["aira", "mods"])
        app.root.callbacks.pop(0)()
        self.assertEqual(events, ["aira", "mods", "integrations"])
        app.root.callbacks.pop(0)()
        self.assertEqual(events, ["aira", "mods", "integrations", "app_ready"])
        self.assertTrue(app._deferred_startup_completed)


class PackagingHardeningTests(unittest.TestCase):
    def test_spec_supports_fixed_runtime_and_single_file_release(self):
        spec = Path(Passer.__file__).with_name("Passer.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn('PASSER_ONEFILE', spec)
        self.assertIn('a.zipfiles', spec)
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn('contents_directory="PasserRuntime"', spec)
        self.assertIn("coll = COLLECT(", spec)
        self.assertIn('name="Passer"', spec)


class ResponsivenessTests(unittest.TestCase):
    @staticmethod
    def _item(target: str = r"C:\Apps\demo.exe") -> Passer.DockItem:
        return Passer.DockItem(
            id="demo",
            kind="app",
            target=target,
            title="Demo",
            added_at="2026-07-15T00:00:00",
        )

    def test_external_launch_returns_without_waiting_for_slow_shell_work(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._closing = False
        app.root = _QueuedRoot()
        statuses: list[str] = []
        app.write_status = statuses.append
        started = threading.Event()
        release = threading.Event()
        worker_ids: list[int] = []

        def slow_launch():
            worker_ids.append(threading.get_ident())
            started.set()
            release.wait(timeout=2)

        before = time.perf_counter()
        worker = app._queue_open_task(
            self._item(),
            slow_launch,
            action="test_slow_launch",
            success_message="opened",
        )
        elapsed = time.perf_counter() - before
        self.assertIsNotNone(worker)
        self.assertTrue(started.wait(timeout=1))
        self.assertLess(elapsed, 0.2)
        self.assertNotEqual(worker_ids, [threading.get_ident()])
        self.assertEqual(statuses, ["正在打开：Demo"])
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(app.root.callbacks), 1)
        app.root.callbacks.pop(0)()
        self.assertEqual(statuses[-1], "opened")

    def test_open_item_routes_an_executable_to_the_background_launcher(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._closing = False
        app.root = _QueuedRoot()
        app.recent_search_items = []
        app.settings = {}
        app._recent_search_dirty = False
        app.emit_mod_event = lambda *_args, **_kwargs: None
        statuses: list[str] = []
        app.write_status = statuses.append
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original_open_target = Passer.open_target

        def slow_open(_item):
            started.set()
            release.wait(timeout=2)
            finished.set()

        Passer.open_target = slow_open
        try:
            before = time.perf_counter()
            app.open_item(self._item())
            elapsed = time.perf_counter() - before
            self.assertTrue(started.wait(timeout=1))
            self.assertLess(elapsed, 0.2)
            self.assertIn("正在打开：Demo", statuses)
        finally:
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            Passer.open_target = original_open_target

    def test_open_history_is_kept_in_memory_until_the_next_real_save(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.recent_search_items = []
        app.settings = {}
        app._recent_search_dirty = False
        saves: list[bool] = []
        app.save = lambda: saves.append(True)

        app.remember_search_result(self._item())

        self.assertEqual(saves, [])
        self.assertTrue(app._recent_search_dirty)
        self.assertEqual(app.settings["recent_search_items"][0]["target"], r"C:\Apps\demo.exe")

    def test_executable_classification_does_not_probe_the_disk_for_every_file_type(self):
        class ProbePath:
            suffix = ".exe"
            name = "demo.exe"

            def is_file(self):
                raise AssertionError("an .exe should be rejected by suffix before disk access")

        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        item = self._item()
        original_path = Passer.Path
        Passer.Path = lambda _value: ProbePath()
        try:
            checks = (
                app.is_viewable_image_item,
                app.is_viewable_pdf_item,
                app.is_viewable_office_pdf_item,
                app.is_viewable_excel_item,
                app.is_viewable_code_item,
                app.is_viewable_text_item,
                app.is_viewable_flash_item,
                app.is_viewable_media_item,
                app.is_viewable_archive_item,
                app.is_office_document_item,
            )
            self.assertTrue(all(not check(item) for check in checks))
        finally:
            Passer.Path = original_path

    def test_shared_code_parts_reuse_content_addressed_compile_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "part.py"
            source = b"cached_value = 42\n"
            source_path.write_bytes(source)
            cache_path = root / "part.cache"
            with mock.patch.object(Passer, "_passer_part_cache_path", return_value=cache_path):
                first = Passer._compile_passer_part("part.py", source_path, source)
                self.assertTrue(cache_path.is_file())
                with mock.patch("builtins.compile", side_effect=AssertionError("cache miss")):
                    second = Passer._compile_passer_part("part.py", source_path, source)
            first_ns: dict = {}
            second_ns: dict = {}
            exec(first, first_ns)
            exec(second, second_ns)
            self.assertEqual(first_ns["cached_value"], 42)
            self.assertEqual(second_ns["cached_value"], 42)


class ShutdownHardeningTests(unittest.TestCase):
    def test_close_stops_services_process_controllers_and_instance_socket(self):
        events: list[str] = []

        class Controller:
            def close(self): events.append("controller-close")

        class Service:
            def stop_sharing(self): events.append("share-stop")

        class StopController:
            def stop(self): events.append("device-stop")

        class Advanced:
            closed = False
            def stop_scrcpy(self): events.append("advanced-stop")
            def hide(self, notify_parent=False): events.append("advanced-hide")

        class Phone:
            advanced_window = Advanced()
            def stop_projection(self): events.append("projection-stop")
            def close(self): events.append("phone-close")

        class Server:
            def close(self): events.append("server-close")

        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._closing = False
        app._shutdown_event = threading.Event()
        app.root = _DummyRoot()
        app.save = lambda: events.append("save")
        app.hide_tooltip = lambda: None
        app.close_group_overlay = lambda: None
        app.ai_chat = None
        app.file_share_window = Controller()
        app.file_share_service = Service()
        app.phone_mirror_window = Phone()
        app.aira_service = Controller()
        app.drop_hook = None
        app.office_preview_jobs = {}
        for name in (
            "image_viewers", "pdf_viewers", "text_viewers", "excel_viewers",
            "shell_preview_viewers", "folder_viewers", "media_viewers",
            "archive_viewers", "audio_editors",
        ):
            setattr(app, name, [])
        for name in (
            "clicker_window", "random_window", "plan_window", "automation_window",
            "calculator_window", "shutdown_window", "network_window", "mail_window",
            "qr_window", "markdown_window", "file_search_window", "screen_record_window",
            "magnet_window", "map_window", "device_info_window", "aira_window",
            "device_lock_window",
        ):
            setattr(app, name, None)
        app.module_windows = {}
        app.device_lock_controller = StopController()
        app._instance_server = Server()
        app._instance_mutex = None

        app.close()
        app.close()  # idempotent
        self.assertTrue(app.root.destroyed)
        self.assertTrue(app._shutdown_event.is_set())
        self.assertEqual(events.count("save"), 1)
        for expected in (
            "share-stop", "projection-stop", "advanced-stop", "advanced-hide",
            "phone-close", "device-stop", "server-close",
        ):
            self.assertIn(expected, events)


if __name__ == "__main__":
    unittest.main()
