from __future__ import annotations

import threading
import json
import urllib.request
import unittest

from aira_relay_client import AiraRelayClient, relay_credential
from relay.aira_relay_server import (
    RelayFailure,
    RelayHttpServer,
    RelayState,
    relay_identity,
)


COMPUTER_ID = "00112233445566778899aabbccddeeff"
TOKEN = "A" * 43
CREDENTIAL = relay_credential(COMPUTER_ID, TOKEN)


class RelayStateTests(unittest.TestCase):
    def test_identity_is_bound_to_token_and_computer(self):
        first = relay_identity(COMPUTER_ID, CREDENTIAL)
        self.assertEqual(first, relay_identity(COMPUTER_ID, CREDENTIAL))
        self.assertNotEqual(
            first,
            relay_identity(COMPUTER_ID, relay_credential(COMPUTER_ID, "B" * 43)),
        )

    def test_complete_opaque_exchange(self):
        state = RelayState()
        desktop_result = {}
        mobile_result = {}
        self.assertIsNone(state.desktop_pull(COMPUTER_ID, CREDENTIAL, timeout=0))

        def desktop():
            opened = state.desktop_pull(COMPUTER_ID, CREDENTIAL, timeout=2)
            self.assertEqual(opened["payload"], {"frame": "hello-ciphertext"})
            session_id = opened["session_id"]
            response = state.desktop_exchange(
                COMPUTER_ID,
                CREDENTIAL,
                session_id,
                {"frame": "challenge-ciphertext"},
                timeout=2,
            )
            self.assertEqual(response["payload"], {"frame": "proof-ciphertext"})
            desktop_result.update(state.desktop_finish(
                COMPUTER_ID,
                CREDENTIAL,
                session_id,
                {"frame": "result-ciphertext"},
            ))

        # A desktop long poll marks the computer online before the phone opens.
        worker = threading.Thread(target=desktop, daemon=True)
        worker.start()

        def mobile():
            opened = state.mobile_open(
                COMPUTER_ID,
                CREDENTIAL,
                {"frame": "hello-ciphertext"},
                timeout=2,
            )
            self.assertEqual(opened["payload"], {"frame": "challenge-ciphertext"})
            mobile_result.update(state.mobile_continue(
                COMPUTER_ID,
                CREDENTIAL,
                opened["session_id"],
                {"frame": "proof-ciphertext"},
                timeout=2,
            ))

        phone = threading.Thread(target=mobile, daemon=True)
        phone.start()
        phone.join(3)
        worker.join(3)
        self.assertFalse(phone.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertEqual(mobile_result["payload"], {"frame": "result-ciphertext"})
        self.assertTrue(desktop_result["ok"])

    def test_wrong_remote_token_cannot_reach_computer(self):
        state = RelayState()
        self.assertIsNone(state.desktop_pull(COMPUTER_ID, CREDENTIAL, timeout=0))
        waiting = threading.Thread(
            target=lambda: state.desktop_pull(COMPUTER_ID, CREDENTIAL, timeout=0.2),
            daemon=True,
        )
        waiting.start()
        with self.assertRaises(RelayFailure) as raised:
            state.mobile_open(
                COMPUTER_ID,
                relay_credential(COMPUTER_ID, "B" * 43),
                {"frame": "hello"},
                timeout=0.1,
            )
        self.assertEqual(raised.exception.status, 503)
        waiting.join(1)

    def test_http_relay_and_desktop_client_exchange_opaque_frames(self):
        server = RelayHttpServer(("127.0.0.1", 0))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        relay_url = f"http://127.0.0.1:{server.server_address[1]}"
        online = threading.Event()
        handled = threading.Event()

        def handle(client, opened):
            self.assertEqual(opened["payload"], {"frame": "hello"})
            response = client.exchange(
                opened["session_id"], {"frame": "challenge"}
            )
            self.assertEqual(response, {"frame": "proof"})
            client.finish(opened["session_id"], {"frame": "result"})
            handled.set()

        client = AiraRelayClient(
            relay_url,
            COMPUTER_ID,
            TOKEN,
            handle,
            status_callback=lambda connected, _error: online.set() if connected else None,
        )
        client.start()
        try:
            self.assertTrue(online.wait(2), client.last_error)

            def post(path, value):
                body = dict(value)
                body["computer_id"] = COMPUTER_ID
                request = urllib.request.Request(
                    relay_url + path,
                    data=json.dumps(body).encode("utf-8"),
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {CREDENTIAL}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(request, timeout=4) as response:
                    return json.loads(response.read().decode("utf-8"))

            opened = post("/v1/mobile/open", {"payload": {"frame": "hello"}})
            self.assertEqual(opened["payload"], {"frame": "challenge"})
            result = post("/v1/mobile/continue", {
                "session_id": opened["session_id"],
                "payload": {"frame": "proof"},
            })
            self.assertEqual(result["payload"], {"frame": "result"})
            self.assertTrue(handled.wait(2))
        finally:
            client.stop()
            server.shutdown()
            server.server_close()
            server_thread.join(2)


if __name__ == "__main__":
    unittest.main()
