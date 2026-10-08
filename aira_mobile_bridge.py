from __future__ import annotations

"""Authenticated LAN bridge used by Aira Mobile to control Passer.

The bridge is deliberately separate from Passer's loopback-only OpenClaw and
single-instance sockets.  It exposes only a small non-destructive action
allowlist, accepts private-network peers, and proves knowledge of the connection
code with PBKDF2/HMAC while encrypting each session with ephemeral ECDH/AES-GCM.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aira_relay_client import AiraRelayClient, normalize_relay_url
from aira_phone_files import AiraPhoneFileStore, PHONE_FILE_ACTIONS
from device_lock_tool import protect_password, unprotect_password


PROTOCOL = "aira-passer-v2"
DEFAULT_PORT = 50720
DISCOVERY_PROTOCOL = "aira-passer-discovery-v1"
DISCOVERY_PORT = 50721
DISCOVERY_REQUEST = b"AIRA_PASSER_DISCOVER_V1\n"
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CONTROL_PARAMS_BYTES = 32 * 1024
MAX_FILE_PARAMS_BYTES = 700 * 1024
PBKDF2_ROUNDS = 120_000
NONCE_BYTES = 16
AES_NONCE_BYTES = 12
SESSION_MATERIAL_BYTES = 96
AUTH_FAILURE_WINDOW = 300.0
AUTH_MAX_FAILURES = 5
AUTH_BLOCK_SECONDS = 300.0
MAX_CONCURRENT_CLIENTS = 8

# Phone Aira gets a narrower set than OpenClaw. It may list/create scheduled
# Aira tasks and the dedicated phone-share directory, but cannot add arbitrary
# paths/URLs to Passer, change settings, operate MODs, delete tasks, or run a shell.
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
) + PHONE_FILE_ACTIONS


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


def _valid_device_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{8,80}", str(value or "")))


def derive_auth_key(code: str, computer_id: str, rounds: int = PBKDF2_ROUNDS) -> bytes:
    if not _valid_code(code) or len(str(computer_id or "")) != 32:
        raise ValueError("连接码或电脑身份无效。")
    try:
        salt = bytes.fromhex(str(computer_id))
    except ValueError as exc:
        raise ValueError("电脑身份必须是 32 位十六进制。") from exc
    safe_rounds = max(10_000, min(500_000, int(rounds)))
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("ascii"), salt, safe_rounds
    )


def derive_remote_auth_key(base_auth_key: bytes, remote_token: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", str(remote_token or "")):
        raise ValueError("远程令牌无效。")
    return hmac.new(
        remote_token.encode("ascii"),
        f"{PROTOCOL}|relay-auth|".encode("ascii") + bytes(base_auth_key),
        hashlib.sha256,
    ).digest()


def payload_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def protocol_transcript(
    client_nonce: str,
    server_nonce: str,
    device_id: str,
    computer_id: str,
    client_public_key: str,
    server_public_key: str,
) -> str:
    return "|".join((
        PROTOCOL,
        str(client_nonce),
        str(server_nonce),
        str(device_id),
        str(computer_id),
        str(client_public_key),
        str(server_public_key),
    ))


def derive_session_keys(
    auth_key: bytes,
    shared_secret: bytes,
    transcript: str,
) -> tuple[bytes, bytes, bytes]:
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=SESSION_MATERIAL_BYTES,
        salt=auth_key,
        info=(
            f"{PROTOCOL}|session|{payload_sha256(transcript)}"
        ).encode("utf-8"),
    ).derive(shared_secret)
    return material[:32], material[32:64], material[64:96]


def session_proof(
    proof_key: bytes,
    role: str,
    transcript: str,
    payload_binding: str,
) -> str:
    payload = "|".join((
        PROTOCOL,
        str(role),
        payload_sha256(transcript),
        payload_sha256(payload_binding),
    )).encode("utf-8")
    return hmac.new(proof_key, payload, hashlib.sha256).hexdigest()


def encrypt_payload(key: bytes, aad: str, plaintext: str) -> tuple[str, str]:
    nonce = secrets.token_bytes(AES_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        str(plaintext).encode("utf-8"),
        str(aad).encode("utf-8"),
    )
    return (
        base64.b64encode(nonce).decode("ascii"),
        base64.b64encode(ciphertext).decode("ascii"),
    )


def decrypt_payload(key: bytes, aad: str, nonce: str, ciphertext: str) -> str:
    try:
        raw_nonce = base64.b64decode(str(nonce), validate=True)
        raw_ciphertext = base64.b64decode(str(ciphertext), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("手机加密请求编码无效。") from exc
    if len(raw_nonce) != AES_NONCE_BYTES or len(raw_ciphertext) < 16:
        raise ValueError("手机加密请求长度无效。")
    plaintext = AESGCM(key).decrypt(
        raw_nonce,
        raw_ciphertext,
        str(aad).encode("utf-8"),
    )
    return plaintext.decode("utf-8")


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
        relay_url: str = "",
        file_share_dir: str | os.PathLike | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.action_handler = action_handler
        self.status_callback = status_callback
        self.unexpected_callback = unexpected_callback
        self.port = int(port)
        self.discovery_port = int(discovery_port)
        self._relay_url = normalize_relay_url(relay_url)
        self.file_store = AiraPhoneFileStore(
            file_share_dir if file_share_dir is not None
            else self.state_dir.parent / "PhoneFiles"
        )
        self._lock = threading.RLock()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._discovery_socket: socket.socket | None = None
        self._discovery_thread: threading.Thread | None = None
        self._discovery_error = ""
        self._computer_id = ""
        self._running = False
        self._code = ""
        self._cached_auth_key = b""
        self._cached_auth_code = ""
        self._cached_auth_computer_id = ""
        self._last_error = ""
        self._last_device = ""
        self._last_ip = ""
        self._last_seen = 0.0
        self._auth_failures: dict[str, list[float]] = {}
        self._auth_blocked_until: dict[str, float] = {}
        self._client_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CLIENTS)
        self._relay_client: AiraRelayClient | None = None
        self._remote_token = ""
        self._remote_connected = False
        self._remote_error = ""

    @property
    def code_path(self) -> Path:
        return self.state_dir / "connection.code"

    @property
    def computer_id_path(self) -> Path:
        return self.state_dir / "computer.id"

    @property
    def remote_token_path(self) -> Path:
        return self.state_dir / "remote.token"

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

    @property
    def relay_url(self) -> str:
        with self._lock:
            return self._relay_url

    @property
    def remote_token(self) -> str:
        with self._lock:
            if self._remote_token:
                return self._remote_token
        try:
            protected = self.remote_token_path.read_text(encoding="utf-8").strip()
        except OSError:
            protected = ""
        token = unprotect_password(protected) if protected else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", str(token or "")):
            token = secrets.token_urlsafe(32)
            self._persist_remote_token(token)
        with self._lock:
            self._remote_token = token
        return token

    def _persist_remote_token(self, token: str) -> None:
        protected = protect_password(token)
        if not protected:
            raise OSError("无法使用 Windows 当前账户保护远程连接令牌。")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.remote_token_path.with_name(
            f".{self.remote_token_path.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            temporary.write_text(protected + "\n", encoding="utf-8")
            os.replace(temporary, self.remote_token_path)
        finally:
            temporary.unlink(missing_ok=True)

    def configure_relay(self, relay_url: str) -> None:
        normalized = normalize_relay_url(relay_url)
        with self._lock:
            changed = normalized != self._relay_url
            self._relay_url = normalized
            running = self._running
            client = self._relay_client
            self._relay_client = None
            self._remote_connected = False
            self._remote_error = ""
        if changed and client is not None:
            client.stop()
        elif client is not None:
            with self._lock:
                self._relay_client = client
            return
        if running and normalized:
            self._start_relay()
        self._notify_status()

    def remote_pairing_payload(self) -> dict[str, str]:
        with self._lock:
            relay_url = self._relay_url
            running = self._running
        if not relay_url or not running:
            return {}
        return {
            "relay_url": relay_url,
            "remote_token": self.remote_token,
        }

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
        remote_token = secrets.token_urlsafe(32)
        self._persist_remote_token(remote_token)
        with self._lock:
            self._code = code
            self._remote_token = remote_token
            self._cached_auth_key = b""
            self._cached_auth_code = ""
            self._cached_auth_computer_id = ""
            self._last_device = ""
            self._last_ip = ""
            self._last_seen = 0.0
            self._auth_failures.clear()
            self._auth_blocked_until.clear()
            relay_url = self._relay_url
            relay_client = self._relay_client
            self._relay_client = None
            running = self._running
        if relay_client is not None:
            relay_client.stop()
        if running and relay_url:
            self._start_relay()
        self._notify_status()
        return code

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._code = self._load_or_create_code()
            self._cached_auth_key = b""
            self._cached_auth_code = ""
            self._cached_auth_computer_id = ""
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
            relay_enabled = bool(self._relay_url)
        if relay_enabled:
            self._start_relay()
        self._notify_status()

    def _start_relay(self) -> None:
        with self._lock:
            if not self._running or not self._relay_url or self._relay_client is not None:
                return
            client = AiraRelayClient(
                self._relay_url,
                self.computer_id,
                self.remote_token,
                self._handle_relay_session,
                status_callback=self._relay_status_changed,
            )
            self._relay_client = client
        client.start()

    def _relay_status_changed(self, connected: bool, error: str) -> None:
        with self._lock:
            self._remote_connected = bool(connected)
            self._remote_error = str(error or "")
        self._notify_status()

    def _handle_relay_session(
        self,
        relay: AiraRelayClient,
        opened: dict[str, Any],
    ) -> None:
        session_id = str(opened.get("session_id") or "")
        hello = opened.get("payload")
        if not session_id or not isinstance(hello, dict):
            raise ValueError("公网中继会话无效。")
        server_side, relay_side = socket.socketpair()
        server_side.settimeout(20.0)
        relay_side.settimeout(20.0)
        worker = threading.Thread(
            target=self._handle_client,
            args=(server_side, ("127.0.0.1", 0), self.remote_token),
            daemon=True,
            name="Passer-Aira-Relay-Session",
        )
        worker.start()
        try:
            _send_json(relay_side, hello)
            challenge_raw = _recv_line(relay_side)
            if not challenge_raw:
                raise OSError("Passer 没有生成远程认证挑战。")
            challenge = json.loads(challenge_raw.decode("utf-8"))
            response = relay.exchange(session_id, challenge)
            _send_json(relay_side, response)
            final_raw = _recv_line(relay_side)
            if not final_raw:
                raise OSError("Passer 没有生成远程执行结果。")
            final = json.loads(final_raw.decode("utf-8"))
            relay.finish(session_id, final)
            with self._lock:
                self._last_ip = "公网中继"
                self._last_seen = time.time()
        finally:
            try:
                relay_side.close()
            except OSError:
                pass
            worker.join(timeout=1.0)
        self._notify_status()

    def _base_auth_key(self) -> bytes:
        code = self.connection_code
        computer_id = self.computer_id
        with self._lock:
            if (
                self._cached_auth_key
                and self._cached_auth_code == code
                and self._cached_auth_computer_id == computer_id
            ):
                return self._cached_auth_key
        value = derive_auth_key(code, computer_id)
        with self._lock:
            self._cached_auth_key = value
            self._cached_auth_code = code
            self._cached_auth_computer_id = computer_id
        return value

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
            relay_client = self._relay_client
            self._relay_client = None
            self._remote_connected = False
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
        if relay_client is not None:
            relay_client.stop()
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
                "relay_url": self._relay_url,
                "remote_configured": bool(self._relay_url),
                "remote_connected": self._remote_connected,
                "remote_error": self._remote_error,
                "allowed_actions": list(PHONE_CONTROL_ACTIONS),
            }

    def status_text(self) -> str:
        state = self.snapshot()
        if not state["running"]:
            detail = str(state["last_error"] or "")
            return f"未启用{f' · {detail}' if detail else ' · 开启后可让同一 Wi-Fi 的手机连接'}"
        endpoint = f"{state['ip']}:{state['port']}"
        if state["remote_connected"]:
            remote = " · 公网中继在线"
        elif state["remote_configured"]:
            error = str(state["remote_error"] or "正在连接")[:55]
            remote = f" · 公网中继：{error}"
        else:
            remote = " · 未配置公网中继"
        device = str(state["last_device"] or "")
        if device:
            return (
                f"已连接 {device}（{state['last_ip']}） · "
                f"{endpoint} · 连接码 {state['code']}{remote}"
            )
        discovery = "" if state["discovery_available"] else " · 自动发现不可用"
        return f"等待手机连接 · {endpoint} · 连接码 {state['code']}{discovery}{remote}"

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
            if not self._client_slots.acquire(blocking=False):
                try:
                    _send_json(conn, {
                        "protocol": PROTOCOL,
                        "ok": False,
                        "auth": "busy",
                        "error": "Passer 手机连接繁忙，请稍后重试。",
                    })
                except OSError:
                    pass
                finally:
                    conn.close()
                continue
            threading.Thread(
                target=self._handle_client_in_slot,
                args=(conn, address),
                daemon=True,
                name="Passer-Aira-Mobile-Client",
            ).start()

    def _handle_client_in_slot(self, conn: socket.socket, address) -> None:
        try:
            self._handle_client(conn, address)
        finally:
            self._client_slots.release()

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

    def _encrypted_result(
        self,
        conn: socket.socket,
        *,
        proof_key: bytes,
        response_key: bytes,
        transcript: str,
        envelope: dict[str, Any],
    ) -> None:
        result_json = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":"), default=str
        )
        nonce, ciphertext = encrypt_payload(
            response_key,
            transcript + "|server",
            result_json,
        )
        binding = f"{nonce}|{ciphertext}"
        proof = session_proof(
            proof_key,
            "server",
            transcript,
            binding,
        )
        _send_json(conn, {
            "protocol": PROTOCOL,
            "ok": True,
            "auth": "ok",
            "server_proof": proof,
            "response_nonce": nonce,
            "response_ciphertext": ciphertext,
        })

    def _handle_client(
        self,
        conn: socket.socket,
        address,
        remote_token: str = "",
    ) -> None:
        ip = str(address[0] if address else "")
        action = "authenticate"
        try:
            conn.settimeout(15.0)
            if not is_private_peer(ip):
                _send_json(conn, {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "error": "只允许局域网设备连接。",
                })
                return
            retry_after = self._retry_after(ip)
            if retry_after:
                _send_json(conn, {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "auth": "blocked",
                    "error": "连接码尝试过于频繁。",
                    "retry_after": retry_after,
                })
                return

            hello = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
            client_nonce = str(hello.get("client_nonce") or "")
            device_id = str(hello.get("device_id") or "").strip()
            device_name = str(hello.get("device_name") or "Aira 手机").strip()[:80]
            client_public_text = str(hello.get("client_public_key") or "")
            valid_hello = (
                hello.get("protocol") == PROTOCOL
                and hello.get("auth") == "hello"
                and _valid_nonce(client_nonce)
                and _valid_device_id(device_id)
                and bool(client_public_text)
            )
            if not valid_hello:
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "auth": "error",
                    "error": "连接请求无效。",
                    "retry_after": blocked,
                })
                return

            try:
                client_public = serialization.load_der_public_key(
                    base64.b64decode(client_public_text, validate=True)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("手机临时公钥无效。") from exc
            if (
                not isinstance(client_public, ec.EllipticCurvePublicKey)
                or not isinstance(client_public.curve, ec.SECP256R1)
            ):
                raise ValueError("手机临时公钥必须使用 P-256。")

            server_nonce = secrets.token_hex(NONCE_BYTES)
            server_private = ec.generate_private_key(ec.SECP256R1())
            server_public_text = base64.b64encode(
                server_private.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).decode("ascii")
            computer_id = self.computer_id
            _send_json(conn, {
                "protocol": PROTOCOL,
                "auth": "challenge",
                "server_nonce": server_nonce,
                "rounds": PBKDF2_ROUNDS,
                "computer_id": computer_id,
                "computer_name": self.computer_name,
                "server_public_key": server_public_text,
                "relay": bool(remote_token),
            })
            shared_secret = server_private.exchange(ec.ECDH(), client_public)
            transcript = protocol_transcript(
                client_nonce,
                server_nonce,
                device_id,
                computer_id,
                client_public_text,
                server_public_text,
            )
            if remote_token:
                transcript += "|relay"
            auth_key = self._base_auth_key()
            if remote_token:
                auth_key = derive_remote_auth_key(auth_key, remote_token)
            proof_key, request_key, response_key = derive_session_keys(
                auth_key,
                shared_secret,
                transcript,
            )

            response = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
            request_nonce = str(response.get("request_nonce") or "")
            request_ciphertext = str(response.get("request_ciphertext") or "")
            request_binding = f"{request_nonce}|{request_ciphertext}"
            if (
                response.get("protocol") != PROTOCOL
                or response.get("auth") != "response"
                or str(response.get("client_nonce") or "") != client_nonce
                or not request_nonce
                or not request_ciphertext
            ):
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "auth": "failed",
                    "error": "动作或鉴权响应无效。",
                    "retry_after": blocked,
                })
                return

            expected = session_proof(
                proof_key,
                "client",
                transcript,
                request_binding,
            )
            if not hmac.compare_digest(str(response.get("proof") or ""), expected):
                blocked = self._record_failure(ip)
                _send_json(conn, {
                    "protocol": PROTOCOL,
                    "ok": False,
                    "auth": "failed",
                    "error": "连接码错误。",
                    "retry_after": blocked,
                })
                return

            self._clear_failures(ip)
            try:
                request_json = decrypt_payload(
                    request_key,
                    transcript + "|client",
                    request_nonce,
                    request_ciphertext,
                )
                request_size = len(request_json.encode("utf-8"))
                if request_size > MAX_FILE_PARAMS_BYTES:
                    raise ValueError("手机参数过大。")
                request_value = json.loads(request_json)
            except (TypeError, ValueError) as exc:
                raise ValueError("手机加密参数不是有效 JSON。") from exc
            if not isinstance(request_value, dict):
                raise ValueError("手机请求必须是 JSON 对象。")
            action = str(request_value.get("action") or "").strip().casefold()
            params = request_value.get("params")
            if (
                action not in PHONE_FILE_ACTIONS
                and request_size > MAX_CONTROL_PARAMS_BYTES
            ):
                raise ValueError("手机控制参数过大。")
            if action not in PHONE_CONTROL_ACTIONS:
                self._encrypted_result(
                    conn,
                    proof_key=proof_key,
                    response_key=response_key,
                    transcript=transcript,
                    envelope={"ok": False, "error": "手机动作不在允许范围内。"},
                )
                return
            if not isinstance(params, dict):
                raise ValueError("手机参数必须是 JSON 对象。")
            if remote_token and action in PHONE_FILE_ACTIONS:
                self._encrypted_result(
                    conn,
                    proof_key=proof_key,
                    response_key=response_key,
                    transcript=transcript,
                    envelope={"ok": False, "error": "文件共享仅支持同一局域网直连。"},
                )
                return

            with self._lock:
                self._last_device = device_name or "Aira 手机"
                self._last_ip = ip
                self._last_seen = time.time()
                self._last_error = ""
            self._notify_status()

            try:
                if action in PHONE_FILE_ACTIONS:
                    result = self.file_store.execute(action, params, device_id)
                else:
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

            self._encrypted_result(
                conn,
                proof_key=proof_key,
                response_key=response_key,
                transcript=transcript,
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
