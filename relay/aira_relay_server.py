from __future__ import annotations

"""Small opaque-message relay for Aira Mobile and Passer.

Run this process behind an HTTPS reverse proxy.  It never decrypts the
aira-passer-v2 payloads; it only matches a phone and a desktop that possess the
same 256-bit remote token and forwards their JSON envelopes.
"""

import argparse
import hashlib
import hmac
import json
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


RELAY_PROTOCOL = "aira-relay-v1"
MAX_BODY_BYTES = 80 * 1024
MAX_FRAME_BYTES = 64 * 1024
DESKTOP_POLL_SECONDS = 25.0
SESSION_WAIT_SECONDS = 30.0
DESKTOP_ONLINE_SECONDS = 40.0
SESSION_TTL_SECONDS = 90.0
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_REQUESTS = 180


class RelayFailure(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)


@dataclass
class RelaySession:
    session_id: str
    relay_id: str
    initial_payload: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    phone_to_desktop: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    desktop_to_phone: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2))


@dataclass
class DesktopChannel:
    pending_sessions: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=32))
    last_seen: float = field(default_factory=time.monotonic)


def valid_computer_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", str(value or "").casefold()))


def valid_relay_credential(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "").casefold()))


def relay_identity(computer_id: str, credential: str) -> str:
    if not valid_computer_id(computer_id) or not valid_relay_credential(credential):
        raise RelayFailure(HTTPStatus.UNAUTHORIZED, "远程身份无效。")
    return hashlib.sha256(
        f"{RELAY_PROTOCOL}|{computer_id.casefold()}|{credential.casefold()}".encode("ascii")
    ).hexdigest()


