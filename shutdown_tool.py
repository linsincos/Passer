from __future__ import annotations

import datetime as dt
import subprocess
import tkinter as tk
from tkinter import messagebox

from clicker_tool import ClickerTheme


MAX_SHUTDOWN_SECONDS = 315360000


def parse_shutdown_time(text: str, now: dt.datetime | None = None) -> tuple[dt.datetime, int]:
    now = now or dt.datetime.now()
    raw = (text or "").strip()
    if not raw:
        raise ValueError("请输入关机时间。")
    formats = (
        "%H:%M",
        "%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    )
    target: dt.datetime | None = None
    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):
            target = now.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
        else:
            target = parsed
        break
    if target is None:
        raise ValueError("时间格式示例：23:30 或 2026-06-23 23:30。")
    seconds = int((target - now).total_seconds())
    if seconds <= 0:
        raise ValueError("关机时间必须晚于当前时间。")
    if seconds > MAX_SHUTDOWN_SECONDS:
        raise ValueError("关机时间太远，Windows 不支持这么长的计划。")
    return target, seconds


def parse_countdown(hours: str, minutes: str, seconds: str) -> int:
    try:
        h = int((hours or "0").strip() or "0")
        m = int((minutes or "0").strip() or "0")
        s = int((seconds or "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("倒计时只能输入数字。") from exc
    if h < 0 or m < 0 or s < 0:
        raise ValueError("倒计时不能为负数。")
    total = h * 3600 + m * 60 + s
    if total <= 0:
        raise ValueError("倒计时至少 1 秒。")
    if total > MAX_SHUTDOWN_SECONDS:
        raise ValueError("倒计时太长，Windows 不支持这么长的计划。")
    return total


def run_shutdown(seconds: int) -> None:
    subprocess.run(
        ["shutdown.exe", "/s", "/t", str(seconds)],
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def abort_shutdown() -> None:
    subprocess.run(
        ["shutdown.exe", "/a"],
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class ShutdownWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    WIDTH = 560
    HEIGHT = 430

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None

        self.mode_var = tk.StringVar(value="time")
        self.time_var = tk.StringVar(value=(dt.datetime.now() + dt.timedelta(hours=1)).strftime("%H:%M"))
        self.hours_var = tk.StringVar(value="0")
        self.minutes_var = tk.StringVar(value="30")
        self.seconds_var = tk.StringVar(value="0")
        self.status_var = tk.StringVar(value="选择时间关机或倒计时关机。")

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
            self.theme.place_toplevel_absolute(self.window, self.WIDTH, self.HEIGHT, x, y)
        except Exception:
            pass

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
        body_h = self.HEIGHT - self.CHROME_TOP - self.CHROME_BOTTOM - 2
        body = tk.Frame(self.shell, width=self.WIDTH - 2, height=body_h, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=False)
        body.pack_propagate(False)

        mode_frame = tk.Frame(body, bg=t.surface_bg)
        mode_frame.pack(fill=tk.X, padx=22, pady=(24, 12))
        for value, text in (("time", "时间关机"), ("countdown", "倒计时关机")):
            tk.Radiobutton(
                mode_frame, text=text, value=value, variable=self.mode_var, command=self._refresh_mode,
                bg=t.surface_bg, fg="#334155", selectcolor="#e0f2fe", activebackground=t.surface_bg,
                activeforeground="#111827", font=self._font(10, "bold"), cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 18))

        panel_box = tk.Frame(body, bg=t.surface_bg, height=120)
        panel_box.pack(fill=tk.X, padx=22, pady=(4, 12))
        panel_box.pack_propagate(False)
        self.panel_box = panel_box

        self.time_panel = tk.Frame(panel_box, bg=t.surface_bg)
        tk.Label(self.time_panel, text="关机时间", bg=t.surface_bg, fg="#334155",
                 font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 8))
        time_entry = tk.Entry(self.time_panel, textvariable=self.time_var, bd=0, bg="#f8fafc", fg="#111827",
                              insertbackground="#111827", highlightthickness=1, highlightbackground=t.border,
                              highlightcolor=t.accent, font=self._font(14, "bold"), width=22)
        time_entry.pack(anchor=tk.W, ipady=8)
        tk.Label(self.time_panel, text="支持 23:30、23:30:00、2026-06-23 23:30。",
                 bg=t.surface_bg, fg="#64748b", font=self._font(9), anchor=tk.W).pack(fill=tk.X, pady=(8, 0))

        self.count_panel = tk.Frame(panel_box, bg=t.surface_bg)
        tk.Label(self.count_panel, text="倒计时", bg=t.surface_bg, fg="#334155",
                 font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 8))
        row = tk.Frame(self.count_panel, bg=t.surface_bg)
        row.pack(anchor=tk.W)
        for label, var in (("小时", self.hours_var), ("分钟", self.minutes_var), ("秒", self.seconds_var)):
            box = tk.Frame(row, bg=t.surface_bg)
            box.pack(side=tk.LEFT, padx=(0, 12))
            entry = tk.Entry(box, textvariable=var, bd=0, bg="#f8fafc", fg="#111827",
                             insertbackground="#111827", highlightthickness=1, highlightbackground=t.border,
                             highlightcolor=t.accent, font=self._font(13, "bold"), width=6, justify=tk.CENTER)
            entry.pack(ipady=8)
            tk.Label(box, text=label, bg=t.surface_bg, fg="#64748b", font=self._font(9)).pack(pady=(5, 0))

        tk.Label(body, textvariable=self.status_var, bg="#f8fafc", fg="#111827",
                 anchor=tk.W, justify=tk.LEFT, wraplength=490, font=self._font(10),
                 padx=14, pady=12, highlightthickness=1, highlightbackground=t.border).pack(fill=tk.X, padx=22)

        actions = tk.Frame(body, bg=t.surface_bg, height=62)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(0, 14))
        actions.pack_propagate(False)
        self.cancel_button = self._button(actions, "取消关机", self.cancel_shutdown)
        self.cancel_button.pack(side=tk.RIGHT, pady=9)
        self.start_button = self._button(actions, "开始计划", self.schedule_shutdown, primary=True)
        self.start_button.pack(side=tk.RIGHT, padx=(0, 10), pady=9)

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self._refresh_mode()

    def _refresh_mode(self) -> None:
        for child in self.panel_box.winfo_children():
            child.pack_forget()
        if self.mode_var.get() == "time":
            self.time_panel.pack(fill=tk.BOTH, expand=True)
        else:
            self.count_panel.pack(fill=tk.BOTH, expand=True)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, width=12, bd=0, padx=0, pady=8,
                         bg=(self.theme.accent if primary else "#eef2f9"),
                         fg=("#ffffff" if primary else "#1f2937"),
                         activebackground=(self.theme.accent_hover if primary else "#e2e8f4"),
                         activeforeground=("#ffffff" if primary else "#111827"),
                         cursor="hand2", font=self._font(10, "bold" if primary else "normal"))

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                           bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                           activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=self.theme.title_button_bg))
        return button

    def schedule_shutdown(self) -> None:
        try:
            if self.mode_var.get() == "time":
                target, seconds = parse_shutdown_time(self.time_var.get())
                summary = f"{target.strftime('%Y-%m-%d %H:%M:%S')} 关机"
            else:
                seconds = parse_countdown(self.hours_var.get(), self.minutes_var.get(), self.seconds_var.get())
                target = dt.datetime.now() + dt.timedelta(seconds=seconds)
                summary = f"{seconds} 秒后关机（约 {target.strftime('%H:%M:%S')}）"
        except ValueError as exc:
            messagebox.showinfo("定时关机", str(exc), parent=self.window)
            return
        if not messagebox.askyesno("确认关机计划", f"确认设置 {summary}？", parent=self.window):
            return
        try:
            run_shutdown(seconds)
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or str(exc)).strip()
            self.status_var.set(f"设置失败：{err or exc}")
            return
        except Exception as exc:
            self.status_var.set(f"设置失败：{exc}")
            return
        self.status_var.set(f"已设置：{summary}。可点击“取消关机”撤销。")
        try:
            self.app.write_status(f"已设置定时关机：{summary}")
        except Exception:
            pass

    def cancel_shutdown(self) -> None:
        try:
            abort_shutdown()
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or exc.stdout or str(exc)).strip()
            self.status_var.set(f"取消失败：{err or '当前可能没有关机计划。'}")
            return
        except Exception as exc:
            self.status_var.set(f"取消失败：{exc}")
            return
        self.status_var.set("已取消计划关机。")
        try:
            self.app.write_status("已取消计划关机。")
        except Exception:
            pass

    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(self.window, self.WIDTH, self.HEIGHT,
                                           wx + event.x_root - sx, wy + event.y_root - sy)

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "shutdown_window", None) is self:
            self.app.shutdown_window = None
        self.window.destroy()
