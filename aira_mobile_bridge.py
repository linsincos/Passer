from __future__ import annotations

"""Authenticated LAN bridge used by Aira Mobile to control Passer.

The bridge is deliberately separate from Passer's loopback-only OpenClaw and
single-instance sockets.  It exposes only a small non-destructive action
allowlist, accepts private-network peers, and proves knowledge of the connection
code with PBKDF2/HMAC without ever putting that code on the wire.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from device_lock_tool import protect_password, unprotect_password


PROTOCOL = "aira-passer-v1"
DEFAULT_PORT = 50720
DISCOVERY_PROTOCOL = "aira-passer-discovery-v1"
DISCOVERY_PORT = 50721
DISCOVERY_REQUEST = b"AIRA_PASSER_DISCOVER_V1\n"
MAX_MESSAGE_BYTES = 64 * 1024
MAX_PARAMS_BYTES = 32 * 1024
PBKDF2_ROUNDS = 120_000
NONCE_BYTES = 16
AUTH_FAILURE_WINDOW = 300.0
AUTH_MAX_FAILURES = 5
AUTH_BLOCK_SECONDS = 300.0

# Phone Aira gets a narrower set than OpenClaw. It may list/create scheduled
# Aira tasks, but cannot add a path/URL to Passer, change settings, operate
# MODs, read files, delete tasks, or run a shell.
PHONE_CONTROL_ACTIONS = (
    "status",
    "summon",
    "list_tools",
    "list_items",
    "search",
    "open_item",
    "open_tool",
    "start_screenshot",
    "clear_search",
    "list_tasks",
    "add_task",
)


def local_ipv4() -> str:
    """Return the IPv4 address normally used to reach this PC on the LAN."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect selects an interface without sending application data.
        sock.connect(("8.8.8.8", 80))
        value = str(sock.getsockname()[0])
        return value if value else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def is_private_peer(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def _recv_line(conn: socket.socket, limit: int = MAX_MESSAGE_BYTES) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= limit:
        chunk = conn.recv(min(8192, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if b"\n" in chunk:
            break
    data = b"".join(chunks)
    if len(data) > limit:
        raise ValueError("请求内容过大。")
    return data.split(b"\n", 1)[0].strip()


def _send_json(conn: socket.socket, value: dict[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("响应内容过大。")
    conn.sendall(encoded)


def _valid_nonce(value: str) -> bool:
    if len(value) != NONCE_BYTES * 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _valid_code(value: str) -> bool:
    return len(str(value or "")) == 8 and str(value).isdigit()


def derive_auth_key(code: str, server_nonce: str, rounds: int = PBKDF2_ROUNDS) -> bytes:
    if not _valid_code(code) or not _valid_nonce(server_nonce):
        raise ValueError("连接码或鉴权随机数无效。")
    safe_rounds = max(10_000, min(500_000, int(rounds)))
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("ascii"), bytes.fromhex(server_nonce), safe_rounds
    )


def payload_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def auth_proof(
    key: bytes,
    role: str,
    client_nonce: str,
    server_nonce: str,
    device_id: str,
    action: str,
    payload_hash: str,
) -> str:
    payload = "|".join((
        PROTOCOL,
        str(role),
        str(client_nonce),
        str(server_nonce),
        str(device_id),
        str(action),
        str(payload_hash),
    )).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


class AiraMobileBridge:
    """Small authenticated TCP server with no Tk work on network threads."""

    def __init__(
        self,
        state_dir: str | os.PathLike,
        action_handler: Callable[[str, dict[str, Any]], dict[str, Any]],
        *,
        status_callback: Callable[[], None] | None = None,
        unexpected_callback: Callable[[BaseException, str, Path], None] | None = None,
        port: int = DEFAULT_PORT,
        discovery_port: int = DISCOVERY_PORT,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.action_handler = action_handler
        self.status_callback = status_callback
        self.unexpected_callback = unexpected_callback
        self.port = int(port)
        self.discovery_port = int(discovery_port)
        self._lock = threading.RLock()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._discovery_socket: socket.socket | None = None
        self._discovery_thread: threading.Thread | None = None
        self._discovery_error = ""
        self._computer_id = ""
        self._running = False
        self._code = ""
        self._last_error = ""
        self._last_device = ""
        self._last_ip = ""
        self._last_seen = 0.0
        self._auth_failures: dict[str, list[float]] = {}
        self._auth_blocked_until: dict[str, float] = {}

    @property
    def code_path(self) -> Path:
        return self.state_dir / "connection.code"

    @property
    def computer_id_path(self) -> Path:
        return self.state_dir / "computer.id"

    @property
    def computer_id(self) -> str:
        with self._lock:
            if self._computer_id:
                return self._computer_id
        try:
            value = self.computer_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if len(value) == 32:
            try:
                bytes.fromhex(value)
                with self._lock:
                    self._computer_id = value
                return value
            except ValueError:
                pass
        value = secrets.token_hex(16)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.computer_id_path.with_name(
            f".{self.computer_id_path.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            temporary.write_text(value + "\n", encoding="utf-8")
            os.replace(temporary, self.computer_id_path)
        finally:
            temporary.unlink(missing_ok=True)
        with self._lock:
            self._computer_id = value
        return value

    @property
    def computer_name(self) -> str:
        value = str(os.environ.get("COMPUTERNAME") or socket.gethostname() or "Passer")
        return value.strip()[:80] or "Passer"

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def connection_code(self) -> str:
        with self._lock:
            if not self._code:
                self._code = self._load_or_create_code()
            return self._code

    def _persist_code(self, code: str) -> None:
        protected = protect_password(code)
        if not protected:
            raise OSError("无法使用 Windows 当前账户保护手机连接码。")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.code_path.with_name(
            f".{self.code_path.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            temporary.write_text(protected + "\n", encoding="utf-8")
            os.replace(temporary, self.code_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_or_create_code(self) -> str:
        try:
            protected = self.code_path.read_text(encoding="utf-8").strip()
        except OSError:
            protected = ""
        code = unprotect_password(protected) if protected else ""
        if _valid_code(code):
            return code
        code = f"{secrets.randbelow(100_000_000):08d}"
        self._persist_code(code)
        return code

    def reset_code(self) -> str:
        code = f"{secrets.randbelow(100_000_000):08d}"
        self._persist_code(code)
        with self._lock:
            self._code = code
            self._last_device = ""
            self._last_ip = ""
            self._last_seen = 0.0
            self._auth_failures.clear()
            self._auth_blocked_until.clear()
        self._notify_status()
        return code

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._code = self._load_or_create_code()
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("", self.port))
                server.listen(8)
                server.settimeout(1.0)
            except Exception:
                server.close()
                raise
            self._server = server
            self._running = True
            self._last_error = ""
            self._thread = threading.Thread(
                target=self._serve,
                daemon=True,
                name="Passer-Aira-Mobile",
            )
            self._thread.start()
            self._start_discovery()
        self._notify_status()

    def _start_discovery(self) -> None:
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            discovery.bind(("", self.discovery_port))
            discovery.settimeout(1.0)
        except OSError as exc:
            discovery.close()
            self._discovery_error = f"自动发现不可用：{exc}"
            return
        self._discovery_socket = discovery
        self._discovery_error = ""
        self._discovery_thread = threading.Thread(
            target=self._serve_discovery,
            daemon=True,
            name="Passer-Aira-Discovery",
        )
        self._discovery_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            server = self._server
            self._server = None
            discovery = self._discovery_socket
            self._discovery_socket = None
        if server is not None:
            try:
                server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                server.close()
            except OSError:
                pass
        if discovery is not None:
            try:
                discovery.close()
            except OSError:
                pass
        self._notify_status()

    close = stop

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "ip": local_ipv4(),
                "port": self.port,
                "code": self.connection_code if self._running else "",
                "last_device": self._last_device,
                "last_ip": self._last_ip,
                "last_seen": self._last_seen,
                "last_error": self._last_error,
                "computer_id": self.computer_id,
                "computer_name": self.computer_name,
                "discovery_port": self.discovery_port,
                "discovery_available": self._discovery_socket is not None,
                "discovery_error": self._discovery_error,
                "allowed_actions": list(PHONE_CONTROL_ACTIONS),
            }

    def status_text(self) -> str:
        state = self.snapshot()
        if not state["running"]:
            detail = str(state["last_error"] or "")
            return f"未启用{f' · {detail}' if detail else ' · 开启后可让同一 Wi-Fi 的手机连接'}"
        endpoint = f"{state['ip']}:{state['port']}"
        device = str(state["last_device"] or "")
        if device:
            return (
                f"已连接 {device}（{state['last_ip']}） · "
                f"{endpoint} · 连接码 {state['code']}"
            )
        discovery = "" if state["discovery_available"] else " · 自动发现不可用"
        return f"等待手机连接 · {endpoint} · 连接码 {state['code']}{discovery}"

    def _notify_status(self) -> None:
        if callable(self.status_callback):
            try:
                self.status_callback()
            except Exception:
                pass

    def _serve(self) -> None:
        while self.running:
            with self._lock:
                server = self._server
            if server is None:
                break
            try:
                conn, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(conn, address),
                daemon=True,
                name="Passer-Aira-Mobile-Client",
            ).start()

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "protocol": DISCOVERY_PROTOCOL,
            "service": "aira-passer",
            "computer_id": self.computer_id,
            "computer_name": self.computer_name,
            "port": self.port,
            "pairing_required": True,
        }

    def _serve_discovery(self) -> None:
        while self.running:
            with self._lock:
                discovery = self._discovery_socket
            if discovery is None:
                break
            try:
                payload, address = discovery.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            peer = str(address[0] if address else "")
            if payload.strip() != DISCOVERY_REQUEST.strip() or not is_private_peer(peer):
                continue
            try:
                encoded = (
                    json.dumps(
                        self.discovery_payload(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                discovery.sendto(encoded, address)
            except OSError:
                continue
            except Exception as exc:  # noqa: BLE001 - keep discovery alive
                if callable(self.unexpected_callback):
                    try:
                        self.unexpected_callback(exc, "discovery", self.state_dir)
                    except Exception:
                        pass

    def _retry_after(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            blocked_until = self._auth_blocked_until.get(ip, 0.0)
            if blocked_until > now:
                return max(1, int(blocked_until - now + 0.999))
            self._auth_blocked_until.pop(ip, None)
            recent = [
                value for value in self._auth_failures.get(ip, [])
                if now - value <= AUTH_FAILURE_WINDOW
            ]
            if recent:
                self._auth_failures[ip] = recent
            else:
                self._auth_failures.pop(ip, None)
        return 0

    def _record_failure(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            recent = [
                value for value in self._auth_failures.get(ip, [])
                if now - value <= AUTH_FAILURE_WINDOW
            ]
            recent.append(now)
            self._auth_failures[ip] = recent
            if len(recent) >= AUTH_MAX_FAILURES:
                self._auth_blocked_until[ip] = now + AUTH_BLOCK_SECONDS
                self._auth_failures.pop(ip, None)
                return int(AUTH_BLOCK_SECONDS)
        return 0

    def _clear_failures(self, ip: str) -> None:
        with self._lock:
            self._auth_failures.pop(ip, None)
            self._auth_blocked_until.pop(ip, None)

    def _signed_result(
        self,
        conn: socket.socket,
        *,
        key: bytes,
        client_nonce: str,
        server_nonce: str,
        device_id: str,
        action: str,
        envelope: dict[str, Any],
    ) -> None:
        result_json = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":"), default=str
        )
        proof = auth_proof(
            key,
            "server",
            client_nonce,
            server_nonce,
            device_id,
            action,
            payload_sha256(result_json),
        )
        _send_json(conn, {
            "ok": True,
            "auth": "ok",
            "server_proof": proof,
            "result_json": result_json,
        })

    def _handle_client(self, conn: socket.socket, address) -> None:
        ip = str(address[0] if address else "")
        action = "authenticate"
        try:
            conn.settimeout(45.0)
            if not is_private_peer(ip):
                _send_json(conn, {"ok": False, "error": "只允许局域网设备连接。"})
                return
            retry_after = self._retry_after(ip)
            if retry_after:
                _send_json(conn, {
                    "ok": False,
                    "auth": "blocked",
                    "error": "连接码尝试过于频繁。",
                    "retry_after": retry_after,
                })
                return

            hello = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
            client_nonce = str(hello.get("client_nonce") or "")
            device_id = str(hello.get("device_id") or "").strip()[:128]
            device_name = str(hello.get("device_name") or "Aira 手机").strip()[:80]
            valid_hello = (
                hello.get("protocol") == PROTOCOL
                and hello.get("auth") == "hello"
                and _valid_nonce(client_nonce)
                and bool(device_id)
            )
            if not valid_hello:
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "ok": False,
                    "auth": "error",
                    "error": "连接请求无效。",
                    "retry_after": blocked,
                })
                return

            server_nonce = secrets.token_hex(NONCE_BYTES)
            _send_json(conn, {
                "protocol": PROTOCOL,
                "auth": "challenge",
                "server_nonce": server_nonce,
                "rounds": PBKDF2_ROUNDS,
            })
            response = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
            action = str(response.get("action") or "").strip().casefold()
            params_json = str(response.get("params_json") or "{}")
            if (
                response.get("auth") != "response"
                or str(response.get("client_nonce") or "") != client_nonce
                or action not in PHONE_CONTROL_ACTIONS
                or len(params_json.encode("utf-8")) > MAX_PARAMS_BYTES
            ):
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "ok": False,
                    "auth": "failed",
                    "error": "动作或鉴权响应无效。",
                    "retry_after": blocked,
                })
                return

            key = derive_auth_key(self.connection_code, server_nonce)
            expected = auth_proof(
                key,
                "client",
                client_nonce,
                server_nonce,
                device_id,
                action,
                payload_sha256(params_json),
            )
            if not hmac.compare_digest(str(response.get("proof") or ""), expected):
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "ok": False,
                    "auth": "failed",
                    "error": "连接码错误。",
                    "retry_after": blocked,
                })
                return

            self._clear_failures(ip)
            try:
                params = json.loads(params_json)
            except (TypeError, ValueError) as exc:
                raise ValueError("手机参数不是有效 JSON。") from exc
            if not isinstance(params, dict):
                raise ValueError("手机参数必须是 JSON 对象。")

            with self._lock:
                self._last_device = device_name or "Aira 手机"
                self._last_ip = ip
                self._last_seen = time.time()
                self._last_error = ""
            self._notify_status()

            try:
                result = self.action_handler(action, params)
                envelope = {"ok": True, "result": result}
            except (OSError, PermissionError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                envelope = {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - isolate remote requests
                if callable(self.unexpected_callback):
                    try:
                        self.unexpected_callback(exc, action, self.state_dir)
                    except Exception:
                        pass
                envelope = {"ok": False, "error": "Passer 执行动作时发生意外错误。"}

            self._signed_result(
                conn,
                key=key,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
                device_id=device_id,
                action=action,
                envelope=envelope,
            )
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
            # Bad/closed LAN clients are expected at this boundary.
            pass
        except Exception as exc:  # noqa: BLE001 - keep the listener alive
            with self._lock:
                self._last_error = str(exc)[:180]
            if callable(self.unexpected_callback):
                try:
                    self.unexpected_callback(exc, action, self.state_dir)
                except Exception:
                    pass
            self._notify_status()
        finally:
            try:
                conn.close()
            except OSError:
                pass
