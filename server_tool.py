from __future__ import annotations

import functools
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from clicker_tool import ClickerTheme


CONFIG_VERSION = 1
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@\\-]{0,127}$")
GATEWAY_AUTH_KEY = "key"
GATEWAY_AUTH_PASSWORD = "password"
GATEWAY_AUTH_LABELS = {
    GATEWAY_AUTH_KEY: "私钥 / SSH Agent",
    GATEWAY_AUTH_PASSWORD: "账户密码（连接时输入）",
}


@dataclass(frozen=True)
class ServerConfig:
    root: str
    bind_address: str = "127.0.0.1"
    port: int = 8080
    gateway_enabled: bool = False
    gateway_host: str = ""
    gateway_ssh_port: int = 22
    gateway_user: str = ""
    gateway_remote_address: str = "127.0.0.1"
    gateway_remote_port: int = 8080
    gateway_key_path: str = ""
    gateway_auth: str = GATEWAY_AUTH_KEY


def validate_port(value, *, name: str = "端口", allow_zero: bool = False) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是数字。") from exc
    minimum = 0 if allow_zero else 1
    if not minimum <= port <= 65535:
        suffix = "0–65535" if allow_zero else "1–65535"
        raise ValueError(f"{name}必须在 {suffix} 之间。")
    return port


def normalize_ipv4(value: str, *, name: str) -> str:
    raw = str(value or "").strip()
    if raw.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError(f"{name}必须是有效的 IPv4 地址。") from exc
    if address.version != 4:
        raise ValueError(f"{name}当前仅支持 IPv4 地址。")
    return str(address)


def normalize_gateway_host(value: str) -> str:
    host = str(value or "").strip()
    if not host or not HOST_RE.fullmatch(host):
        raise ValueError("网关主机必须是有效的域名或 IPv4 地址。")
    return host


def normalize_config(
    config: ServerConfig | dict,
    *,
    default_root: str | os.PathLike | None = None,
    allow_port_zero: bool = False,
) -> ServerConfig:
    raw = asdict(config) if isinstance(config, ServerConfig) else dict(config or {})
    root_raw = str(raw.get("root") or default_root or "").strip()
    if not root_raw:
        raise ValueError("请选择服务器目录。")
    root = Path(root_raw).expanduser().resolve()
    bind_address = normalize_ipv4(raw.get("bind_address", "127.0.0.1"), name="监听地址")
    port = validate_port(raw.get("port", 8080), name="本地端口", allow_zero=allow_port_zero)
    gateway_enabled = bool(raw.get("gateway_enabled", False))

    gateway_host = str(raw.get("gateway_host") or "").strip()
    gateway_user = str(raw.get("gateway_user") or "").strip()
    gateway_key_path = str(raw.get("gateway_key_path") or "").strip()
    gateway_auth = str(raw.get("gateway_auth") or GATEWAY_AUTH_KEY).strip().lower()
    gateway_ssh_port = validate_port(raw.get("gateway_ssh_port", 22), name="SSH 端口")
    gateway_remote_address = normalize_ipv4(
        raw.get("gateway_remote_address", "127.0.0.1"),
        name="网关监听地址",
    )
    gateway_remote_port = validate_port(
        raw.get("gateway_remote_port", 8080),
        name="网关端口",
    )

    if gateway_enabled:
        gateway_host = normalize_gateway_host(gateway_host)
        if not USER_RE.fullmatch(gateway_user):
            raise ValueError(
                "SSH 用户名只能包含英文、数字、点、下划线、@、反斜线和连字符。"
            )
        if gateway_auth not in (GATEWAY_AUTH_KEY, GATEWAY_AUTH_PASSWORD):
            raise ValueError("请选择有效的 SSH 认证方式。")
        if gateway_auth == GATEWAY_AUTH_KEY and gateway_key_path:
            key = Path(gateway_key_path).expanduser().resolve()
            if not key.is_file():
                raise ValueError("SSH 私钥文件不存在。")
            gateway_key_path = str(key)
        elif gateway_auth == GATEWAY_AUTH_PASSWORD:
            gateway_key_path = ""

    return ServerConfig(
        root=str(root),
        bind_address=bind_address,
        port=port,
        gateway_enabled=gateway_enabled,
        gateway_host=gateway_host,
        gateway_ssh_port=gateway_ssh_port,
        gateway_user=gateway_user,
        gateway_remote_address=gateway_remote_address,
        gateway_remote_port=gateway_remote_port,
        gateway_key_path=gateway_key_path,
        gateway_auth=gateway_auth,
    )


