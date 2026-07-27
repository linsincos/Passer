from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from aira_mobile_bridge import (
    AiraMobileBridge,
    DISCOVERY_PROTOCOL,
    PHONE_CONTROL_ACTIONS,
    PROTOCOL,
    auth_proof,
    derive_auth_key,
    is_private_peer,
    payload_sha256,
)


def _send(sock: socket.socket, value: dict) -> None:
    sock.sendall(
        (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    )


def _receive(sock: socket.socket) -> dict:
    data = bytearray()
    while b"\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))


class AiraMobileBridgeTests(unittest.TestCase):
    def test_private_peer_gate(self):
        self.assertTrue(is_private_peer("127.0.0.1"))
        self.assertTrue(is_private_peer("192.168.10.22"))
        self.assertTrue(is_private_peer("10.0.0.8"))
        self.assertFalse(is_private_peer("8.8.8.8"))
        self.assertFalse(is_private_peer("not-an-ip"))

    def test_discovery_identity_is_stable_and_tasks_are_narrowly_allowlisted(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(Path(folder), lambda _action, _params: {})
            first = bridge.discovery_payload()
            second = bridge.discovery_payload()
            self.assertEqual(first["protocol"], DISCOVERY_PROTOCOL)
            self.assertEqual(first["computer_id"], second["computer_id"])
            self.assertEqual(len(first["computer_id"]), 32)
            self.assertTrue(first["pairing_required"])
            self.assertIn("list_tasks", PHONE_CONTROL_ACTIONS)
            self.assertIn("add_task", PHONE_CONTROL_ACTIONS)
            self.assertNotIn("delete_task", PHONE_CONTROL_ACTIONS)

    def test_udp_discovery_returns_only_pairing_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(
                Path(folder),
                lambda _action, _params: {},
                port=0,
                discovery_port=0,
            )
            bridge._load_or_create_code = lambda: "12345678"
            bridge.start()
            try:
                discovery_port = bridge._discovery_socket.getsockname()[1]
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                client.settimeout(2)
                try:
                    client.sendto(
                        b"AIRA_PASSER_DISCOVER_V1\n",
                        ("127.0.0.1", discovery_port),
                    )
                    payload, _address = client.recvfrom(2048)
                finally:
                    client.close()
                value = json.loads(payload.decode("utf-8"))
                self.assertEqual(value["protocol"], DISCOVERY_PROTOCOL)
                self.assertTrue(value["pairing_required"])
                self.assertNotIn("code", value)
            finally:
                bridge.stop()

    def test_authenticated_action_and_signed_result(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(
                Path(folder),
                lambda action, params: calls.append((action, params)) or {
                    "action": action,
                    "messages": ["ok"],
                },
            )
            bridge._code = "12345678"
            server, client = socket.socketpair()
            worker = threading.Thread(
                target=bridge._handle_client,
                args=(server, ("127.0.0.1", 50000)),
                daemon=True,
            )
            worker.start()

            client_nonce = "11" * 16
            device_id = "test-device"
            _send(client, {
                "protocol": PROTOCOL,
                "auth": "hello",
                "client_nonce": client_nonce,
                "device_id": device_id,
                "device_name": "测试手机",
            })
            challenge = _receive(client)
            params_json = json.dumps({"query": "记事本"}, ensure_ascii=False, separators=(",", ":"))
            key = derive_auth_key("12345678", challenge["server_nonce"], challenge["rounds"])
            proof = auth_proof(
                key,
                "client",
                client_nonce,
                challenge["server_nonce"],
                device_id,
                "search",
                payload_sha256(params_json),
            )
            _send(client, {
                "auth": "response",
                "client_nonce": client_nonce,
                "action": "search",
                "params_json": params_json,
                "proof": proof,
            })
            response = _receive(client)
            self.assertTrue(response["ok"])
            result_json = response["result_json"]
            expected = auth_proof(
                key,
                "server",
                client_nonce,
                challenge["server_nonce"],
                device_id,
                "search",
                payload_sha256(result_json),
            )
            self.assertEqual(response["server_proof"], expected)
            self.assertTrue(json.loads(result_json)["ok"])
            self.assertEqual(calls, [("search", {"query": "记事本"})])
            self.assertEqual(bridge.snapshot()["last_device"], "测试手机")
            client.close()
            worker.join(timeout=2)

    def test_wrong_code_is_rejected_without_dispatch(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(Path(folder), lambda action, params: calls.append(action))
            bridge._code = "12345678"
            server, client = socket.socketpair()
            worker = threading.Thread(
                target=bridge._handle_client,
                args=(server, ("127.0.0.1", 50001)),
                daemon=True,
            )
            worker.start()
            client_nonce = "22" * 16
            _send(client, {
                "protocol": PROTOCOL,
                "auth": "hello",
                "client_nonce": client_nonce,
                "device_id": "wrong-code-device",
                "device_name": "手机",
            })
            challenge = _receive(client)
            params_json = "{}"
            wrong_key = derive_auth_key("87654321", challenge["server_nonce"], challenge["rounds"])
            _send(client, {
                "auth": "response",
                "client_nonce": client_nonce,
                "action": "status",
                "params_json": params_json,
                "proof": auth_proof(
                    wrong_key,
                    "client",
                    client_nonce,
                    challenge["server_nonce"],
                    "wrong-code-device",
                    "status",
                    payload_sha256(params_json),
                ),
            })
            response = _receive(client)
            self.assertFalse(response["ok"])
            self.assertEqual(response["auth"], "failed")
            self.assertEqual(calls, [])
            client.close()
            worker.join(timeout=2)

    def test_action_outside_phone_allowlist_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(Path(folder), lambda _action, _params: {})
            bridge._code = "12345678"
            server, client = socket.socketpair()
            worker = threading.Thread(
                target=bridge._handle_client,
                args=(server, ("127.0.0.1", 50002)),
                daemon=True,
            )
            worker.start()
            _send(client, {
                "protocol": PROTOCOL,
                "auth": "hello",
                "client_nonce": "33" * 16,
                "device_id": "blocked-action",
                "device_name": "手机",
            })
            _receive(client)
            _send(client, {
                "auth": "response",
                "client_nonce": "33" * 16,
                "action": "add_target",
                "params_json": "{}",
                "proof": "0" * 64,
            })
            response = _receive(client)
            self.assertFalse(response["ok"])
            self.assertIn("无效", response["error"])
            client.close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
