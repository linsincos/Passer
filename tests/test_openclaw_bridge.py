from __future__ import annotations

import json
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import Passer
import ai_cli_bridge
import aira_tool
import openclaw_bridge


class OpenClawBridgeTests(unittest.TestCase):
    def test_aira_auto_config_enables_and_persists_openclaw(self):
        class Value:
            def __init__(self):
                self.value = ""

            def set(self, value):
                self.value = value

        class Widget:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class App:
            def __init__(self):
                self.settings = {}
                self.openclaw_enabled = False
                self.saved = 0
                self.configured = False
                self.status = ""

            def save(self):
                self.saved += 1

            def apply_openclaw_setting(self, enabled, *, notify, on_complete):
                self.configured = bool(enabled and not notify)
                on_complete(True, "已在 OpenClaw 中启用 Passer 受控桥。")

            def write_status(self, value):
                self.status = value

        window = aira_tool.AiraWindow.__new__(aira_tool.AiraWindow)
        window.app = App()
        window.theme = type("Theme", (), {"accent": "#2563eb"})()
        window.closed = False
        window.openclaw_status_var = Value()
        window.openclaw_status_label = Widget()
        window.openclaw_button = Widget()
        window.openclaw_qr_button = Widget()
        window._openclaw_configuring = False
        window._openclaw_config_result = None
        window._openclaw_config_message = ""
        window._openclaw_qr_launching = False
        window._openclaw_qr_result = None
        window._openclaw_qr_message = ""
        window.auto_configure_openclaw()
        self.assertTrue(window.app.openclaw_enabled)
        self.assertTrue(window.app.settings["openclaw_enabled"])
        self.assertEqual(window.app.saved, 1)
        self.assertTrue(window.app.configured)
        self.assertTrue(window._openclaw_config_result)
        self.assertIn("配置完成", window.openclaw_status_var.value)
        self.assertEqual(window.openclaw_button.options["text"], "重新配置")

    def test_aira_wechat_qr_button_opens_pairing_window(self):
        class Value:
            def __init__(self):
                self.value = ""

            def set(self, value):
                self.value = value

        class Widget:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class Root:
            @staticmethod
            def after(_delay, callback):
                callback()

        class App:
            def __init__(self):
                self.root = Root()
                self.openclaw_enabled = True
                self.status = ""

            def write_status(self, value):
                self.status = value

        window = aira_tool.AiraWindow.__new__(aira_tool.AiraWindow)
        window.app = App()
        window.theme = type("Theme", (), {"accent": "#2563eb"})()
        window.closed = False
        window.openclaw_status_var = Value()
        window.openclaw_status_label = Widget()
        window.openclaw_button = Widget()
        window.openclaw_qr_button = Widget()
        window._openclaw_configuring = False
        window._openclaw_config_result = True
        window._openclaw_config_message = ""
        window._openclaw_qr_launching = False
        window._openclaw_qr_result = None
        window._openclaw_qr_message = ""
        with mock.patch.object(
            ai_cli_bridge,
            "run_wechat_action",
            return_value="已打开微信 CLI 扫码登录窗口；请使用手机微信扫码并确认授权。",
        ):
            window.generate_openclaw_wechat_qr()
            deadline = time.monotonic() + 2
            while window._openclaw_qr_launching and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertTrue(window._openclaw_qr_result)
        self.assertIn("二维码窗口已打开", window.openclaw_status_var.value)
        self.assertEqual(window.openclaw_qr_button.options["text"], "生成微信二维码")

    def test_passer_entrypoint_serves_mcp_without_starting_ui(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
        completed = subprocess.run(
            [
                sys.executable, str(Path(Passer.__file__).resolve()),
                "--openclaw-mcp", "--token-file", "missing-test-token",
                "--port", "50719",
            ],
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        response = json.loads(completed.stdout.strip())
        self.assertEqual(response["result"]["serverInfo"]["name"], "passer-openclaw-bridge")

    def test_mcp_lists_and_calls_only_passer_control(self):
        calls = []

        def sender(action, params, **kwargs):
            calls.append((action, params, kwargs))
            return {"ok": True, "result": {"action": action, "messages": ["ok"]}}

        server = openclaw_bridge.PasserMCPServer("token.txt", 50719, sender=sender)
        initialized = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        })
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "passer-openclaw-bridge")
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["passer_control"])
        called = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "passer_control",
                "arguments": {"action": "open_item", "query": "记事本"},
            },
        })
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(calls[0][0:2], ("open_item", {"query": "记事本"}))

    def test_loopback_request_uses_token_and_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / "bridge.token"
            token_file.write_text("x" * 40, encoding="utf-8")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            captured = {}

            def serve():
                conn, _address = listener.accept()
                with conn:
                    raw = conn.recv(openclaw_bridge.MAX_MESSAGE_BYTES)
                    captured.update(json.loads(raw.decode("utf-8")))
                    conn.sendall(b'{"ok":true,"result":{"messages":["done"]}}\n')
                listener.close()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            response = openclaw_bridge.send_passer_request(
                "summon", {}, token_file=token_file, port=port, timeout=3,
            )
            thread.join(timeout=3)
            self.assertTrue(response["ok"])
            self.assertEqual(captured["protocol"], openclaw_bridge.BRIDGE_PROTOCOL)
            self.assertEqual(captured["token"], "x" * 40)

    def test_passer_host_rejects_bad_token_before_queueing_action(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.5)
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app._instance_server = listener
        app._instance_queue = queue.Queue()
        app.openclaw_enabled = True
        app._openclaw_bridge_token = "good-token-" + ("x" * 32)
        worker = threading.Thread(target=app._accept_instance_pings, daemon=True)
        worker.start()
        payload = {
            "protocol": openclaw_bridge.BRIDGE_PROTOCOL,
            "token": "bad-token-" + ("x" * 32),
            "action": "open_item",
            "params": {"query": "记事本"},
        }
        with socket.create_connection(listener.getsockname(), timeout=2) as conn:
            conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            response = json.loads(conn.recv(4096).decode("utf-8"))
        worker.join(timeout=2)
        listener.close()
        self.assertFalse(response["ok"])
        self.assertIn("authentication", response["error"])
        self.assertTrue(app._instance_queue.empty())

    def test_passer_host_rejects_unknown_actions(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.openclaw_enabled = True
        app.items = [object(), object()]
        app.root = type("Root", (), {"winfo_viewable": lambda self: 1})()
        app.ai_enabled_var = type("Value", (), {"get": lambda self: True})()
        app.execute_ai_actions = lambda actions: [f"ran:{actions[0]['action']}"]
        app.summon_window = lambda: None
        status = app._dispatch_openclaw_request("status", {})
        self.assertEqual(status["items"], 2)
        opened = app._dispatch_openclaw_request("open_item", {"query": "文档"})
        self.assertEqual(opened["messages"], ["ran:open_item"])
        with self.assertRaises(PermissionError):
            app._dispatch_openclaw_request("delete_mod", {"query": "demo"})
        app.openclaw_enabled = False
        with self.assertRaises(PermissionError):
            app._dispatch_openclaw_request("status", {})

    def test_aira_mobile_uses_independent_narrower_allowlist(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.settings = {"aira_mobile_enabled": True}
        app.items = [object()]
        app.root = type("Root", (), {"winfo_viewable": lambda self: 1})()
        app.ai_enabled_var = type("Value", (), {"get": lambda self: True})()
        app.execute_ai_actions = lambda actions: [f"ran:{actions[0]['action']}"]
        app.summon_window = lambda: None
        status = app._dispatch_aira_mobile_request("status", {})
        self.assertNotIn("add_target", status["allowed_actions"])
        opened = app._dispatch_aira_mobile_request("open_tool", {"tool": "计算器"})
        self.assertEqual(opened["messages"], ["ran:open_tool"])
        with self.assertRaises(PermissionError):
            app._dispatch_aira_mobile_request("add_target", {"target": "C:\\secret"})
        app.settings["aira_mobile_enabled"] = False
        with self.assertRaises(PermissionError):
            app._dispatch_aira_mobile_request("status", {})

    def test_aira_mobile_can_list_and_add_but_not_delete_tasks(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.settings = {"aira_mobile_enabled": True}
        app.items = []
        app.automations = []
        app.root = type("Root", (), {"winfo_viewable": lambda self: 1})()
        app.ai_enabled_var = type("Value", (), {"get": lambda self: True})()
        app.aira_service = None
        app.save_automations = mock.Mock()
        app._refresh_automation_window = mock.Mock()
        added = app._dispatch_aira_mobile_request("add_task", {
            "title": "手机创建的任务",
            "prompt": "总结今天的工作并通知我",
            "mode": "once",
            "when": "2099-01-02 09:30",
        })
        self.assertEqual(added["task"]["title"], "手机创建的任务")
        self.assertEqual(len(app.automations), 1)
        app.automations[0].update({
            "_running": True,
            "_run_started_at": "2099-01-02T09:30:00",
            "_run_updated_at": "2099-01-02T09:31:00",
            "_run_stage": "正在执行电脑操作（1 项）",
            "_run_round": 2,
            "_run_preview": "正在整理今天的工作记录",
        })
        listed = app._dispatch_aira_mobile_request("list_tasks", {})
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["active_count"], 1)
        self.assertEqual(listed["refresh_after_ms"], 2500)
        self.assertEqual(listed["tasks"][0]["title"], "手机创建的任务")
        self.assertTrue(listed["tasks"][0]["running"])
        self.assertEqual(listed["tasks"][0]["run_round"], 2)
        self.assertEqual(
            listed["tasks"][0]["run_stage"],
            "正在执行电脑操作（1 项）",
        )
        self.assertEqual(
            listed["tasks"][0]["run_preview"],
            "正在整理今天的工作记录",
        )
        app.save_automations.assert_called_once()
        with self.assertRaises(PermissionError):
            app._dispatch_aira_mobile_request("delete_task", {"id": "anything"})

    def test_openclaw_registry_definition_can_be_disabled(self):
        commands = []

        def run(command, timeout=120):
            commands.append((command, timeout))
            return 0, "ok"

        with mock.patch.object(ai_cli_bridge, "find_openclaw_cli", return_value=Path("openclaw.cmd")), \
                mock.patch.object(ai_cli_bridge, "_run_cli", side_effect=run):
            ok, _message = ai_cli_bridge.configure_openclaw_passer_mcp(
                enabled=False,
                command="python.exe",
                args=["Passer.py", "--openclaw-mcp", "--token-file", "token.txt"],
                cwd="C:/Passer",
            )
        self.assertTrue(ok)
        definition = json.loads(commands[0][0][4])
        self.assertFalse(definition["enabled"])
        self.assertEqual(definition["toolFilter"]["include"], ["passer_control"])
        self.assertNotIn("exclude", definition["toolFilter"])
        self.assertEqual(commands[1][0][1:], ["mcp", "reload"])


if __name__ == "__main__":
    unittest.main()