def load_config(path: str | os.PathLike, default_root: str | os.PathLike) -> ServerConfig:
    config_path = Path(path)
    defaults = ServerConfig(root=str(Path(default_root).resolve()))
    if not config_path.is_file():
        return defaults
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置文件格式错误。")
        payload.pop("version", None)
        return normalize_config(payload, default_root=default_root, allow_port_zero=False)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults


def save_config(path: str | os.PathLike, config: ServerConfig) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CONFIG_VERSION, **asdict(config)}
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, config_path)


def find_ssh_executable() -> str | None:
    found = shutil.which("ssh")
    if found:
        return found
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidate = windows / "System32" / "OpenSSH" / "ssh.exe"
    return str(candidate) if candidate.is_file() else None


def find_ssh_askpass() -> str | None:
    """Return a graphical askpass helper without ever persisting a password."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        if executable.is_file():
            return str(executable)

    configured = str(os.environ.get("GIT_ASKPASS") or "").strip().strip('"')
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("git-askpass")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git"
        / "mingw64"
        / "bin"
        / "git-askpass.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Git"
        / "mingw32"
        / "bin"
        / "git-askpass.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def run_ssh_askpass(prompt: str = "") -> int:
    """Ask OpenSSH for one password and write it only to the inherited pipe."""
    root = tk.Tk()
    root.withdraw()
    try:
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        question = str(prompt or "请输入 SSH 网关账户密码：").strip()
        password = simpledialog.askstring(
            "SSH 网关认证",
            question,
            show="*",
            parent=root,
        )
        if password is None:
            return 1
        sys.stdout.write(password + "\n")
        sys.stdout.flush()
        return 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def build_gateway_command(
    config: ServerConfig,
    local_port: int,
    *,
    ssh_executable: str = "ssh",
) -> list[str]:
    cfg = normalize_config(config, allow_port_zero=True)
    if not cfg.gateway_enabled:
        raise ValueError("网关尚未启用。")
    local_target = (
        "127.0.0.1" if cfg.bind_address == "0.0.0.0" else cfg.bind_address
    )
    remote = (
        f"{cfg.gateway_remote_address}:{cfg.gateway_remote_port}:"
        f"{local_target}:{validate_port(local_port, name='实际本地端口')}"
    )
    command = [
        ssh_executable,
        "-N",
        "-T",
        "-o",
        (
            "BatchMode=no"
            if cfg.gateway_auth == GATEWAY_AUTH_PASSWORD
            else "BatchMode=yes"
        ),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "LogLevel=ERROR",
        "-p",
        str(cfg.gateway_ssh_port),
    ]
    if cfg.gateway_auth == GATEWAY_AUTH_PASSWORD:
        command.extend(
            [
                "-o",
                "PreferredAuthentications=keyboard-interactive,password",
                "-o",
                "PubkeyAuthentication=no",
            ]
        )
    elif cfg.gateway_key_path:
        command.extend(["-i", cfg.gateway_key_path])
    command.extend(["-R", remote, "-l", cfg.gateway_user, cfg.gateway_host])
    return command


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def http_url(address: str, port: int, *, local_for_wildcard: bool = False) -> str:
    host = address
    if address == "0.0.0.0":
        host = "127.0.0.1" if local_for_wildcard else local_ip()
    return f"http://{host}:{port}/"


class SafeStaticRequestHandler(SimpleHTTPRequestHandler):
    server_version = "PasserStaticServer/1.0"

    def _resolved_request_path(self) -> Path:
        translated = super().translate_path(self.path)
        return Path(translated).resolve(strict=False)

    def _inside_document_root(self) -> bool:
        root = Path(self.directory).resolve(strict=False)
        candidate = self._resolved_request_path()
        return candidate == root or root in candidate.parents

    def send_head(self):
        if not self._inside_document_root():
            self.send_error(403, "Path outside server root")
            return None
        return super().send_head()

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed

    def log_message(self, format: str, *args) -> None:
        callback = getattr(self.server, "passer_log_callback", None)
        if callback is None:
            return
        message = format % args
        callback(f"{self.client_address[0]} · {message}")


class PasserThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ServerService:
    def __init__(self, app):
        self.app = app
        self.data_dir = Path(app.data_dir) / "Server"
        self.default_root = self.data_dir / "www"
        self.config_path = self.data_dir / "config.json"
        self.default_root.mkdir(parents=True, exist_ok=True)
        self.config = load_config(self.config_path, self.default_root)
        self._server: PasserThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._gateway_process: subprocess.Popen | None = None
        self._gateway_thread: threading.Thread | None = None
        self._listener: Callable[[str, str], None] | None = None
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self.actual_port = 0

    def set_listener(self, listener: Callable[[str, str], None] | None) -> None:
        self._listener = listener

    def _emit(self, kind: str, message: str) -> None:
        listener = self._listener
        if listener is not None:
            try:
                listener(kind, message)
            except Exception:
                pass

    def _log_unexpected(self, exc: BaseException, action: str, target_path=None) -> None:
        logger = getattr(self.app, "_log_unexpected", None)
        if callable(logger):
            logger(
                exc,
                module="server.tool",
                action=action,
                target_path=target_path or self.config.root,
                expected=(OSError, RuntimeError, ValueError, subprocess.SubprocessError),
            )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None

    @property
    def gateway_running(self) -> bool:
        with self._lock:
            process = self._gateway_process
            return process is not None and process.poll() is None

    @property
    def local_url(self) -> str:
        if not self.actual_port:
            return ""
        return http_url(self.config.bind_address, self.actual_port)

    @property
    def browser_url(self) -> str:
        if not self.actual_port:
            return ""
        return http_url(self.config.bind_address, self.actual_port, local_for_wildcard=True)

    @property
    def gateway_url(self) -> str:
        if not self.config.gateway_enabled:
            return ""
        return f"http://{self.config.gateway_host}:{self.config.gateway_remote_port}/"

    def update_config(self, config: ServerConfig) -> ServerConfig:
        cfg = normalize_config(config, default_root=self.default_root)
        save_config(self.config_path, cfg)
        self.config = cfg
        return cfg

    def start(self, config: ServerConfig | None = None) -> None:
        with self._lock:
            if self._server is not None:
                raise RuntimeError("服务器已经在运行。")
        cfg = normalize_config(
            config or self.config,
            default_root=self.default_root,
            allow_port_zero=True,
        )
        root = Path(cfg.root)
        if not root.is_dir():
            raise ValueError("服务器目录不存在或不是文件夹。")

        handler = functools.partial(SafeStaticRequestHandler, directory=str(root))
        try:
            server = PasserThreadingHTTPServer((cfg.bind_address, cfg.port), handler)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048:
                raise OSError(f"端口 {cfg.port} 已被其他程序占用。") from exc
            raise
        server.passer_log_callback = lambda line: self._emit("request", line)

        with self._lock:
            self.config = cfg
            self._server = server
            self.actual_port = int(server.server_address[1])
            self._stop_requested.clear()
            thread = threading.Thread(
                target=self._serve,
                args=(server,),
                daemon=True,
                name="Passer-StaticServer",
            )
            self._server_thread = thread
            thread.start()

        try:
            save_config(self.config_path, cfg)
            if cfg.gateway_enabled:
                self._start_gateway()
        except Exception:
            self.stop()
            raise
        self._emit("status", f"服务器已启动：{self.local_url}")

    def _serve(self, server: PasserThreadingHTTPServer) -> None:
        try:
            server.serve_forever(poll_interval=0.25)
        except (OSError, RuntimeError) as exc:
            if not self._stop_requested.is_set():
                self._emit("error", f"服务器异常停止：{exc}")
        except Exception as exc:
            self._log_unexpected(exc, "serve_forever")
            self._emit("error", f"服务器发生意外错误：{exc}")
        finally:
            if not self._stop_requested.is_set():
                with self._lock:
                    if self._server is server:
                        self._server = None
                        self._server_thread = None
                        self.actual_port = 0
                try:
                    server.server_close()
                except OSError:
                    pass

    def _start_gateway(self) -> None:
        ssh = find_ssh_executable()
        if not ssh:
            raise RuntimeError("未找到 Windows OpenSSH（ssh.exe）。")
        command = build_gateway_command(
            self.config,
            self.actual_port,
            ssh_executable=ssh,
        )
        process_env = None
        if self.config.gateway_auth == GATEWAY_AUTH_PASSWORD:
            askpass = find_ssh_askpass()
            if not askpass:
                raise RuntimeError(
                    "未找到可用的 SSH 密码输入窗口。请安装 Git for Windows，"
                    "或改用私钥 / SSH Agent。"
                )
            process_env = os.environ.copy()
            process_env["SSH_ASKPASS"] = askpass
            process_env["SSH_ASKPASS_REQUIRE"] = "force"
            process_env["DISPLAY"] = "passer-ssh-askpass"
            if getattr(sys, "frozen", False):
                process_env["PASSER_SSH_ASKPASS"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            env=process_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        with self._lock:
            self._gateway_process = process
            thread = threading.Thread(
                target=self._watch_gateway,
                args=(process,),
                daemon=True,
                name="Passer-ServerGateway",
            )
            self._gateway_thread = thread
            thread.start()
        self._emit("status", f"SSH 网关正在连接：{self.gateway_url}")

    def _watch_gateway(self, process: subprocess.Popen) -> None:
        last_lines: list[str] = []
        try:
            stream = process.stdout
            if stream is not None:
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line:
                        continue
                    last_lines.append(line)
                    del last_lines[:-4]
                    self._emit("gateway", line)
            code = process.wait()
            with self._lock:
                if self._gateway_process is process:
                    self._gateway_process = None
            if not self._stop_requested.is_set():
                detail = last_lines[-1] if last_lines else f"退出代码 {code}"
                self._emit("error", f"SSH 网关连接已断开：{detail}")
        except (OSError, subprocess.SubprocessError) as exc:
            if not self._stop_requested.is_set():
                self._emit("error", f"SSH 网关读取失败：{exc}")
        except Exception as exc:
            self._log_unexpected(exc, "watch_gateway", self.config.gateway_host)
            if not self._stop_requested.is_set():
                self._emit("error", f"SSH 网关发生意外错误：{exc}")

    def stop(self) -> None:
        self._stop_requested.set()
        with self._lock:
            process = self._gateway_process
            server = self._server
            server_thread = self._server_thread
            gateway_thread = self._gateway_thread
            self._gateway_process = None
            self._server = None
            self._server_thread = None
            self._gateway_thread = None

        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.SubprocessError):
                    pass
            except (OSError, subprocess.SubprocessError):
                pass
        if gateway_thread is not None and gateway_thread is not threading.current_thread():
            gateway_thread.join(timeout=1)

        if server is not None:
            try:
                server.shutdown()
            except (OSError, RuntimeError) as exc:
                self._emit("error", f"停止服务器时出错：{exc}")
            try:
                server.server_close()
            except OSError:
                pass
        if server_thread is not None and server_thread is not threading.current_thread():
            server_thread.join(timeout=2)

        self.actual_port = 0
        self._emit("status", "服务器已停止。")

    def close(self) -> None:
        self.stop()
        self.set_listener(None)


class ServerWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    WIDTH = 820
    HEIGHT = 710

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.service: ServerService = app.ensure_server_service()
        cfg = self.service.config

        self.root_var = tk.StringVar(value=cfg.root)
        self.bind_var = tk.StringVar(value=cfg.bind_address)
        self.port_var = tk.StringVar(value=str(cfg.port))
        self.gateway_enabled_var = tk.BooleanVar(value=cfg.gateway_enabled)
        self.gateway_host_var = tk.StringVar(value=cfg.gateway_host)
        self.gateway_ssh_port_var = tk.StringVar(value=str(cfg.gateway_ssh_port))
        self.gateway_user_var = tk.StringVar(value=cfg.gateway_user)
        self.gateway_remote_address_var = tk.StringVar(value=cfg.gateway_remote_address)
        self.gateway_remote_port_var = tk.StringVar(value=str(cfg.gateway_remote_port))
        self.gateway_key_var = tk.StringVar(value=cfg.gateway_key_path)
        self.gateway_auth_var = tk.StringVar(
            value=GATEWAY_AUTH_LABELS.get(
                cfg.gateway_auth,
                GATEWAY_AUTH_LABELS[GATEWAY_AUTH_KEY],
            )
        )
        self.status_var = tk.StringVar(value="就绪。")
        self.address_var = tk.StringVar(value="尚未启动")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.resizable(False, False)
        self.window.minsize(self.WIDTH, self.HEIGHT)
        self.window.maxsize(self.WIDTH, self.HEIGHT)

        self.shell = tk.Frame(
            self.window,
            width=self.WIDTH - 2,
            height=self.HEIGHT - 2,
            bg=theme.app_bg,
            highlightthickness=1,
            highlightbackground=theme.border,
        )
        self.shell.pack(fill=tk.BOTH, expand=False, padx=1, pady=1)
        self.shell.pack_propagate(False)
        self._build_chrome()
        self._build_body()
        self.service.set_listener(self._on_service_event)
        self.refresh_state()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._place_on_passer()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _place_on_passer(self) -> None:
        try:
            x, y = self.theme.center_over_root(self.app.root, self.WIDTH, self.HEIGHT)
            self.theme.place_toplevel_absolute(
                self.window,
                self.WIDTH,
                self.HEIGHT,
                x,
                y,
            )
        except Exception:
            self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}")

    def _build_chrome(self) -> None:
        bar = tk.Frame(self.shell, bg=self.theme.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(
            bar,
            text=self.theme.title,
            bg=self.theme.title_bg,
            fg="#dbe7ff",
            anchor=tk.W,
            font=self._font(10, "bold"),
        )
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        close = tk.Button(
            bar,
            text="×",
            command=self.close,
            bd=0,
            padx=11,
            pady=5,
            bg=self.theme.title_button_bg,
            fg="#e7eefc",
            activebackground="#ef4444",
            activeforeground="#ffffff",
            font=self._font(10),
            cursor="hand2",
        )
        close.pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _section(self, parent, title: str) -> tk.LabelFrame:
        frame = tk.LabelFrame(
            parent,
            text=title,
            bg=self.theme.surface_bg,
            fg="#111827",
            font=self._font(10, "bold"),
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=8,
        )
        frame.pack(fill=tk.X, pady=(0, 10))
        frame.grid_columnconfigure(1, weight=1)
        return frame

    def _label(self, parent, text: str, row: int, column: int = 0):
        label = tk.Label(
            parent,
            text=text,
            bg=self.theme.surface_bg,
            fg="#1f2937",
            anchor=tk.W,
            font=self._font(9),
        )
        label.grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)
        return label

    def _entry(self, parent, variable, row: int, column: int = 1, width: int = 20):
        entry = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bd=1,
            relief=tk.SOLID,
            highlightthickness=0,
            font=self._font(9),
        )
        entry.grid(row=row, column=column, sticky="ew", pady=4)
        return entry

    def _button(self, parent, text: str, command, *, primary=False, width=11):
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bd=0,
            padx=8,
            pady=7,
            bg=self.theme.accent if primary else "#eef2f9",
            fg="#ffffff" if primary else "#1f2937",
            activebackground=self.theme.accent_hover if primary else "#e2e8f4",
            activeforeground="#ffffff" if primary else "#111827",
            disabledforeground="#94a3b8",
            cursor="hand2",
            font=self._font(9, "bold" if primary else "normal"),
        )

    def _build_body(self) -> None:
        body = tk.Frame(self.shell, bg=self.theme.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=14)

        local = self._section(body, "本地静态服务器")
        self._label(local, "网页目录", 0)
        self._entry(local, self.root_var, 0)
        self._button(local, "选择目录", self.choose_root, width=10).grid(
            row=0, column=2, padx=(8, 0), pady=4
        )
        self._label(local, "监听地址", 1)
        self._entry(local, self.bind_var, 1)
        self._label(local, "端口", 1, 2)
        port_entry = self._entry(local, self.port_var, 1, 3, width=8)
        port_entry.grid_configure(sticky="w", padx=(0, 0))
        hint = tk.Label(
            local,
            text="127.0.0.1 仅本机；0.0.0.0 允许局域网设备访问。",
            bg=self.theme.surface_bg,
            fg=self.theme.muted_fg,
            anchor=tk.W,
            font=self._font(8),
        )
        hint.grid(row=2, column=1, columnspan=3, sticky="w", pady=(0, 2))

        gateway = self._section(body, "SSH 网关（反向隧道）")
        check = tk.Checkbutton(
            gateway,
            text="启用网关",
            variable=self.gateway_enabled_var,
            command=self._refresh_gateway_entries,
            bg=self.theme.surface_bg,
            activebackground=self.theme.surface_bg,
            fg="#111827",
            selectcolor=self.theme.surface_bg,
            font=self._font(9, "bold"),
        )
        check.grid(row=0, column=0, sticky="w", pady=(0, 4))
        gateway_note = tk.Label(
            gateway,
            text="使用 Windows OpenSSH；密码仅在连接时输入，不保存，也不开放远程命令。",
            bg=self.theme.surface_bg,
            fg=self.theme.muted_fg,
            anchor=tk.W,
            font=self._font(8),
        )
        gateway_note.grid(row=0, column=1, columnspan=3, sticky="w")

        self._label(gateway, "网关主机", 1)
        self.gateway_host_entry = self._entry(gateway, self.gateway_host_var, 1)
        self._label(gateway, "SSH 端口", 1, 2)
        self.gateway_ssh_port_entry = self._entry(
            gateway, self.gateway_ssh_port_var, 1, 3, width=8
        )
        self.gateway_ssh_port_entry.grid_configure(sticky="w")

        self._label(gateway, "SSH 用户", 2)
        self.gateway_user_entry = self._entry(gateway, self.gateway_user_var, 2)
        self._label(gateway, "远端地址", 2, 2)
        self.gateway_remote_address_entry = self._entry(
            gateway, self.gateway_remote_address_var, 2, 3, width=12
        )
        self.gateway_remote_address_entry.grid_configure(sticky="w")

        self._label(gateway, "认证方式", 3)
        self.gateway_auth_combo = ttk.Combobox(
            gateway,
            textvariable=self.gateway_auth_var,
            values=tuple(GATEWAY_AUTH_LABELS.values()),
            state="readonly",
            width=24,
            font=self._font(9),
        )
        self.gateway_auth_combo.grid(row=3, column=1, sticky="ew", pady=4)
        self.gateway_auth_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_gateway_entries(),
        )

        self._label(gateway, "远端端口", 4)
        self.gateway_remote_port_entry = self._entry(
            gateway, self.gateway_remote_port_var, 4
        )
        self._label(gateway, "SSH 私钥", 5)
        self.gateway_key_entry = self._entry(gateway, self.gateway_key_var, 5)
        self.gateway_key_button = self._button(
            gateway, "选择私钥", self.choose_key, width=10
        )
        self.gateway_key_button.grid(row=5, column=2, padx=(8, 0), pady=4)
        public_note = tk.Label(
            gateway,
            text="远端地址填 0.0.0.0 可申请公开端口；SSH 服务器需启用 GatewayPorts。",
            bg=self.theme.surface_bg,
            fg=self.theme.muted_fg,
            anchor=tk.W,
            font=self._font(8),
        )
        public_note.grid(row=6, column=1, columnspan=3, sticky="w")
        self._gateway_widgets = (
            self.gateway_host_entry,
            self.gateway_ssh_port_entry,
            self.gateway_user_entry,
            self.gateway_remote_address_entry,
            self.gateway_remote_port_entry,
        )
        self._refresh_gateway_entries()

        state = self._section(body, "运行状态")
        self._label(state, "状态", 0)
        status_label = tk.Label(
            state,
            textvariable=self.status_var,
            bg=self.theme.surface_bg,
            fg="#111827",
            anchor=tk.W,
            font=self._font(9, "bold"),
        )
        status_label.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        self._label(state, "访问地址", 1)
        address = tk.Entry(
            state,
            textvariable=self.address_var,
            state="readonly",
            readonlybackground="#f8fafc",
            fg="#1f2937",
            bd=1,
            relief=tk.SOLID,
            font=self._font(9),
        )
        address.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        log_frame = self._section(body, "访问日志")
        self.log_text = tk.Text(
            log_frame,
            height=5,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#f8fafc",
            fg="#334155",
            bd=1,
            relief=tk.SOLID,
            font=self._font(8),
        )
        self.log_text.grid(row=0, column=0, columnspan=4, sticky="ew")

        actions = tk.Frame(body, bg=self.theme.surface_bg)
        actions.pack(side=tk.BOTTOM, fill=tk.X)
        self.open_button = self._button(actions, "打开地址", self.open_address)
        self.open_button.pack(side=tk.LEFT)
        self.copy_button = self._button(actions, "复制地址", self.copy_address)
        self.copy_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_button = self._button(actions, "停止", self.stop_server)
        self.stop_button.pack(side=tk.RIGHT)
        self.start_button = self._button(
            actions, "启动服务器", self.start_server, primary=True, width=13
        )
        self.start_button.pack(side=tk.RIGHT, padx=(0, 8))

        bottom = tk.Frame(self.shell, bg=self.theme.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _refresh_gateway_entries(self) -> None:
        enabled = self.gateway_enabled_var.get()
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in getattr(self, "_gateway_widgets", ()):
            widget.configure(state=state)
        auth_combo = getattr(self, "gateway_auth_combo", None)
        if auth_combo is not None:
            auth_combo.configure(state="readonly" if enabled else tk.DISABLED)
        key_enabled = (
            enabled
            and self.gateway_auth_var.get()
            == GATEWAY_AUTH_LABELS[GATEWAY_AUTH_KEY]
        )
        key_state = tk.NORMAL if key_enabled else tk.DISABLED
        for widget in (
            getattr(self, "gateway_key_entry", None),
            getattr(self, "gateway_key_button", None),
        ):
            if widget is not None:
                widget.configure(state=key_state)

    def choose_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.window,
            title="选择静态网页目录",
            initialdir=self.root_var.get() or str(Path.home()),
        )
        if selected:
            self.root_var.set(selected)

    def choose_key(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.window,
            title="选择 SSH 私钥",
            initialdir=str(Path(self.gateway_key_var.get()).parent)
            if self.gateway_key_var.get()
            else str(Path.home() / ".ssh"),
            filetypes=(("私钥文件", "*"),),
        )
        if selected:
            self.gateway_key_var.set(selected)

    def _config_from_form(self) -> ServerConfig:
        gateway_auth = next(
            (
                key
                for key, label in GATEWAY_AUTH_LABELS.items()
                if label == self.gateway_auth_var.get()
            ),
            GATEWAY_AUTH_KEY,
        )
        return normalize_config(
            ServerConfig(
                root=self.root_var.get(),
                bind_address=self.bind_var.get(),
                port=self.port_var.get(),
                gateway_enabled=self.gateway_enabled_var.get(),
                gateway_host=self.gateway_host_var.get(),
                gateway_ssh_port=self.gateway_ssh_port_var.get(),
                gateway_user=self.gateway_user_var.get(),
                gateway_remote_address=self.gateway_remote_address_var.get(),
                gateway_remote_port=self.gateway_remote_port_var.get(),
                gateway_key_path=self.gateway_key_var.get(),
                gateway_auth=gateway_auth,
            ),
            default_root=self.service.default_root,
        )

    def start_server(self) -> None:
        try:
            config = self._config_from_form()
            self.service.start(config)
            self.app.write_status(f"服务器已启动：{self.service.local_url}")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.status_var.set(f"启动失败：{exc}")
            messagebox.showerror("服务器启动失败", str(exc), parent=self.window)
        except Exception as exc:
            self.service._log_unexpected(exc, "start_from_window", self.root_var.get())
            self.status_var.set("启动时发生意外错误，详情已写入日志。")
            messagebox.showerror(
                "服务器启动失败",
                "发生意外错误，详情已写入 Passer 日志。",
                parent=self.window,
            )
        self.refresh_state()

    def stop_server(self) -> None:
        self.service.stop()
        self.app.write_status("服务器已停止。")
        self.refresh_state()

    def open_address(self) -> None:
        url = self.service.browser_url
        if not url:
            messagebox.showinfo("服务器", "请先启动服务器。", parent=self.window)
            return
        webbrowser.open(url)

    def copy_address(self) -> None:
        url = self.service.gateway_url if self.service.gateway_running else self.service.local_url
        if not url:
            messagebox.showinfo("服务器", "请先启动服务器。", parent=self.window)
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(url)
        self.status_var.set(f"已复制：{url}")

    def _on_service_event(self, kind: str, message: str) -> None:
        if self.closed:
            return

        def apply_event():
            if self.closed:
                return
            if kind == "request":
                self._append_log(message)
            elif kind == "gateway":
                self._append_log(f"网关 · {message}")
            else:
                self.status_var.set(message)
            self.refresh_state(keep_status=True)

        try:
            self.window.after(0, apply_event)
        except tk.TclError:
            pass

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 120:
            self.log_text.delete("1.0", f"{lines - 100}.0")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def refresh_state(self, *, keep_status: bool = False) -> None:
        running = self.service.running
        self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.open_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        self.copy_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            urls = [self.service.local_url]
            if self.service.config.gateway_enabled:
                urls.append(self.service.gateway_url)
            self.address_var.set("  |  ".join(filter(None, urls)))
            if not keep_status:
                gateway = "，SSH 网关已连接" if self.service.gateway_running else ""
                self.status_var.set(f"运行中{gateway}")
        else:
            self.address_var.set("尚未启动")
            if not keep_status:
                self.status_var.set("就绪。")

    def start_move(self, event) -> None:
        self.move_start = (
            event.x_root,
            event.y_root,
            self.window.winfo_x(),
            self.window.winfo_y(),
        )

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        x = wx + event.x_root - sx
        y = wy + event.y_root - sy
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def show(self) -> None:
        self.window.deiconify()
        self._place_on_passer()
        self.window.lift()
        self.window.focus_force()
        self.service.set_listener(self._on_service_event)
        self.refresh_state()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.service.set_listener(None)
        if getattr(self.app, "server_window", None) is self:
            self.app.server_window = None
        self.window.destroy()
