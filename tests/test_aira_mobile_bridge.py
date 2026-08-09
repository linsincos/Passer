from __future__ import annotations

import base64
import hmac
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from aira_mobile_bridge import (
    AiraMobileBridge,
    DISCOVERY_PROTOCOL,
    PHONE_CONTROL_ACTIONS,
    PROTOCOL,
    decrypt_payload,
    derive_auth_key,
    derive_remote_auth_key,
    derive_session_keys,
    encrypt_payload,
    is_private_peer,
    protocol_transcript,
    session_proof,
)
from aira_relay_client import relay_credential
from relay.aira_relay_server import RelayHttpServer


def _send(sock: socket.socket, value: dict) -> None:
    sock.sendall(
        (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        .encode("utf-8")
    )


def _receive(sock: socket.socket) -> dict:
    data = bytearray()
    while b"\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))


def _session(
    client: socket.socket,
    *,
    code: str,
    client_nonce: str,
    device_id: str,
):
    client_private = ec.generate_private_key(ec.SECP256R1())
    client_public_text = base64.b64encode(
        client_private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode("ascii")
    _send(client, {
        "protocol": PROTOCOL,
        "auth": "hello",
        "client_nonce": client_nonce,
        "device_id": device_id,
        "device_name": "测试手机",
        "client_public_key": client_public_text,
    })
    challenge = _receive(client)
    server_public = serialization.load_der_public_key(
        base64.b64decode(challenge["server_public_key"], validate=True)
    )
    shared_secret = client_private.exchange(ec.ECDH(), server_public)
    transcript = protocol_transcript(
        client_nonce,
        challenge["server_nonce"],
        device_id,
        challenge["computer_id"],
        client_public_text,
        challenge["server_public_key"],
    )
    keys = derive_session_keys(
        derive_auth_key(code, challenge["computer_id"], challenge["rounds"]),
        shared_secret,
        transcript,
    )
    return challenge, transcript, keys


def _encrypted_request(
    *,
    client_nonce: str,
    transcript: str,
    keys: tuple[bytes, bytes, bytes],
    action: str,
    params: dict,
) -> dict:
    proof_key, request_key, _response_key = keys
    request_json = json.dumps(
        {"action": action, "params": params},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    nonce, ciphertext = encrypt_payload(
        request_key,
        transcript + "|client",
        request_json,
    )
    return {
        "protocol": PROTOCOL,
        "auth": "response",
        "client_nonce": client_nonce,
        "proof": session_proof(
            proof_key,
            "client",
            transcript,
            f"{nonce}|{ciphertext}",
        ),
        "request_nonce": nonce,
        "request_ciphertext": ciphertext,
    }


def _decrypted_response(
    response: dict,
    transcript: str,
    keys: tuple[bytes, bytes, bytes],
) -> dict:
    proof_key, _request_key, response_key = keys
    binding = f"{response['response_nonce']}|{response['response_ciphertext']}"
    expected = session_proof(proof_key, "server", transcript, binding)
    if not hmac.compare_digest(response["server_proof"], expected):
        raise AssertionError("server proof mismatch")
    raw = decrypt_payload(
        response_key,
        transcript + "|server",
        response["response_nonce"],
        response["response_ciphertext"],
    )
    return json.loads(raw)


class AiraMobileBridgeTests(unittest.TestCase):
    def test_remote_auth_vector_matches_android(self):
        computer_id = "00112233445566778899aabbccddeeff"
        token = "A" * 43
        base = derive_auth_key("12345678", computer_id)
        self.assertEqual(
            base.hex(),
            "23b8e77d3286f365a7c119d157d39d7c9003b439e726a9a8797d6d251edb1c5b",
        )
        self.assertEqual(
            derive_remote_auth_key(base, token).hex(),
            "8a2e8b32b1f517997fbdaca4fd6d6e237be3cc3ab831e7e22b97db5483a14ca3",
        )

    def test_full_encrypted_action_over_http_relay(self):
        token = "A" * 43
        code = "12345678"
        calls = []
        relay_server = RelayHttpServer(("127.0.0.1", 0))
        relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
        relay_thread.start()
        relay_url = f"http://127.0.0.1:{relay_server.server_address[1]}"

        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(
                Path(folder),
                lambda action, params: calls.append((action, params)) or {
                    "messages": ["remote-ok"]
                },
                port=0,
                discovery_port=0,
                relay_url=relay_url,
            )
            bridge._load_or_create_code = lambda: code
            bridge._remote_token = token
            bridge.start()
            try:
                deadline = time.monotonic() + 3
                while not bridge.snapshot()["remote_connected"] and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(
                    bridge.snapshot()["remote_connected"],
                    bridge.snapshot()["remote_error"],
                )
                computer_id = bridge.computer_id
                credential = relay_credential(computer_id, token)

                def post(path, value):
                    body = dict(value)
                    body["computer_id"] = computer_id
                    request = urllib.request.Request(
                        relay_url + path,
                        data=json.dumps(body).encode("utf-8"),
                        method="POST",
                        headers={
                            "Authorization": f"Bearer {credential}",
                            "Content-Type": "application/json",
                        },
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.loads(response.read().decode("utf-8"))

                client_nonce = "55" * 16
                device_id = "remote-phone"
                private_key = ec.generate_private_key(ec.SECP256R1())
                client_public = base64.b64encode(
                    private_key.public_key().public_bytes(
                        serialization.Encoding.DER,
                        serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                ).decode("ascii")
                opened = post("/v1/mobile/open", {"payload": {
                    "protocol": PROTOCOL,
                    "auth": "hello",
                    "client_nonce": client_nonce,
                    "device_id": device_id,
                    "device_name": "远程测试手机",
                    "client_public_key": client_public,
                }})
                challenge = opened["payload"]
                self.assertTrue(challenge["relay"])
                server_public = serialization.load_der_public_key(
                    base64.b64decode(challenge["server_public_key"], validate=True)
                )
                shared = private_key.exchange(ec.ECDH(), server_public)
                transcript = protocol_transcript(
                    client_nonce,
                    challenge["server_nonce"],
                    device_id,
                    challenge["computer_id"],
                    client_public,
                    challenge["server_public_key"],
                ) + "|relay"
                base_key = derive_auth_key(code, computer_id, challenge["rounds"])
                keys = derive_session_keys(
                    derive_remote_auth_key(base_key, token), shared, transcript
                )
                request_frame = _encrypted_request(
                    client_nonce=client_nonce,
                    transcript=transcript,
                    keys=keys,
                    action="search",
                    params={"query": "远程项目"},
                )
                finished = post("/v1/mobile/continue", {
                    "session_id": opened["session_id"],
                    "payload": request_frame,
                })
                result = _decrypted_response(finished["payload"], transcript, keys)
                self.assertTrue(result["ok"])
                self.assertEqual(calls, [("search", {"query": "远程项目"})])
            finally:
                bridge.stop()
        relay_server.shutdown()
        relay_server.server_close()
        relay_thread.join(2)

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

    def test_authenticated_action_and_encrypted_result(self):
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
            _challenge, transcript, keys = _session(
                client,
                code="12345678",
                client_nonce=client_nonce,
                device_id="test-device",
            )
            request = _encrypted_request(
                client_nonce=client_nonce,
                transcript=transcript,
                keys=keys,
                action="search",
                params={"query": "记事本"},
            )
            self.assertNotIn("记事本", json.dumps(request, ensure_ascii=False))
            _send(client, request)
            response = _receive(client)
            self.assertTrue(response["ok"])
            self.assertNotIn("messages", json.dumps(response, ensure_ascii=False))
            result = _decrypted_response(response, transcript, keys)
            self.assertTrue(result["ok"])
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
            _challenge, transcript, wrong_keys = _session(
                client,
                code="87654321",
                client_nonce=client_nonce,
                device_id="wrong-code-device",
            )
            _send(client, _encrypted_request(
                client_nonce=client_nonce,
                transcript=transcript,
                keys=wrong_keys,
                action="status",
                params={},
            ))
            response = _receive(client)
            self.assertFalse(response["ok"])
            self.assertEqual(response["auth"], "failed")
            self.assertEqual(calls, [])
            client.close()
            worker.join(timeout=2)

    def test_invalid_device_identity_is_rejected_before_challenge(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = AiraMobileBridge(Path(folder), lambda _action, _params: {})
            server, client = socket.socketpair()
            worker = threading.Thread(
                target=bridge._handle_client,
                args=(server, ("127.0.0.1", 50003)),
                daemon=True,
            )
            worker.start()
            public_key = ec.generate_private_key(ec.SECP256R1()).public_key()
            public_text = base64.b64encode(public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )).decode("ascii")
            _send(client, {
                "protocol": PROTOCOL,
                "auth": "hello",
                "client_nonce": "44" * 16,
                "device_id": "bad|id",
                "device_name": "测试手机",
                "client_public_key": public_text,
            })
            response = _receive(client)
            self.assertFalse(response["ok"])
            self.assertEqual(response["auth"], "error")
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
            client_nonce = "33" * 16
            _challenge, transcript, keys = _session(
                client,
                code="12345678",
                client_nonce=client_nonce,
                device_id="blocked-action",
            )
            _send(client, _encrypted_request(
                client_nonce=client_nonce,
                transcript=transcript,
                keys=keys,
                action="add_target",
                params={},
            ))
            response = _receive(client)
            result = _decrypted_response(response, transcript, keys)
            self.assertFalse(result["ok"])
            self.assertIn("允许范围", result["error"])
            client.close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
