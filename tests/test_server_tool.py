from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import server_tool


class _App:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.logged: list[tuple] = []

    def _log_unexpected(self, exc, **context):
        self.logged.append((exc, context))
        return True


class ServerConfigTests(unittest.TestCase):
    def test_normalizes_addresses_ports_and_localhost(self):
        cfg = server_tool.normalize_config(
            {
                "root": ".",
                "bind_address": "localhost",
                "port": "8090",
                "gateway_enabled": False,
                "gateway_ssh_port": "22",
                "gateway_remote_address": "127.0.0.1",
                "gateway_remote_port": "9000",
            }
        )
        self.assertEqual(cfg.bind_address, "127.0.0.1")
        self.assertEqual(cfg.port, 8090)
        self.assertEqual(cfg.gateway_remote_port, 9000)
        with self.assertRaises(ValueError):
            server_tool.validate_port("65536")
        with self.assertRaises(ValueError):
            server_tool.normalize_ipv4("example.com", name="监听地址")

    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "www"
            root.mkdir()
            path = base / "config.json"
            cfg = server_tool.normalize_config(
                server_tool.ServerConfig(
                    root=str(root),
                    bind_address="0.0.0.0",
                    port=8123,
                    gateway_remote_port=9123,
                )
            )
            server_tool.save_config(path, cfg)
            loaded = server_tool.load_config(path, root)
            self.assertEqual(loaded, cfg)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], server_tool.CONFIG_VERSION)

    def test_gateway_command_uses_reverse_tunnel_and_never_password(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "www"
            root.mkdir()
            key = base / "id_ed25519"
            key.write_text("test", encoding="utf-8")
            cfg = server_tool.ServerConfig(
                root=str(root),
                bind_address="0.0.0.0",
                port=8080,
                gateway_enabled=True,
                gateway_host="gateway.example.com",
                gateway_ssh_port=2222,
                gateway_user="passer",
                gateway_remote_address="0.0.0.0",
                gateway_remote_port=18080,
                gateway_key_path=str(key),
            )
            command = server_tool.build_gateway_command(
                cfg,
                8080,
                ssh_executable="ssh.exe",
            )
            self.assertEqual(command[0], "ssh.exe")
            self.assertIn("BatchMode=yes", command)
            self.assertIn("ExitOnForwardFailure=yes", command)
            self.assertIn("-R", command)
            self.assertIn("0.0.0.0:18080:127.0.0.1:8080", command)
            self.assertEqual(command[-3:], ["-l", "passer", "gateway.example.com"])
            self.assertNotIn("password", " ".join(command).lower())

    def test_gateway_password_auth_never_persists_or_passes_password(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "www"
            root.mkdir()
            path = base / "config.json"
            cfg = server_tool.normalize_config(
                server_tool.ServerConfig(
                    root=str(root),
                    gateway_enabled=True,
                    gateway_host="gateway.example.com",
                    gateway_user=r"DOMAIN\passer@example.com",
                    gateway_auth=server_tool.GATEWAY_AUTH_PASSWORD,
                    gateway_key_path=str(base / "missing-key"),
                )
            )
            command = server_tool.build_gateway_command(
                cfg,
                8080,
                ssh_executable="ssh.exe",
            )
            self.assertIn("BatchMode=no", command)
            self.assertIn(
                "PreferredAuthentications=keyboard-interactive,password",
                command,
            )
            self.assertIn("PubkeyAuthentication=no", command)
            self.assertNotIn("-i", command)
            self.assertEqual(
                command[-3:],
                ["-l", r"DOMAIN\passer@example.com", "gateway.example.com"],
            )
            server_tool.save_config(path, cfg)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["gateway_auth"], server_tool.GATEWAY_AUTH_PASSWORD)
            self.assertNotIn("password", payload)


class ServerWindowStyleTests(unittest.TestCase):
    def test_window_uses_wide_passer_theme_fields_without_black_borders(self):
        self.assertGreaterEqual(server_tool.ServerWindow.WIDTH, 900)
        source = Path(server_tool.__file__).read_text(encoding="utf-8")
        section_start = source.index("    def _section(")
        section_end = source.index("    def _label(", section_start)
        section = source[section_start:section_end]
        self.assertIn("relief=tk.FLAT", section)
        self.assertIn("highlightbackground=self.theme.border", section)
        self.assertNotIn("relief=tk.SOLID", section)
        self.assertNotIn("tk.LabelFrame", section)
        self.assertIn("pady=(7, 3)", section)

        entry_start = source.index("    def _entry(")
        entry_end = source.index("    def _button(", entry_start)
        entry = source[entry_start:entry_end]
        self.assertIn("highlightbackground=self.theme.border", entry)
        self.assertIn("highlightcolor=self.theme.accent", entry)
        self.assertNotIn("relief=tk.SOLID", entry)
        self.assertIn("padx=7, pady=3", entry)
        self.assertIn('entry.bind("<ButtonRelease-1>", focus_entry', entry)

        body_start = source.index("    def _build_body(")
        body_end = source.index("    def _refresh_gateway_entries(", body_start)
        body = source[body_start:body_end]
        self.assertLess(body.index("actions = tk.Frame"), body.index("local = self._section"))
        self.assertIn("log_frame._passer_card.pack_configure(fill=tk.BOTH, expand=True)", body)
        self.assertIn("height=3", body)


class StaticServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "www"
        self.root.mkdir()
        (self.root / "index.html").write_text("Passer server", encoding="utf-8")
        self.app = _App(self.base / "data")
        self.service = server_tool.ServerService(self.app)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def _start(self, port=0):
        self.service.start(
            server_tool.ServerConfig(
                root=str(self.root),
                bind_address="127.0.0.1",
                port=port,
                gateway_remote_port=8080,
            )
        )

    def test_get_head_and_write_methods(self):
        self._start()
        with urllib.request.urlopen(self.service.browser_url, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read().decode("utf-8"), "Passer server")
            self.assertEqual(
                response.headers.get("X-Content-Type-Options"),
                "nosniff",
            )

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.service.actual_port,
            timeout=3,
        )
        connection.request("HEAD", "/")
        head = connection.getresponse()
        self.assertEqual(head.status, 200)
        self.assertEqual(head.read(), b"")
        connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.service.actual_port,
            timeout=3,
        )
        connection.request("POST", "/", body=b"blocked")
        post = connection.getresponse()
        self.assertEqual(post.status, 405)
        self.assertEqual(post.getheader("Allow"), "GET, HEAD")
        post.read()
        connection.close()

    def test_symlink_cannot_escape_document_root(self):
        outside = self.base / "private.txt"
        outside.write_text("private", encoding="utf-8")
        link = self.root / "escape.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"当前环境不能创建符号链接：{exc}")
        self._start()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                self.service.browser_url + "escape.txt",
                timeout=3,
            )
        self.assertEqual(caught.exception.code, 403)

    def test_stop_releases_bound_port(self):
        self._start()
        port = self.service.actual_port
        self.service.stop()
        self.assertFalse(self.service.running)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()

    def test_port_conflict_is_reported(self):
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = int(occupied.getsockname()[1])
        try:
            with self.assertRaises(OSError):
                self._start(port)
        finally:
            occupied.close()

    def test_password_gateway_uses_graphical_askpass_environment(self):
        cfg = server_tool.normalize_config(
            server_tool.ServerConfig(
                root=str(self.root),
                gateway_enabled=True,
                gateway_host="gateway.example.com",
                gateway_user="passer",
                gateway_auth=server_tool.GATEWAY_AUTH_PASSWORD,
            )
        )
        self.service.config = cfg
        self.service.actual_port = 8080
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        with mock.patch.object(
            server_tool,
            "find_ssh_executable",
            return_value="ssh.exe",
        ), mock.patch.object(
            server_tool,
            "find_ssh_askpass",
            return_value=r"C:\Program Files\Git\mingw64\bin\git-askpass.exe",
        ), mock.patch.object(
            server_tool.subprocess,
            "Popen",
            return_value=fake_process,
        ) as popen, mock.patch.object(server_tool.threading, "Thread") as thread:
            self.service._start_gateway()

        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertIn("BatchMode=no", command)
        self.assertEqual(
            environment["SSH_ASKPASS"],
            r"C:\Program Files\Git\mingw64\bin\git-askpass.exe",
        )
        self.assertEqual(environment["SSH_ASKPASS_REQUIRE"], "force")
        self.assertFalse(any("secret" in part.lower() for part in command))
        thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
