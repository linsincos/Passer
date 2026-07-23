from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import socket
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from clicker_tool import ClickerTheme
from popup_manager import default_popup_manager

try:
    from tkinterdnd2 import COPY, DND_FILES

    TKDND_AVAILABLE = True
except Exception:
    COPY = "copy"
    DND_FILES = None
    TKDND_AVAILABLE = False

# 局域网文件共享：UDP 广播发现发送方，TCP 传输文件内容。
UDP_DISCOVERY_PORT = 50713
TCP_TRANSFER_PORT = 50714
BUFSIZE = 65536
TRANSFER_PROTOCOL = 3
RESUME_PROTOCOL = 2
TRANSFER_IDLE_TIMEOUT = 180.0
BEACON_INTERVAL = 2.0       # 发送方广播心跳的间隔（秒）
DEVICE_TTL = 20.0           # 超过该时间没有心跳/探测响应的设备视为离线（秒）
ACTIVE_SCAN_INTERVAL = 60.0 # UDP 长时间没有结果时才低频回退到同网段 TCP 探测
DISCOVERY_CONNECT_TIMEOUT = 0.32
DISCOVERY_SCAN_WORKERS = 12
TRANSFER_CODE_MIN_LENGTH = 4
TRANSFER_CODE_MAX_LENGTH = 8
AUTH_PBKDF2_ROUNDS = 120_000
AUTH_FAILURE_WINDOW = 300.0
AUTH_MAX_FAILURES = 5
AUTH_BLOCK_SECONDS = 300.0
AUTH_NONCE_BYTES = 16
PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://ident.me",
)


