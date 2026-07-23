from __future__ import annotations

import random
import re
import tkinter as tk
from tkinter import messagebox, ttk

from clicker_tool import ClickerTheme


class RandomWindow:
    """内置随机数生成器：可设置范围与数量，支持去重 / 排序 / 复制。"""

    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 440
    MIN_H = 470

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.results: list[int] = []

        self.min_var = tk.StringVar(value="1")
        self.max_var = tk.StringVar(value="100")
        self.count_var = tk.StringVar(value="5")
        self.unique_var = tk.BooleanVar(value=False)
        self.sort_var = tk.BooleanVar(value=False)

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

        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<Return>", lambda event: self.generate())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    # -- chrome --------------------------------------------------------
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

        self._chrome_button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        for widget in (self.toolbar, self.title_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        form = tk.Frame(body, bg=t.surface_bg)
        form.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(18, 6))
        form.grid_columnconfigure(0, minsize=72)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(2, minsize=72)
        form.grid_columnconfigure(3, weight=1)

        self._label(form, "最小值").grid(row=0, column=0, sticky="w", pady=6, padx=(0, 10))
        self._entry(form, self.min_var).grid(row=0, column=1, sticky="ew", pady=6, padx=(0, 12))
        self._label(form, "最大值").grid(row=0, column=2, sticky="w", pady=6, padx=(0, 10))
        self._entry(form, self.max_var).grid(row=0, column=3, sticky="ew", pady=6)

        self._label(form, "数量").grid(row=1, column=0, sticky="w", pady=6, padx=(0, 10))
        self._entry(form, self.count_var).grid(row=1, column=1, sticky="ew", pady=6, padx=(0, 12))

        options = tk.Frame(body, bg=t.surface_bg)
        options.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(0, 6))
        self._check(options, "不重复", self.unique_var).pack(side=tk.LEFT, padx=(0, 18))
        self._check(options, "从小到大排序", self.sort_var).pack(side=tk.LEFT)

        result_wrap = tk.Frame(body, bg=t.border)
        result_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(4, 6))
        self.result_text = tk.Text(
            result_wrap,
            height=6,
            bd=0,
            relief=tk.FLAT,
            wrap=tk.WORD,
            bg="#f8fafc",
            fg="#111827",
            insertbackground="#111827",
            font=self._font(11),
        )
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        # 只读但可选中、可按 Ctrl+C 复制。
        self.result_text.bind("<Key>", self._readonly_key)
        scroll = ttk.Scrollbar(result_wrap, orient=tk.VERTICAL, command=self.result_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_text.configure(yscrollcommand=scroll.set)

        actions = tk.Frame(body, bg=t.surface_bg)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(4, 16))
        self.generate_button = tk.Button(
            actions,
            text="生成",
            command=self.generate,
            bd=0,
            padx=24,
            pady=8,
            bg=t.accent,
            fg="#ffffff",
            activebackground=self.theme.accent_hover,
            activeforeground="#ffffff",
            cursor="hand2",
            font=self._font(10, "bold"),
            width=6,
        )
        self.generate_button.pack(side=tk.RIGHT)
        self.copy_button = tk.Button(
            actions,
            text="复制",
            command=self.copy_results,
            bd=0,
            padx=24,
            pady=8,
            bg="#eef2f9",
            fg="#1f2937",
            activebackground="#e2e8f4",
            activeforeground="#111827",
            cursor="hand2",
            font=self._font(10, "bold"),
            width=6,
        )
        self.copy_button.bind("<Enter>", lambda e: self.copy_button.configure(bg="#e2e8f4"))
        self.copy_button.bind("<Leave>", lambda e: self.copy_button.configure(bg="#eef2f9"))
        self.copy_button.pack(side=tk.RIGHT, padx=(0, 8))

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    # -- widgets -------------------------------------------------------
    def _chrome_button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=self._font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=self.theme.title_button_bg))
        return button

    def _body_button(self, parent, text: str, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=13, pady=6, width=6,
            bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4", activeforeground="#111827",
            font=self._font(9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg="#e2e8f4"))
        button.bind("<Leave>", lambda event: button.configure(bg="#eef2f9"))
        return button

    def _label(self, parent, text: str) -> tk.Label:
        return tk.Label(parent, text=text, bg=self.theme.surface_bg, fg="#334155", anchor=tk.W, font=self._font(10))

    def _check(self, parent, text: str, variable: tk.BooleanVar) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent, text=text, variable=variable, bg=self.theme.surface_bg, fg="#334155",
            activebackground=self.theme.surface_bg, activeforeground="#111827", selectcolor="#f8fafc",
            anchor=tk.W, cursor="hand2", font=self._font(9),
        )

    def _entry(self, parent, variable: tk.StringVar) -> tk.Entry:
        return tk.Entry(
            parent, textvariable=variable, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#111827",
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
            self.window,
            max(self.window.winfo_width(), self.MIN_W),
            max(self.window.winfo_height(), self.MIN_H),
            wx + event.x_root - sx,
            wy + event.y_root - sy,
        )

    def _parse_int(value: str) -> int | None:
        match = re.search(r"-?\d+", value or "")
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    def generate(self) -> None:
        low = self._parse_int(self.min_var.get())
        high = self._parse_int(self.max_var.get())
        count = self._parse_int(self.count_var.get())
        if low is None or high is None:
            messagebox.showinfo("范围无效", "请填写有效的最小值与最大值。", parent=self.window)
            return
        if low > high:
            low, high = high, low
        if count is None or count <= 0:
            messagebox.showinfo("数量无效", "数量必须是大于 0 的整数。", parent=self.window)
            return
        span = high - low + 1
        if self.unique_var.get() and count > span:
            messagebox.showinfo(
                "数量过大",
                f"不重复模式下，{low}~{high} 之间最多只能取 {span} 个数。",
                parent=self.window,
            )
            return
        if self.unique_var.get():
            self.results = random.sample(range(low, high + 1), count)
        else:
            self.results = [random.randint(low, high) for _ in range(count)]
        if self.sort_var.get():
            self.results.sort()
        self._render_results()

    def _render_results(self) -> None:
        text = ", ".join(str(n) for n in self.results)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)

    def copy_results(self) -> None:
        if not self.results:
            return
        text = ", ".join(str(n) for n in self.results)
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
        except Exception:
            pass

    @staticmethod
    def _readonly_key(event):
        # 允许复制 / 全选 / 光标移动，拦截一切编辑按键。
        if (event.state & 0x4) and event.keysym.lower() in ("c", "a"):
            return None
        if event.keysym in (
            "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
        ):
            return None
        return "break"

    def show(self) -> None:
        if self.closed:
            return
        try:
            self.window.deiconify()
            self.window.lift(self.app.root)
            self.window.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "random_window", None) is self:
            self.app.random_window = None
        try:
            self.window.destroy()
        except Exception:
            pass
