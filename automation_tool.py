"""内置工具「自动化」：Aira 定时任务。

类似 GPT 的「Scheduled tasks」/ Claude Code 的定时例程——用户（或 Aira 对话）登记一条
「到点自动让 Aira 执行的任务」，到设定的时间/周期由 Passer 在后台自动跑一遍：把任务
prompt 当作一条用户消息发给当前 Aira 模型，按动作协议自动多轮执行（可联网/检索/读写本机），
把结果记录下来并通知用户。

本模块只负责：调度时间计算 + 任务管理窗口（UI）。任务的持久化、轮询、以及真正调用模型的
运行器在 Passer.py（那里有 provider/key/模型/execute_ai_actions 等上下文）。
"""

from __future__ import annotations

import tkinter as tk
import uuid
from datetime import datetime, timedelta
from tkinter import messagebox, ttk


# 周期单位（中文标签 → timedelta 关键字 / 秒数倍率）。
INTERVAL_UNITS = (
    ("分钟", "minutes"),
    ("小时", "hours"),
    ("天", "days"),
)
UNIT_LABEL = {key: label for label, key in INTERVAL_UNITS}
MODE_ONCE = "once"
MODE_INTERVAL = "interval"
MODE_DAILY = "daily"


def new_task(title: str, prompt: str, mode: str = MODE_DAILY, *,
             every: int = 1, unit: str = "hours", at: str = "09:00",
             when: str = "", enabled: bool = True) -> dict:
    task = {
        "id": uuid.uuid4().hex,
        "title": str(title or "").strip()[:80] or "未命名任务",
        "prompt": str(prompt or "").strip(),
        "mode": mode if mode in (MODE_ONCE, MODE_INTERVAL, MODE_DAILY) else MODE_DAILY,
        "every": max(1, int(every or 1)),
        "unit": unit if unit in UNIT_LABEL else "hours",
        "at": str(at or "09:00").strip(),
        "when": str(when or "").strip(),
        "enabled": bool(enabled),
        "created": datetime.now().isoformat(timespec="seconds"),
        "last_run": "",
        "last_result": "",
        "next_run": "",
    }
    nxt = compute_next_run(task, datetime.now())
    task["next_run"] = nxt.isoformat(timespec="seconds") if nxt else ""
    if task["mode"] == MODE_ONCE and not task["next_run"]:
        task["enabled"] = False
    return task


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    text = str(text or "").strip()
    for sep in (":", "："):
        if sep in text:
            h, _, m = text.partition(sep)
            try:
                hh, mm = int(h), int(m)
            except ValueError:
                return None
            if 0 <= hh < 24 and 0 <= mm < 60:
                return hh, mm
            return None
    return None


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """解析一次性任务时间：支持 `YYYY-MM-DD HH:MM`、`MM-DD HH:MM`、`HH:MM`（缺省补全到未来）。"""
    text = str(text or "").strip().replace("/", "-")
    if not text:
        return None
    now = now or datetime.now()
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M", "%H:%M:%S", "%H:%M")
    for fmt in fmts:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            dt = dt.replace(year=now.year)
            if "%m" not in fmt:
                dt = dt.replace(month=now.month, day=now.day)
            if dt <= now:
                dt = dt + (timedelta(days=1) if "%m" not in fmt else timedelta(days=365))
        return dt
    return None


def compute_next_run(task: dict, now: datetime | None = None) -> datetime | None:
    """根据任务模式算出下一次运行时刻；一次性任务时间已过则返回 None。"""
    now = now or datetime.now()
    mode = task.get("mode")
    if mode == MODE_INTERVAL:
        every = max(1, int(task.get("every", 1) or 1))
        unit = task.get("unit", "hours")
        kwargs = {unit if unit in ("minutes", "hours", "days") else "hours": every}
        return now + timedelta(**kwargs)
    if mode == MODE_DAILY:
        hm = _parse_hhmm(task.get("at", "09:00")) or (9, 0)
        candidate = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if mode == MODE_ONCE:
        return parse_when(task.get("when", ""), now)
    return None


def describe_schedule(task: dict) -> str:
    mode = task.get("mode")
    if mode == MODE_INTERVAL:
        return f"每 {task.get('every', 1)} {UNIT_LABEL.get(task.get('unit'), '小时')}"
    if mode == MODE_DAILY:
        return f"每天 {task.get('at', '09:00')}"
    if mode == MODE_ONCE:
        return f"一次 {task.get('when', '')}"
    return "—"