# ---------------------------------------------------------------------------
# 网络辅助
# ---------------------------------------------------------------------------
def local_ip() -> str:
    """获取本机在局域网内的 IPv4 地址。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def public_ip(timeout: float = 4.0) -> str | None:
    """获取广域网出口 IP；失败时返回 None，不影响局域网传输。"""
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PasserFileShare/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read(96).decode("utf-8", errors="ignore").strip()
            if text and len(text) <= 64:
                return text
        except Exception:
            continue
    return None


def _recv_line(conn: socket.socket, limit: int = 65536) -> bytes:
    """逐字节读取一行（以 \\n 结尾），不会越界读到后续二进制负载。"""
    buf = bytearray()
    while True:
        ch = conn.recv(1)
        if not ch:
            break
        if ch == b"\n":
            break
        buf += ch
        if len(buf) > limit:
            raise ValueError("请求头过长")
    return bytes(buf)


def _send_json(conn: socket.socket, obj: dict) -> None:
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def _valid_transfer_code(value: str) -> bool:
    value = str(value or "").strip()
    return TRANSFER_CODE_MIN_LENGTH <= len(value) <= TRANSFER_CODE_MAX_LENGTH and value.isdigit()


def _auth_key(code: str, server_nonce: str, rounds: int = AUTH_PBKDF2_ROUNDS) -> bytes:
    """Derive a per-connection key without ever putting the transfer code on the wire."""
    try:
        salt = bytes.fromhex(server_nonce)
    except ValueError as exc:
        raise ValueError("无效的鉴权随机数") from exc
    rounds = max(10_000, min(500_000, int(rounds)))
    return hashlib.pbkdf2_hmac("sha256", str(code).encode("utf-8"), salt, rounds)


def _auth_proof(key: bytes, client_nonce: str, server_nonce: str, role: str) -> str:
    payload = f"passer-share-v3|{role}|{client_nonce}|{server_nonce}".encode("ascii")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _authenticate_client_v3(conn: socket.socket, code: str) -> bool:
    """Try protocol-v3 challenge/response. Return False only for a legacy peer."""
    client_nonce = secrets.token_hex(AUTH_NONCE_BYTES)
    _send_json(conn, {
        "protocol": TRANSFER_PROTOCOL,
        "auth": "hello",
        "client_nonce": client_nonce,
    })
    challenge = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
    if challenge.get("auth") == "blocked" or challenge.get("retry_after"):
        retry_after = max(1, int(challenge.get("retry_after") or 1))
        raise RuntimeError(f"鉴权尝试过于频繁，请在 {retry_after} 秒后重试")
    if challenge.get("auth") == "error":
        raise RuntimeError(str(challenge.get("error") or "对方拒绝了传输"))
    if challenge.get("auth") != "challenge":
        return False
    server_nonce = str(challenge.get("server_nonce") or "")
    rounds = int(challenge.get("rounds") or AUTH_PBKDF2_ROUNDS)
    if len(server_nonce) != AUTH_NONCE_BYTES * 2:
        raise RuntimeError("对方返回了无效的鉴权随机数")
    key = _auth_key(code, server_nonce, rounds)
    _send_json(conn, {
        "auth": "response",
        "client_nonce": client_nonce,
        "proof": _auth_proof(key, client_nonce, server_nonce, "client"),
    })
    result = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
    if not result.get("ok"):
        retry_after = int(result.get("retry_after") or 0)
        detail = str(result.get("error") or "传输码错误")
        if retry_after:
            detail += f"，请在 {retry_after} 秒后重试"
        raise RuntimeError(detail)
    expected_server_proof = _auth_proof(key, client_nonce, server_nonce, "server")
    if not hmac.compare_digest(str(result.get("server_proof") or ""), expected_server_proof):
        raise RuntimeError("无法验证发送方身份，传输已终止")
    return True


def _broadcast_targets(ip: str) -> list[str]:
    """返回通用广播和当前 /24 网段定向广播地址。"""
    targets = ["255.255.255.255"]
    parts = ip.split(".")
    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        directed = ".".join(parts[:3] + ["255"])
        if directed not in targets:
            targets.append(directed)
    return targets


def _same_subnet_candidates(ip: str) -> list[str]:
    """在没有管理员权限/第三方库时，保守扫描当前 IPv4 /24 网段。"""
    parts = ip.split(".")
    if len(parts) != 4 or not all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        return []
    if ip.startswith("127."):
        return []
    prefix = ".".join(parts[:3])
    return [f"{prefix}.{host}" for host in range(1, 255) if f"{prefix}.{host}" != ip]


def _share_announcement(host: str, ip: str, names: list[str]) -> dict:
    return {
        "app": "passer-share",
        "host": host,
        "ip": ip,
        "tcp": TCP_TRANSFER_PORT,
        "count": len(names),
        "items": names[:8],
    }


def probe_share_host(ip: str, port: int = TCP_TRANSFER_PORT) -> dict | None:
    """主动探测一个 IP 是否正在共享。用于校园网禁用 UDP 广播的场景。"""
    try:
        sock = socket.create_connection((ip, port), timeout=DISCOVERY_CONNECT_TIMEOUT)
    except Exception:
        return None
    try:
        sock.settimeout(DISCOVERY_CONNECT_TIMEOUT)
        _send_json(sock, {"probe": "discover"})
        msg = json.loads((_recv_line(sock).decode("utf-8") or "{}"))
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass
    if isinstance(msg, dict) and msg.get("app") == "passer-share" and msg.get("count", 0):
        return msg
    return None

def _safe_name(name: str) -> str:
    name = os.path.basename(str(name))
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*')
    return cleaned[:120] or "received.bin"


def _unique_path(folder, name: str) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    stem, suffix = dest.stem, dest.suffix
    index = 2
    while dest.exists():
        dest = folder / f"{stem}_{index}{suffix}"
        index += 1
    return dest


def _format_bytes(size: int | float) -> str:
    """把字节数格式化成适合进度区域显示的短文本。"""
    value = max(0.0, float(size or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return "0 B"


def _build_payload(shares: list[str], _store_dir) -> tuple[str, int, str, bool, str | None]:
    """把共享内容打包成可传输的实体。

    返回 (展示名, 字节数, 实际读取路径, 是否为压缩包, 临时文件路径或 None)。
    单个文件直接传原文件；文件夹或多项则现场打成 ZIP。
    """
    if len(shares) == 1 and os.path.isfile(shares[0]):
        path = shares[0]
        return os.path.basename(path), os.path.getsize(path), path, False, None

    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="passer_share_")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for src in shares:
                src = src.rstrip("/\\")
                if os.path.isdir(src):
                    base = os.path.basename(src) or "folder"
                    for root_dir, _dirs, files in os.walk(src):
                        for fn in files:
                            full = os.path.join(root_dir, fn)
                            arc = os.path.join(base, os.path.relpath(full, src))
                            zf.write(full, arc)
                    if not os.listdir(src):
                        zf.writestr(base + "/", "")
                elif os.path.isfile(src):
                    zf.write(src, os.path.basename(src))
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

    if len(shares) == 1:
        base = os.path.basename(shares[0].rstrip("/\\")) or "共享"
        name = base + ".zip"
    else:
        name = f"共享_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return name, os.path.getsize(tmp), tmp, True, tmp


class TransferCancelled(RuntimeError):
    pass


class ChecksumMismatch(RuntimeError):
    pass


def _sha256_file(path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fobj:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise TransferCancelled("传输已取消")
            chunk = fobj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resume_paths(save_dir, name: str, remote_id: str) -> tuple[Path, Path]:
    folder = Path(save_dir)
    folder.mkdir(parents=True, exist_ok=True)
    remote = "".join(ch if ch.isalnum() else "_" for ch in str(remote_id))[:40] or "remote"
    part = folder / f".{_safe_name(name)}.{remote}.passer.part"
    return part, Path(str(part) + ".json")


def _read_resume_meta(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _write_resume_meta(path: Path, metadata: dict) -> None:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _remove_resume_files(part: Path, meta: Path) -> None:
    for path in (part, meta):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def download_from(
    ip: str,
    port: int,
    code: str,
    save_dir,
    progress=None,
    cancel_event: threading.Event | None = None,
) -> str:
    """接收共享内容；优先使用挑战鉴权，旧端自动降级到兼容协议。"""
    if not _valid_transfer_code(code):
        raise ValueError("传输码必须是 4–8 位数字")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    part: Path | None = None
    meta_path: Path | None = None
    try:
        sock.connect((ip, port))
        if not _authenticate_client_v3(sock, str(code)):
            # 老版本不认识 challenge/response；重新连接后使用 v2 兼容握手。
            # 只有这里会发送明文传输码，新版 Passer 之间始终使用 v3。
            try:
                sock.close()
            except OSError:
                pass
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15)
            sock.connect((ip, port))
            _send_json(sock, {"code": str(code), "protocol": RESUME_PROTOCOL})
        header = json.loads((_recv_line(sock).decode("utf-8") or "{}"))
        if not header.get("ok"):
            raise RuntimeError(header.get("error") or "对方拒绝了传输")
        name = _safe_name(header.get("name") or "received.bin")
        size = max(0, int(header.get("size") or 0))
        protocol = int(header.get("protocol") or 1)
        expected_hash = str(header.get("sha256") or "").strip().lower()
        part, meta_path = _resume_paths(save_dir, name, ip)
        metadata = {"name": name, "size": size, "sha256": expected_hash}
        received = 0

        if protocol >= RESUME_PROTOCOL and expected_hash:
            old_meta = _read_resume_meta(meta_path)
            if old_meta == metadata and part.exists():
                try:
                    received = min(size, max(0, part.stat().st_size))
                except OSError:
                    received = 0
            else:
                _remove_resume_files(part, meta_path)
            _write_resume_meta(meta_path, metadata)
            _send_json(sock, {"offset": received})
            resume_ack = json.loads((_recv_line(sock).decode("utf-8") or "{}"))
            if not resume_ack.get("ok"):
                raise RuntimeError(resume_ack.get("error") or "对方拒绝断点续传")
            accepted = max(0, min(size, int(resume_ack.get("offset") or 0)))
            if accepted != received:
                received = accepted
                with open(part, "a+b") as fobj:
                    fobj.truncate(received)
            mode = "ab" if received else "wb"
        else:
            # 旧版发送端在首个响应后直接发送正文，不能进行续传握手。
            _remove_resume_files(part, meta_path)
            mode = "wb"

        if progress:
            progress(received, size)
        sock.settimeout(0.75)
        last_data_at = time.monotonic()
        with open(part, mode) as fobj:
            while received < size:
                if cancel_event is not None and cancel_event.is_set():
                    raise TransferCancelled("传输已取消，已保留断点")
                try:
                    chunk = sock.recv(min(BUFSIZE, size - received))
                except socket.timeout:
                    if time.monotonic() - last_data_at >= TRANSFER_IDLE_TIMEOUT:
                        raise RuntimeError("传输超时，已保留断点")
                    continue
                if not chunk:
                    break
                fobj.write(chunk)
                received += len(chunk)
                last_data_at = time.monotonic()
                if progress:
                    progress(received, size)
        if received < size:
            raise RuntimeError("传输中断，已保留断点，可点击重试继续")

        if expected_hash:
            actual_hash = _sha256_file(part, cancel_event)
            if actual_hash.lower() != expected_hash:
                _remove_resume_files(part, meta_path)
                raise ChecksumMismatch("SHA-256 校验失败，损坏的临时文件已清除，请重试")

        dest = _unique_path(save_dir, name)
        os.replace(part, dest)
        try:
            meta_path.unlink()
        except OSError:
            pass
        return str(dest)
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 发送方服务：常驻 app，窗口关闭后仍继续共享
# ---------------------------------------------------------------------------
class FileShareService:
    def __init__(self, app):
        self.app = app
        self.code = ""
        self.shares: list[str] = []
        self.hostname = socket.gethostname()
        self.local_ip = local_ip()
        self._lock = threading.Lock()
        self._serving = False
        self._server_sock: socket.socket | None = None
        self._transfer_serial = 0
        self._active_transfer_events: dict[int, threading.Event] = {}
        self._active_transfer_sockets: dict[int, socket.socket] = {}
        self._checksum_cache: dict[str, tuple[int, int, str]] = {}
        self._auth_failures: dict[str, list[float]] = {}
        self._auth_blocked_until: dict[str, float] = {}
        self._threads: set[threading.Thread] = set()

    def set_code(self, code: str) -> None:
        normalized = str(code or "").strip()
        if normalized and not _valid_transfer_code(normalized):
            raise ValueError("传输码必须是 4–8 位数字")
        with self._lock:
            self.code = normalized
            self._auth_failures.clear()
            self._auth_blocked_until.clear()

    def is_sharing(self) -> bool:
        return bool(self.shares)

    def set_shares(self, paths: list[str]) -> None:
        """用所选项目替换当前共享集合，并确保服务运行。"""
        cleaned: list[str] = []
        for p in paths:
            p = str(p)
            if os.path.exists(p) and p not in cleaned:
                cleaned.append(p)
        with self._lock:
            self.shares = cleaned
        if cleaned:
            self._ensure_running()

    def stop_sharing(self) -> None:
        with self._lock:
            self.shares = []
        self._serving = False
        srv = self._server_sock
        self._server_sock = None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass
        self.cancel_active_transfer()
        deadline = time.monotonic() + 3.0
        with self._lock:
            workers = list(self._threads)
        current = threading.current_thread()
        for worker in workers:
            if worker is current or not worker.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)

    def has_active_transfer(self) -> bool:
        with self._lock:
            return bool(self._active_transfer_events)

    def cancel_active_transfer(self) -> bool:
        """取消当前所有发送连接；接收方会保留已完成的断点。"""
        with self._lock:
            events = list(self._active_transfer_events.values())
            sockets = list(self._active_transfer_sockets.values())
        for event in events:
            event.set()
        for active_socket in sockets:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return bool(events)

    def _auth_retry_after(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            blocked_until = self._auth_blocked_until.get(ip, 0.0)
            if blocked_until > now:
                return max(1, int(blocked_until - now + 0.999))
            self._auth_blocked_until.pop(ip, None)
            attempts = [
                moment for moment in self._auth_failures.get(ip, [])
                if now - moment <= AUTH_FAILURE_WINDOW
            ]
            if attempts:
                self._auth_failures[ip] = attempts
            else:
                self._auth_failures.pop(ip, None)
        return 0

    def _record_auth_failure(self, ip: str) -> int:
        now = time.monotonic()
        with self._lock:
            attempts = [
                moment for moment in self._auth_failures.get(ip, [])
                if now - moment <= AUTH_FAILURE_WINDOW
            ]
            attempts.append(now)
            self._auth_failures[ip] = attempts
            if len(attempts) >= AUTH_MAX_FAILURES:
                self._auth_blocked_until[ip] = now + AUTH_BLOCK_SECONDS
                self._auth_failures.pop(ip, None)
                return int(AUTH_BLOCK_SECONDS)
        return 0

    def _clear_auth_failures(self, ip: str) -> None:
        with self._lock:
            self._auth_failures.pop(ip, None)
            self._auth_blocked_until.pop(ip, None)

    def refresh_local_ip(self) -> None:
        self.local_ip = local_ip()

    def _announcement(self) -> dict | None:
        with self._lock:
            names = [os.path.basename(p.rstrip("/\\")) for p in self.shares]
        if not names:
            return None
        return _share_announcement(self.hostname, self.local_ip, names)

    # -- internals -------------------------------------------------------
    def _start_worker(self, target, *args, name: str) -> threading.Thread:
        def run() -> None:
            try:
                target(*args)
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())

        worker = threading.Thread(target=run, daemon=True, name=name)
        with self._lock:
            self._threads.add(worker)
        worker.start()
        return worker

    def _post_status(self, msg: str) -> None:
        try:
            self.app.root.after(0, lambda: self.app.write_status(msg))
        except Exception:
            pass

    def _next_transfer_id(self) -> int:
        with self._lock:
            self._transfer_serial += 1
            return self._transfer_serial

    def _payload_checksum(self, path: str, cancel_event: threading.Event) -> str:
        try:
            stat = os.stat(path)
            signature = (int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            return _sha256_file(path, cancel_event)
        with self._lock:
            cached = self._checksum_cache.get(path)
        if cached and cached[:2] == signature:
            return cached[2]
        checksum = _sha256_file(path, cancel_event)
        with self._lock:
            self._checksum_cache[path] = (signature[0], signature[1], checksum)
            if len(self._checksum_cache) > 32:
                oldest = next(iter(self._checksum_cache))
                self._checksum_cache.pop(oldest, None)
        return checksum

    def _post_transfer_progress(
        self,
        transfer_id: int,
        ip: str,
        name: str,
        done: int,
        total: int,
        started: float,
        state: str = "active",
        error: str = "",
    ) -> None:
        """把发送进度安全地投递到当前打开的文件共享窗口。"""
        def deliver() -> None:
            window = getattr(self.app, "file_share_window", None)
            if window is None or getattr(window, "closed", True):
                return
            window.update_service_transfer_progress(
                transfer_id, ip, name, done, total, started, state, error
            )

        try:
            self.app.root.after(0, deliver)
        except Exception:
            pass

    def _ensure_running(self) -> None:
        if self._serving:
            return
        self._serving = True
        self._start_worker(self._serve_loop, name="Passer-Share-Server")
        self._start_worker(self._broadcast_loop, name="Passer-Share-Broadcast")

    def _serve_loop(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("", TCP_TRANSFER_PORT))
            srv.listen(5)
            srv.settimeout(1.0)
            self._server_sock = srv
        except Exception as exc:
            self._serving = False
            self._post_status(f"文件共享端口启动失败：{exc}")
            return
        while self._serving:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._start_worker(self._handle_client, conn, addr, name="Passer-Share-Client")
        try:
            srv.close()
        except Exception:
            pass

    def _broadcast_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            while self._serving:
                announcement = self._announcement()
                if announcement:
                    payload = json.dumps(announcement, ensure_ascii=False).encode("utf-8")
                    for target in _broadcast_targets(self.local_ip):
                        try:
                            sock.sendto(payload, (target, UDP_DISCOVERY_PORT))
                        except Exception:
                            pass
                time.sleep(BEACON_INTERVAL)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _handle_client(self, conn: socket.socket, addr) -> None:
        ip = addr[0]
        transfer_id: int | None = None
        transfer_name = "共享内容"
        transfer_size = 0
        sent = 0
        started = time.monotonic()
        cancel_event: threading.Event | None = None
        tmp: str | None = None
        try:
            conn.settimeout(60)
            req = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
            if req.get("probe") == "discover":
                announcement = self._announcement()
                _send_json(conn, announcement or {"app": "passer-share", "ok": False})
                return
            protocol = max(1, int(req.get("protocol") or 1))
            with self._lock:
                shares = list(self.shares)
                my_code = self.code
            if not my_code:
                _send_json(conn, {"ok": False, "auth": "error", "error": "对方未设置传输码"})
                return
            if not shares:
                _send_json(conn, {"ok": False, "auth": "error", "error": "对方当前没有共享内容"})
                return

            retry_after = self._auth_retry_after(ip)
            if retry_after:
                _send_json(conn, {
                    "ok": False, "auth": "blocked", "error": "鉴权尝试过于频繁",
                    "retry_after": retry_after,
                })
                return

            if protocol >= TRANSFER_PROTOCOL and req.get("auth") == "hello":
                client_nonce = str(req.get("client_nonce") or "")
                if len(client_nonce) != AUTH_NONCE_BYTES * 2:
                    blocked_for = self._record_auth_failure(ip)
                    _send_json(conn, {
                        "ok": False, "auth": "error", "error": "鉴权请求无效",
                        "retry_after": blocked_for,
                    })
                    return
                server_nonce = secrets.token_hex(AUTH_NONCE_BYTES)
                _send_json(conn, {
                    "protocol": TRANSFER_PROTOCOL,
                    "auth": "challenge",
                    "server_nonce": server_nonce,
                    "rounds": AUTH_PBKDF2_ROUNDS,
                })
                response = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
                key = _auth_key(my_code, server_nonce)
                expected = _auth_proof(key, client_nonce, server_nonce, "client")
                received = str(response.get("proof") or "")
                valid = (
                    response.get("auth") == "response"
                    and str(response.get("client_nonce") or "") == client_nonce
                    and hmac.compare_digest(received, expected)
                )
                if not valid:
                    blocked_for = self._record_auth_failure(ip)
                    _send_json(conn, {
                        "ok": False, "auth": "failed", "error": "传输码错误",
                        "retry_after": blocked_for,
                    })
                    self._post_status(f"已拒绝 {ip}：传输码错误。")
                    return
                self._clear_auth_failures(ip)
                _send_json(conn, {
                    "ok": True,
                    "auth": "ok",
                    "server_proof": _auth_proof(key, client_nonce, server_nonce, "server"),
                })
            else:
                # v1/v2 兼容入口：保留旧客户端可用性，但同样执行失败次数限流。
                code = str(req.get("code", "")).strip()
                if not hmac.compare_digest(code, my_code):
                    blocked_for = self._record_auth_failure(ip)
                    _send_json(conn, {
                        "ok": False, "error": "传输码错误", "retry_after": blocked_for,
                    })
                    self._post_status(f"已拒绝 {ip}：传输码错误。")
                    return
                self._clear_auth_failures(ip)

            if not self._serving:
                _send_json(conn, {"ok": False, "error": "共享服务正在关闭"})
                return

            name, size, src_path, is_zip, tmp = _build_payload(shares, self.app.store_dir)
            transfer_id = self._next_transfer_id()
            cancel_event = threading.Event()
            with self._lock:
                self._active_transfer_events[transfer_id] = cancel_event
                self._active_transfer_sockets[transfer_id] = conn
            transfer_name = name
            transfer_size = size
            started = time.monotonic()
            self._post_transfer_progress(transfer_id, ip, name, 0, size, started)

            offset = 0
            if protocol >= RESUME_PROTOCOL:
                self._post_status(f"正在准备校验：{name}")
                checksum = self._payload_checksum(src_path, cancel_event)
                _send_json(conn, {
                    "ok": True,
                    "protocol": TRANSFER_PROTOCOL,
                    "name": name,
                    "size": size,
                    "zip": is_zip,
                    "sha256": checksum,
                })
                resume_req = json.loads((_recv_line(conn).decode("utf-8") or "{}"))
                offset = max(0, min(size, int(resume_req.get("offset") or 0)))
                _send_json(conn, {"ok": True, "offset": offset})
            else:
                _send_json(conn, {"ok": True, "name": name, "size": size, "zip": is_zip})

            sent = offset
            self._post_transfer_progress(transfer_id, ip, name, sent, size, started)
            last_report = started
            with open(src_path, "rb") as fobj:
                if offset:
                    fobj.seek(offset)
                while sent < size:
                    if cancel_event.is_set():
                        raise TransferCancelled("发送已取消")
                    chunk = fobj.read(min(BUFSIZE, size - sent))
                    if not chunk:
                        break
                    conn.sendall(chunk)
                    sent += len(chunk)
                    now = time.monotonic()
                    if sent < size and now - last_report >= 0.1:
                        self._post_transfer_progress(transfer_id, ip, name, sent, size, started)
                        last_report = now
            if sent < size:
                raise RuntimeError("源文件在传输期间发生变化")
            self._post_transfer_progress(transfer_id, ip, name, sent, size, started, "complete")
            self._post_status(f"已发送给 {ip}：{name}")
        except TransferCancelled as exc:
            if transfer_id is not None:
                self._post_transfer_progress(
                    transfer_id, ip, transfer_name, sent, transfer_size,
                    started, "cancelled", str(exc),
                )
            self._post_status(f"已取消发送（{ip}）")
        except Exception as exc:
            cancelled = bool(cancel_event is not None and cancel_event.is_set())
            if transfer_id is not None:
                self._post_transfer_progress(
                    transfer_id, ip, transfer_name, sent, transfer_size,
                    started, "cancelled" if cancelled else "error",
                    "发送已取消" if cancelled else str(exc),
                )
            if cancelled:
                self._post_status(f"已取消发送（{ip}）")
            else:
                self._post_status(f"发送失败（{ip}）：{exc}")
        finally:
            if transfer_id is not None:
                with self._lock:
                    self._active_transfer_events.pop(transfer_id, None)
                    self._active_transfer_sockets.pop(transfer_id, None)
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 文件共享窗口（接收方界面 + 发送方状态）
# ---------------------------------------------------------------------------
class FileShareWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    PREFERRED_W = 1440
    PREFERRED_H = 1040
    MIN_W = 1080
    MIN_H = 840
    SCREEN_MARGIN_X = 48
    SCREEN_MARGIN_Y = 16

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.pending_share_paths: list[str] = []

        self.service = app.ensure_file_share_service()
        self.service.refresh_local_ip()
        self.local_ip = self.service.local_ip

        self.discovered: dict[str, dict] = {}
        self._device_index: list[str] = []
        self._listening = False
        self._udp_sock: socket.socket | None = None
        self._active_scan_running = False
        self._scan_executor: ThreadPoolExecutor | None = None
        self._last_active_scan = time.time()
        self._tick_after = None
        self._receiving = False
        self._receive_token = 0
        self._receive_label = ""
        self._receive_started = 0.0
        self._receive_done_bytes = 0
        self._receive_total_bytes = 0
        self._receive_cancel_event: threading.Event | None = None
        self._last_receive_request: tuple[str, int, str, str] | None = None
        self._service_transfer_id = 0

        self.code_var = tk.StringVar(value=self.service.code)
        self.lan_ip_var = tk.StringVar(value=f"局域网 IP：{self.local_ip}")
        self.wan_ip_var = tk.StringVar(value="广域网 IP：获取中…")
        self.status_var = tk.StringVar(value="就绪。优先通过局域网广播发现设备；可点刷新立即直连探测。")
        self.transfer_title_var = tk.StringVar(value="传输进度")
        self.transfer_percent_var = tk.StringVar(value="0%")
        self.transfer_detail_var = tk.StringVar(value="暂无传输任务")
        self.transfer_progress_var = tk.DoubleVar(value=0.0)

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        available_w, available_h = default_popup_manager.available_size_for_root(
            app.root,
            margin_x=self.SCREEN_MARGIN_X,
            margin_y=self.SCREEN_MARGIN_Y,
            fallback_width=480,
            fallback_height=420,
        )
        self.initial_w = min(self.PREFERRED_W, available_w)
        self.initial_h = min(self.PREFERRED_H, available_h)
        self.min_w = min(self.MIN_W, self.initial_w)
        self.min_h = min(self.MIN_H, self.initial_h)
        self.window.minsize(self.min_w, self.min_h)
        self.shell = tk.Frame(self.window, bg=theme.app_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = theme.center_over_root(app.root, self.initial_w, self.initial_h)
        theme.place_toplevel_absolute(self.window, self.initial_w, self.initial_h, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()

        self._start_listening()
        self.refresh_ip_info()
        self.refresh_mine()
        self._tick()

    # -- chrome ----------------------------------------------------------
    def _font(self, size=9, weight="normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self):
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text=t.title, bg=t.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "×", self.close, True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self):
        t = self.theme
        viewport = tk.Frame(self.shell, bg=t.surface_bg)
        viewport.pack(fill=tk.BOTH, expand=True)
        self.body_canvas = tk.Canvas(
            viewport, bg=t.surface_bg, bd=0, highlightthickness=0
        )
        self.body_scrollbar = tk.Scrollbar(
            viewport,
            orient=tk.VERTICAL,
            command=self.body_canvas.yview,
            bd=0,
            highlightthickness=0,
            width=12,
        )
        self._body_scrollbar_visible = False
        self.body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.body_canvas.configure(yscrollcommand=self.body_scrollbar.set)

        body = tk.Frame(self.body_canvas, bg=t.surface_bg)
        self.body_frame = body
        self.body_window_id = self.body_canvas.create_window(
            (0, 0), window=body, anchor=tk.NW
        )
        body.bind("<Configure>", self._sync_body_scrollregion)
        self.body_canvas.bind("<Configure>", self._resize_body_to_viewport)
        self.window.bind("<MouseWheel>", self._on_body_mousewheel, add="+")

        # 传输码 ---------------------------------------------------------
        tk.Label(body, text="传输码（4–8 位数字，推荐 6 位以上）", bg=t.surface_bg, fg="#334155",
                 font=self._font(10), anchor=tk.W).pack(fill=tk.X, padx=18, pady=(16, 6))
        code_row = tk.Frame(body, bg=t.surface_bg)
        code_row.pack(fill=tk.X, padx=18)
        code_entry = tk.Entry(code_row, textvariable=self.code_var, bd=0, bg="#f8fafc", fg="#111827",
                              insertbackground="#111827", highlightthickness=1, highlightbackground=t.border,
                              highlightcolor=t.accent, font=self._font(13, "bold"), justify=tk.CENTER, width=8)
        code_entry.pack(side=tk.LEFT, ipady=6)
        code_entry.configure(validate="key",
                             validatecommand=(code_entry.register(self._validate_code), "%P"))
        self.code_entry = code_entry
        self._button(code_row, "保存传输码", self.save_code, True).pack(side=tk.LEFT, padx=(10, 0))
        ip_row = tk.Frame(body, bg=t.surface_bg)
        ip_row.pack(fill=tk.X, padx=18, pady=(6, 4))
        ip_text = tk.Frame(ip_row, bg=t.surface_bg)
        ip_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(ip_text, textvariable=self.lan_ip_var,
                 bg=t.surface_bg, fg="#64748b", font=self._font(9), anchor=tk.W).pack(fill=tk.X)
        tk.Label(ip_text, textvariable=self.wan_ip_var,
                 bg=t.surface_bg, fg="#64748b", font=self._font(9), anchor=tk.W).pack(fill=tk.X, pady=(2, 0))
        self._button(ip_row, "刷新 IP", self.refresh_ip_info).pack(side=tk.RIGHT, padx=(10, 0))

        # 我的共享 -------------------------------------------------------
        mine_head = tk.Frame(body, bg=t.surface_bg)
        mine_head.pack(fill=tk.X, padx=18, pady=(10, 4))
        tk.Label(mine_head, text="我的共享", bg=t.surface_bg, fg="#334155",
                 font=self._font(10, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        self.stop_btn = self._button(mine_head, "停止共享", self.stop_sharing)
        self.stop_btn.pack(side=tk.RIGHT)
        self._button(mine_head, "添加文件夹", self.add_share_folder).pack(side=tk.RIGHT, padx=(0, 8))
        self._button(mine_head, "添加文件", self.add_share_files).pack(side=tk.RIGHT, padx=(0, 8))
        self.mine_list = tk.Listbox(body, height=4, bd=0, bg="#f8fafc", fg="#111827",
                                    highlightthickness=1, highlightbackground=t.border,
                                    selectbackground=t.accent_soft, selectforeground="#111827",
                                    font=self._font(9), activestyle="none")
        self.mine_list.pack(fill=tk.X, padx=18)
        for widget in (self.mine_list, mine_head):
            self._register_share_drop_target(widget)

        # 局域网设备 -----------------------------------------------------
        dev_head = tk.Frame(body, bg=t.surface_bg)
        dev_head.pack(fill=tk.X, padx=18, pady=(14, 4))
        tk.Label(dev_head, text="局域网共享设备", bg=t.surface_bg, fg="#334155",
                 font=self._font(10, "bold"), anchor=tk.W).pack(side=tk.LEFT)
        self._button(dev_head, "接收所选", self.receive_selected, True).pack(side=tk.RIGHT)
        self._button(dev_head, "刷新/直连探测", self.refresh_devices).pack(side=tk.RIGHT, padx=(0, 8))
        self.device_list = tk.Listbox(body, bd=0, bg="#f8fafc", fg="#111827",
                                      highlightthickness=1, highlightbackground=t.border,
                                      selectbackground=t.accent_soft, selectforeground="#111827",
                                      font=self._font(10), activestyle="none")
        self.device_list.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 4))
        self.device_list.bind("<Double-Button-1>", lambda _e: self.receive_selected())

        # 实时传输进度 ---------------------------------------------------
        progress_box = tk.Frame(body, bg="#f8fafc", highlightthickness=1,
                                highlightbackground=t.border)
        progress_box.pack(fill=tk.X, padx=18, pady=(8, 0))
        progress_head = tk.Frame(progress_box, bg="#f8fafc")
        progress_head.pack(fill=tk.X, padx=10, pady=(8, 4))
        progress_controls = tk.Frame(progress_head, bg="#f8fafc")
        progress_controls.pack(side=tk.RIGHT)
        self.retry_transfer_btn = self._button(
            progress_controls, "重试", self.retry_transfer
        )
        self.retry_transfer_btn.configure(padx=10, pady=4, font=self._font(8))
        self.retry_transfer_btn.pack(side=tk.RIGHT)
        self.cancel_transfer_btn = self._button(
            progress_controls, "取消传输", self.cancel_transfer
        )
        self.cancel_transfer_btn.configure(padx=10, pady=4, font=self._font(8))
        self.cancel_transfer_btn.pack(side=tk.RIGHT, padx=(0, 6))
        tk.Label(progress_controls, textvariable=self.transfer_percent_var, bg="#f8fafc",
                 fg=t.accent, font=self._font(9, "bold"), anchor=tk.E).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        tk.Label(progress_head, textvariable=self.transfer_title_var, bg="#f8fafc",
                 fg="#334155", font=self._font(9, "bold"), anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        style = ttk.Style(self.window)
        style.configure("Passer.Horizontal.TProgressbar", troughcolor="#e2e8f0",
                        background=t.accent, bordercolor="#e2e8f0", lightcolor=t.accent,
                        darkcolor=t.accent, thickness=10)
        self.transfer_progress = ttk.Progressbar(
            progress_box,
            orient=tk.HORIZONTAL,
            mode="determinate",
            maximum=100.0,
            variable=self.transfer_progress_var,
            style="Passer.Horizontal.TProgressbar",
        )
        self.transfer_progress.pack(fill=tk.X, padx=10)
        tk.Label(progress_box, textvariable=self.transfer_detail_var, bg="#f8fafc",
                 fg="#64748b", font=self._font(8), anchor=tk.W).pack(
            fill=tk.X, padx=10, pady=(4, 8)
        )
        self._set_transfer_buttons(active=False, retry=False)

        tk.Label(body, textvariable=self.status_var, bg=t.surface_bg, fg="#334155",
                 wraplength=1320, justify=tk.LEFT, anchor=tk.W, font=self._font(9)).pack(
            fill=tk.X, padx=18, pady=(8, 12))

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _sync_body_scrollregion(self, _event=None) -> None:
        if self.closed:
            return
        try:
            viewport_h = max(1, self.body_canvas.winfo_height())
            requested_h = max(1, self.body_frame.winfo_reqheight())
            needs_scroll = requested_h > viewport_h + 2
            if needs_scroll and not self._body_scrollbar_visible:
                self.body_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
                self._body_scrollbar_visible = True
            elif not needs_scroll and self._body_scrollbar_visible:
                self.body_scrollbar.pack_forget()
                self._body_scrollbar_visible = False
                self.body_canvas.yview_moveto(0)
            content_h = max(viewport_h, requested_h)
            self.body_canvas.itemconfigure(self.body_window_id, height=content_h)
            self.body_canvas.configure(
                scrollregion=(0, 0, max(1, self.body_canvas.winfo_width()), content_h)
            )
        except Exception:
            pass

    def _resize_body_to_viewport(self, event) -> None:
        try:
            self.body_canvas.itemconfigure(self.body_window_id, width=max(1, event.width))
        except Exception:
            return
        self._sync_body_scrollregion()

    def _on_body_mousewheel(self, event):
        """窗口内容超过可视高度时，滚轮可抵达最下面的进度与状态区域。"""
        if event.widget in (getattr(self, "mine_list", None), getattr(self, "device_list", None)):
            return None
        try:
            first, last = self.body_canvas.yview()
            if first <= 0.0 and last >= 1.0:
                return None
            steps = -int(event.delta / 120) if event.delta else 0
            if not steps:
                steps = -1 if event.delta > 0 else 1
            self.body_canvas.yview_scroll(steps, "units")
            return "break"
        except Exception:
            return None

    def _button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bd=0, padx=14, pady=7,
                         bg=(self.theme.accent if primary else "#eef2f9"),
                         fg=("#ffffff" if primary else "#1f2937"),
                         activebackground=(self.theme.accent_hover if primary else "#e2e8f4"),
                         activeforeground=("#ffffff" if primary else "#111827"),
                         cursor="hand2", font=self._font(9, "bold" if primary else "normal"))

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                           bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                           activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=self.theme.title_button_bg))
        return button

    def _register_share_drop_target(self, widget) -> None:
        if not TKDND_AVAILABLE or DND_FILES is None:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._on_share_drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._on_share_drop_enter)
            widget.dnd_bind("<<DropLeave>>", self._on_share_drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_share_drop)
        except Exception:
            pass

    def _set_share_drop_hover(self, active: bool) -> None:
        try:
            self.mine_list.configure(
                highlightbackground=(self.theme.accent if active else self.theme.border),
                highlightcolor=(self.theme.accent if active else self.theme.border),
            )
        except Exception:
            pass

    def _drop_paths_from_event(self, event) -> list[str]:
        raw_data = getattr(event, "data", "")
        try:
            return [str(path) for path in self.window.tk.splitlist(raw_data)]
        except Exception:
            return [str(raw_data)] if raw_data else []

    def _on_share_drop_enter(self, _event):
        self._set_share_drop_hover(True)
        return COPY

    def _on_share_drop_leave(self, _event):
        self._set_share_drop_hover(False)
        return COPY

    def _on_share_drop(self, event):
        self._set_share_drop_hover(False)
        paths = self._drop_paths_from_event(event)
        if paths:
            self._add_shares(paths)
        return COPY

    def refresh_ip_info(self) -> None:
        self.service.refresh_local_ip()
        self.local_ip = self.service.local_ip
        try:
            self.lan_ip_var.set(f"局域网 IP：{self.local_ip}")
            self.wan_ip_var.set("广域网 IP：获取中…")
        except Exception:
            pass

        def work() -> None:
            ip = public_ip()
            text = f"广域网 IP：{ip}" if ip else "广域网 IP：获取失败"
            self._post(lambda: self.wan_ip_var.set(text))

        threading.Thread(target=work, daemon=True, name="Passer-PublicIP").start()

    # -- transfer code ---------------------------------------------------
    @staticmethod
    def _validate_code(proposed: str) -> bool:
        return proposed == "" or (proposed.isdigit() and len(proposed) <= 4)

    def focus_code_entry(self) -> None:
        try:
            self.code_entry.focus_set()
        except Exception:
            pass

    def save_code(self) -> None:
        code = self.code_var.get().strip()
        if not _valid_transfer_code(code):
            messagebox.showinfo("传输码", "请输入 4–8 位数字传输码，推荐使用 6 位以上。", parent=self.window)
            return
        self.service.set_code(code)
        self.app.file_share_code = code
        try:
            self.app.save()
        except Exception:
            pass
        self.app.write_status("传输码已保存。")
        if self.pending_share_paths:
            self.service.set_shares(self.pending_share_paths)
            self.pending_share_paths = []
            self.app.write_status("已开始共享，等待对方接收。")
        self.refresh_mine()

    # -- my shares -------------------------------------------------------
    def refresh_mine(self) -> None:
        if self.closed:
            return
        shares = list(self.service.shares)
        try:
            self.mine_list.delete(0, tk.END)
            if shares:
                for p in shares:
                    label = ("📁 " if os.path.isdir(p) else "📄 ") + os.path.basename(p.rstrip("/\\"))
                    self.mine_list.insert(tk.END, label)
            else:
                self.mine_list.insert(tk.END, "（暂无共享，可把文件/文件夹拖到这里，或右键选择「共享」）")
            self.stop_btn.configure(state=tk.NORMAL if shares else tk.DISABLED)
        except Exception:
            pass

    def stop_sharing(self) -> None:
        self.service.stop_sharing()
        self.refresh_mine()
        self.app.write_status("已停止共享。")

    def add_share_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.window, title="添加共享文件")
        if paths:
            self._add_shares(list(paths))

    def add_share_folder(self) -> None:
        path = filedialog.askdirectory(parent=self.window, title="添加共享文件夹")
        if path:
            self._add_shares([path])

    def _add_shares(self, new_paths: list[str]) -> None:
        new_paths = [p for p in new_paths if p and os.path.exists(p)]
        if not new_paths:
            return
        merged = list(self.service.shares)
        for p in new_paths:
            if p not in merged:
                merged.append(p)
        self.service.set_shares(merged)
        self.refresh_mine()
        if not self.service.code:
            self.focus_code_entry()
            self.app.write_status("已添加共享；请设置 4–8 位数字传输码，对方才能接收。")
        else:
            self.app.write_status(f"已添加共享，共 {len(self.service.shares)} 项。")

    # -- discovery / receive --------------------------------------------
    def _start_listening(self) -> None:
        self._listening = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _remember_device(self, ip: str, msg: dict) -> None:
        if ip == self.local_ip:
            return
        try:
            count = int(msg.get("count") or 0)
            tcp = int(msg.get("tcp") or TCP_TRANSFER_PORT)
        except Exception:
            return
        if count <= 0:
            return
        self.discovered[ip] = {
            "host": str(msg.get("host") or ip),
            "count": count,
            "items": list(msg.get("items") or []),
            "tcp": tcp,
            "last": time.time(),
        }

    def _start_active_scan(self) -> None:
        if self._active_scan_running or self.closed:
            return
        candidates = _same_subnet_candidates(self.local_ip)
        if not candidates:
            return
        self._active_scan_running = True
        self._last_active_scan = time.time()
        threading.Thread(target=self._active_scan_loop, args=(candidates,), daemon=True).start()

    def _active_scan_loop(self, candidates: list[str]) -> None:
        found = 0
        pool = ThreadPoolExecutor(max_workers=DISCOVERY_SCAN_WORKERS)
        self._scan_executor = pool
        try:
            futures = {pool.submit(probe_share_host, ip): ip for ip in candidates}
            for future in as_completed(futures):
                if self.closed or not self._listening:
                    break
                ip = futures[future]
                try:
                    msg = future.result()
                except Exception:
                    continue
                if msg:
                    self._remember_device(ip, msg)
                    found += 1
        finally:
            for future in locals().get("futures", {}):
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            if self._scan_executor is pool:
                self._scan_executor = None
            self._active_scan_running = False
        if found:
            self._post(lambda: self._rebuild_device_list())

    def _listen_loop(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.bind(("", UDP_DISCOVERY_PORT))
            sock.settimeout(1.0)
            self._udp_sock = sock
        except Exception as exc:
            try:
                self.app.root.after(0, lambda: self.status_var.set(f"无法监听局域网：{exc}"))
            except Exception:
                pass
            return
        while self._listening:
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("app") != "passer-share":
                continue
            ip = str(msg.get("ip") or addr[0])
            self._remember_device(ip, msg)
        try:
            sock.close()
        except Exception:
            pass

    def _tick(self) -> None:
        if self.closed:
            return
        now = time.time()
        for ip in [ip for ip, info in self.discovered.items() if now - info["last"] > DEVICE_TTL]:
            self.discovered.pop(ip, None)
        # 广播没有发现任何设备时才低频回退扫描；已有设备或窗口关闭时不再扫整段网段。
        if not self.discovered and now - self._last_active_scan > ACTIVE_SCAN_INTERVAL:
            self._start_active_scan()
        self._rebuild_device_list()
        self.refresh_mine()
        try:
            self._tick_after = self.window.after(1000, self._tick)
        except Exception:
            pass

    def refresh_devices(self) -> None:
        """清空已发现设备并重新扫描（广播和直连探测会同时进行）。"""
        self.discovered.clear()
        self._rebuild_device_list()
        self._start_active_scan()
        self.status_var.set("正在重新扫描局域网设备……")

    def _rebuild_device_list(self) -> None:
        selected_ip = self._selected_device_ip()
        order = sorted(self.discovered.items(), key=lambda kv: kv[1]["host"].lower())
        self._device_index = [ip for ip, _ in order]
        try:
            self.device_list.delete(0, tk.END)
            for ip, info in order:
                preview = "、".join(info["items"][:3])
                if info["count"] > 3:
                    preview += " 等"
                line = f"{info['host']}  ({ip})  ·  {info['count']} 项" + (f"：{preview}" if preview else "")
                self.device_list.insert(tk.END, line)
            if selected_ip in self._device_index:
                idx = self._device_index.index(selected_ip)
                self.device_list.selection_set(idx)
        except Exception:
            pass

    def _selected_device_ip(self) -> str | None:
        try:
            sel = self.device_list.curselection()
        except Exception:
            return None
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self._device_index):
            return self._device_index[idx]
        return None

    def receive_selected(self) -> None:
        ip = self._selected_device_ip()
        if not ip:
            self.status_var.set("请先在列表中选择一个设备。")
            return
        info = self.discovered.get(ip)
        if not info:
            self.status_var.set("该设备已离线，请刷新后重试。")
            return
        was_topmost = bool(self.app.topmost_var.get())
        if was_topmost:
            try:
                self.window.attributes("-topmost", False)
            except Exception:
                pass
        code = simpledialog.askstring(
            "接收文件",
            f"请输入 {info['host']} ({ip}) 的 4–8 位传输码：",
            parent=self.window,
        )
        if was_topmost and not self.closed:
            try:
                self.window.attributes("-topmost", True)
            except Exception:
                pass
        if code is None:
            return
        code = code.strip()
        if not _valid_transfer_code(code):
            messagebox.showinfo("传输码", "请输入 4–8 位数字传输码。", parent=self.window)
            return
        port = int(info.get("tcp", TCP_TRANSFER_PORT))
        self._receive_from_address(ip, port, code, f"{info['host']} ({ip})")

    def _receive_from_address(self, host: str, port: int, code: str, label: str) -> None:
        if self._receiving:
            self.status_var.set("已有文件正在接收，请等待当前传输完成。")
            return
        self._receiving = True
        self._receive_token += 1
        token = self._receive_token
        self._receive_label = label
        self._receive_started = time.monotonic()
        self._receive_done_bytes = 0
        self._receive_total_bytes = 0
        self._receive_cancel_event = threading.Event()
        self._last_receive_request = (host, int(port), str(code), label)
        self.status_var.set(f"正在从 {label} 接收……")
        self._set_transfer_buttons(active=True, retry=False)
        self._render_transfer_progress(
            "接收", label, 0, 0, self._receive_started, "active"
        )

        def work() -> None:
            last_report = [0.0]

            def report(received: int, total: int) -> None:
                now = time.monotonic()
                if received < total and received > 0 and now - last_report[0] < 0.1:
                    return
                last_report[0] = now
                self._post(
                    lambda d=received, t=total: self._receive_progress(token, d, t)
                )

            try:
                dest = download_from(
                    host, port, code, self.app.store_dir, progress=report,
                    cancel_event=self._receive_cancel_event,
                )
            except TransferCancelled as exc:
                msg = str(exc)
                self._post(lambda: self._receive_done(None, msg, token, "cancelled"))
                return
            except Exception as exc:
                msg = str(exc)
                self._post(lambda: self._receive_done(None, msg, token, "error"))
                return
            self._post(lambda: self._receive_done(dest, None, token))

        threading.Thread(target=work, daemon=True).start()

    def _render_transfer_progress(
        self,
        direction: str,
        label: str,
        done: int,
        total: int,
        started: float,
        state: str = "active",
        error: str = "",
    ) -> None:
        if self.closed:
            return
        done = max(0, int(done or 0))
        total = max(0, int(total or 0))
        if state == "complete":
            percent = 100.0
        elif total:
            percent = min(100.0, done * 100.0 / total)
        else:
            percent = 0.0
        elapsed = max(0.001, time.monotonic() - started) if started else 0.0
        speed = done / elapsed if elapsed else 0.0

        if state == "complete":
            title = f"{direction}完成：{label}"
        elif state == "cancelled":
            title = f"{direction}已取消：{label}"
        elif state == "error":
            title = f"{direction}失败：{label}"
        else:
            title = f"正在{direction}：{label}"
        self.transfer_title_var.set(title)
        self.transfer_percent_var.set(
            f"{percent:.0f}%" if total or state == "complete" else "准备中"
        )
        self.transfer_progress_var.set(percent)

        amounts = f"{_format_bytes(done)} / {_format_bytes(total)}" if total else _format_bytes(done)
        speed_text = f" · {_format_bytes(speed)}/秒" if speed > 0 else ""
        if state == "complete":
            detail = f"{amounts}{speed_text} · 已完成"
        elif state == "cancelled":
            detail = f"{amounts}{speed_text} · {error or '已取消，可重试续传'}"
        elif state == "error":
            detail = f"{amounts}{speed_text} · {error or '传输中断'}"
        else:
            detail = f"{amounts}{speed_text}"
        self.transfer_detail_var.set(detail)

    def update_service_transfer_progress(
        self,
        transfer_id: int,
        ip: str,
        name: str,
        done: int,
        total: int,
        started: float,
        state: str = "active",
        error: str = "",
    ) -> None:
        """由常驻发送服务在 Tk 主线程中调用。"""
        if transfer_id < self._service_transfer_id:
            return
        self._service_transfer_id = transfer_id
        if self._receiving:
            return
        self._render_transfer_progress(
            "发送", f"{name} → {ip}", done, total, started, state, error
        )
        self._set_transfer_buttons(active=(state == "active"), retry=False)

    def _receive_progress(self, token: int, received: int, total: int) -> None:
        if token != self._receive_token or not self._receiving:
            return
        self._receive_done_bytes = received
        self._receive_total_bytes = total
        self._render_transfer_progress(
            "接收",
            self._receive_label,
            received,
            total,
            self._receive_started,
            "active",
        )
        self._set_transfer_buttons(active=True, retry=False)

    def _post(self, func) -> None:
        try:
            self.app.root.after(0, func)
        except Exception:
            pass

    def _receive_done(
        self,
        dest: str | None,
        err: str | None,
        token: int | None = None,
        state: str = "error",
    ) -> None:
        if token is not None and token != self._receive_token:
            return
        self._receiving = False
        self._receive_cancel_event = None
        if err:
            self._render_transfer_progress(
                "接收",
                self._receive_label,
                self._receive_done_bytes,
                self._receive_total_bytes,
                self._receive_started,
                state,
                err,
            )
            self._set_transfer_buttons(active=False, retry=True)
            if state == "cancelled":
                self.status_var.set("接收已取消，断点已保留，可点击重试继续。")
            else:
                self.status_var.set(f"接收失败：{err}")
            if state != "cancelled" and not self.closed:
                messagebox.showinfo("接收失败", err, parent=self.window)
            return
        try:
            self.app.add_paths([dest])
        except Exception:
            pass
        name = os.path.basename(dest) if dest else ""
        self._render_transfer_progress(
            "接收",
            name or self._receive_label,
            self._receive_total_bytes,
            self._receive_total_bytes,
            self._receive_started,
            "complete",
        )
        self.status_var.set(f"已接收并存入 Passer：{name}")
        self._set_transfer_buttons(active=False, retry=False)

    def _set_transfer_buttons(self, active: bool, retry: bool) -> None:
        try:
            self.cancel_transfer_btn.configure(state=(tk.NORMAL if active else tk.DISABLED))
            self.retry_transfer_btn.configure(state=(tk.NORMAL if retry else tk.DISABLED))
        except (AttributeError, tk.TclError):
            pass

    def cancel_transfer(self) -> None:
        if self._receiving and self._receive_cancel_event is not None:
            self._receive_cancel_event.set()
            self._set_transfer_buttons(active=False, retry=False)
            self.status_var.set("正在取消接收……")
            return
        if self.service.cancel_active_transfer():
            self._set_transfer_buttons(active=False, retry=False)
            self.status_var.set("正在取消发送……")

    def retry_transfer(self) -> None:
        if self._receiving:
            return
        request = self._last_receive_request
        if request is None:
            self.status_var.set("暂无可重试的接收任务。")
            return
        self._receive_from_address(*request)

    # -- window plumbing -------------------------------------------------
    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(self.window, max(self.window.winfo_width(), self.min_w),
                                           max(self.window.winfo_height(), self.min_h),
                                           wx + event.x_root - sx, wy + event.y_root - sy)

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._listening = False
        scan_executor = self._scan_executor
        self._scan_executor = None
        if scan_executor is not None:
            scan_executor.shutdown(wait=False, cancel_futures=True)
        if self._receive_cancel_event is not None:
            self._receive_cancel_event.set()
        self._receive_token += 1
        if self._tick_after is not None:
            try:
                self.window.after_cancel(self._tick_after)
            except Exception:
                pass
        sock = self._udp_sock
        self._udp_sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        if getattr(self.app, "file_share_window", None) is self:
            self.app.file_share_window = None
        self.window.destroy()
