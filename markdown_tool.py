from __future__ import annotations

import html
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from clicker_tool import ClickerTheme


def markdown_to_html(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if not line.strip():
            out.append("")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline_md(m.group(2))}</h{level}>")
        elif re.match(r"^\s*[-*+]\s+", line):
            out.append(f"<li>{_inline_md(re.sub(r'^\\s*[-*+]\\s+', '', line))}</li>")
        elif re.match(r"^\s*\d+\.\s+", line):
            out.append(f"<li>{_inline_md(re.sub(r'^\\s*\\d+\\.\\s+', '', line))}</li>")
        elif line.startswith(">"):
            out.append(f"<blockquote>{_inline_md(line.lstrip('> ').strip())}</blockquote>")
        else:
            out.append(f"<p>{_inline_md(line)}</p>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def _inline_md(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def html_to_markdown(source: str) -> str:
    text = source.replace("\r\n", "\n")
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</\s*p\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"<\s*p[^>]*>", "", text, flags=re.I)
    for level in range(6, 0, -1):
        pat = re.compile(rf"<\s*h{level}[^>]*>(.*?)</\s*h{level}\s*>", re.I | re.S)
        text = pat.sub(lambda m: "#" * level + " " + _strip_tags(m.group(1)) + "\n\n", text)
    text = re.sub(r"<\s*(strong|b)[^>]*>(.*?)</\s*\1\s*>", r"**\2**", text, flags=re.I | re.S)
    text = re.sub(r"<\s*(em|i)[^>]*>(.*?)</\s*\1\s*>", r"*\2*", text, flags=re.I | re.S)
    text = re.sub(r"<\s*code[^>]*>(.*?)</\s*code\s*>", r"`\1`", text, flags=re.I | re.S)
    text = re.sub(r"<\s*li[^>]*>(.*?)</\s*li\s*>", lambda m: "- " + _strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<\s*blockquote[^>]*>(.*?)</\s*blockquote\s*>", lambda m: "> " + _strip_tags(m.group(1)) + "\n", text, flags=re.I | re.S)
    text = re.sub(r"<\s*a[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</\s*a\s*>", r"[\2](\1)", text, flags=re.I | re.S)
    text = _strip_tags(text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


class MarkdownWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 1176
    MIN_H = 816

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.file_path: str | None = None

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
        self.window.bind("<Control-r>", lambda _e: self.render_markdown())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        self.editor.focus_set()
        self.render_markdown()

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
        actions = tk.Frame(body, bg=t.surface_bg)
        actions.pack(fill=tk.X, padx=14, pady=(12, 8))
        for text, cmd in (
            ("打开", self.open_file), ("保存", self.save_file), ("渲染", self.render_markdown),
            ("HTML", self.copy_html), ("反渲染", self.reverse_html), ("复制结果", self.copy_result),
        ):
            self._button(actions, text, cmd).pack(side=tk.LEFT, padx=(0, 8))

        panes = tk.PanedWindow(body, orient=tk.HORIZONTAL, sashwidth=6, bg=t.border, bd=0)
        panes.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))

        left = tk.Frame(panes, bg=t.surface_bg)
        right = tk.Frame(panes, bg=t.surface_bg)
        panes.add(left, minsize=360)
        panes.add(right, minsize=360)

        tk.Label(left, text="Markdown / HTML 输入", bg=t.surface_bg, fg="#334155",
                 anchor=tk.W, font=self._font(10, "bold")).pack(fill=tk.X, pady=(0, 6))
        self.editor = self._text(left)
        self.editor.pack(fill=tk.BOTH, expand=True)
        self.editor.insert("1.0", "# Markdown 预览器\n\n输入 **Markdown** 后点击渲染。\n\n- 支持标题\n- 支持列表\n- 支持 `code`\n\n也可以粘贴 HTML 后点击反渲染。")

        tk.Label(right, text="预览 / 反渲染结果", bg=t.surface_bg, fg="#334155",
                 anchor=tk.W, font=self._font(10, "bold")).pack(fill=tk.X, pady=(0, 6))
        self.preview = self._text(right)
        self.preview.pack(fill=tk.BOTH, expand=True)
        self._configure_preview_tags()

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=t.title_bg, fg="#8aa0c0", cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, padx=(0, 6))
        grip.bind("<ButtonPress-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)

    def _text(self, parent) -> tk.Text:
        wrap = tk.Text(parent, bd=0, relief=tk.FLAT, wrap=tk.WORD, undo=True,
                       bg="#f8fafc", fg="#111827", insertbackground="#111827",
                       selectbackground=self.theme.accent, selectforeground="#ffffff",
                       font=self._font(10), padx=10, pady=10)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=wrap.yview)
        wrap.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return wrap

    def _configure_preview_tags(self) -> None:
        self.preview.tag_configure("h1", font=self._font(18, "bold"), spacing3=8)
        self.preview.tag_configure("h2", font=self._font(15, "bold"), spacing3=6)
        self.preview.tag_configure("h3", font=self._font(12, "bold"), spacing3=4)
        self.preview.tag_configure("bold", font=self._font(10, "bold"))
        self.preview.tag_configure("code", background="#e2e8f0", foreground="#0f172a", font=("Consolas", 10))
        self.preview.tag_configure("quote", lmargin1=18, lmargin2=18, foreground="#475569")
        self.preview.tag_configure("list", lmargin1=18, lmargin2=34)

    def render_markdown(self) -> None:
        source = self.editor.get("1.0", "end-1c")
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        in_code = False
        for line in source.replace("\r\n", "\n").split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                self.preview.insert(tk.END, line + "\n", ("code",))
                continue
            m = re.match(r"^(#{1,3})\s+(.*)$", line)
            if m:
                self.preview.insert(tk.END, m.group(2) + "\n", (f"h{len(m.group(1))}",))
            elif re.match(r"^\s*[-*+]\s+", line):
                self.preview.insert(tk.END, "• " + re.sub(r"^\s*[-*+]\s+", "", line) + "\n", ("list",))
            elif line.startswith(">"):
                self.preview.insert(tk.END, line.lstrip("> ").strip() + "\n", ("quote",))
            else:
                self._insert_inline_preview(line)
                self.preview.insert(tk.END, "\n")
        self.preview.configure(state=tk.DISABLED)

    def _insert_inline_preview(self, line: str) -> None:
        pos = 0
        for m in re.finditer(r"(`[^`]+`|\*\*[^*]+\*\*)", line):
            self.preview.insert(tk.END, line[pos:m.start()])
            token = m.group(0)
            if token.startswith("`"):
                self.preview.insert(tk.END, token.strip("`"), ("code",))
            else:
                self.preview.insert(tk.END, token[2:-2], ("bold",))
            pos = m.end()
        self.preview.insert(tk.END, line[pos:])

    def reverse_html(self) -> None:
        result = html_to_markdown(self.editor.get("1.0", "end-1c"))
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", result)
        self.preview.configure(state=tk.DISABLED)

    def copy_html(self) -> None:
        self._copy(markdown_to_html(self.editor.get("1.0", "end-1c")))

    def copy_result(self) -> None:
        self._copy(self.preview.get("1.0", "end-1c"))

    def _copy(self, text: str) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window,
            filetypes=[("Markdown / HTML", "*.md *.markdown *.html *.htm *.txt"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = fh.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk", errors="replace") as fh:
                data = fh.read()
        self.file_path = path
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", data)
        self.render_markdown()

    def save_file(self) -> None:
        path = self.file_path or filedialog.asksaveasfilename(
            parent=self.window,
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("HTML", "*.html"), ("Text", "*.txt")],
        )
        if not path:
            return
        self.file_path = path
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.editor.get("1.0", "end-1c"))

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        btn = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                        bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                        activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=self.theme.title_button_bg))
        return btn

    def _button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command, bd=0, padx=14, pady=6,
                        bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                        activeforeground="#111827", font=self._font(9, "bold"), cursor="hand2")
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
        self.window.destroy()
        if getattr(self.app, "markdown_window", None) is self:
            self.app.markdown_window = None
