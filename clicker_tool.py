from __future__ import annotations

import ctypes
import re
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable


_ACCENT_DERIVED = {
    "#2563eb": ("#1d4ed8", "#e8f1ff", "#dbeafe"),
    "#db2777": ("#be185d", "#fce7f3", "#fbcfe8"),
    "#16a34a": ("#15803d", "#dcfce7", "#bbf7d0"),
    "#7c3aed": ("#6d28d9", "#ede9fe", "#ddd6fe"),
    "#ca8a04": ("#a16207", "#fef3c7", "#fde68a"),
    "#ea580c": ("#c2410c", "#ffedd5", "#fed7aa"),
    "#dc2626": ("#b91c1c", "#fee2e2", "#fecaca"),
    "#0891b2": ("#0e7490", "#cffafe", "#a5f3fc"),
}
_TITLE_BUTTON_DERIVED = {
    "#101827": ("#172235", "#223047"),
    "#3b1028": ("#541635", "#7a1d4b"),
    "#052e16": ("#093d1f", "#14532d"),
    "#251047": ("#32165f", "#4c1d95"),
    "#422006": ("#57310a", "#713f12"),
    "#431407": ("#5b1b09", "#7c2d12"),
    "#450a0a": ("#5f1111", "#7f1d1d"),
    "#083344": ("#0e4358", "#155e75"),
}


@dataclass(frozen=True)
class ClickerTheme:
    title: str
    border: str
    app_bg: str
    surface_bg: str
    title_bg: str
    muted_fg: str
    accent: str
    danger: str
    app_font: Callable[[int, str], Any]
    center_over_root: Callable[[tk.Tk, int, int], tuple[int, int]]
    place_toplevel_absolute: Callable[[tk.Toplevel, int, int, int, int], None]

    @property
    def accent_hover(self) -> str:
        return _ACCENT_DERIVED.get(str(self.accent).lower(), (self.accent, "", ""))[0]

    @property
    def accent_soft(self) -> str:
        return _ACCENT_DERIVED.get(str(self.accent).lower(), ("", "#eef2f9", ""))[1]

    @property
    def accent_soft_hover(self) -> str:
        return _ACCENT_DERIVED.get(str(self.accent).lower(), ("", "", "#e2e8f4"))[2]

    @property
    def title_button_bg(self) -> str:
        return _TITLE_BUTTON_DERIVED.get(str(self.title_bg).lower(), ("#172235", "#223047"))[0]

    @property
    def title_button_hover(self) -> str:
        return _TITLE_BUTTON_DERIVED.get(str(self.title_bg).lower(), ("#172235", "#223047"))[1]


def minimize_frameless_window(window: tk.Misc) -> bool:
    """Minimize an overrideredirect window without losing its custom frame."""
    try:
        window.overrideredirect(False)
        window.update_idletasks()
        window.iconify()
        return True
    except (tk.TclError, RuntimeError):
        pass

    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            hwnd = int(user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id())
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            return bool(user32.IsIconic(hwnd))
        except Exception:
            pass
    try:
        window.overrideredirect(True)
    except (tk.TclError, RuntimeError):
        pass
    return False


class ClickerWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    # 宽度约为原 460 的 1.6 倍。
    MIN_W = 736
    # 固定窗口高度：足以容纳「多点顺序」面板，切换形态时不再改变大小。
    MIN_H = 640
    ALGORITHM_INTERVAL = "时间间隔"
    ALGORITHM_RATE = "每秒次数"
    ALGORITHM_FREE = "自由连点"
    STOP_KEYS = {
        "F6": 0x75,
        "F7": 0x76,
        "F8": 0x77,
        "F9": 0x78,
        "F10": 0x79,
        "F11": 0x7A,
        "F12": 0x7B,
        "无": None,
    }

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.running = False
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.move_start = None
        self.capture_after_id = None
        self.capture_countdown = 0
        self.capture_target = "single"
        self.points: list[tuple[int, int]] = []
        self.undo_stack: list[tuple[list[tuple[int, int]], str]] = []
        self.hotkey_after_id = None
        self.hotkey_prev_down = False

        self.position_var = tk.StringVar()
        self.interval_var = tk.StringVar(value="50")
        self.delay_var = tk.StringVar(value="5")
        self.duration_var = tk.StringVar(value="60")
        self.algorithm_var = tk.StringVar(value=self.ALGORITHM_INTERVAL)
        self.multi_var = tk.BooleanVar(value=False)
        self.stop_key_var = tk.StringVar(value="F8")
        self.status_var = tk.StringVar(value="就绪。")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)

        self.shell = tk.Frame(
            self.window,
            bg=theme.app_bg,
            highlightthickness=1,
            highlightbackground=theme.border,
        )
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._build_chrome()
        self._build_body()
        self._bind_events()

        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        # 显式锁定 tk 几何，禁用「按内容自动缩放」——否则切换单点/多点时
        # overrideredirect 窗口会随子控件请求大小而改变高度。
        self.window.geometry(f"{self.MIN_W}x{self.MIN_H}+{x}+{y}")
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.refresh_controls()
        self.window.deiconify()
        self.window.focus_force()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self) -> None:
        t = self.theme
        self.toolbar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)

        self.title_label = tk.Label(
            self.toolbar,
            text=t.title,
            bg=t.title_bg,
            fg="#dbe7ff",
            anchor=tk.W,
            font=self._font(10, "bold"),
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))

        self._chrome_button(self.toolbar, "×", self.close, close=True).pack(
            side=tk.RIGHT, padx=(0, 8), pady=8
        )
        for widget in (self.toolbar, self.title_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        form = tk.Frame(body, bg=t.surface_bg)
        form.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(16, 8))
        form.grid_columnconfigure(0, minsize=82)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(2, minsize=66)
        form.grid_columnconfigure(3, minsize=52)
        form.grid_columnconfigure(4, weight=1)
        form.grid_columnconfigure(5, minsize=90)

        self.mode_check = tk.Checkbutton(
            form,
            text="多点顺序点击（在多个目标点间依次点击）",
            variable=self.multi_var,
            command=self.toggle_multi,
            bg=t.surface_bg,
            fg="#334155",
            activebackground=t.surface_bg,
            activeforeground="#111827",
            selectcolor="#f8fafc",
            anchor=tk.W,
            cursor="hand2",
            font=self._font(9),
        )
        self.mode_check.grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))

        # --- 单点位置行 ---
        self.position_label = self._label(form, "位置")
        self.position_label.grid(row=1, column=0, sticky="w", pady=6, padx=(0, 10))
        self.position_entry = self._entry(form, self.position_var)
        self.position_entry.grid(row=1, column=1, columnspan=4, sticky="ew", pady=6)
        self.capture_button = self._body_button(form, "定义位置", self.begin_capture_position)
        self.capture_button.grid(row=1, column=5, sticky="e", padx=(10, 0), pady=6)

        # --- 多点面板（与单点位置行占同一区域，按模式切换显示）---
        self.multi_panel = self._build_multi_panel(form)

        self.metric_label = self._label(form, "时间间隔")
        self.metric_label.grid(row=2, column=0, sticky="w", pady=6, padx=(0, 10))
        self.interval_entry = self._entry(form, self.interval_var, width=10)
        self.interval_entry.grid(row=2, column=1, sticky="ew", pady=6)
        self.metric_unit = self._unit(form, "毫秒")
        self.metric_unit.grid(row=2, column=2, sticky="w", padx=(8, 16), pady=6)

        self._label(form, "滞后").grid(row=3, column=0, sticky="w", pady=6, padx=(0, 10))
        self.delay_entry = self._entry(form, self.delay_var, width=10)
        self.delay_entry.grid(row=3, column=1, sticky="ew", pady=6)
        self._unit(form, "秒").grid(row=3, column=2, sticky="w", padx=(8, 16), pady=6)

        self._label(form, "时长").grid(row=4, column=0, sticky="w", pady=6, padx=(0, 10))
        self.duration_entry = self._entry(form, self.duration_var, width=10)
        self.duration_entry.grid(row=4, column=1, sticky="ew", pady=6)
        self._unit(form, "秒").grid(row=4, column=2, sticky="w", padx=(8, 16), pady=6)

        self._label(form, "算法").grid(row=5, column=0, sticky="w", pady=6, padx=(0, 10))
        self.algorithm_combo = ttk.Combobox(
            form,
            textvariable=self.algorithm_var,
            values=(self.ALGORITHM_INTERVAL, self.ALGORITHM_RATE, self.ALGORITHM_FREE),
            state="readonly",
            width=10,
            font=self._font(10),
        )
        self.algorithm_combo.grid(row=5, column=1, sticky="ew", pady=6)

        self._label(form, "终止键").grid(row=6, column=0, sticky="w", pady=6, padx=(0, 10))
        self.stop_key_combo = ttk.Combobox(
            form,
            textvariable=self.stop_key_var,
            values=tuple(self.STOP_KEYS.keys()),
            state="readonly",
            width=10,
            font=self._font(10),
        )
        self.stop_key_combo.grid(row=6, column=1, sticky="ew", pady=6)

        actions = tk.Frame(body, bg=t.surface_bg)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(4, 16))
        self.status_label = tk.Label(
            actions,
            textvariable=self.status_var,
            bg=t.surface_bg,
            fg=t.muted_fg,
            anchor=tk.W,
            justify=tk.LEFT,
            font=self._font(9),
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        self.start_button = tk.Button(
            actions,
            text="开始",
            command=self.toggle_running,
            bd=0,
            padx=24,
            pady=8,
            bg=t.accent,
            fg="#ffffff",
            activebackground=self.theme.accent_hover,
            activeforeground="#ffffff",
            disabledforeground="#94a3b8",
            cursor="hand2",
            font=self._font(10, "bold"),
            width=8,
        )
        self.start_button.pack(side=tk.RIGHT)

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _build_multi_panel(self, form) -> tk.Frame:
        t = self.theme
        panel = tk.Frame(form, bg=t.surface_bg)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        tk.Label(
            panel,
            text="目标点（按顺序循环点击）",
            bg=t.surface_bg,
            fg="#334155",
            anchor=tk.W,
            font=self._font(9),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        list_wrap = tk.Frame(panel, bg=self.theme.border, highlightthickness=0)
        list_wrap.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.points_list = tk.Listbox(
            list_wrap,
            height=6,
            bd=0,
            relief=tk.FLAT,
            bg="#f8fafc",
            fg="#111827",
            selectbackground=t.accent,
            selectforeground="#ffffff",
            activestyle="none",
            highlightthickness=0,
            font=self._font(10),
        )
        self.points_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        list_scroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.points_list.yview)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.points_list.configure(yscrollcommand=list_scroll.set)

        buttons = tk.Frame(panel, bg=t.surface_bg)
        buttons.grid(row=1, column=1, sticky="ns")
        self.add_point_button = self._body_button(buttons, "添加点", self.begin_capture_point)
        self.add_point_button.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.del_point_button = self._body_button(buttons, "删除", self.delete_point)
        self.del_point_button.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.up_point_button = self._body_button(buttons, "上移", lambda: self.move_point(-1))
        self.up_point_button.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.down_point_button = self._body_button(buttons, "下移", lambda: self.move_point(1))
        self.down_point_button.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.clear_points_button = self._body_button(buttons, "清空", self.clear_points)
        self.clear_points_button.pack(side=tk.TOP, fill=tk.X)
        return panel

    def _bind_events(self) -> None:
        for var in (self.position_var, self.interval_var, self.delay_var, self.duration_var):
            var.trace_add("write", lambda *_args: self.refresh_controls())
        self.algorithm_var.trace_add("write", lambda *_args: self.refresh_algorithm())
        self.window.bind("<Escape>", lambda event: self.stop_clicking() if self.running else self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def _chrome_button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            padx=11,
            pady=5,
            bg=self.theme.title_button_bg,
            fg="#e7eefc",
            activebackground=hover,
            activeforeground="#ffffff",
            font=self._font(10 if close else 9),
            cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=self.theme.title_button_bg))
        return button

    def _body_button(self, parent, text: str, command) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            padx=13,
            pady=6,
            width=10,
            bg="#eef2f9",
            fg="#1f2937",
            activebackground="#e2e8f4",
            activeforeground="#111827",
            font=self._font(9),
            cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg="#e2e8f4"))
        button.bind("<Leave>", lambda event: button.configure(bg="#eef2f9"))
        return button

    def _label(self, parent, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=self.theme.surface_bg,
            fg="#334155",
            anchor=tk.W,
            width=8,
            font=self._font(10),
        )

    def _unit(self, parent, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=self.theme.surface_bg,
            fg=self.theme.muted_fg,
            anchor=tk.W,
            width=6,
            font=self._font(9),
        )

    def _entry(self, parent, variable: tk.StringVar, width: int | None = None) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width or 18,
            bd=0,
            relief=tk.FLAT,
            bg="#f8fafc",
            fg="#111827",
            insertbackground="#111827",
            highlightthickness=1,
            highlightbackground=self.theme.border,
            highlightcolor=self.theme.accent,
            selectbackground=self.theme.accent,
            selectforeground="#ffffff",
            font=self._font(10),
        )

    def show(self) -> None:
        if self.closed:
            return
        try:
            self.window.deiconify()
            self.window.lift(self.app.root)
            self.window.focus_force()
        except Exception:
            pass

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(
            self.window,
            max(self.window.winfo_width(), self.MIN_W),
            max(self.window.winfo_height(), self.MIN_H),
            wx + event.x_root - sx,
            wy + event.y_root - sy,
        )

    def current_algorithm(self) -> str:
        value = self.algorithm_var.get()
        if value in (self.ALGORITHM_INTERVAL, self.ALGORITHM_RATE, self.ALGORITHM_FREE):
            return value
        return self.ALGORITHM_INTERVAL

    def refresh_algorithm(self) -> None:
        algorithm = self.current_algorithm()
        if algorithm == self.ALGORITHM_INTERVAL:
            self.metric_label.configure(text="时间间隔")
            self.metric_unit.configure(text="毫秒")
            self.position_entry.configure(state=tk.NORMAL)
            self.capture_button.configure(state=tk.NORMAL, cursor="hand2")
        else:
            self.metric_label.configure(text="每秒次数")
            self.metric_unit.configure(text="次/秒")
            if algorithm == self.ALGORITHM_FREE:
                self.position_entry.configure(state=tk.DISABLED)
                self.capture_button.configure(state=tk.DISABLED, cursor="arrow")
            else:
                self.position_entry.configure(state=tk.NORMAL)
                self.capture_button.configure(state=tk.NORMAL, cursor="hand2")
        self.refresh_controls()

    def toggle_multi(self) -> None:
        if self.running:
            self.multi_var.set(not self.multi_var.get())
            return
        self._cancel_capture()
        multi = self.multi_var.get()
        if multi:
            self.position_label.grid_remove()
            self.position_entry.grid_remove()
            self.capture_button.grid_remove()
            self.multi_panel.grid(row=1, column=0, columnspan=6, sticky="nsew", pady=6)
            self.algorithm_combo.configure(values=(self.ALGORITHM_INTERVAL, self.ALGORITHM_RATE))
            if self.current_algorithm() == self.ALGORITHM_FREE:
                self.algorithm_var.set(self.ALGORITHM_INTERVAL)
            self.refresh_points_list()
            self.status_var.set("多点模式：用“添加点”逐个记录目标点。")
        else:
            self.multi_panel.grid_remove()
            self.position_label.grid()
            self.position_entry.grid()
            self.capture_button.grid()
            self.algorithm_combo.configure(
                values=(self.ALGORITHM_INTERVAL, self.ALGORITHM_RATE, self.ALGORITHM_FREE)
            )
            self.status_var.set("就绪。")
        self.refresh_algorithm()

    def _cancel_capture(self) -> None:
        if self.capture_after_id is not None:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        self._reset_capture_buttons()

    def _reset_capture_buttons(self) -> None:
        try:
            self.capture_button.configure(text="定义位置")
            self.add_point_button.configure(text="添加点")
        except Exception:
            pass

    def begin_capture_position(self) -> None:
        if self.running or self.current_algorithm() == self.ALGORITHM_FREE:
            return
        self._begin_capture("single")

    def begin_capture_point(self) -> None:
        if self.running:
            return
        self._begin_capture("multi")

    def _begin_capture(self, target: str) -> None:
        if self.capture_after_id is not None:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
        self.capture_target = target
        self.capture_countdown = 3
        self.status_var.set("把鼠标移到目标位置...")
        self._tick_capture_position()

    def _tick_capture_position(self) -> None:
        if self.closed:
            return
        if self.capture_countdown <= 0:
            self.capture_after_id = None
            point = self.current_cursor_position()
            if point is None:
                self.status_var.set("无法读取鼠标位置。")
            elif self.capture_target == "multi":
                self._push_undo()
                self.points.append(point)
                self.refresh_points_list(select=len(self.points) - 1)
                self.status_var.set(f"已添加目标点 {len(self.points)}：{point[0]}, {point[1]}")
            else:
                self._push_undo()
                self.position_var.set(f"{point[0]}, {point[1]}")
                self.status_var.set(f"已记录位置：{point[0]}, {point[1]}")
            self._reset_capture_buttons()
            self.refresh_controls()
            return
        label = f"{self.capture_countdown}..."
        if self.capture_target == "multi":
            self.add_point_button.configure(text=label)
        else:
            self.capture_button.configure(text=label)
        self.capture_countdown -= 1
        self.capture_after_id = self.window.after(1000, self._tick_capture_position)

    def refresh_points_list(self, select: int | None = None) -> None:
        if not hasattr(self, "points_list"):
            return
        self.points_list.delete(0, tk.END)
        for index, (x, y) in enumerate(self.points, start=1):
            self.points_list.insert(tk.END, f"{index}.   {x}, {y}")
        if select is not None and 0 <= select < len(self.points):
            self.points_list.selection_clear(0, tk.END)
            self.points_list.selection_set(select)
            self.points_list.see(select)
        self.refresh_controls()

    def _selected_point_index(self) -> int | None:
        try:
            selection = self.points_list.curselection()
        except Exception:
            return None
        return selection[0] if selection else None

    def delete_point(self) -> None:
        if self.running:
            return
        index = self._selected_point_index()
        if index is None:
            return
        self._push_undo()
        del self.points[index]
        nxt = min(index, len(self.points) - 1) if self.points else None
        self.refresh_points_list(select=nxt)
        self.status_var.set("已删除目标点。")

    def clear_points(self) -> None:
        if self.running or not self.points:
            return
        self._push_undo()
        self.points.clear()
        self.refresh_points_list()
        self.status_var.set("已清空目标点。")

    def move_point(self, delta: int) -> None:
        if self.running:
            return
        index = self._selected_point_index()
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self.points):
            return
        self._push_undo()
        self.points[index], self.points[target] = self.points[target], self.points[index]
        self.refresh_points_list(select=target)

    def _push_undo(self) -> None:
        self.undo_stack.append((list(self.points), self.position_var.get()))
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)

    def undo(self) -> None:
        if self.running or not self.undo_stack:
            self.status_var.set("没有可撤销的操作。")
            return
        points, position = self.undo_stack.pop()
        self.points[:] = points
        self.position_var.set(position)
        self.refresh_points_list()
        self.status_var.set("已撤销上一步操作。")

    @staticmethod
    def current_cursor_position() -> tuple[int, int] | None:
        if sys.platform != "win32":
            return None
        try:
            point = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                return int(point.x), int(point.y)
        except Exception:
            return None
        return None

    @staticmethod
    def parse_position(value: str) -> tuple[int, int] | None:
        parts = re.findall(r"-?\d+", value or "")
        if len(parts) < 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    @staticmethod
    def parse_number(value: str) -> float | None:
        match = re.search(r"-?\d+(?:[\.,]\d+)?", value or "")
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except ValueError:
            return None

    def read_form(self, show_errors: bool = False) -> tuple[list[tuple[int, int] | None], float, float, float] | None:
        algorithm = self.current_algorithm()
        multi = self.multi_var.get()
        if multi:
            points: list[tuple[int, int] | None] = list(self.points)
            if not points:
                if show_errors:
                    messagebox.showinfo("缺少目标点", "请先用“添加点”记录至少一个目标点。", parent=self.window)
                return None
        elif algorithm == self.ALGORITHM_FREE:
            points = [None]
        else:
            point = self.parse_position(self.position_var.get())
            if point is None:
                if show_errors:
                    messagebox.showinfo("位置无效", "请先定义位置，或输入形如 1200, 800 的屏幕坐标。", parent=self.window)
                return None
            points = [point]
        metric = self.parse_number(self.interval_var.get())
        if metric is None or metric <= 0:
            if show_errors:
                name = "时间间隔" if algorithm == self.ALGORITHM_INTERVAL else "每秒次数"
                messagebox.showinfo(f"{name}无效", f"{name}必须大于 0。", parent=self.window)
            return None
        interval = max(0.001, metric / 1000.0) if algorithm == self.ALGORITHM_INTERVAL else max(0.001, 1.0 / metric)
        delay = self.parse_number(self.delay_var.get())
        if delay is None or delay < 0:
            if show_errors:
                messagebox.showinfo("滞后无效", "滞后时间不能小于 0。", parent=self.window)
            return None
        duration = self.parse_number(self.duration_var.get())
        if duration is None or duration < 0:
            if show_errors:
                messagebox.showinfo("时长无效", "时长不能小于 0；填 0 表示手动停止。", parent=self.window)
            return None
        return points, interval, delay, duration

    def _set_multi_buttons(self, state: str) -> None:
        for name in (
            "add_point_button",
            "del_point_button",
            "up_point_button",
            "down_point_button",
            "clear_points_button",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.configure(state=state, cursor=("arrow" if state == tk.DISABLED else "hand2"))
        if getattr(self, "mode_check", None) is not None:
            self.mode_check.configure(state=state)

    def refresh_controls(self) -> None:
        if self.running:
            self.start_button.configure(
                text="停止",
                state=tk.NORMAL,
                bg=self.theme.danger,
                fg="#ffffff",
                activebackground="#dc2626",
                cursor="hand2",
            )
            self.capture_button.configure(state=tk.DISABLED, cursor="arrow")
            self._set_multi_buttons(tk.DISABLED)
            if getattr(self, "stop_key_combo", None) is not None:
                self.stop_key_combo.configure(state=tk.DISABLED)
            return

        free_mode = self.current_algorithm() == self.ALGORITHM_FREE
        ready = self.read_form(show_errors=False) is not None
        self.start_button.configure(
            text="开始",
            state=(tk.NORMAL if ready else tk.DISABLED),
            bg=(self.theme.accent if ready else "#e2e8f0"),
            fg=("#ffffff" if ready else "#94a3b8"),
            activebackground=(self.theme.accent_hover if ready else "#e2e8f0"),
            cursor=("hand2" if ready else "arrow"),
        )
        self.capture_button.configure(state=(tk.DISABLED if free_mode else tk.NORMAL), cursor=("arrow" if free_mode else "hand2"))
        self._set_multi_buttons(tk.NORMAL)
        if getattr(self, "stop_key_combo", None) is not None:
            self.stop_key_combo.configure(state="readonly")

    def toggle_running(self) -> None:
        if self.running:
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_clicking(self) -> None:
        if sys.platform != "win32":
            messagebox.showinfo("无法启动", "内置连点器当前只支持 Windows。", parent=self.window)
            return
        self._cancel_capture()
        form = self.read_form(show_errors=True)
        if form is None:
            self.refresh_controls()
            return
        points, interval, delay, duration = form
        self.stop_event.clear()
        self.running = True
        self.refresh_controls()
        if delay:
            self.status_var.set("等待滞后时间...")
        elif len(points) > 1:
            self.status_var.set(f"正在多点连点（{len(points)} 个目标点）...")
        else:
            self.status_var.set("正在连点...")
        self.worker = threading.Thread(
            target=self._click_worker,
            args=(points, interval, delay, duration),
            daemon=True,
            name="Passer-Clicker",
        )
        self.worker.start()
        self._start_stop_hotkey()

    def stop_clicking(self) -> None:
        self.stop_event.set()
        if self.running:
            self.status_var.set("正在停止...")

    # -- 全局终止热键（GetAsyncKeyState 轮询，后台运行也能终止）----------
    def _stop_key_vk(self) -> int | None:
        return self.STOP_KEYS.get(self.stop_key_var.get())

    @staticmethod
    def _is_key_down(vk: int) -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _start_stop_hotkey(self) -> None:
        self._stop_stop_hotkey()
        vk = self._stop_key_vk()
        # 记录当前按键状态，避免启动瞬间误触发。
        self.hotkey_prev_down = self._is_key_down(vk) if vk is not None else False
        self._poll_stop_hotkey()

    def _stop_stop_hotkey(self) -> None:
        if self.hotkey_after_id is not None:
            try:
                self.window.after_cancel(self.hotkey_after_id)
            except Exception:
                pass
            self.hotkey_after_id = None

    def _poll_stop_hotkey(self) -> None:
        self.hotkey_after_id = None
        if self.closed or not self.running:
            return
        vk = self._stop_key_vk()
        if vk is not None:
            down = self._is_key_down(vk)
            if down and not self.hotkey_prev_down:
                self.hotkey_prev_down = down
                self.status_var.set(f"已通过 {self.stop_key_var.get()} 终止。")
                self.stop_clicking()
                return
            self.hotkey_prev_down = down
        try:
            self.hotkey_after_id = self.window.after(40, self._poll_stop_hotkey)
        except Exception:
            self.hotkey_after_id = None

    def _click_worker(self, points: list[tuple[int, int] | None], interval: float, delay: float, duration: float) -> None:
        import time

        clicks = 0
        if delay > 0 and self.stop_event.wait(delay):
            self._finish_from_worker("已停止。")
            return

        count = len(points)
        index = 0
        deadline = None if duration <= 0 else time.monotonic() + duration
        while not self.stop_event.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            if self._send_left_click(points[index % count]):
                clicks += 1
            else:
                self._finish_from_worker("点击失败。")
                return
            index += 1
            if self.stop_event.wait(interval):
                break

        message = f"已停止，共点击 {clicks} 次。" if self.stop_event.is_set() else f"已完成，共点击 {clicks} 次。"
        self._finish_from_worker(message)

    def _finish_from_worker(self, message: str) -> None:
        try:
            self.app.root.after(0, lambda: self.finish_clicking(message))
        except Exception:
            pass

    def finish_clicking(self, message: str) -> None:
        if self.closed:
            return
        self.running = False
        self.worker = None
        self.stop_event.set()
        self._stop_stop_hotkey()
        self.status_var.set(message)
        self.refresh_controls()

    @staticmethod
    def _send_left_click(point: tuple[int, int] | None) -> bool:
        try:
            user32 = ctypes.windll.user32
            if point is not None:
                user32.SetCursorPos(int(point[0]), int(point[1]))
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        self._stop_stop_hotkey()
        if self.capture_after_id is not None:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        if getattr(self.app, "clicker_window", None) is self:
            self.app.clicker_window = None
        try:
            self.window.destroy()
        except Exception:
            pass
