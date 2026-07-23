from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

import Passer
from browser_bridge import (
    BrowserBridge, BrowserBridgeError, _CDPWebSocket, browser_site_origin,
    validate_browser_url,
)


class BrowserUrlPolicyTests(unittest.TestCase):
    def test_allows_public_http_and_refuses_local_or_internal_urls(self):
        self.assertEqual(validate_browser_url("https://example.com/path"), "https://example.com/path")
        self.assertEqual(validate_browser_url("about:blank"), "about:blank")
        for value in (
            "file:///C:/secret.txt",
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://192.168.1.10",
            "http://169.254.169.254/latest/meta-data",
            "chrome://settings",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_browser_url(value)

    def test_trusted_sites_are_persisted_and_match_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BrowserBridge(folder)
            self.assertEqual(bridge.trust_site("https://Example.com/path"), "https://example.com")
            restored = BrowserBridge(folder)
            self.assertEqual(restored.trusted_sites(), ["https://example.com"])
            self.assertTrue(restored.action_is_trusted(
                "browser_navigate", {"url": "https://example.com/next"}
            ))
            restored.untrust_site("https://example.com")
            self.assertFalse(restored.is_trusted("https://example.com/next"))

    def test_cancel_event_interrupts_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BrowserBridge(folder)
            bridge._cancel_event.set()
            with self.assertRaises(BrowserBridgeError):
                bridge.wait(0.1)


class WebSocketProtocolTests(unittest.TestCase):
    def test_client_frames_are_masked_and_server_text_frames_are_decoded(self):
        client_sock, server_sock = socket.socketpair()
        websocket = _CDPWebSocket("ws://127.0.0.1/devtools")
        websocket.sock = client_sock
        try:
            websocket._send_frame(b"hello")
            header = server_sock.recv(2)
            self.assertEqual(header[0] & 0x0F, 0x1)
            self.assertTrue(header[1] & 0x80)
            length = header[1] & 0x7F
            mask = server_sock.recv(4)
            payload = server_sock.recv(length)
            decoded = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            self.assertEqual(decoded, b"hello")

            message = json.dumps({"id": 1, "result": {"ok": True}}).encode("utf-8")
            server_sock.sendall(bytes([0x81, len(message)]) + message)
            self.assertIn('"ok": true', websocket._recv_message())
        finally:
            client_sock.close()
            server_sock.close()


class BrowserActionBridgeTests(unittest.TestCase):
    @staticmethod
    def app_with_bridge(bridge, *, permission="full"):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.ai_permission = permission
        app.browser_bridge = bridge
        app.data_dir = Path("browser-data")
        app.mod_ai_actions = {}
        app.run_on_ui_thread = lambda func, *args, timeout=60.0, **kwargs: func(*args, **kwargs)
        app.write_status = lambda _message: None
        return app

    def test_browser_result_is_returned_to_aira(self):
        class Bridge:
            def run_action(self, action, spec):
                return {"action": action, "url": spec.get("url"), "elements": [{"ref": "e1"}]}

        app = self.app_with_bridge(Bridge())
        result = app.execute_ai_actions([{
            "action": "browser_navigate",
            "url": "https://example.com",
        }])
        self.assertIn("Aira 受控浏览器结果", result[0])
        self.assertIn("https://example.com", result[0])
        self.assertIn("e1", result[0])

    def test_auto_permission_requires_explicit_browser_approval(self):
        calls = []

        class Bridge:
            def run_action(self, action, spec):
                calls.append((action, spec))
                return {"ok": True}

        app = self.app_with_bridge(Bridge(), permission="auto_approve")
        app._request_ai_action_approval = lambda _actions: False
        result = app.execute_ai_actions([{"action": "browser_snapshot"}])
        self.assertIn("用户拒绝", result[0])
        self.assertEqual(calls, [])

    def test_trusted_site_skips_repeated_auto_permission_prompt(self):
        class Bridge:
            def action_is_trusted(self, _action, _spec):
                return True

            def run_action(self, action, spec):
                return {"action": action, "url": spec.get("url")}

        app = self.app_with_bridge(Bridge(), permission="auto_approve")
        app._request_ai_action_approval = lambda _actions: self.fail(
            "trusted site should not request another approval"
        )
        result = app.execute_ai_actions([{
            "action": "browser_navigate", "url": "https://example.com/next",
        }])
        self.assertIn("https://example.com/next", result[0])

    def test_snapshot_marks_page_content_as_untrusted(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = BrowserBridge(folder)
            bridge._evaluate = lambda _expression: {
                "title": "Demo", "url": "https://example.com", "text": "page",
                "elements": [],
            }
            snapshot = bridge.snapshot()
            self.assertIn("不可信", snapshot["security"])


if __name__ == "__main__":
    unittest.main()
