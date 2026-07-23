from __future__ import annotations

import os
import time
import tkinter as tk
from collections import Counter
from datetime import date, datetime, timedelta
from functools import lru_cache
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import simpledialog
from tkinter import ttk

from clicker_tool import ClickerTheme

try:
    import cnlunar
    CNLUNAR_OK = True
except Exception:  # pragma: no cover - 农历库缺失时退化为只显示公历
    cnlunar = None
    CNLUNAR_OK = False

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")  # 周一为一周第一天
LUNAR_MONTHS = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊")
# 副文本（农历/节气/节日）按类型配色，融入 Passer 浅色 UI。
SUB_COLORS = {"fest": "#dc2626", "term": "#0e7490", "month": "#7c3aed", "day": "#94a3b8"}
# 只显示主流节日（公历 + 传统农历），避免农历库里成百上千的民俗/神诞条目刷屏。
SOLAR_FESTIVALS = {(1, 1): "元旦", (5, 1): "劳动节", (10, 1): "国庆节"}
LUNAR_FESTIVALS = {
    (1, 1): "春节", (1, 15): "元宵", (5, 5): "端午节",
    (7, 7): "七夕", (8, 15): "中秋", (9, 9): "重阳节", (12, 8): "腊八",
}


def nth_weekday_of_month(day: date, weekday: int, nth: int) -> bool:
    if day.weekday() != weekday:
        return False
    return ((day.day - 1) // 7) + 1 == nth


def solar_festival_labels(day: date) -> list[str]:
    labels = []
    fixed = SOLAR_FESTIVALS.get((day.month, day.day))
    if fixed:
        labels.append(fixed)
    if day.month == 5 and nth_weekday_of_month(day, 6, 2):
        labels.append("母亲节")
    if day.month == 6 and nth_weekday_of_month(day, 6, 3):
        labels.append("父亲节")
    return labels


@lru_cache(maxsize=4096)
def lunar_labels(day: date) -> tuple[tuple[str, str], ...]:
    """Return all calendar sub-labels for a day; multiple festivals are rendered smaller."""
    if not CNLUNAR_OK:
        return (("", "day"),)
    try:
        info = cnlunar.Lunar(datetime(day.year, day.month, day.day))
        month, lday = info.lunarMonth, info.lunarDay
        labels: list[tuple[str, str]] = []
        for solar in solar_festival_labels(day):
            labels.append((solar, "fest"))
        if not info.isLunarLeapMonth:
            lunar_fest = LUNAR_FESTIVALS.get((month, lday))
            if lunar_fest:
                labels.append((lunar_fest, "fest"))
            if month == 12 and lday == (30 if info.lunarMonthLong else 29):
                labels.append(("除夕", "fest"))
        if labels:
            return tuple(labels)
        term = (info.todaySolarTerms or "").strip()
        if term and term != "无":
            return ((term[:4], "term"),)
        if lday == 1:
            return ((("闰" if info.isLunarLeapMonth else "") + LUNAR_MONTHS[month - 1] + "月", "month"),)
        return (((info.lunarDayCn or "")[:4], "day"),)
    except Exception:
        return (("", "day"),)


@lru_cache(maxsize=4096)
def lunar_label(day: date) -> tuple[str, str]:
    labels = lunar_labels(day)
    return labels[0] if labels else ("", "day")


def calendar_subtext(day: date) -> tuple[str, str, bool]:
    labels = [item for item in lunar_labels(day) if item[0]]
    if not labels:
        return "", "day", False
    if len(labels) > 1 and all(kind == "fest" for _text, kind in labels):
        return " ".join(text for text, _kind in labels), "fest", True
    text, kind = labels[0]
    return text, kind, False


class PlanWindow:
    """内置计划：农历日历（公历+农历/节气/节日）。点击某一天才展开该日的计划编写，
    展开带平滑过渡。计划持久化，到点/逾期在下次打开 Passer 时于询问框上方提醒。
    """

    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 1440   # 拓展为原 720 的 2 倍：左半日历，右半计划编写区
    MIN_H = 720

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.undo_stack: list[list[dict]] = []

        today = date.today()
        # 打开时自动选中：优先续上「上次选中的日期」，否则今天。视图锚定到该日所在月。
        remembered = getattr(app, "plan_last_selected", None)
        self.selected_date: date | None = remembered if isinstance(remembered, date) else today
        anchor = self.selected_date
        self.view_year = anchor.year
        self.view_month = anchor.month
        first = date(anchor.year, anchor.month, 1)
        self.view_start = first - timedelta(days=first.weekday())
        now = datetime.now().replace(second=0, microsecond=0)
        self.time_var = tk.StringVar(value=(now + timedelta(minutes=10)).strftime("%H:%M"))
        self.notify_var = tk.StringVar(value="passer")   # 提醒方式：passer 内通知 / windows 系统通知
        # 附件：可添加多个地点/文件/文件夹，以卡片形式显示在事件框顶部。
        # 每项为 {"kind": "place"|"file"|"folder", "value": <地点或路径>, "name": <显示名>}。
        self.attachments: list[dict] = []
        self._time_popup = None                          # 时间选择器弹窗（iPhone 式时/分滚轮）
        self.month_var = tk.StringVar()
        self.next_var = tk.StringVar(value="点击日历中的某一天，添加该日计划。")
        self._day_buttons: dict = {}
        self._listed_plans: list[dict] = []
        self._editor_target = 210
        self._editor_open = False
        self._editor_anim = None
        self._editor_h = 0.0
        self._calendar_wheel_delta = 0
        self._calendar_anim = None
        self._calendar_pending = 0
        self._calendar_group_seq = 0
        self._calendar_group = ""
        self._calendar_resize_job = None
        self._lunar_warm_job = None
        self._lunar_warm_days: list[date] = []
        self._event_date_hint_active = False
        self._event_date_hint_value = ""

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)

        self.shell = tk.Frame(
            self.window, bg=theme.app_bg, highlightthickness=1, highlightbackground=theme.border
        )
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._build_chrome()
        self._build_body()

        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<Return>", lambda event: self.add_plan())
        self.window.bind("<MouseWheel>", self._on_calendar_wheel, add="+")
        self.window.bind("<Button-4>", self._on_calendar_wheel, add="+")
        self.window.bind("<Button-5>", self._on_calendar_wheel, add="+")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.geometry(f"{self.MIN_W}x{self.MIN_H}+{x}+{y}")
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.refresh_list()
        self.window.deiconify()
        self.window.focus_force()
        # 已自动选中某天：窗口就绪后把右侧编写区滑出。
        if self.selected_date is not None:
            self.window.update_idletasks()
            self._open_editor()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    # -- chrome --------------------------------------------------------
    def _build_chrome(self) -> None:
        t = self.theme
        self.toolbar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)
        self.title_label = tk.Label(
            self.toolbar, text=t.title, bg=t.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold")
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        for widget in (self.toolbar, self.title_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.body = body

        # 左半：日历列（占窗口左侧一半，保持原 720 宽时的观感）。
        left_col = tk.Frame(body, bg=t.surface_bg)
        left_col.place(relx=0.0, rely=0.0, relwidth=0.5, relheight=1.0)
        self.left_col = left_col

        # 月份/年份导航：◀◀ 上一年 · ◀ 上一月 · 月份标题 · ▶ 下一月 · ▶▶ 下一年 · 今天
        header = tk.Frame(left_col, bg=t.surface_bg)
        header.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(16, 6))
        self._nav_button(header, "◀◀", lambda: self._shift_year(-1)).pack(side=tk.LEFT)
        self._nav_button(header, "◀", lambda: self._shift_month(-1)).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(header, textvariable=self.month_var, bg=t.surface_bg, fg="#1f2937",
                 font=self._font(14, "bold")).pack(side=tk.LEFT, expand=True)
        self._nav_button(header, "今天", self._go_today).pack(side=tk.RIGHT)
        self._nav_button(header, "▶▶", lambda: self._shift_year(1)).pack(side=tk.RIGHT, padx=(6, 0))
        self._nav_button(header, "▶", lambda: self._shift_month(1)).pack(side=tk.RIGHT, padx=(6, 0))

        # 星期表头
        self.week = tk.Frame(left_col, bg=t.surface_bg)
        self.week.pack(side=tk.TOP, fill=tk.X, padx=18)
        for i, label in enumerate(WEEKDAY_LABELS):
            self.week.grid_columnconfigure(i, weight=1, uniform="cal")
            tk.Label(self.week, text=label, bg=t.surface_bg, fg=("#ef4444" if i >= 5 else "#64748b"),
                     font=self._font(9, "bold")).grid(row=0, column=i, sticky="nsew", pady=(2, 2))

        # 日历网格
        self.cal_viewport = tk.Canvas(
            left_col, bg=t.surface_bg, bd=0, highlightthickness=0, relief=tk.FLAT
        )
        self.cal_viewport.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(2, 6))
        self.cal_viewport.bind("<Configure>", self._on_calendar_resize)

        # 右半：计划编写区。挂在 body 右半，点击某一天时从右侧水平滑入，收起时滑出。
        self.editor = tk.Frame(body, bg=t.surface_bg, highlightthickness=1, highlightbackground=t.border)
        self._build_editor(self.editor)
        # 关闭态：整体推到右侧窗外（x 偏移 ≈ 右半宽度）。动画把 x 偏移收到 0 即露出。
        self.editor.place(in_=body, relx=0.5, rely=0.0, anchor=tk.NW,
                          relwidth=0.5, relheight=1.0, x=self.MIN_W // 2)

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _build_editor(self, parent) -> None:
        t = self.theme
        form = tk.Frame(parent, bg=t.surface_bg)
        form.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(8, 6))
        form.grid_columnconfigure(0, weight=1)   # 日期标签占左侧，其余控件靠右
        self.sel_label = tk.Label(form, text="", bg=t.surface_bg, fg="#1f2937", anchor=tk.W,
                                  font=self._font(11, "bold"))
        self.sel_label.grid(row=0, column=0, sticky="w", padx=(0, 12))
        # 时间框：下拉框外观（输入框 + ▾），可直接键入 HH:MM，也可点 ▾ 弹出 iPhone
        # 式时/分双滚轮选择器；整体高度与「添加计划」按钮一致（grid sticky=ns 撑满行高）。
        self.time_box = tk.Frame(form, bg="#f8fafc", highlightthickness=1,
                                 highlightbackground=t.border, bd=0)
        self.time_box.grid(row=0, column=1, sticky="nse", padx=(0, 8))
        self.time_entry = tk.Entry(
            self.time_box, textvariable=self.time_var, width=6, bd=0, relief=tk.FLAT,
            bg="#f8fafc", fg="#111827", insertbackground="#111827", justify=tk.CENTER,
            highlightthickness=0, font=self._font(10),
        )
        self.time_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        time_arrow = tk.Label(self.time_box, text="▾", bg="#f8fafc", fg="#64748b",
                              cursor="hand2", font=self._font(9))
        time_arrow.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 6))
        time_arrow.bind("<Button-1>", lambda _e: self._open_time_picker())
        # 提醒方式：与时间框同款下拉外观（仅两个选项），点击弹出小菜单切换。
        self.notify_display = tk.StringVar(
            value="Windows通知" if self.notify_var.get() == "windows" else "Passer通知")
        self.notify_box = tk.Frame(form, bg="#f8fafc", highlightthickness=1,
                                   highlightbackground=t.border, bd=0)
        self.notify_box.grid(row=0, column=2, sticky="nse", padx=(0, 8))
        # 固定标签宽度（按更长的「Windows通知」预留），切换选项时框宽不再变化。
        notify_label = tk.Label(self.notify_box, textvariable=self.notify_display, bg="#f8fafc",
                                fg="#111827", anchor=tk.W, cursor="hand2", font=self._font(10),
                                width=11)
        notify_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        notify_arrow = tk.Label(self.notify_box, text="▾", bg="#f8fafc", fg="#64748b",
                                cursor="hand2", font=self._font(9))
        notify_arrow.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 6))
        for _w in (notify_label, notify_arrow):
            _w.bind("<Button-1>", self._open_notify_menu)
        self.add_button = tk.Button(
            form, text="添加计划", command=self.add_plan, bd=0, padx=16, pady=6,
            bg=t.accent, fg="#ffffff", activebackground=t.accent_hover, activeforeground="#ffffff",
            cursor="hand2", font=self._font(10, "bold"),
        )
        self.add_button.grid(row=0, column=3, sticky="e")

        # 附加：地点 / 文件 / 文件夹——给计划挂上目标，提醒时一并展示并可点击打开。
        attach_row = tk.Frame(parent, bg=t.surface_bg)
        attach_row.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(6, 2))
        self.place_button = self._body_button(attach_row, "📍 添加地点", self._add_place)
        self.place_button.configure(width=10)
        self.place_button.pack(side=tk.LEFT)
        self.file_button = self._body_button(attach_row, "📎 添加文件", self._add_file)
        self.file_button.configure(width=10)
        self.file_button.pack(side=tk.LEFT, padx=(8, 0))
        self.folder_button = self._body_button(attach_row, "📁 添加文件夹", self._add_folder)
        self.folder_button.configure(width=11)
        self.folder_button.pack(side=tk.LEFT, padx=(8, 0))
        self.task_button = self._body_button(attach_row, "📋 添加任务", self._add_task)
        self.task_button.configure(width=10)
        self.task_button.pack(side=tk.LEFT, padx=(8, 0))

        # 操作行钉在底部，其上是「已有计划」列表（缩小），列表之上是放大的事件填写框。
        actions = tk.Frame(parent, bg=t.surface_bg)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(2, 12))
        self.collapse_button = self._body_button(actions, "收起", self._collapse_editor)
        self.collapse_button.pack(side=tk.LEFT)
        self.clear_button = self._body_button(actions, "清空", self.clear_plans)
        self.clear_button.pack(side=tk.RIGHT)
        self.delete_button = self._body_button(actions, "删除", self.delete_plan)
        self.delete_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.edit_button = self._body_button(actions, "编辑", self.edit_plan)
        self.edit_button.pack(side=tk.RIGHT, padx=(0, 8))

        # 已有计划列表：高度缩小，钉在操作行之上。
        list_wrap = tk.Frame(parent, bg=t.border)
        list_wrap.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(0, 6))
        self.plan_list = tk.Listbox(
            list_wrap, height=4, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#111827",
            selectbackground=t.accent, selectforeground="#ffffff", activestyle="none",
            highlightthickness=0, font=self._font(10),
        )
        self.plan_list.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(parent, textvariable=self.next_var, bg=t.surface_bg, fg="#334155",
                 anchor=tk.W, font=self._font(9)).pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(0, 2))

        # 事件填写框：放大为多行输入，列表下移后占据中间主区域（撑满剩余空间）。
        # 附件卡片托盘置于事件框顶部内侧：添加的地点/文件/文件夹以卡片形式显示于此。
        event_wrap = tk.Frame(parent, bg=t.border)
        event_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(4, 8))
        self.attach_tray = tk.Frame(event_wrap, bg="#f8fafc")
        self.event_text = tk.Text(
            event_wrap, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#111827",
            insertbackground="#111827", wrap=tk.WORD, padx=8, pady=6,
            highlightthickness=0, font=self._font(10),
        )
        self.event_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
        self.event_text.tag_configure("date_hint", foreground="#94a3b8")
        self.event_text.bind("<KeyPress>", self._on_event_keypress, add="+")
        self._rebuild_attach_chips()
        self._update_event_date_hint()

    def _selected_festivals(self) -> list[str]:
        if self.selected_date is None:
            return []
        return [
            label for label, kind in lunar_labels(self.selected_date)
            if label and kind == "fest"
        ]

    def _clear_event_date_hint(self) -> None:
        if not self._event_date_hint_active:
            return
        try:
            self.event_text.delete("1.0", "end")
        except tk.TclError:
            pass
        self._event_date_hint_active = False
        self._event_date_hint_value = ""

    def _update_event_date_hint(self) -> None:
        if not hasattr(self, "event_text"):
            return
        if self._event_date_hint_active:
            self._clear_event_date_hint()
        try:
            if self.event_text.get("1.0", "end-1c").strip() or not self._selected_festivals():
                return
            festivals = self._selected_festivals()
            if not festivals:
                return
            self._event_date_hint_value = " ".join(festivals)
            self.event_text.insert("1.0", self._event_date_hint_value, ("date_hint",))
            self._event_date_hint_active = True
            self.event_text.mark_set("insert", "1.0")
        except tk.TclError:
            self._event_date_hint_active = False
            self._event_date_hint_value = ""

    def _accept_event_date_hint(self) -> str:
        if not self._event_date_hint_active:
            return ""
        value = self._event_date_hint_value
        self.event_text.delete("1.0", "end")
        self.event_text.insert("1.0", value)
        self.event_text.mark_set("insert", "end-1c")
        self._event_date_hint_active = False
        self._event_date_hint_value = ""
        return "break"

    def _on_event_keypress(self, event):
        if not self._event_date_hint_active:
            return None
        if event.keysym in ("Tab", "Return", "KP_Enter"):
            return self._accept_event_date_hint()
        if event.keysym not in (
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Caps_Lock", "Num_Lock", "Scroll_Lock",
        ):
            self._clear_event_date_hint()
        return None

    # -- widgets -------------------------------------------------------
    def _chrome_button(self, parent, text, command, close=False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=self.theme.title_button_bg, fg="#e7eefc",
            activebackground=hover, activeforeground="#ffffff", font=self._font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda e=None: button.configure(bg=hover))
        button.bind("<Leave>", lambda e=None: button.configure(bg=self.theme.title_button_bg))
        return button

    def _nav_button(self, parent, text, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=12, pady=4,
            bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4", activeforeground="#111827",
            font=self._font(10, "bold"), cursor="hand2",
        )
        button.bind("<Enter>", lambda e=None: button.configure(bg="#e2e8f4"))
        button.bind("<Leave>", lambda e=None: button.configure(bg="#eef2f9"))
        return button

    def _body_button(self, parent, text, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=13, pady=6, width=6,
            bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4", activeforeground="#111827",
            font=self._font(9), cursor="hand2",
        )
        button.bind("<Enter>", lambda e=None: button.configure(bg="#e2e8f4"))
        button.bind("<Leave>", lambda e=None: button.configure(bg="#eef2f9"))
        return button

    def _entry(self, parent, variable, width=None) -> tk.Entry:
        return tk.Entry(
            parent, textvariable=variable, width=width or 18, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#111827",
            insertbackground="#111827", highlightthickness=1, highlightbackground=self.theme.border,
            highlightcolor=self.theme.accent, selectbackground=self.theme.accent, selectforeground="#ffffff",
            font=self._font(10),
        )

    # -- window dragging / resize -------------------------------------
    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), self.MIN_W), max(self.window.winfo_height(), self.MIN_H),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(
            f"{max(self.MIN_W, sw + event.x_root - sx)}x{max(self.MIN_H, sh + event.y_root - sy)}"
        )

    # -- calendar ------------------------------------------------------
    def _month_after(self, year: int, month: int, delta: int) -> tuple[int, int]:
        month = month - 1 + delta
        return year + month // 12, month % 12 + 1

    def _month_grid_start(self, year: int, month: int) -> date:
        first = date(year, month, 1)
        return first - timedelta(days=first.weekday())

    def _dominant_month(self, start: date) -> tuple[int, int]:
        counts = Counter((day.year, day.month) for day in (start + timedelta(days=i) for i in range(42)))
        highest = max(counts.values())
        winners = [year_month for year_month, count in counts.items() if count == highest]
        if len(winners) == 1:
            return winners[0]
        middle = start + timedelta(days=20)
        return middle.year, middle.month

    def _shift_month(self, delta: int, animate: bool = True) -> None:
        if not delta:
            return
        self.view_year, self.view_month = self._month_after(self.view_year, self.view_month, delta)
        self.view_start = self._month_grid_start(self.view_year, self.view_month)
        self.refresh_list()

    def _shift_year(self, delta: int) -> None:
        if not delta:
            return
        self.view_year += delta
        self.view_start = self._month_grid_start(self.view_year, self.view_month)
        self.refresh_list()

    def _shift_week(self, delta: int) -> None:
        """按周滚动；一个单位始终只移动一行（7 天）。"""
        if not delta:
            return
        self._calendar_pending += delta
        if self._calendar_anim is None:
            self._start_calendar_animation()

    def _start_calendar_animation(self) -> None:
        if self._calendar_anim is not None or not self._calendar_pending:
            return
        if self._lunar_warm_job is not None:
            try:
                self.window.after_cancel(self._lunar_warm_job)
            except tk.TclError:
                pass
            self._lunar_warm_job = None
        direction = 1 if self._calendar_pending > 0 else -1
        self._calendar_pending -= direction
        target_start = self.view_start + timedelta(days=direction * 7)
        target_year, target_month = self._dominant_month(target_start)

        self.cal_viewport.update_idletasks()
        height = max(1, self.cal_viewport.winfo_height())
        outgoing = self._calendar_group
        incoming = self._next_calendar_group()
        row_height = max(1, round(height / 6))
        incoming_buttons = self._draw_calendar(
            target_start, target_year, target_month, incoming, direction * row_height
        )
        # Canvas 在同一绘图表面内移动整组图元，避免窗口控件逐个重排和重绘。
        self.cal_viewport.tag_lower(incoming, outgoing)
        started = time.perf_counter()
        # 单次保持顺滑；连续滚动时自动缩短，避免动画队列越积越多。
        duration = 0.10 if self._calendar_pending else 0.15
        last_offset = 0

        def tick():
            nonlocal last_offset
            if self.closed:
                return
            progress = min(1.0, (time.perf_counter() - started) / duration)
            eased = 1.0 - (1.0 - progress) ** 3
            offset = int(row_height * eased)
            try:
                distance = offset - last_offset
                if distance:
                    self.cal_viewport.move(outgoing, 0, -direction * distance)
                    self.cal_viewport.move(incoming, 0, -direction * distance)
                    last_offset = offset
            except tk.TclError:
                self._calendar_anim = None
                return
            if progress < 1.0:
                self._calendar_anim = self.window.after(15, tick)
                return

            self.cal_viewport.delete(outgoing)
            self.view_start = target_start
            self.view_year, self.view_month = target_year, target_month
            self.month_var.set(f"{target_year}年{target_month}月")
            self._calendar_group = incoming
            self._day_buttons = incoming_buttons
            self._calendar_anim = None
            if self._calendar_pending:
                self._start_calendar_animation()
            else:
                self._schedule_lunar_prewarm()

        self._calendar_anim = self.window.after(0, tick)

    def _on_calendar_wheel(self, event):
        """在星期表头和日历网格上滚动切月，方向与 Windows 日历一致。"""
        try:
            left = self.cal_viewport.winfo_rootx()
            right = left + self.cal_viewport.winfo_width()
            top = self.week.winfo_rooty()
            bottom = self.cal_viewport.winfo_rooty() + self.cal_viewport.winfo_height()
            if not (left <= event.x_root < right and top <= event.y_root < bottom):
                return None
        except (AttributeError, tk.TclError):
            return None

        button = getattr(event, "num", 0)
        if button in (4, 5):
            self._calendar_wheel_delta = 0
            self._shift_week(-1 if button == 4 else 1)
            return "break"

        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return None
        if self._calendar_wheel_delta and (delta > 0) != (self._calendar_wheel_delta > 0):
            self._calendar_wheel_delta = 0
        self._calendar_wheel_delta += delta

        # Windows 鼠标通常每格为 120；触控板会产生更小的增量，因此先累计。
        steps = abs(self._calendar_wheel_delta) // 120
        if steps:
            direction = -1 if self._calendar_wheel_delta > 0 else 1
            self._calendar_wheel_delta -= (120 * steps) * (1 if self._calendar_wheel_delta > 0 else -1)
            self._shift_week(direction * steps)
        return "break"

    def _go_today(self) -> None:
        self._select_day(date.today())

    def _select_day(self, day: date) -> None:
        previous = self.selected_date
        is_visible = day in self._day_buttons
        self.selected_date = day
        self.app.plan_last_selected = day   # 记住本次选择，下次打开计划工具自动续上
        if is_visible:
            # 日期选择只重绘前后两个单元，避免销毁并重建整张日历造成闪烁。
            self._restyle_day(previous)
            self._restyle_day(day)
            self.refresh_list(render_calendar=False)
        else:
            self.view_year, self.view_month = day.year, day.month
            self.view_start = self._month_grid_start(day.year, day.month)
            self.refresh_list()
        self._update_event_date_hint()
        self._open_editor()

    def _next_calendar_group(self) -> str:
        self._calendar_group_seq += 1
        return f"calendar_{self._calendar_group_seq}"

    def _calendar_style(self, day: date, year: int, month: int):
        in_month = day.year == year and day.month == month
        is_today = day == date.today()
        selected = day == self.selected_date
        _sub, sub_type, _multi_sub = calendar_subtext(day)
        if selected:
            bg, num_fg, sub_fg = self.theme.accent, "#ffffff", "#e8efff"
        elif not in_month:
            bg, num_fg, sub_fg = "#fbfcfe", "#cbd5e1", "#d8e0ea"
        elif is_today:
            bg, num_fg, sub_fg = "#eef2ff", "#1e3a8a", SUB_COLORS.get(sub_type, "#94a3b8")
        else:
            bg = "#f8fafc"
            num_fg = "#ef4444" if day.weekday() >= 5 else "#1f2937"
            sub_fg = SUB_COLORS.get(sub_type, "#94a3b8")
        return bg, num_fg, sub_fg, selected, is_today

    def _draw_calendar(self, start: date, year: int, month: int, group: str, y_offset=0) -> dict:
        canvas = self.cal_viewport
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        column_width = width / 7.0
        row_height = height / 6.0
        buttons = {}
        for index in range(42):
            day = start + timedelta(days=index)
            row, column = divmod(index, 7)
            x0 = column * column_width + 2
            x1 = (column + 1) * column_width - 2
            y0 = y_offset + row * row_height + 2
            y1 = y_offset + (row + 1) * row_height - 2
            center_x = (x0 + x1) / 2
            cell_height = max(1, y1 - y0)
            sub, _sub_type, multi_sub = calendar_subtext(day)
            bg, num_fg, sub_fg, selected, is_today = self._calendar_style(day, year, month)
            cell_tag = f"{group}_{day:%Y%m%d}"
            tags = (group, cell_tag)
            rect = canvas.create_rectangle(
                x0, y0, x1, y1,
                fill=bg,
                outline="#6366f1" if (is_today and not selected) else bg,
                width=2 if (is_today and not selected) else 0,
                tags=tags,
            )
            num = canvas.create_text(
                center_x, y0 + cell_height * 0.37,
                text=str(day.day), fill=num_fg,
                font=self._font(13, "bold" if (selected or is_today) else "normal"),
                tags=tags,
            )
            sub_item = canvas.create_text(
                center_x, y0 + cell_height * 0.68,
                text=sub or " ", fill=sub_fg, font=self._font(6 if multi_sub else 8),
                width=max(1, int((x1 - x0) - 6)), tags=tags,
            )
            canvas.tag_bind(cell_tag, "<Button-1>", lambda _event, d=day: self._select_day(d))
            canvas.tag_bind(cell_tag, "<Enter>", lambda _event, d=day: self._hover_cell(d, True))
            canvas.tag_bind(cell_tag, "<Leave>", lambda _event, d=day: self._hover_cell(d, False))
            buttons[day] = {"rect": rect, "num": num, "sub": sub_item}
        return buttons

    def _restyle_day(self, day: date | None) -> None:
        if day is None:
            return
        items = self._day_buttons.get(day)
        if items is None:
            return
        bg, num_fg, sub_fg, selected, is_today = self._calendar_style(
            day, self.view_year, self.view_month
        )
        try:
            self.cal_viewport.itemconfigure(
                items["rect"], fill=bg,
                outline="#6366f1" if (is_today and not selected) else bg,
                width=2 if (is_today and not selected) else 0,
            )
            self.cal_viewport.itemconfigure(
                items["num"], fill=num_fg,
                font=self._font(13, "bold" if (selected or is_today) else "normal"),
            )
            _sub, _sub_type, multi_sub = calendar_subtext(day)
            self.cal_viewport.itemconfigure(items["sub"], fill=sub_fg, font=self._font(6 if multi_sub else 8))
        except tk.TclError:
            pass

    def _hover_cell(self, day, entering) -> None:
        if self._calendar_anim is not None:
            return
        if day == self.selected_date:
            return
        items = self._day_buttons.get(day)
        if items is None:
            return
        in_month = day.year == self.view_year and day.month == self.view_month
        is_today = day == date.today()
        if entering:
            bg = "#e2e8f4"
        elif is_today:
            bg = "#eef2ff"
        elif not in_month:
            bg = "#fbfcfe"
        else:
            bg = "#f8fafc"
        try:
            self.cal_viewport.itemconfigure(items["rect"], fill=bg)
            self.cal_viewport.configure(cursor="hand2" if entering else "")
        except tk.TclError:
            pass

    def _render_calendar(self) -> None:
        if self._calendar_anim is not None:
            try:
                self.window.after_cancel(self._calendar_anim)
            except tk.TclError:
                pass
            self._calendar_anim = None
        self._calendar_pending = 0
        old_group = self._calendar_group
        new_group = self._next_calendar_group()
        self.view_year, self.view_month = self._dominant_month(self.view_start)
        new_buttons = self._draw_calendar(
            self.view_start, self.view_year, self.view_month, new_group
        )
        self._calendar_group = new_group
        self._day_buttons = new_buttons
        self.month_var.set(f"{self.view_year}年{self.view_month}月")
        if old_group:
            self.cal_viewport.delete(old_group)
        self._schedule_lunar_prewarm()

    def _on_calendar_resize(self, event) -> None:
        if self.closed or event.width < 20 or event.height < 20:
            return
        if self._calendar_resize_job is not None:
            try:
                self.window.after_cancel(self._calendar_resize_job)
            except tk.TclError:
                pass

        def redraw():
            self._calendar_resize_job = None
            if self.closed:
                return
            if self._calendar_anim is not None:
                self._calendar_resize_job = self.window.after(40, redraw)
                return
            self._render_calendar()

        self._calendar_resize_job = self.window.after(40, redraw)

    def _schedule_lunar_prewarm(self) -> None:
        """在空闲帧逐日预热相邻两周，避免下一次滚动临时计算农历。"""
        if self.closed:
            return
        if self._lunar_warm_job is not None:
            try:
                self.window.after_cancel(self._lunar_warm_job)
            except tk.TclError:
                pass
        before = [self.view_start - timedelta(days=i) for i in range(1, 8)]
        after = [self.view_start + timedelta(days=i) for i in range(42, 49)]
        self._lunar_warm_days = before + after

        def warm_one():
            self._lunar_warm_job = None
            if self.closed or self._calendar_anim is not None or not self._lunar_warm_days:
                return
            lunar_labels(self._lunar_warm_days.pop())
            if self._lunar_warm_days:
                self._lunar_warm_job = self.window.after(2, warm_one)

        self._lunar_warm_job = self.window.after_idle(warm_one)

    # -- editor reveal -------------------------------------------------
    def _place_editor_progress(self, progress: float) -> None:
        if self._editor_anim is not None:
            try:
                self.window.after_cancel(self._editor_anim)
            except Exception:
                pass
            self._editor_anim = None
        self._editor_h = 1.0 if progress else 0.0
        try:
            panel_w = max(1, int(self.body.winfo_width() * 0.5))
            self.editor.place_configure(
                relx=0.5, rely=0.0, anchor=tk.NW, relwidth=0.5, relheight=1.0,
                x=int((1.0 - self._editor_h) * panel_w),
            )
            if self._editor_h > 0:
                self.editor.lift()
        except Exception:
            pass

    def _open_editor(self) -> None:
        self._editor_open = True
        self._place_editor_progress(1.0)

    def _close_editor(self) -> None:
        if not self._editor_open:
            return
        self._editor_open = False
        self._place_editor_progress(0.0)

    def _collapse_editor(self) -> None:
        """收起编写区：取消选中并把浮层平滑收回底部。"""
        previous = self.selected_date
        self.selected_date = None
        self._restyle_day(previous)
        self.refresh_list(render_calendar=False)

    # -- 附加地点 / 文件 / 文件夹 + 外部预填 ---------------------------
    def _add_place(self) -> None:
        """打开地图工具：在地图上右键某点选「添加计划」即可把该地点带回本计划。"""
        try:
            self.app.open_map_tool()
            self.app.write_status("已打开地图：右键某点选「添加计划」即可作为本计划的地点。")
        except Exception:
            pass

    def _open_notify_menu(self, _event=None) -> None:
        menu = tk.Menu(self.window, tearoff=False)
        for value, label in (("passer", "Passer通知"), ("windows", "Windows通知")):
            menu.add_command(label=label,
                             command=lambda v=value, l=label: self._set_notify(v, l))
        try:
            x = self.notify_box.winfo_rootx()
            y = self.notify_box.winfo_rooty() + self.notify_box.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_notify(self, value: str, label: str) -> None:
        self.notify_var.set(value)
        self.notify_display.set(label)

    def _add_file(self) -> None:
        # 支持一次选择多个文件。
        paths = filedialog.askopenfilenames(parent=self.window, title="选择要提醒的文件（可多选）")
        for path in paths:
            self._append_attachment("file", path)
        if paths:
            self._rebuild_attach_chips()

    def _add_folder(self) -> None:
        path = filedialog.askdirectory(parent=self.window, title="选择要提醒的文件夹")
        if not path:
            return
        self._append_attachment("folder", path)
        self._rebuild_attach_chips()

    def _add_task(self) -> None:
        """引用一个「自动化」任务：提醒时可点击跳转到该任务界面。"""
        tasks = [t for t in getattr(self.app, "automations", []) if isinstance(t, dict)]
        if not tasks:
            messagebox.showinfo(
                "暂无自动化任务",
                "还没有自动化任务可引用。\n请先在「自动化」内置工具中新建任务。",
                parent=self.window,
            )
            return
        menu = tk.Menu(self.window, tearoff=0, font=self._font(10))
        for task in tasks:
            tid = str(task.get("id") or "")
            if not tid:
                continue
            title = str(task.get("title") or "未命名任务").replace("\n", " ")
            status = "启用" if task.get("enabled") else "停用"
            label = self._ellipsize(f"{title}（{status}）", 30)
            menu.add_command(
                label=label,
                command=lambda i=tid, n=str(task.get("title") or "未命名任务"): self._pick_task(i, n),
            )
        try:
            x = self.task_button.winfo_rootx()
            y = self.task_button.winfo_rooty() + self.task_button.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _pick_task(self, task_id: str, title: str) -> None:
        self._append_attachment("task", task_id, name=title)
        self._rebuild_attach_chips()

    def _append_attachment(self, kind: str, value: str, name: str | None = None) -> None:
        value = str(value or "").strip()
        if not value:
            return
        if name is not None and str(name).strip():
            name = str(name).strip()
        elif kind in ("file", "folder"):
            name = value.replace("\\", "/").rstrip("/").split("/")[-1] or value
        else:
            name = value
        # 去重：同 kind+value 不重复添加。
        for att in self.attachments:
            if att.get("kind") == kind and att.get("value") == value:
                return
        self.attachments.append({"kind": kind, "value": value, "name": name})

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self.attachments):
            del self.attachments[index]
            self._rebuild_attach_chips()

    def _rebuild_attach_chips(self) -> None:
        """以「附件卡片」形式在事件框顶部显示已添加的地点/文件/文件夹（仿询问框附件）。
        卡片过多时自动换行到下一排。"""
        if not hasattr(self, "attach_tray"):
            return
        for child in self.attach_tray.winfo_children():
            child.destroy()
        if not self.attachments:
            self.attach_tray.pack_forget()
            return
        # before=event_text 确保托盘排在事件框上方（事件框 expand 会吃掉后续空间）。
        self.attach_tray.pack(side=tk.TOP, fill=tk.X, padx=1, pady=(1, 0),
                              before=self.event_text)
        self.attach_tray.update_idletasks()
        avail = self.event_text.winfo_width() or self.attach_tray.winfo_width() or 600
        avail = max(220, avail - 10)
        fnt = tkfont.Font(font=self._font(9))
        icons = {"place": "📍", "file": "📄", "folder": "📁", "task": "📋"}
        row = tk.Frame(self.attach_tray, bg="#f8fafc")
        row.pack(fill=tk.X, anchor=tk.W)
        row_w = 0
        for i, att in enumerate(self.attachments):
            disp = self._ellipsize(att["name"], 16)
            est = 34 + fnt.measure(disp) + 30   # 图标+间距 + 文字 + ✕+内边距
            if row_w > 0 and row_w + est > avail:
                row = tk.Frame(self.attach_tray, bg="#f8fafc")
                row.pack(fill=tk.X, anchor=tk.W)
                row_w = 0
            chip = tk.Frame(row, bg="#ffffff", highlightthickness=1,
                            highlightbackground="#dbe3ef", bd=0)
            chip.pack(side=tk.LEFT, padx=(0, 6), pady=4)
            tk.Label(chip, text=icons.get(att["kind"], "📄"), bg="#ffffff",
                     font=self._font(9)).pack(side=tk.LEFT, padx=(6, 2), pady=3)
            tk.Label(chip, text=disp, bg="#ffffff", fg="#1f2937",
                     font=self._font(9)).pack(side=tk.LEFT)
            x = tk.Label(chip, text="✕", bg="#ffffff", fg="#94a3b8", cursor="hand2",
                         font=self._font(8))
            x.pack(side=tk.LEFT, padx=(5, 6))
            x.bind("<Button-1>", lambda _e, idx=i: self._remove_attachment(idx))
            x.bind("<Enter>", lambda _e, w=x: w.configure(fg="#dc2626"))
            x.bind("<Leave>", lambda _e, w=x: w.configure(fg="#94a3b8"))
            row_w += est + 6

    @staticmethod
    def _ellipsize(text: str, limit: int) -> str:
        text = str(text)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def prefill(self, event: str | None = None, place: str | None = None,
                path: str | None = None) -> None:
        """外部（地图右键 / 主界面右键）唤起计划工具并追加目标地点或文件（支持多个）。"""
        if self.closed:
            return
        if self.selected_date is None:
            today = date.today()
            self.selected_date = today
            self.view_year, self.view_month = today.year, today.month
            self.view_start = self._month_grid_start(today.year, today.month)
            self.refresh_list()
        if place:
            self._append_attachment("place", place)
        if path:
            kind = "folder" if os.path.isdir(str(path)) else "file"
            self._append_attachment(kind, path)
        # 仅在事件框为空时填入默认事件，避免覆盖用户已输入的内容（如从地图回填地点时）。
        if event and hasattr(self, "event_text"):
            if not self.event_text.get("1.0", "end-1c").strip():
                self.event_text.delete("1.0", "end")
                self.event_text.insert("1.0", str(event))
        self._rebuild_attach_chips()
        self._open_editor()
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            if hasattr(self, "event_text"):
                self.event_text.focus_set()
        except tk.TclError:
            pass

    # -- time picker (iPhone 式时/分双滚轮) ----------------------------
    def _open_time_picker(self) -> None:
        if self._time_popup is not None:
            self._close_time_picker()
            return
        cur = (self.time_var.get() or "").strip()
        try:
            parts = cur.split(":")
            ch, cm = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            n = datetime.now()
            ch, cm = n.hour, n.minute
        t = self.theme
        pop = tk.Toplevel(self.window)
        pop.overrideredirect(True)
        pop.configure(bg=t.border)
        try:
            pop.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._time_popup = pop
        frame = tk.Frame(pop, bg="#ffffff", highlightthickness=1, highlightbackground=t.border)
        frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        cols = tk.Frame(frame, bg="#ffffff")
        cols.pack(padx=12, pady=(10, 6))
        hour_lb = self._picker_column(cols, [f"{h:02d}" for h in range(24)], ch)
        hour_lb.pack(side=tk.LEFT)
        tk.Label(cols, text=":", bg="#ffffff", fg="#111827",
                 font=self._font(15, "bold")).pack(side=tk.LEFT, padx=6)
        minute_lb = self._picker_column(cols, [f"{m:02d}" for m in range(60)], cm)
        minute_lb.pack(side=tk.LEFT)
        btns = tk.Frame(frame, bg="#ffffff")
        btns.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Button(btns, text="确定", command=lambda: self._confirm_time(hour_lb, minute_lb),
                  bd=0, padx=16, pady=5, bg=t.accent, fg="#ffffff", activebackground=t.accent_hover,
                  activeforeground="#ffffff", cursor="hand2", font=self._font(9, "bold")).pack(side=tk.RIGHT)
        tk.Button(btns, text="取消", command=self._close_time_picker,
                  bd=0, padx=14, pady=5, bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                  activeforeground="#111827", cursor="hand2", font=self._font(9)).pack(side=tk.RIGHT, padx=(0, 8))
        pop.bind("<Escape>", lambda _e: self._close_time_picker())
        # 摆在时间框正下方。
        self.time_box.update_idletasks()
        bx = self.time_box.winfo_rootx()
        by = self.time_box.winfo_rooty() + self.time_box.winfo_height() + 4
        pop.update_idletasks()
        pop.geometry(f"+{bx}+{by}")
        self.app.keep_window_above_main(pop)
        pop.lift()
        pop.focus_set()

    def _picker_column(self, parent, values: list[str], current: int) -> tk.Listbox:
        t = self.theme
        lb = tk.Listbox(
            parent, height=7, width=4, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#111827",
            selectbackground=t.accent, selectforeground="#ffffff", activestyle="none",
            highlightthickness=1, highlightbackground=t.border, exportselection=False,
            font=self._font(14), justify=tk.CENTER,
        )
        for v in values:
            lb.insert(tk.END, v)
        idx = current if 0 <= current < len(values) else 0
        lb.selection_set(idx)
        lb.activate(idx)
        lb.yview(max(0, idx - 3))   # 把选中项滚到中间，像滚轮一样
        # 滚轮滚动浏览（指针悬停在该列即可）。
        lb.bind("<MouseWheel>", lambda e, w=lb: (w.yview_scroll(int(-e.delta / 120), "units"), "break")[1])
        return lb

    def _confirm_time(self, hour_lb: tk.Listbox, minute_lb: tk.Listbox) -> None:
        def pick(lb):
            sel = lb.curselection()
            return lb.get(sel[0]) if sel else lb.get(lb.index(tk.ACTIVE))
        try:
            self.time_var.set(f"{pick(hour_lb)}:{pick(minute_lb)}")
        except (tk.TclError, IndexError):
            pass
        self._close_time_picker()

    def _close_time_picker(self) -> None:
        pop = self._time_popup
        self._time_popup = None
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass

    # -- plans ---------------------------------------------------------
    def _compose_when(self) -> datetime | None:
        if self.selected_date is None:
            return None
        text = (self.time_var.get() or "").strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                clock = datetime.strptime(text, fmt).time()
                return datetime.combine(self.selected_date, clock)
            except ValueError:
                continue
        return None

    def add_plan(self) -> None:
        if self.selected_date is None:
            messagebox.showinfo("请选择日期", "请先在日历中点击一个日期。", parent=self.window)
            return
        when = self._compose_when()
        if when is None:
            messagebox.showinfo("时间无效", "请输入有效时间，例如 14:30。", parent=self.window)
            return
        if when <= datetime.now():
            messagebox.showinfo("时间已过", "提醒时间必须晚于当前时间。", parent=self.window)
            return
        if self._event_date_hint_active:
            self._clear_event_date_hint()
        event = self.event_text.get("1.0", "end-1c").strip() or "（无事件说明）"
        notify = "windows" if self.notify_var.get() == "windows" else "passer"
        plan = {"when": when, "event": event, "notify": notify}
        if self.attachments:
            plan["attachments"] = [dict(att) for att in self.attachments]
        self._push_undo()
        self.app.plans.append(plan)
        self.app.plans.sort(key=lambda a: a["when"])
        self.app.save_plans()
        self.event_text.delete("1.0", "end")
        self._event_date_hint_active = False
        self._event_date_hint_value = ""
        self.attachments = []
        self._rebuild_attach_chips()
        self.refresh_list()

    def delete_plan(self) -> None:
        sel = self.plan_list.curselection()
        if not sel:
            return
        index = sel[0]
        if 0 <= index < len(self._listed_plans):
            plan = self._listed_plans[index]
            self._push_undo()
            try:
                self.app.plans.remove(plan)
            except ValueError:
                pass
            self.app.save_plans()
            self.refresh_list()

    def edit_plan(self) -> None:
        sel = self.plan_list.curselection()
        if not sel:
            return
        index = sel[0]
        if not (0 <= index < len(self._listed_plans)):
            return
        plan = self._listed_plans[index]
        when = plan.get("when")
        if not isinstance(when, datetime):
            return
        self._push_undo()
        try:
            self.app.plans.remove(plan)
        except ValueError:
            pass
        self.selected_date = when.date()
        self.app.plan_last_selected = self.selected_date
        self.time_var.set(when.strftime("%H:%M"))
        notify = "windows" if plan.get("notify") == "windows" else "passer"
        self._set_notify(notify, "Windows通知" if notify == "windows" else "Passer通知")
        self._clear_event_date_hint()
        self.event_text.delete("1.0", "end")
        self.event_text.insert("1.0", str(plan.get("event") or ""))
        self.attachments = [dict(att) for att in plan.get("attachments", []) if isinstance(att, dict)]
        self._rebuild_attach_chips()
        self.app.save_plans()
        self.refresh_list(render_calendar=True)
        self._open_editor()
        try:
            self.event_text.focus_set()
        except tk.TclError:
            pass

    def clear_plans(self) -> None:
        if not self.app.plans:
            return
        if not messagebox.askyesno("清空计划", "确定清空全部待提醒计划？", parent=self.window):
            return
        self._push_undo()
        self.app.plans.clear()
        self.app.save_plans()
        self.refresh_list()

    def _push_undo(self) -> None:
        self.undo_stack.append([dict(plan) for plan in self.app.plans])
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)

    def undo(self) -> None:
        if not self.undo_stack:
            self.app.write_status("计划没有可撤销的操作。")
            return
        self.app.plans[:] = self.undo_stack.pop()
        self.app.save_plans()
        self.refresh_list()
        self.app.write_status("已撤销计划的上一步操作。")

    def refresh_list(self, select_when: datetime | None = None, *, render_calendar: bool = True) -> None:
        if self.closed or not hasattr(self, "plan_list"):
            return
        if select_when is not None:
            self.selected_date = select_when.date()
            self.view_year, self.view_month = self.selected_date.year, self.selected_date.month
            self.view_start = self._month_grid_start(self.view_year, self.view_month)
        if render_calendar:
            self._render_calendar()

        if self.selected_date is None:
            self.next_var.set("点击日历中的某一天，添加该日计划。")
            self._listed_plans = []
            self.plan_list.delete(0, tk.END)
            self._close_editor()
            return

        weekday = "一二三四五六日"[self.selected_date.weekday()]
        self.sel_label.configure(text=f"{self.selected_date.strftime('%Y-%m-%d')}（周{weekday}）")
        self._listed_plans = [p for p in self.app.plans if p["when"].date() == self.selected_date]
        self._listed_plans.sort(key=lambda a: a["when"])
        self.plan_list.delete(0, tk.END)
        if self._listed_plans:
            for plan in self._listed_plans:
                event_line = str(plan.get("event") or "").replace("\n", " ")
                kinds = {att.get("kind") for att in plan.get("attachments", [])}
                tags = (("  📍" if "place" in kinds else "") + ("  📄" if "file" in kinds else "")
                        + ("  📁" if "folder" in kinds else "") + ("  📋" if "task" in kinds else ""))
                self.plan_list.insert(tk.END, f"{plan['when'].strftime('%H:%M')}   {event_line}{tags}")
        else:
            self.plan_list.insert(tk.END, "（该日暂无计划，填写时间与事件后点添加）")
        if select_when is not None:
            for i, plan in enumerate(self._listed_plans):
                if plan["when"] == select_when:
                    self.plan_list.selection_clear(0, tk.END)
                    self.plan_list.selection_set(i)
                    self.plan_list.see(i)
                    break

        upcoming = sorted((p for p in self.app.plans if p["when"] >= datetime.now()), key=lambda a: a["when"])
        if upcoming:
            nxt = upcoming[0]
            self.next_var.set(f"即将提醒：{nxt['when'].strftime('%Y-%m-%d %H:%M')}  {nxt['event']}")
        elif self.app.plans:
            self.next_var.set("全部计划均已过期。")
        else:
            self.next_var.set("该日暂无计划。")
        if select_when is not None:
            self._open_editor()

    def show(self) -> None:
        if self.closed:
            return
        try:
            self.window.deiconify()
            self.window.lift(self.app.root)
            self.window.focus_force()
            self.refresh_list()
        except Exception:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._calendar_anim is not None:
            try:
                self.window.after_cancel(self._calendar_anim)
            except Exception:
                pass
        if self._calendar_resize_job is not None:
            try:
                self.window.after_cancel(self._calendar_resize_job)
            except Exception:
                pass
        if self._lunar_warm_job is not None:
            try:
                self.window.after_cancel(self._lunar_warm_job)
            except Exception:
                pass
        if self._editor_anim is not None:
            try:
                self.window.after_cancel(self._editor_anim)
            except Exception:
                pass
        self._close_time_picker()
        if getattr(self.app, "plan_window", None) is self:
            self.app.plan_window = None
        try:
            self.window.destroy()
        except Exception:
            pass