def validate_frame(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RelayFailure(HTTPStatus.BAD_REQUEST, "中继帧必须是 JSON 对象。")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise RelayFailure(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "中继帧过大。")
    return value


class RelayState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._channels: dict[str, DesktopChannel] = {}
        self._sessions: dict[str, RelaySession] = {}
        self._rate: dict[str, list[float]] = {}

    def check_rate(self, peer: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [
                stamp for stamp in self._rate.get(peer, [])
                if now - stamp <= RATE_WINDOW_SECONDS
            ]
            if len(recent) >= RATE_MAX_REQUESTS:
                raise RelayFailure(HTTPStatus.TOO_MANY_REQUESTS, "请求过于频繁。")
            recent.append(now)
            self._rate[peer] = recent
            self._cleanup_locked(now)

    def desktop_pull(
        self,
        computer_id: str,
        token: str,
        timeout: float = DESKTOP_POLL_SECONDS,
    ) -> dict[str, Any] | None:
        identity = relay_identity(computer_id, token)
        with self._lock:
            channel = self._channels.setdefault(identity, DesktopChannel())
            channel.last_seen = time.monotonic()
        try:
            session_id = channel.pending_sessions.get(timeout=max(0.0, timeout))
        except queue.Empty:
            with self._lock:
                channel.last_seen = time.monotonic()
            return None
        with self._lock:
            session = self._sessions.get(str(session_id))
            channel.last_seen = time.monotonic()
        if session is None:
            return None
        return {
            "protocol": RELAY_PROTOCOL,
            "session_id": session.session_id,
            "payload": session.initial_payload,
        }

    def desktop_presence(self, computer_id: str, token: str) -> dict[str, Any]:
        identity = relay_identity(computer_id, token)
        with self._lock:
            channel = self._channels.setdefault(identity, DesktopChannel())
            channel.last_seen = time.monotonic()
        return {"protocol": RELAY_PROTOCOL, "ok": True}

    def mobile_open(
        self,
        computer_id: str,
        token: str,
        payload: Any,
        timeout: float = SESSION_WAIT_SECONDS,
    ) -> dict[str, Any]:
        identity = relay_identity(computer_id, token)
        frame = validate_frame(payload)
        now = time.monotonic()
        with self._lock:
            channel = self._channels.get(identity)
            if channel is None or now - channel.last_seen > DESKTOP_ONLINE_SECONDS:
                raise RelayFailure(HTTPStatus.SERVICE_UNAVAILABLE, "电脑当前未连接中继。")
            session = RelaySession(secrets.token_urlsafe(24), identity, frame)
            self._sessions[session.session_id] = session
        try:
            channel.pending_sessions.put_nowait(session.session_id)
        except queue.Full as exc:
            self._remove_session(session.session_id)
            raise RelayFailure(HTTPStatus.SERVICE_UNAVAILABLE, "电脑待处理请求过多。") from exc
        try:
            response = session.desktop_to_phone.get(timeout=max(0.1, timeout))
        except queue.Empty as exc:
            self._remove_session(session.session_id)
            raise RelayFailure(HTTPStatus.GATEWAY_TIMEOUT, "等待电脑响应超时。") from exc
        return {
            "protocol": RELAY_PROTOCOL,
            "session_id": session.session_id,
            "payload": response,
        }

    def desktop_exchange(
        self,
        computer_id: str,
        token: str,
        session_id: str,
        payload: Any,
        timeout: float = SESSION_WAIT_SECONDS,
    ) -> dict[str, Any]:
        session = self._session_for(computer_id, token, session_id)
        try:
            session.desktop_to_phone.put_nowait(validate_frame(payload))
        except queue.Full as exc:
            raise RelayFailure(HTTPStatus.CONFLICT, "中继会话状态冲突。") from exc
        try:
            response = session.phone_to_desktop.get(timeout=max(0.1, timeout))
        except queue.Empty as exc:
            self._remove_session(session.session_id)
            raise RelayFailure(HTTPStatus.GATEWAY_TIMEOUT, "等待手机响应超时。") from exc
        return {"protocol": RELAY_PROTOCOL, "payload": response}

    def mobile_continue(
        self,
        computer_id: str,
        token: str,
        session_id: str,
        payload: Any,
        timeout: float = SESSION_WAIT_SECONDS,
    ) -> dict[str, Any]:
        session = self._session_for(computer_id, token, session_id)
        try:
            session.phone_to_desktop.put_nowait(validate_frame(payload))
        except queue.Full as exc:
            raise RelayFailure(HTTPStatus.CONFLICT, "中继会话状态冲突。") from exc
        try:
            response = session.desktop_to_phone.get(timeout=max(0.1, timeout))
        except queue.Empty as exc:
            self._remove_session(session.session_id)
            raise RelayFailure(HTTPStatus.GATEWAY_TIMEOUT, "等待电脑结果超时。") from exc
        self._remove_session(session.session_id)
        return {"protocol": RELAY_PROTOCOL, "payload": response}

    def desktop_finish(
        self,
        computer_id: str,
        token: str,
        session_id: str,
        payload: Any,
    ) -> dict[str, Any]:
        session = self._session_for(computer_id, token, session_id)
        try:
            session.desktop_to_phone.put_nowait(validate_frame(payload))
        except queue.Full as exc:
            raise RelayFailure(HTTPStatus.CONFLICT, "中继会话状态冲突。") from exc
        return {"protocol": RELAY_PROTOCOL, "ok": True}

    def _session_for(
        self,
        computer_id: str,
        token: str,
        session_id: str,
    ) -> RelaySession:
        identity = relay_identity(computer_id, token)
        clean_id = str(session_id or "")
        with self._lock:
            session = self._sessions.get(clean_id)
        if session is None or not hmac.compare_digest(session.relay_id, identity):
            raise RelayFailure(HTTPStatus.NOT_FOUND, "中继会话不存在。")
        return session

    def _remove_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(session_id), None)

    def _cleanup_locked(self, now: float) -> None:
        stale_sessions = [
            key for key, value in self._sessions.items()
            if now - value.created_at > SESSION_TTL_SECONDS
        ]
        for key in stale_sessions:
            self._sessions.pop(key, None)
        stale_channels = [
            key for key, value in self._channels.items()
            if now - value.last_seen > max(SESSION_TTL_SECONDS, DESKTOP_ONLINE_SECONDS * 2)
        ]
        for key in stale_channels:
            self._channels.pop(key, None)
        stale_peers = [
            key for key, values in self._rate.items()
            if not values or now - values[-1] > RATE_WINDOW_SECONDS
        ]
        for key in stale_peers:
            self._rate.pop(key, None)


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "AiraRelay/1"

    @property
    def state(self) -> RelayState:
        return self.server.relay_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._write(HTTPStatus.OK, {"ok": True, "protocol": RELAY_PROTOCOL})
            return
        self._write(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self.state.check_rate(str(self.client_address[0] if self.client_address else ""))
            credential = self._bearer_credential()
            value = self._read_json()
            computer_id = str(value.get("computer_id") or "").casefold()
            path = self.path.rstrip("/")
            if path == "/v1/desktop/presence":
                self._write(
                    HTTPStatus.OK,
                    self.state.desktop_presence(computer_id, credential),
                )
                return
            if path == "/v1/desktop/pull":
                result = self.state.desktop_pull(computer_id, credential)
                if result is None:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self._write(HTTPStatus.OK, result)
                return
            if path == "/v1/mobile/open":
                result = self.state.mobile_open(computer_id, credential, value.get("payload"))
            elif path == "/v1/desktop/exchange":
                result = self.state.desktop_exchange(
                    computer_id, credential, str(value.get("session_id") or ""), value.get("payload")
                )
            elif path == "/v1/mobile/continue":
                result = self.state.mobile_continue(
                    computer_id, credential, str(value.get("session_id") or ""), value.get("payload")
                )
            elif path == "/v1/desktop/finish":
                result = self.state.desktop_finish(
                    computer_id, credential, str(value.get("session_id") or ""), value.get("payload")
                )
            else:
                raise RelayFailure(HTTPStatus.NOT_FOUND, "接口不存在。")
            self._write(HTTPStatus.OK, result)
        except RelayFailure as exc:
            self._write(exc.status, {"ok": False, "error": exc.message})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._write(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": "中继服务发生内部错误。"},
            )

    def _bearer_credential(self) -> str:
        value = str(self.headers.get("Authorization") or "")
        prefix = "Bearer "
        if not value.startswith(prefix) or not valid_relay_credential(value[len(prefix):]):
            raise RelayFailure(HTTPStatus.UNAUTHORIZED, "远程令牌无效。")
        return value[len(prefix):]

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise RelayFailure(HTTPStatus.BAD_REQUEST, "Content-Length 无效。") from exc
        if length < 2 or length > MAX_BODY_BYTES:
            raise RelayFailure(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求正文大小无效。")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RelayFailure(HTTPStatus.BAD_REQUEST, "请求不是有效 JSON。") from exc
        if not isinstance(value, dict):
            raise RelayFailure(HTTPStatus.BAD_REQUEST, "请求必须是 JSON 对象。")
        return value

    def _write(self, status: int, value: dict[str, Any]) -> None:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log headers or message bodies. Reverse proxies may log paths/IPs.
        return


class RelayHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, state: RelayState | None = None) -> None:
        super().__init__(address, RelayHandler)
        self.relay_state = state or RelayState()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aira opaque HTTPS relay backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = RelayHttpServer((args.host, args.port))
    print(f"Aira relay listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