def _fmt_dt(iso: str) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M")
    except ValueError:
        return iso[:16]


class AutomationWindow:
    """自动化任务管理窗口：登记、编辑、启停或立即运行 Aira 定时任务，并查看上次结果。"""

    CHROME_TOP = 46
    MIN_W = 1440
    MIN_H = 1040

    def __init__(self, app, theme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self._editing_id: str | None = None
        self._expanded_id: str | None = None
        self._selected_id: str | None = None

        self.title_var = tk.StringVar()
        self.prompt_widget: tk.Text | None = None
        self.tree: ttk.Treeview | None = None
        self.empty_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=MODE_DAILY)
        self.every_var = tk.StringVar(value="1")
        self.unit_var = tk.StringVar(value="小时")
        self.at_var = tk.StringVar(value="09:00")
        self.when_var = tk.StringVar(value="")
        self.enabled_var = tk.BooleanVar(value=True)

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)
        self.shell = tk.Frame(self.window, bg=theme.app_bg,
                              highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._build_chrome()
        self._build_body()
        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.geometry(f"{self.MIN_W}x{self.MIN_H}+{x}+{y}")
        self.window.attributes("-topmost", app.topmost_var.get())
        try:
            app.apply_window_transparency(self.window)
        except Exception:
            pass
        self.refresh_list()
        self._sync_mode_fields()
        self.window.deiconify()
        self.window.focus_force()

    # --- chrome ------------------------------------------------------------
    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        lbl = tk.Label(bar, text=t.title, bg=t.title_bg, fg="#dbe7ff",
                       anchor=tk.W, font=self._font(10, "bold"))
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        close = tk.Label(bar, text="×", bg=t.title_button_bg, fg="#dbe7ff", cursor="hand2",
                         font=self._font(13, "bold"))
        close.pack(side=tk.RIGHT, padx=(0, 10))
        close.bind("<Button-1>", lambda _e: self.close())
        close.bind("<Enter>", lambda _e: close.configure(bg=self.theme.danger, fg="#ffffff"))
        close.bind("<Leave>", lambda _e: close.configure(bg=self.theme.title_button_bg, fg="#dbe7ff"))
        for w in (bar, lbl):
            w.bind("<ButtonPress-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event):
        self.move_start = (event.x_root, event.y_root,
                           self.window.winfo_x(), self.window.winfo_y())

    def _do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    # --- body --------------------------------------------------------------
    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=self.theme.surface_bg, fg="#334155",
                        anchor=tk.W, font=self._font(9))

    def _entry(self, parent, var, width=12):
        return tk.Entry(parent, textvariable=var, width=width, relief=tk.FLAT,
                        bg="#ffffff", fg="#111827", insertbackground="#111827",
                        highlightthickness=1, highlightbackground="#cbd5e1",
                        highlightcolor=self.theme.accent, font=self._font(9))

    def _btn(self, parent, text, command, *, accent=False, danger=False):
        if danger:
            bg, fg = "#fee2e2", "#b91c1c"
        elif accent:
            bg, fg = self.theme.accent, "#ffffff"
        else:
            bg, fg = "#eef2f7", "#334155"
        b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2",
                     padx=12, pady=5, font=self._font(9, "bold"))
        b.bind("<Button-1>", lambda _e: command())
        if accent:
            b.bind("<Enter>", lambda _e: b.configure(bg=self.theme.accent_hover))
            b.bind("<Leave>", lambda _e: b.configure(bg=self.theme.accent))
        elif danger:
            b.bind("<Enter>", lambda _e: b.configure(bg="#fecaca"))
            b.bind("<Leave>", lambda _e: b.configure(bg="#fee2e2"))
        else:
            b.bind("<Enter>", lambda _e: b.configure(bg="#e2e8f0"))
            b.bind("<Leave>", lambda _e: b.configure(bg="#eef2f7"))
        return b

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        toolbar = tk.Frame(body, bg=t.surface_bg)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(16, 10))
        self._btn(toolbar, "新建任务", self._new_task, accent=True).pack(side=tk.LEFT)
        self._btn(toolbar, "编辑任务", self._edit_selected).pack(side=tk.LEFT, padx=(8, 0))
        self._btn(toolbar, "启用/禁用任务", self._toggle_selected).pack(side=tk.LEFT, padx=(8, 0))
        self._btn(toolbar, "删除任务", self._delete_selected, danger=True).pack(side=tk.LEFT, padx=(8, 0))

        header = tk.Frame(body, bg="#e2e8f0")
        header.pack(side=tk.TOP, fill=tk.X, padx=18)
        for text, width in (("状态", 8), ("任务名称", 26), ("计划", 20), ("下次运行", 16), ("指令预览", 64)):
            tk.Label(header, text=text, width=width, bg="#e2e8f0", fg="#334155",
                     anchor=tk.W, font=self._font(9, "bold"), padx=8, pady=7).pack(side=tk.LEFT, fill=tk.X)

        list_wrap = tk.Frame(body, bg=t.surface_bg, highlightthickness=1, highlightbackground=t.border)
        list_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(0, 14))
        self.list_canvas = tk.Canvas(list_wrap, bg=t.surface_bg, bd=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list_frame = tk.Frame(self.list_canvas, bg=t.surface_bg)
        self.list_window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind(
            "<Configure>",
            lambda _e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")),
        )
        self.list_canvas.bind(
            "<Configure>",
            lambda e: self.list_canvas.itemconfigure(self.list_window, width=e.width),
        )

    def _sync_mode_fields(self) -> None:
        mode = self.mode_var.get()
        rows = (
            (getattr(self, "daily_row", None), MODE_DAILY),
            (getattr(self, "interval_row", None), MODE_INTERVAL),
            (getattr(self, "once_row", None), MODE_ONCE),
        )
        for row, m in rows:
            if row is None:
                continue
            try:
                if not row.winfo_exists():
                    continue
                if mode == m:
                    row.grid()
                else:
                    row.grid_remove()
            except tk.TclError:
                continue

    # --- list --------------------------------------------------------------
    def refresh_list(self) -> None:
        for child in list(self.list_frame.winfo_children()):
            child.destroy()
        self.prompt_widget = None
        tasks = list(getattr(self.app, "automations", []))
        if self._expanded_id == "__new__":
            self._render_edit_row(None)
        for task in tasks:
            if self._expanded_id == task.get("id"):
                self._render_edit_row(task)
            else:
                self._render_task_row(task)
        if not tasks and self._expanded_id != "__new__":
            tk.Label(self.list_frame, text="还没有任务。点击「新建任务」添加自动化，或让 Aira 自动添加任务。",
                     bg=self.theme.surface_bg, fg="#94a3b8", anchor=tk.W,
                     font=self._font(10), padx=12, pady=18).pack(side=tk.TOP, fill=tk.X)

    def _bind_row_select(self, widget, task_id: str) -> None:
        widget.bind("<Button-1>", lambda _e, i=task_id: self._select_task(i), add="+")
        for child in widget.winfo_children():
            self._bind_row_select(child, task_id)

    def _select_task(self, task_id: str) -> None:
        if self._selected_id != task_id:
            self._selected_id = task_id
            self.refresh_list()

    def _selected_task(self) -> dict | None:
        if not self._selected_id:
            return None
        return next((t for t in self.app.automations if t.get("id") == self._selected_id), None)

    def _cell(self, parent, text: str, width: int, *, bg: str, fg: str = "#111827", bold: bool = False):
        return tk.Label(parent, text=text, width=width, bg=bg, fg=fg, anchor=tk.W,
                        padx=8, pady=9, font=self._font(9, "bold" if bold else "normal"))

    def _render_task_row(self, task: dict) -> None:
        task_id = task.get("id", "")
        selected = task_id == self._selected_id
        bg = self.theme.accent_soft if selected else "#ffffff"
        row = tk.Frame(self.list_frame, bg=bg, highlightthickness=1,
                       highlightbackground=(self.theme.accent if selected else "#e2e8f0"))
        row.pack(side=tk.TOP, fill=tk.X)
        status = "启用" if task.get("enabled") else "停用"
        prompt = (task.get("prompt") or "").strip().replace("\n", " ")
        prompt = prompt[:78] + ("…" if len(prompt) > 78 else "")
        values = (
            (status, 8, "#15803d" if task.get("enabled") else "#64748b", True),
            (task.get("title", ""), 26, "#111827", True),
            (describe_schedule(task), 20, "#334155", False),
            (_fmt_dt(task.get("next_run", "")), 16, "#334155", False),
            (prompt, 64, "#475569", False),
        )
        for text, width, fg, bold in values:
            self._cell(row, text, width, bg=bg, fg=fg, bold=bold).pack(side=tk.LEFT, fill=tk.X)
        self._bind_row_select(row, task_id)

    def _render_edit_row(self, task: dict | None) -> None:
        t = self.theme
        is_new = task is None
        self._editing_id = None if is_new else task.get("id")
        self.title_var.set("" if is_new else task.get("title", ""))
        self.mode_var.set(MODE_DAILY if is_new else task.get("mode", MODE_DAILY))
        self.every_var.set("1" if is_new else str(task.get("every", 1)))
        self.unit_var.set("小时" if is_new else UNIT_LABEL.get(task.get("unit"), "小时"))
        self.at_var.set("09:00" if is_new else task.get("at", "09:00"))
        self.when_var.set("" if is_new else task.get("when", ""))
        self.enabled_var.set(True if is_new else bool(task.get("enabled", True)))

        frame = tk.Frame(self.list_frame, bg="#f8fafc", highlightthickness=2, highlightbackground=t.accent)
        frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 1))
        title_row = tk.Frame(frame, bg="#f8fafc")
        title_row.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(12, 6))
        tk.Label(title_row, text=("新建任务" if is_new else "编辑任务"), bg="#f8fafc", fg="#111827",
                 font=self._font(10, "bold")).pack(side=tk.LEFT)
        self._btn(title_row, "保存", self._save_task, accent=True).pack(side=tk.RIGHT)
        self._btn(title_row, "取消", self._cancel_edit).pack(side=tk.RIGHT, padx=(0, 8))

        form = tk.Frame(frame, bg="#f8fafc")
        form.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)

        self._label(form, "名称").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self._entry(form, self.title_var, width=30).grid(row=0, column=1, sticky="ew", pady=5)
        tk.Checkbutton(form, text="启用", variable=self.enabled_var, bg="#f8fafc",
                       fg="#334155", activebackground="#f8fafc", selectcolor="#ffffff",
                       highlightcolor=t.accent,
                       font=self._font(9), cursor="hand2").grid(row=0, column=2, sticky="w", padx=(16, 8))

        self._label(form, "频率").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        mode_row = tk.Frame(form, bg="#f8fafc")
        mode_row.grid(row=1, column=1, sticky="w", pady=5)
        for label, value in (("每天", MODE_DAILY), ("周期", MODE_INTERVAL), ("一次", MODE_ONCE)):
            tk.Radiobutton(mode_row, text=label, value=value, variable=self.mode_var,
                           command=self._sync_mode_fields, bg="#f8fafc", fg="#334155",
                           activebackground="#f8fafc", selectcolor="#ffffff",
                           highlightcolor=t.accent,
                           font=self._font(9), cursor="hand2").pack(side=tk.LEFT, padx=(0, 12))

        self.daily_row = tk.Frame(form, bg="#f8fafc")
        self.daily_row.grid(row=1, column=3, sticky="w", pady=5)
        self._label(self.daily_row, "时间").pack(side=tk.LEFT, padx=(0, 6))
        self._entry(self.daily_row, self.at_var, width=8).pack(side=tk.LEFT)
        tk.Label(self.daily_row, text="HH:MM", bg="#f8fafc", fg="#94a3b8",
                 font=self._font(8)).pack(side=tk.LEFT, padx=(6, 0))

        self.interval_row = tk.Frame(form, bg="#f8fafc")
        self.interval_row.grid(row=1, column=3, sticky="w", pady=5)
        self._label(self.interval_row, "每").pack(side=tk.LEFT, padx=(0, 6))
        self._entry(self.interval_row, self.every_var, width=6).pack(side=tk.LEFT)
        ttk.Combobox(self.interval_row, textvariable=self.unit_var, state="readonly", width=6,
                     values=[label for label, _ in INTERVAL_UNITS]).pack(side=tk.LEFT, padx=(6, 0))

        self.once_row = tk.Frame(form, bg="#f8fafc")
        self.once_row.grid(row=1, column=3, sticky="w", pady=5)
        self._label(self.once_row, "时刻").pack(side=tk.LEFT, padx=(0, 6))
        self._entry(self.once_row, self.when_var, width=20).pack(side=tk.LEFT)

        self._label(form, "指令").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=5)
        self.prompt_widget = tk.Text(form, height=5, wrap=tk.WORD, relief=tk.FLAT,
                                     bg="#ffffff", fg="#111827", insertbackground="#111827",
                                     highlightthickness=1, highlightbackground="#cbd5e1",
                                     highlightcolor=t.accent, font=self._font(9))
        self.prompt_widget.grid(row=2, column=1, columnspan=3, sticky="ew", pady=5)
        if not is_new:
            self.prompt_widget.insert("1.0", task.get("prompt", ""))
        self._sync_mode_fields()
        self.prompt_widget.focus_set()

    # --- actions -----------------------------------------------------------
    def _new_task(self) -> None:
        self._clear_form()
        self._expanded_id = "__new__"
        self._selected_id = None
        self.refresh_list()

    def _expand_task(self, task_id: str) -> None:
        if not next((t for t in self.app.automations if t.get("id") == task_id), None):
            return
        self._expanded_id = task_id
        self._selected_id = task_id
        self.refresh_list()

    def _edit_selected(self) -> None:
        task = self._selected_task()
        if not task:
            messagebox.showinfo("自动化", "请先在列表中选择一个任务。", parent=self.window)
            return
        self._expand_task(task["id"])

    def _delete_selected(self) -> None:
        task = self._selected_task()
        if not task:
            messagebox.showinfo("自动化", "请先在列表中选择一个任务。", parent=self.window)
            return
        self._delete(task["id"])

    def _toggle_selected(self) -> None:
        task = self._selected_task()
        if not task:
            messagebox.showinfo("自动化", "请先在列表中选择一个任务。", parent=self.window)
            return
        self._toggle(task["id"])

    def _cancel_edit(self) -> None:
        self._clear_form()
        self.refresh_list()

    def _collect_form(self) -> dict | None:
        title = self.title_var.get().strip()
        prompt = self.prompt_widget.get("1.0", "end-1c").strip() if self.prompt_widget else ""
        if not prompt:
            messagebox.showwarning("自动化", "请填写要让 Aira 执行的「指令」。", parent=self.window)
            return None
        mode = self.mode_var.get()
        try:
            every = int(self.every_var.get() or 1)
        except ValueError:
            every = 1
        unit_key = next((k for label, k in INTERVAL_UNITS if label == self.unit_var.get()), "hours")
        if mode == MODE_ONCE and parse_when(self.when_var.get()) is None:
            messagebox.showwarning("自动化", "一次性任务的「时刻」无法识别，请用 HH:MM 或 YYYY-MM-DD HH:MM。",
                                   parent=self.window)
            return None
        return new_task(title or prompt[:20], prompt, mode, every=every, unit=unit_key,
                        at=self.at_var.get(), when=self.when_var.get(),
                        enabled=self.enabled_var.get())

    def _save_task(self) -> None:
        task = self._collect_form()
        if task is None:
            return
        if self._editing_id:
            task["id"] = self._editing_id
            existing = next((t for t in self.app.automations if t["id"] == self._editing_id), None)
            if existing:
                task["created"] = existing.get("created", task["created"])
                self.app.automations[self.app.automations.index(existing)] = task
            else:
                self.app.automations.append(task)
        else:
            self.app.automations.append(task)
        self.app.save_automations()
        self._clear_form()
        self.refresh_list()
        self.app.write_status(f"已保存自动化任务：{task['title']}")

    def _clear_form(self) -> None:
        self._editing_id = None
        self._expanded_id = None
        self.title_var.set("")
        if self.prompt_widget:
            try:
                if self.prompt_widget.winfo_exists():
                    self.prompt_widget.delete("1.0", "end")
            except tk.TclError:
                pass
            self.prompt_widget = None
        self.mode_var.set(MODE_DAILY)
        self.every_var.set("1")
        self.unit_var.set("小时")
        self.at_var.set("09:00")
        self.when_var.set("")
        self.enabled_var.set(True)
        self._sync_mode_fields()

    def _load_into_form(self, task_id: str) -> None:
        self._expand_task(task_id)

    def _toggle(self, task_id: str) -> None:
        self.app.toggle_automation(task_id)
        self.refresh_list()

    def _delete(self, task_id: str) -> None:
        task = next((t for t in self.app.automations if t["id"] == task_id), None)
        name = task.get("title", "") if task else ""
        if not messagebox.askyesno("自动化", f"删除任务「{name}」？", parent=self.window):
            return
        self.app.delete_automation(task_id)
        if self._editing_id == task_id or self._expanded_id == task_id:
            self._clear_form()
        if self._selected_id == task_id:
            self._selected_id = None
        self.refresh_list()

    def _run_now(self, task_id: str) -> None:
        self.app.run_automation_now(task_id)
        self.app.write_status("已开始运行该自动化任务…")

    # --- lifecycle ---------------------------------------------------------
    def show(self) -> None:
        self.closed = False
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self.refresh_list()
        except tk.TclError:
            pass

    def close(self) -> None:
        self.closed = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
