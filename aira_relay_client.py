from __future__ import annotations

"""Outbound HTTPS client used by Passer to reach the Aira relay."""

import json
import hashlib
import hmac
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


RELAY_PROTOCOL = "aira-relay-v1"
MAX_RESPONSE_BYTES = 80 * 1024


def normalize_relay_url(value: str) -> str:
    clean = str(value or "").strip().rstrip("/")
    if not clean:
        return ""
    parsed = urllib.parse.urlsplit(clean)
    local = (parsed.hostname or "").casefold() in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("公网中继必须使用 https；仅本机测试允许 http://127.0.0.1。")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("公网中继地址无效。")
    if parsed.query or parsed.fragment:
        raise ValueError("公网中继地址不能包含查询参数或片段。")
    return clean


def valid_remote_token(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{43,128}", str(value or "")))


def relay_credential(computer_id: str, remote_token: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", str(computer_id or "").casefold()):
        raise ValueError("电脑 ID 无效。")
    if not valid_remote_token(remote_token):
        raise ValueError("远程令牌无效。")
    return hmac.new(
        remote_token.encode("ascii"),
        f"{RELAY_PROTOCOL}|{computer_id.casefold()}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AiraRelayClient:
    def __init__(
        self,
        relay_url: str,
        computer_id: str,
        remote_token: str,
        session_handler: Callable[["AiraRelayClient", dict[str, Any]], None],
        *,
        status_callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        self.relay_url = normalize_relay_url(relay_url)
        self.computer_id = str(computer_id or "").casefold()
        self.remote_token = str(remote_token or "")
        if not re.fullmatch(r"[0-9a-f]{32}", self.computer_id):
            raise ValueError("电脑 ID 无效。")
        if not valid_remote_token(self.remote_token):
            raise ValueError("远程令牌无效。")
        self.relay_credential = relay_credential(
            self.computer_id, self.remote_token
        )
        self.session_handler = session_handler
        self.status_callback = status_callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._opener = urllib.request.build_opener(_NoRedirect())
        self.connected = False
        self.last_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="Passer-Aira-Relay",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._set_status(False, "")

    close = stop

    def exchange(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            "/v1/desktop/exchange",
            {"session_id": session_id, "payload": payload},
            timeout=35.0,
        )
        if not isinstance(response, dict) or not isinstance(response.get("payload"), dict):
            raise OSError("中继没有返回有效的手机响应。")
        return response["payload"]

    def finish(self, session_id: str, payload: dict[str, Any]) -> None:
        response = self._post(
            "/v1/desktop/finish",
            {"session_id": session_id, "payload": payload},
            timeout=10.0,
        )
        if not isinstance(response, dict) or not response.get("ok"):
            raise OSError("中继没有确认最终结果。")

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                presence = self._post("/v1/desktop/presence", {}, timeout=10.0)
                if not isinstance(presence, dict) or not presence.get("ok"):
                    raise OSError("公网中继没有确认电脑在线状态。")
                self._set_status(True, "")
                opened = self._post("/v1/desktop/pull", {}, timeout=32.0)
                self._set_status(True, "")
                backoff = 1.0
                if opened is None:
                    continue
                if (
                    not isinstance(opened, dict)
                    or opened.get("protocol") != RELAY_PROTOCOL
                    or not isinstance(opened.get("payload"), dict)
                    or not str(opened.get("session_id") or "")
                ):
                    raise OSError("中继返回了无效会话。")
                self.session_handler(self, opened)
            except Exception as exc:  # keep reconnecting after network/proxy failures
                if self._stop.is_set():
                    break
                message = str(exc).strip() or exc.__class__.__name__
                self._set_status(False, message[:180])
                self._stop.wait(backoff)
                backoff = min(15.0, backoff * 2.0)

    def _set_status(self, connected: bool, error: str) -> None:
        changed = self.connected != bool(connected) or self.last_error != str(error or "")
        self.connected = bool(connected)
        self.last_error = str(error or "")
        if changed and callable(self.status_callback):
            try:
                self.status_callback(self.connected, self.last_error)
            except Exception:
                pass

    def _post(
        self,
        path: str,
        value: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any] | None:
        payload = dict(value)
        payload["computer_id"] = self.computer_id
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            self.relay_url + path,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.relay_credential}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "Passer-Aira-Relay/1",
                "Cache-Control": "no-store",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if int(response.status) == 204:
                    return None
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            try:
                error = json.loads(raw.decode("utf-8")).get("error")
            except Exception:
                error = ""
            raise OSError(str(error or f"中继返回 HTTP {exc.code}。")) from exc
        except urllib.error.URLError as exc:
            raise OSError(f"无法连接公网中继：{exc.reason}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OSError("中继响应过大。")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise OSError("中继响应不是有效 JSON。") from exc
        if not isinstance(result, dict):
            raise OSError("中继响应格式无效。")
        return result
