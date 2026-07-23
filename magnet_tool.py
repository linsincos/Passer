from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tkinter as tk
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clicker_tool import ClickerTheme


MAGNET_RE = re.compile(r"^magnet:\?[^\s]*\bxt=urn:btih:[A-Za-z0-9]+", re.IGNORECASE)


def find_aria2c() -> Path | None:
    bundled = Path(__file__).resolve().parent / "tools" / "aria2" / "aria2c.exe"
    if bundled.is_file():
        return bundled
    found = shutil.which("aria2c")
    return Path(found) if found else None


def format_bytes(value: str | int, *, speed: bool = False) -> str:
    try:
        size = max(0.0, float(value))
    except (TypeError, ValueError):
        size = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    text = f"{size:.0f}" if index == 0 else f"{size:.1f}"
    return f"{text} {units[index]}{'/s' if speed else ''}"


class Aria2Backend:
    def __init__(self, executable: Path, download_dir: Path):
        self.executable = executable
        self.download_dir = download_dir
        self.secret = uuid.uuid4().hex
        self.port = self._free_port()
        self.process: subprocess.Popen | None = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.download_dir.mkdir(parents=True, exist_ok=True)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        command = [
            str(self.executable),
            "--no-conf=true",
            "--enable-rpc=true",
            "--rpc-listen-all=false",
            f"--rpc-listen-port={self.port}",
            f"--rpc-secret={self.secret}",
            "--rpc-allow-origin-all=false",
            "--console-log-level=warn",
            "--summary-interval=0",
            "--seed-time=0",
            "--file-allocation=none",
            f"--dir={self.download_dir}",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("aria2 后端启动失败。")
            try:
                self.call("aria2.getVersion")
                return
            except (OSError, RuntimeError):
                time.sleep(0.08)
        self.stop()
        raise RuntimeError("aria2 后端启动超时。")

    def call(self, method: str, *params):
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": [f"token:{self.secret}", *params],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/jsonrpc",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1.5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法连接 aria2 后端：{exc}") from exc
        if result.get("error"):
            detail = result["error"].get("message") or str(result["error"])
            raise RuntimeError(detail)
        return result.get("result")

    def add(self, magnet: str, download_dir: Path) -> str:
        self.start()
        options = {
            "dir": str(download_dir),
            "continue": "true",
            "bt-save-metadata": "true",
            "seed-time": "0",
        }
        return str(self.call("aria2.addUri", [magnet], options))

    def tasks(self) -> list[dict]:
        if self.process is None or self.process.poll() is not None:
            return []
        keys = [
            "gid", "status", "totalLength", "completedLength", "downloadSpeed",
            "uploadSpeed", "files", "bittorrent", "errorMessage",
        ]
        active = self.call("aria2.tellActive", keys) or []
        waiting = self.call("aria2.tellWaiting", 0, 100, keys) or []
        stopped = self.call("aria2.tellStopped", 0, 100, keys) or []
        return [*active, *waiting, *stopped]

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            self.call("aria2.shutdown")
        except RuntimeError:
            pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.terminate()


class MagnetDownloadWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 900
    MIN_H = 620

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.poll_after = None
        self.task_by_gid: dict[str, dict] = {}
        self.aria2_path = find_aria2c()
        default_dir = Path.home() / "Downloads"
        self.download_dir = default_dir if default_dir.exists() else Path(app.store_dir)
        self.backend = Aria2Backend(self.aria2_path, self.download_dir) if self.aria2_path else None

        self.magnet_var = tk.StringVar(value="")
        self.dir_var = tk.StringVar(value=str(self.download_dir))
        self.status_var = tk.StringVar(value=self._backend_status())

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)
        self.shell = tk.Frame(self.window, bg=theme.app_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        self.magnet_entry.focus_set()
        self.poll_after = self.window.after(700, self._poll_tasks)

    def _font(self, size=9, weight="normal"):
        return self.theme.app_font(size, weight)

    def _backend_status(self) -> str:
        if self.aria2_path:
            return f"后端：aria2c · 下载目录：{self.download_dir}"
        return "未找到 aria2c 后端。"

    def _build_chrome(self) -> None:
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

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(fill=tk.BOTH, expand=True)

        form = tk.Frame(body, bg=t.surface_bg)
        form.pack(fill=tk.X, padx=18, pady=(18, 10))
        tk.Label(form, text="磁力链接", bg=t.surface_bg, fg="#334155", anchor=tk.W, font=self._font(10)).pack(fill=tk.X, pady=(0, 6))
        link_row = tk.Frame(form, bg=t.surface_bg)
        link_row.pack(fill=tk.X)
        self.magnet_entry = tk.Entry(
            link_row, textvariable=self.magnet_var, bd=0, bg="#f8fafc", fg="#111827",
            insertbackground="#111827", highlightthickness=1, highlightbackground=t.border,
            highlightcolor=t.accent, font=self._font(9),
        )
        self.magnet_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self._button(link_row, "粘贴", self.paste_link).pack(side=tk.LEFT, padx=(8, 0))
        self._button(link_row, "开始下载", self.add_download, primary=True).pack(side=tk.LEFT, padx=(8, 0))

        dir_row = tk.Frame(form, bg=t.surface_bg)
        dir_row.pack(fill=tk.X, pady=(10, 0))
        tk.Entry(
            dir_row, textvariable=self.dir_var, state="readonly", readonlybackground="#f8fafc",
            bd=0, fg="#334155", highlightthickness=1, highlightbackground=t.border, font=self._font(9),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7)
        self._button(dir_row, "选择目录", self.choose_directory).pack(side=tk.LEFT, padx=(8, 0))
        self._button(dir_row, "打开目录", self.open_directory).pack(side=tk.LEFT, padx=(8, 0))

        toolbar = tk.Frame(body, bg=t.surface_bg)
        toolbar.pack(fill=tk.X, padx=18, pady=(2, 8))
        self._button(toolbar, "暂停/继续", self.toggle_selected).pack(side=tk.LEFT)
        self._button(toolbar, "删除任务", self.remove_selected).pack(side=tk.LEFT, padx=(8, 0))
        self._button(toolbar, "刷新", self.refresh_tasks).pack(side=tk.LEFT, padx=(8, 0))

        columns = ("name", "progress", "speed", "status")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for column, label, width in (
            ("name", "名称", 420), ("progress", "进度", 170),
            ("speed", "下载速度", 120), ("status", "状态", 110),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor=tk.W if column == "name" else tk.CENTER)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(18, 0), pady=(0, 12))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 18), pady=(0, 12))

        status = tk.Label(self.shell, textvariable=self.status_var, bg=t.title_bg, fg="#aebbd0", anchor=tk.W, font=self._font(8))
        status.pack(side=tk.BOTTOM, fill=tk.X, ipady=3, padx=8)
        grip = tk.Label(status, text="◢", bg=t.title_bg, fg="#8aa0c0", cursor="size_nw_se")
        grip.pack(side=tk.RIGHT)
        grip.bind("<ButtonPress-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent, text=text, command=command, bd=0, padx=14, pady=8,
            bg=self.theme.accent if primary else "#eef2f9",
            fg="#ffffff" if primary else "#1f2937",
            activebackground=self.theme.accent_hover if primary else "#e2e8f4",
            activeforeground="#ffffff" if primary else "#111827",
            cursor="hand2", font=self._font(9, "bold" if primary else "normal"),
        )

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
            activeforeground="#ffffff", font=self._font(10), cursor="hand2",
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=self.theme.title_button_bg))
        return button

    def paste_link(self) -> None:
        try:
            text = self.window.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if text:
            self.magnet_var.set(text)

    def choose_directory(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self.window)
        if selected:
            self.download_dir = Path(selected)
            self.dir_var.set(str(self.download_dir))
            if self.backend:
                self.backend.download_dir = self.download_dir
            self.status_var.set(self._backend_status())

    def add_download(self) -> None:
        magnet = self.magnet_var.get().strip()
        if not MAGNET_RE.match(magnet):
            messagebox.showinfo("链接无效", "请输入有效的 magnet 磁力链接。", parent=self.window)
            return
        if self.backend is None:
            messagebox.showinfo("缺少后端", "未找到 aria2c.exe。", parent=self.window)
            return
        try:
            gid = self.backend.add(magnet, self.download_dir)
        except (OSError, RuntimeError) as exc:
            messagebox.showinfo("添加失败", str(exc), parent=self.window)
            return
        self.magnet_var.set("")
        self.status_var.set(f"已添加任务：{gid}")
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        if self.backend is None:
            return
        try:
            tasks = self.backend.tasks()
        except RuntimeError as exc:
            self.status_var.set(str(exc))
            return
        self.task_by_gid = {str(task.get("gid", "")): task for task in tasks if task.get("gid")}
        existing = set(self.tree.get_children())
        current = set(self.task_by_gid)
        for gid in existing - current:
            self.tree.delete(gid)
        for gid, task in self.task_by_gid.items():
            values = self._task_values(task)
            if gid in existing:
                self.tree.item(gid, values=values)
            else:
                self.tree.insert("", tk.END, iid=gid, values=values)
        if tasks:
            active = sum(1 for task in tasks if task.get("status") == "active")
            self.status_var.set(f"共 {len(tasks)} 个任务，正在下载 {active} 个。")

    def _task_values(self, task: dict) -> tuple[str, str, str, str]:
        total = int(task.get("totalLength") or 0)
        completed = int(task.get("completedLength") or 0)
        percent = completed * 100 / total if total else 0.0
        name = self._task_name(task)
        progress = f"{percent:.1f}%  {format_bytes(completed)} / {format_bytes(total)}"
        speed = format_bytes(task.get("downloadSpeed") or 0, speed=True)
        statuses = {
            "active": "下载中", "waiting": "等待中", "paused": "已暂停",
            "complete": "已完成", "error": "失败", "removed": "已删除",
        }
        status = statuses.get(str(task.get("status")), str(task.get("status") or "未知"))
        if task.get("errorMessage"):
            status = f"失败：{task['errorMessage']}"
        return name, progress, speed, status

    @staticmethod
    def _task_name(task: dict) -> str:
        bittorrent = task.get("bittorrent") or {}
        info = bittorrent.get("info") or {}
        if info.get("name"):
            return str(info["name"])
        files = task.get("files") or []
        if files and files[0].get("path"):
            return Path(str(files[0]["path"])).name
        return f"磁力任务 {task.get('gid', '')}"

    def selected_gid(self) -> str | None:
        selection = self.tree.selection()
        return str(selection[0]) if selection else None

    def toggle_selected(self) -> None:
        gid = self.selected_gid()
        task = self.task_by_gid.get(gid or "")
        if not gid or not task or self.backend is None:
            return
        method = "aria2.unpause" if task.get("status") == "paused" else "aria2.pause"
        try:
            self.backend.call(method, gid)
            self.refresh_tasks()
        except RuntimeError as exc:
            messagebox.showinfo("操作失败", str(exc), parent=self.window)

    def remove_selected(self) -> None:
        gid = self.selected_gid()
        task = self.task_by_gid.get(gid or "")
        if not gid or not task or self.backend is None:
            return
        try:
            if task.get("status") in ("complete", "error", "removed"):
                self.backend.call("aria2.removeDownloadResult", gid)
            else:
                self.backend.call("aria2.remove", gid)
            self.refresh_tasks()
        except RuntimeError as exc:
            messagebox.showinfo("操作失败", str(exc), parent=self.window)

    def open_directory(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(self.download_dir))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.download_dir)])
        except OSError as exc:
            messagebox.showinfo("打开失败", str(exc), parent=self.window)

    def _poll_tasks(self) -> None:
        self.poll_after = None
        if self.closed:
            return
        self.refresh_tasks()
        self.poll_after = self.window.after(1000, self._poll_tasks)

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(self.MIN_W, sw + event.x_root - sx)}x{max(self.MIN_H, sh + event.y_root - sy)}")

    def show(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.poll_after is not None:
            try:
                self.window.after_cancel(self.poll_after)
            except tk.TclError:
                pass
        if self.backend:
            self.backend.stop()
        self.window.destroy()
        if getattr(self.app, "magnet_window", None) is self:
            self.app.magnet_window = None
