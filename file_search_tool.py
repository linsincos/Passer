from __future__ import annotations

import os
import heapq
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from clicker_tool import ClickerTheme


DEFAULT_SKIP_DIRS = {
    "$Recycle.Bin", "System Volume Information", "Windows", "WinSxS",
    "node_modules", ".git", "__pycache__", ".venv", "venv",
}


class FileSearchWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 1470
    MIN_H = 1020

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.resize_start = None
        # 索引项：(name_cf, path, size, mtime, is_dir, path_cf, suffix)
        # 预存 path_cf 与 suffix，搜索时无需重复 casefold / 解析后缀。
        self.index: list[tuple[str, str, int, float, bool, str, str]] = []
        self.index_lock = threading.Lock()
        self.indexing = False
        self.index_seq = 0
        self.index_events: queue.Queue[tuple[str, int, list, int]] = queue.Queue()
        # 搜索放到后台线程，避免大索引下每次按键卡顿主界面。
        self.search_events: queue.Queue = queue.Queue()
        self.search_seq = 0
        self.search_after = None

        home = Path.home()
        desktop = home / "Desktop"
        self.root_var = tk.StringVar(value=str(desktop if desktop.exists() else home))
        self.query_var = tk.StringVar(value="")
        self.ext_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="未索引")
        self.include_hidden_var = tk.BooleanVar(value=False)

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
        self.query_entry.focus_set()
        self.window.after(50, self._poll_index_events)
        self.start_index()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

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
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        top = tk.Frame(body, bg=t.surface_bg)
        top.pack(fill=tk.X, padx=14, pady=(12, 8))
        top.grid_columnconfigure(1, weight=2)
        top.grid_columnconfigure(5, weight=1, minsize=200)

        self._label(top, "目录").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.root_entry = self._entry(top, self.root_var)
        self.root_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._button(top, "浏览", self.choose_root).grid(row=0, column=2, padx=(0, 8))
        self._button(top, "索引", self.start_index).grid(row=0, column=3, padx=(0, 12))
        self._label(top, "关键词").grid(row=0, column=4, sticky="e", padx=(0, 8))
        self.query_entry = self._entry(top, self.query_var)
        self.query_entry.grid(row=0, column=5, sticky="ew")

        opts = tk.Frame(body, bg=t.surface_bg)
        opts.pack(fill=tk.X, padx=14, pady=(0, 8))
        self._label(opts, "扩展名").pack(side=tk.LEFT, padx=(0, 8))
        self.ext_entry = self._entry(opts, self.ext_var, width=18)
        self.ext_entry.pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(opts, text="包含隐藏文件", variable=self.include_hidden_var,
                       bg=t.surface_bg, fg="#334155", activebackground=t.surface_bg,
                       selectcolor="#f8fafc", font=self._font(9), cursor="hand2").pack(side=tk.LEFT)
        tk.Label(opts, textvariable=self.status_var, bg=t.surface_bg, fg=t.muted_fg,
                 font=self._font(9)).pack(side=tk.RIGHT)

        self.query_var.trace_add("write", lambda *_: self.schedule_search())
        self.ext_var.trace_add("write", lambda *_: self.schedule_search())

        columns = ("name", "folder", "size", "modified")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for col, text, width in (
            ("name", "名称", 260), ("folder", "位置", 430), ("size", "大小", 90), ("modified", "修改时间", 150),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=tk.W)
        self.tree.tag_configure("dir", foreground=self.theme.accent)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())
        self.tree.bind("<Return>", lambda _e: self.open_selected())
        # 在关键词框里按回车直接打开当前命中的第一项。
        self.query_entry.bind("<Return>", lambda _e: self.open_selected())
        self.query_entry.bind("<Down>", lambda _e: self._focus_results())
        scroll = ttk.Scrollbar(self.tree, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        actions = tk.Frame(body, bg=t.surface_bg)
        actions.pack(fill=tk.X, padx=14, pady=(0, 12))
        self._button(actions, "打开", self.open_selected).pack(side=tk.LEFT, padx=(0, 8))
        self._button(actions, "所在目录", self.open_folder).pack(side=tk.LEFT, padx=(0, 8))
        self._button(actions, "复制路径", self.copy_path).pack(side=tk.LEFT)

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=t.title_bg, fg="#8aa0c0", cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, padx=(0, 6))
        grip.bind("<ButtonPress-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)

    def start_index(self) -> None:
        # 递增序列号即可让上一轮索引/搜索线程自行退出。
        self.index_seq += 1
        seq = self.index_seq
        root = self.root_var.get().strip()
        if not root or not os.path.isdir(root):
            self.status_var.set("目录不存在")
            return
        include_hidden = self.include_hidden_var.get()
        self.indexing = True
        self.status_var.set("索引中...")
        with self.index_lock:
            self.index = []
        threading.Thread(
            target=self._index_worker,
            args=(seq, root, include_hidden),
            daemon=True,
            name="Passer-FileSearch",
        ).start()

    def _index_worker(self, seq: int, root: str, include_hidden: bool) -> None:
        pending: list[tuple[str, str, int, float, bool, str, str]] = []
        stack = [root]
        count = 0
        last_update = time.monotonic()
        while stack and not self.closed and seq == self.index_seq:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            name = entry.name
                            if not include_hidden and name.startswith("."):
                                continue
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if is_dir and name in DEFAULT_SKIP_DIRS:
                                continue
                            try:
                                st = entry.stat(follow_symlinks=False)
                                size, mtime = int(st.st_size), float(st.st_mtime)
                            except OSError:
                                size, mtime = 0, 0.0
                            path = entry.path
                            suffix = "" if is_dir else os.path.splitext(name)[1].lower().lstrip(".")
                            # 文件夹也加入索引，可被搜索；并继续向下遍历。
                            pending.append((name.casefold(), path, size, mtime, is_dir, path.casefold(), suffix))
                            count += 1
                            if is_dir:
                                stack.append(path)
                        except OSError:
                            continue
            except OSError:
                continue
            if time.monotonic() - last_update > 0.25:
                batch = pending
                pending = []
                self.index_events.put(("progress", seq, batch, count))
                last_update = time.monotonic()
        self.index_events.put(("finish", seq, pending, count))

    def _poll_index_events(self) -> None:
        if self.closed:
            return
        while True:
            try:
                kind, seq, items, count = self.index_events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self._update_index_progress(seq, items, count)
            else:
                self._finish_index(seq, items, count)
        while True:
            try:
                payload = self.search_events.get_nowait()
            except queue.Empty:
                break
            self._apply_search_results(*payload)
        self.window.after(50, self._poll_index_events)

    def _update_index_progress(self, seq: int, items, count: int) -> None:
        if self.closed or seq != self.index_seq:
            return
        with self.index_lock:
            self.index.extend(items)
        self.status_var.set(f"索引中：{count} 个文件")
        self.schedule_search()

    def _finish_index(self, seq: int, items, count: int) -> None:
        if self.closed or seq != self.index_seq:
            return
        self.indexing = False
        with self.index_lock:
            self.index.extend(items)
        self.status_var.set(f"已索引 {count} 个文件")
        self.search()

    def schedule_search(self) -> None:
        if self.search_after:
            self.window.after_cancel(self.search_after)
        self.search_after = self.window.after(120, self.search)

    MAX_RESULTS = 300

    def search(self) -> None:
        self.search_after = None
        query = self.query_var.get().strip().casefold()
        exts = [e.lower().lstrip(".") for e in self.ext_var.get().replace(";", ",").split(",") if e.strip()]
        words = [w for w in query.split() if w]
        with self.index_lock:
            data = list(self.index)          # 快照，避免与索引线程并发改动
        self.search_seq += 1
        seq = self.search_seq
        threading.Thread(
            target=self._search_worker,
            args=(seq, words, exts, data),
            daemon=True,
            name="Passer-FileSearchQuery",
        ).start()

    def _search_worker(self, seq: int, words: list[str], exts: list[str], data: list) -> None:
        first = words[0] if words else ""
        ranked: list[tuple] = []
        push = ranked.append
        for name_cf, path, size, mtime, is_dir, path_cf, suffix in data:
            if self.closed or seq != self.search_seq:
                return
            if words:
                if not all(w in name_cf or w in path_cf for w in words):
                    continue
            if exts and (is_dir or suffix not in exts):
                continue
            # 排名：完全匹配 > 前缀 > 词首边界 > 文件名包含 > 仅路径包含。
            if not first:
                score = 5
            elif name_cf == first:
                score = 0
            elif name_cf.startswith(first):
                score = 1
            elif first in name_cf:
                pos = name_cf.find(first)
                score = 2 if pos == 0 or not name_cf[pos - 1].isalnum() else 3
            elif first in path_cf:
                score = 4
            else:
                score = 5
            push((score, -mtime, path_cf, path, size, mtime, is_dir))

        top = heapq.nsmallest(self.MAX_RESULTS, ranked, key=lambda r: r[:3])
        rows = []
        for _score, _nm, _pcf, path, size, mtime, is_dir in top:
            base = os.path.basename(path) or path
            parent = os.path.dirname(path)
            size_str = "文件夹" if is_dir else self._format_size(size)
            mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "—"
            rows.append((path, base, parent, size_str, mtime_str, is_dir))
        self.search_events.put((seq, rows, len(ranked), len(data)))

    def _apply_search_results(self, seq: int, rows: list, matched: int, total: int) -> None:
        if self.closed or seq != self.search_seq:
            return
        self.tree.delete(*self.tree.get_children())
        for path, name, parent, size_str, mtime_str, is_dir in rows:
            try:
                self.tree.insert("", tk.END, iid=path, values=(name, parent, size_str, mtime_str),
                                 tags=("dir",) if is_dir else ())
            except tk.TclError:
                continue
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
        prefix = "索引中" if self.indexing else "已索引"
        if matched == 0:
            self.status_var.set(f"{prefix}：无匹配 / 共 {total} 项")
        else:
            self.status_var.set(f"{prefix}：匹配 {matched}，显示 {len(rows)} / 共 {total} 项")

    def _focus_results(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.focus_set()
            target = self.tree.selection() or (children[0],)
            self.tree.focus(target[0])
            self.tree.selection_set(target[0])

    def selected_path(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def open_selected(self) -> None:
        path = self.selected_path()
        if path:
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.window)

    def open_folder(self) -> None:
        path = self.selected_path()
        if path:
            try:
                os.startfile(str(Path(path).parent))  # type: ignore[attr-defined]
            except OSError as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.window)

    def copy_path(self) -> None:
        path = self.selected_path()
        if path:
            self.window.clipboard_clear()
            self.window.clipboard_append(path)

    def choose_root(self) -> None:
        path = filedialog.askdirectory(
            parent=self.window,
            initialdir=self.root_var.get() or str(Path.home()),
        )
        if path:
            self.root_var.set(path)
            self.start_index()

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return str(size)

    def _label(self, parent, text: str):
        return tk.Label(parent, text=text, bg=self.theme.surface_bg, fg="#334155", font=self._font(9))

    def _entry(self, parent, variable, width=None):
        return tk.Entry(parent, textvariable=variable, bd=0, relief=tk.FLAT, width=width or 0,
                        bg="#f8fafc", fg="#111827", insertbackground="#111827",
                        highlightthickness=1, highlightbackground=self.theme.border,
                        highlightcolor=self.theme.accent, font=self._font(10))

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bd=0, padx=13, pady=6,
                         bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                         activeforeground="#111827", font=self._font(9, "bold"), cursor="hand2")

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        btn = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                        bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                        activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=self.theme.title_button_bg))
        return btn

    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(self.window, max(self.window.winfo_width(), self.MIN_W),
                                           max(self.window.winfo_height(), self.MIN_H),
                                           wx + event.x_root - sx, wy + event.y_root - sy)

    def start_resize(self, event):
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event):
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(self.MIN_W, sw + event.x_root - sx)}x{max(self.MIN_H, sh + event.y_root - sy)}")

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        self.closed = True
        self.index_seq += 1
        self.window.destroy()
        if getattr(self.app, "file_search_window", None) is self:
            self.app.file_search_window = None
