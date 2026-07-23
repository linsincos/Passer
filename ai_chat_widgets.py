from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

def _rounded_rectangle(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                       radius: int, **kwargs) -> int:
    """Draw a smooth rounded rectangle and return its canvas item id."""
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2,
        x1 + radius, y2, x1, y2, x1, y2 - radius,
        x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _draw_pencil_icon(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                      color: str = "#64748b", width: int = 1, tags=()) -> None:
    """在给定矩形内画一支朝左下的线条铅笔（笔帽在右上、笔尖在左下）。"""
    w = x1 - x0
    h = y1 - y0
    tip = (x0, y1)                       # 笔尖（左下角）
    a = (x0, y1 - 0.34 * h)              # 笔尖根部（上）
    b = (x0 + 0.34 * w, y1)              # 笔尖根部（下）
    c1 = (x1 - 0.30 * w, y0)             # 笔帽角（上）
    c2 = (x1, y0 + 0.30 * h)             # 笔帽角（下）
    canvas.create_line(a[0], a[1], c1[0], c1[1], fill=color, width=width, tags=tags)   # 笔身上沿
    canvas.create_line(b[0], b[1], c2[0], c2[1], fill=color, width=width, tags=tags)   # 笔身下沿
    canvas.create_line(c1[0], c1[1], c2[0], c2[1], fill=color, width=width, tags=tags)  # 笔帽
    canvas.create_line(a[0], a[1], b[0], b[1], fill=color, width=width, tags=tags)      # 笔尖根部
    canvas.create_line(a[0], a[1], tip[0], tip[1], fill=color, width=width, tags=tags)  # 笔尖
    canvas.create_line(b[0], b[1], tip[0], tip[1], fill=color, width=width, tags=tags)


def _aira_markdown_ranges(text: str) -> dict[str, list]:
    """Return lightweight Markdown ranges without changing the stored reply text."""
    value = str(text or "")
    result: dict[str, list] = {
        "code": [], "inline_code": [], "tables": [], "links": [], "hidden": [],
    }
    occupied: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < right and end > left for left, right in occupied)

    fence_re = re.compile(r"(?ms)^```[^\n]*\n(.*?)\n```[ \t]*$")
    for match in fence_re.finditer(value):
        content_start, content_end = match.span(1)
        result["code"].append((content_start, content_end, match.group(1)))
        result["hidden"].extend(((match.start(), content_start), (content_end, match.end())))
        occupied.append(match.span())

    markdown_link_re = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
    for match in markdown_link_re.finditer(value):
        if overlaps(*match.span()):
            continue
        label_start, label_end = match.span(1)
        result["links"].append((label_start, label_end, match.group(2)))
        result["hidden"].extend(((match.start(), label_start), (label_end, match.end())))
        occupied.append(match.span())

    bare_url_re = re.compile(r"https?://[^\s<>\]\[\)]+", re.IGNORECASE)
    for match in bare_url_re.finditer(value):
        if not overlaps(*match.span()):
            result["links"].append((match.start(), match.end(), match.group(0)))

    inline_re = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
    for match in inline_re.finditer(value):
        if overlaps(*match.span()):
            continue
        start, end = match.span(1)
        result["inline_code"].append((start, end))
        result["hidden"].extend(((match.start(), start), (end, match.end())))

    lines: list[tuple[int, int, str]] = []
    offset = 0
    for line in value.splitlines(keepends=True):
        end = offset + len(line)
        lines.append((offset, end, line.rstrip("\r\n")))
        offset = end
    separator_re = re.compile(
        r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
    )
    for index, (_start, _end, line) in enumerate(lines):
        if index == 0 or not separator_re.match(line) or "|" not in lines[index - 1][2]:
            continue
        start_index = index - 1
        end_index = index + 1
        while end_index < len(lines) and "|" in lines[end_index][2] and lines[end_index][2].strip():
            end_index += 1
        start = lines[start_index][0]
        end = lines[end_index - 1][1]
        if not overlaps(start, end):
            result["tables"].append((start, end))
    return result


def _aira_markdown_visible_text(text: str, ranges: dict[str, list] | None = None) -> str:
    """Strip only elided Markdown markers for bubble size measurement."""
    value = str(text or "")
    hidden = sorted((ranges or _aira_markdown_ranges(value))["hidden"])
    output: list[str] = []
    cursor = 0
    for start, end in hidden:
        if start < cursor:
            continue
        output.append(value[cursor:start])
        cursor = end
    output.append(value[cursor:])
    return "".join(output)


class _RoundedBubble(tk.Canvas):
    """A chat bubble with a rounded canvas background and selectable text.

    文字用只读 Text 承载：可用鼠标选中、Ctrl+C 或右键菜单复制，但不可编辑。
    """

    PAD_X = 14
    PAD_Y = 10
    RADIUS = 14
    ACTION_ICON = 16     # 动作图标方框边长
    ACTION_GAP = 10      # 同一行内相邻图标的水平间距
    ACTION_TOP = 6       # 气泡底边与动作行的垂直间隙
    ACTION_ROW = 22      # 动作行占用的总高度

    def __init__(self, parent, *, text: str, wraplength: int, fill: str,
                 foreground: str, font, role: str = "assistant",
                 app_font=None, line_spacing: int = 2, open_link=None) -> None:
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._fill = fill
        self._fg = foreground
        self._font = font
        self._wrap = wraplength
        self._role = role
        self._app_font = app_font
        self._line_spacing = max(0, min(12, int(line_spacing)))
        self._open_link = open_link
        self._markdown_ranges: dict[str, list] = {}
        self._code_ranges: list[tuple[int, int, str]] = []
        self._popup_text_offset: int | None = None
        self._markdown_after_id = None
        self._page_bg = parent.cget("bg")
        # 动作按钮：list[(name, callback)]；meta：{time, tokens, elapsed} 或 None。
        self._actions: list[tuple] = []
        self._action_map: dict = {}
        self._meta: dict | None = None
        self._meta_font = None
        # 可折叠气泡（如「本地操作结果」）：_full_text 存完整正文，折叠时只在 Text 里
        # 显示一行预览；展开/收起由动作行里的 fold 图标切换。
        self._collapsible = False
        self._collapsed = False
        self._full_text: str | None = None
        self._text = tk.Text(
            self, wrap=tk.WORD, bd=0, highlightthickness=0, padx=0, pady=0,
            bg=fill, fg=foreground, font=font, cursor="xterm", insertwidth=0,
            selectbackground="#2563eb", selectforeground="#ffffff", takefocus=True,
            spacing3=self._line_spacing,
        )
        self._text.insert("1.0", text)
        self._make_readonly()
        self._build_menu()
        self._apply_markdown_formatting()
        # 嵌入文字窗口只创建一次并持续复用：流式刷新时绝不 delete/recreate 它，
        # 否则真实 Text 控件会被反复 unmap/map 造成可见闪烁。
        self._window = None
        self._shape = None
        self._last_shape_size = None
        # 磨砂底图：把气泡所在位置的磨砂背景裁出来贴在画布最底层，
        # 使圆角气泡以外（按钮/时间/tokens 那一行）透出后方模糊的图标背景。
        self._bg_item = None
        self._bg_photo = None
        self._clip_box = None
        # 尺寸缓存：key=(文字内容, 换行宽, 动作数, meta文本, 折叠状态)，避免每帧重建临时 Label
        self._size_cache_key: tuple | None = None
        self._size_cache_val: tuple[int, int] | None = None   # (width_px, height_px)
        # 持久复用的测量 Label：流式刷新时每帧都要量一次折行尺寸，复用同一个隐藏
        # Label（只改 text，不反复 create/destroy 控件）可显著降低每帧开销。
        self._meas: tk.Label | None = None
        self._layout()

    # --- read-only + 复制菜单 -------------------------------------------
    def _make_readonly(self) -> None:
        t = self._text

        def block(event):
            # 放行复制(Ctrl+C)、全选(Ctrl+A)、方向键/选择，其余按键一律拦截。
            if event.state & 0x4 and event.keysym.lower() in ("c", "a"):
                return None
            if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                                 "Prior", "Next", "Shift_L", "Shift_R", "Control_L", "Control_R"):
                return None
            return "break"

        t.bind("<Key>", block)
        t.bind("<<Paste>>", lambda _e: "break")
        t.bind("<<Cut>>", lambda _e: "break")

    def _build_menu(self) -> None:
        self._menu = tk.Menu(self, tearoff=False)
        self._menu.add_command(label="复制", command=self._copy_selection)
        self._menu.add_command(label="复制全部", command=self._copy_all)
        self._menu.add_command(label="复制代码块", command=self._copy_code_block, state=tk.DISABLED)
        self._menu.add_separator()
        self._menu.add_command(label="全选", command=self._select_all)
        self._text.bind("<Button-3>", self._popup_menu)
        self.bind("<Button-3>", self._popup_menu)

    def _popup_menu(self, event):
        self._text.focus_set()
        self._popup_text_offset = None
        if event.widget is self._text:
            try:
                index = self._text.index(f"@{event.x},{event.y}")
                counted = self._text.count("1.0", index, "chars")
                self._popup_text_offset = int(counted[0]) if counted else 0
            except (tk.TclError, TypeError, ValueError):
                self._popup_text_offset = None
        has_code = any(
            start <= self._popup_text_offset < end
            for start, end, _code in self._code_ranges
        ) if self._popup_text_offset is not None else False
        self._menu.entryconfigure(2, state=tk.NORMAL if has_code else tk.DISABLED)
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()
        return "break"

    def _selected_text(self) -> str:
        try:
            return self._text.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def _to_clipboard(self, value: str) -> None:
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)

    def _copy_selection(self) -> None:
        self._to_clipboard(self._selected_text() or self._text.get("1.0", "end-1c"))

    def _copy_all(self) -> None:
        self._to_clipboard(self._text.get("1.0", "end-1c"))

    def _copy_code_block(self) -> None:
        if self._popup_text_offset is None:
            return
        for start, end, code in self._code_ranges:
            if start <= self._popup_text_offset < end:
                self._to_clipboard(code)
                return

    def _apply_markdown_formatting(self) -> None:
        text = self._text.get("1.0", "end-1c")
        ranges = _aira_markdown_ranges(text)
        self._markdown_ranges = ranges
        self._code_ranges = list(ranges["code"])
        for tag in tuple(self._text.tag_names()):
            if str(tag).startswith("md_"):
                self._text.tag_delete(tag)
        try:
            body_font = tkfont.Font(font=self._font)
            code_size = max(6, abs(int(body_font.actual("size"))))
        except (tk.TclError, TypeError, ValueError):
            code_size = 10
        code_font = ("Cascadia Mono", code_size)
        code_bg = "#1d4ed8" if self._role == "user" else "#f1f5f9"
        code_fg = "#ffffff" if self._role == "user" else "#0f172a"
        link_fg = "#dbeafe" if self._role == "user" else "#2563eb"
        self._text.tag_configure("md_code", font=code_font, background=code_bg, foreground=code_fg,
                                 lmargin1=8, lmargin2=8, rmargin=8, spacing1=4, spacing3=4)
        self._text.tag_configure("md_inline_code", font=code_font, background=code_bg, foreground=code_fg)
        self._text.tag_configure("md_table", font=code_font, background=code_bg, foreground=code_fg,
                                 lmargin1=6, lmargin2=6, rmargin=6)
        self._text.tag_configure("md_hidden", elide=True)
        for start, end, _code in ranges["code"]:
            self._text.tag_add("md_code", f"1.0+{start}c", f"1.0+{end}c")
        for start, end in ranges["inline_code"]:
            self._text.tag_add("md_inline_code", f"1.0+{start}c", f"1.0+{end}c")
        for start, end in ranges["tables"]:
            self._text.tag_add("md_table", f"1.0+{start}c", f"1.0+{end}c")
        for start, end in ranges["hidden"]:
            self._text.tag_add("md_hidden", f"1.0+{start}c", f"1.0+{end}c")
        for index, (start, end, url) in enumerate(ranges["links"]):
            tag = f"md_link_{index}"
            self._text.tag_configure(tag, foreground=link_fg, underline=True)
            self._text.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
            self._text.tag_bind(tag, "<Enter>", lambda _event: self._text.configure(cursor="hand2"))
            self._text.tag_bind(tag, "<Leave>", lambda _event: self._text.configure(cursor="xterm"))
            self._text.tag_bind(tag, "<Button-1>", lambda _event, value=url: self._activate_link(value))
        if hasattr(self, "_size_cache_key"):
            self._size_cache_key = None
            self._size_cache_val = None

    def _schedule_markdown_formatting(self) -> None:
        if self._markdown_after_id is not None:
            return

        def apply() -> None:
            self._markdown_after_id = None
            try:
                self._apply_markdown_formatting()
                self._layout()
            except tk.TclError:
                pass

        try:
            self._markdown_after_id = self.after(40, apply)
        except tk.TclError:
            self._markdown_after_id = None

    def _activate_link(self, url: str):
        try:
            if callable(self._open_link):
                self._open_link(url)
        except (OSError, RuntimeError, ValueError):
            return "break"
        return "break"

    def _select_all(self) -> None:
        self._text.tag_add("sel", "1.0", "end-1c")
        self._text.focus_set()

    # --- 动作按钮（复制/分支/编辑）与 meta 提示 -------------------------
    def set_actions(self, actions, app_font=None) -> None:
        """设置气泡下方的动作按钮：actions = list[(name, callback)]。"""
        self._actions = list(actions or [])
        if app_font is not None:
            self._app_font = app_font
        self._layout()

    def set_meta(self, meta: dict | None) -> None:
        """设置回复信息（时间/tokens/用时），直接平铺在分支按钮右侧。"""
        self._meta = dict(meta) if meta else None
        self._layout()

    def set_collapsible(self, collapsed: bool = True) -> None:
        """把当前气泡标记为可折叠：记录完整正文，并按 collapsed 切换显示。"""
        self._collapsible = True
        self._collapsed = bool(collapsed)
        if self._full_text is None:
            self._full_text = self._text.get("1.0", "end-1c")
        self._apply_collapse()

    def toggle_collapse(self) -> None:
        if not self._collapsible:
            return
        self._collapsed = not self._collapsed
        self._apply_collapse()

    def is_collapsed(self) -> bool:
        return self._collapsible and self._collapsed

    def _collapsed_preview(self) -> str:
        full = self._full_text or ""
        lines = full.splitlines()
        head = (lines[0] if lines else full[:48]).strip()
        # 折叠态只显示首行，不附加任何“还有 N 行/点击展开”提示——展开靠右侧 fold 图标。
        return head or " "

    def _apply_collapse(self) -> None:
        if not self._collapsible:
            return
        shown = self._collapsed_preview() if self._collapsed else (self._full_text or "")
        self._set_text(shown)
        self._layout()

    def set_bg_crop(self, photo, offset=(0, 0)) -> None:
        """把裁好的磨砂背景贴到画布最底层（photo=None 时清除，回退纯色底）。"""
        if photo is None:
            if self._bg_item is not None:
                try:
                    self.delete(self._bg_item)
                except tk.TclError:
                    pass
                self._bg_item = None
            self._bg_photo = None
            return
        self._bg_photo = photo   # 必须保留引用，否则会被 GC 回收成空白
        ox, oy = int(offset[0]), int(offset[1])
        try:
            if self._bg_item is None:
                self._bg_item = self.create_image(ox, oy, anchor=tk.NW, image=photo, tags=("bgimg",))
            else:
                self.coords(self._bg_item, ox, oy)
                self.itemconfigure(self._bg_item, image=photo)
            self.tag_lower("bgimg")
        except tk.TclError:
            self._bg_item = None

    def _meta_text(self) -> str:
        if not self._meta:
            return ""
        # 思考中：只显示实时思考时间。
        thinking = self._meta.get("thinking")
        if isinstance(thinking, (int, float)):
            return f"思考时间 {int(thinking)}s"
        # 思考完毕：显示回复的 24 小时制时间（不带“时间”二字）+ tokens。
        parts: list[str] = []
        when = str(self._meta.get("time") or "").strip()
        if when:
            parts.append(when)
        tokens = self._meta.get("tokens")
        if isinstance(tokens, (int, float)) and tokens > 0:
            details = []
            input_tokens = self._meta.get("input_tokens")
            output_tokens = self._meta.get("output_tokens")
            cache_read = self._meta.get("cache_read_tokens")
            if isinstance(input_tokens, (int, float)) and input_tokens > 0:
                details.append(f"入 {int(input_tokens)}")
            if isinstance(output_tokens, (int, float)) and output_tokens > 0:
                details.append(f"出 {int(output_tokens)}")
            if isinstance(cache_read, (int, float)) and cache_read > 0:
                details.append(f"缓 {int(cache_read)}")
            suffix = f" ({' / '.join(details)})" if details else ""
            parts.append(f"Tokens {int(tokens)}{suffix}")
        return "   ·   ".join(parts)

    def _draw_action_icon(self, name: str, cx: int, cy: int) -> None:
        """在 (cx, cy) 为中心绘制一个动作图标并绑定点击（不做悬停变色）。"""
        s = self.ACTION_ICON
        half = s // 2
        x0, y0, x1, y1 = cx - half, cy - half, cx + half, cy + half
        color = "#94a3b8"
        # 稳定 tag（不含坐标）：每次重排只替换绑定而非叠加；统一带 actionrow 便于整体清除。
        tag = f"act-{name}"
        hit_tag = f"{tag}-hit"
        visual_tags = (tag, "actionrow")
        hit_tags = (tag, hit_tag, "actionrow")
        # 命中区比图标略大；单独 tag 并放在底层，避免重排后点击只落在线条像素上。
        hit_item = self.create_rectangle(x0 - 6, y0 - 6, x1 + 6, y1 + 6,
                                         fill=self._page_bg, outline="", tags=hit_tags)
        if name == "copy":
            self.create_rectangle(x0 + 3, y0 + 3, x1, y1, outline=color, width=1, tags=visual_tags)
            self.create_rectangle(x0, y0, x1 - 3, y1 - 3, outline=color, width=1,
                                  fill=self._page_bg, tags=visual_tags)
        elif name == "branch":
            # 方框 + 右上外指箭头（从此处展开为新分支）。
            self.create_rectangle(x0, y0 + 4, x1 - 4, y1, outline=color, width=1, tags=visual_tags)
            self.create_line(cx, cy, x1, y0, fill=color, width=1, tags=visual_tags)
            self.create_line(x1 - 5, y0, x1, y0, fill=color, width=1, tags=visual_tags)
            self.create_line(x1, y0, x1, y0 + 5, fill=color, width=1, tags=visual_tags)
        elif name == "edit":
            # 朝左下的线条铅笔（与历史重命名按钮统一样式）。
            _draw_pencil_icon(self, x0, y0, x1, y1, color="#475569", tags=visual_tags)
        elif name == "fold":
            # 折叠/展开：折叠态画 ⌄（提示可展开），展开态画 ⌃（提示可收起）。
            if self._collapsed:
                self.create_line(x0 + 1, cy - 2, cx, cy + 3, fill=color, width=2, tags=visual_tags)
                self.create_line(cx, cy + 3, x1 - 1, cy - 2, fill=color, width=2, tags=visual_tags)
            else:
                self.create_line(x0 + 1, cy + 2, cx, cy - 3, fill=color, width=2, tags=visual_tags)
                self.create_line(cx, cy - 3, x1 - 1, cy + 2, fill=color, width=2, tags=visual_tags)
        elif name == "undo":
            # 逆时针回退箭头：用于撤销本轮 Aira 生成的文件类产物。
            self.create_arc(x0 + 1, y0 + 2, x1 - 1, y1 - 1, start=35, extent=285,
                            style=tk.ARC, outline=color, width=1.4, tags=visual_tags)
            self.create_line(x0 + 3, cy - 4, x0 + 3, y0 + 2, x0 + 9, y0 + 2,
                             fill=color, width=1.4, tags=visual_tags,
                             capstyle=tk.ROUND, joinstyle=tk.ROUND)
        callback = self._action_map.get(name)
        if callback is not None:
            def invoke(_event=None, cb=callback):
                cb()
                return "break"
            self.tag_bind(tag, "<Button-1>", invoke)
            self.tag_bind(hit_tag, "<Button-1>", invoke)

    # --- 尺寸与外观 -----------------------------------------------------
    def _layout(self) -> None:
        text_content = self._text.get("1.0", "end-1c")
        visible_text = _aira_markdown_visible_text(text_content, self._markdown_ranges)
        has_actions = bool(self._actions)
        names = [name for name, _cb in self._actions]
        # 气泡下方一行：Aira 气泡平铺“时间/思考时间/tokens”；用户气泡只放按钮。
        meta_str = self._meta_text() if self._role != "user" else ""
        # 缓存命中则跳过临时 Label 创建（流式刷新期间文字频繁变化时开销很大）。
        cache_key = (
            text_content, self._wrap, len(names), meta_str, self._collapsed,
            self._line_spacing, str(self._font),
        )
        if cache_key == self._size_cache_key and self._size_cache_val is not None:
            width_px, height_px = self._size_cache_val
        else:
            # 用隐藏 Label 量出按 wraplength 折行后的精确像素尺寸（Label 在未映射时
            # 也能正确报告 reqsize），再把只读 Text 摆成同样大小，折行结果一致。
            # 复用同一个 Label（不每帧重建）以降低流式刷新开销。
            meas = self._meas
            if meas is None:
                meas = tk.Label(self, justify=tk.LEFT, anchor=tk.W, bd=0, padx=0, pady=0)
                self._meas = meas
            meas.configure(text=visible_text or " ", wraplength=self._wrap, font=self._font)
            meas.update_idletasks()
            width_px = max(8, meas.winfo_reqwidth() + 2)
            paragraphs = max(1, visible_text.count("\n") + 1)
            height_px = max(8, meas.winfo_reqheight() + 2 + paragraphs * self._line_spacing)
            self._size_cache_key = cache_key
            self._size_cache_val = (width_px, height_px)
        bubble_h = height_px + 2 * self.PAD_Y
        if meta_str:
            self._meta_font = self._app_font(9) if self._app_font else self._font
            meta_w = tkfont.Font(font=self._meta_font).measure(meta_str)
        else:
            meta_w = 0
        has_row = has_actions or bool(meta_str)
        # 气泡圆角矩形只按文字宽度绘制；画布可更宽以容纳右侧平铺的 meta（不撑大气泡）。
        bubble_w = width_px + 2 * self.PAD_X
        width = bubble_w
        if has_row and self._role != "user":
            row_w = self.PAD_X
            if has_actions:
                row_w += len(names) * self.ACTION_ICON + max(0, len(names) - 1) * self.ACTION_GAP
                if meta_w:
                    row_w += self.ACTION_GAP + meta_w
            else:
                row_w += meta_w
            row_w += self.PAD_X
            width = max(width, row_w)
        total_h = bubble_h + (self.ACTION_TOP + self.ACTION_ROW if has_row else 0)
        super().configure(width=width, height=total_h)
        # 文字窗口持久复用（首次创建，之后仅调坐标/尺寸），避免重排时闪烁。
        if self._window is None:
            self._window = self.create_window(
                self.PAD_X, self.PAD_Y, window=self._text, anchor=tk.NW,
                width=width_px, height=height_px,
            )
        else:
            self.coords(self._window, self.PAD_X, self.PAD_Y)
            self.itemconfigure(self._window, width=width_px, height=height_px)
        # 圆角底仅在尺寸/底色变化时重建，减少每帧重绘。
        shape_size = (bubble_w, bubble_h, self._fill)
        if self._shape is None or shape_size != self._last_shape_size:
            if self._shape is not None:
                self.delete(self._shape)
            self._shape = _rounded_rectangle(
                self, 0, 0, bubble_w, bubble_h, self.RADIUS,
                fill=self._fill, outline=self._fill, width=0,
            )
            self.tag_lower(self._shape)
            # 磨砂底图必须压在圆角底之下，气泡正文区仍是不透明圆角，其余露出磨砂。
            if self._bg_item is not None:
                self.tag_lower("bgimg")
            self._last_shape_size = shape_size
        # 动作行/meta：每次重排清掉旧的再画（文字窗口与底不受影响）。
        self.delete("actionrow")
        if has_row:
            self._action_map = {name: cb for name, cb in self._actions}
            cy = bubble_h + self.ACTION_TOP + self.ACTION_ROW // 2
            step = self.ACTION_ICON + self.ACTION_GAP
            if has_actions and self._role == "user":
                cx = width - self.PAD_X - self.ACTION_ICON // 2
                for name in reversed(names):
                    self._draw_action_icon(name, cx, cy)
                    cx -= step
            else:
                if has_actions:
                    start = self.PAD_X + self.ACTION_ICON // 2
                    for index, name in enumerate(names):
                        self._draw_action_icon(name, start + index * step, cy)
                    meta_x = start + (len(names) - 1) * step + self.ACTION_ICON // 2 + self.ACTION_GAP
                else:
                    meta_x = self.PAD_X
                if meta_str:
                    self.create_text(meta_x, cy, text=meta_str, anchor=tk.W,
                                     fill="#94a3b8", font=self._meta_font, tags=("actionrow",))

    def _set_text(self, value: str) -> None:
        self._text.delete("1.0", "end")
        self._text.insert("1.0", value)
        self._apply_markdown_formatting()

    def stream_update(self, full_text: str) -> None:
        """流式刷新专用：正文只增不减时只把新增后缀追加到 Text，避免每帧整体重写。

        与每帧 delete+insert 全文相比，追加把 Text 改动从 O(全文) 降到 O(新增量)，
        是处理长回复时卡顿的主因。若新文本不是已显示内容的延续（首段替换「思考中…」、
        断线重连后重来等），则回退到整体设置。
        """
        current = self._text.get("1.0", "end-1c")
        if full_text == current:
            return
        if current and full_text.startswith(current):
            self._text.insert("end", full_text[len(current):])
            self._schedule_markdown_formatting()
        else:
            self._set_text(full_text)
        self._layout()

    def configure(self, cnf=None, **kwargs):
        new_text = kwargs.pop("text", None)
        fg = kwargs.pop("fg", kwargs.pop("foreground", None))
        bg = kwargs.pop("bg", kwargs.pop("background", None))
        font = kwargs.pop("font", None)
        line_spacing = kwargs.pop("line_spacing", None)
        wraplength = kwargs.pop("wraplength", None)
        result = super().configure(cnf, **kwargs)
        changed = False
        if wraplength is not None:
            try:
                new_wrap = max(8, int(wraplength))
            except (TypeError, ValueError):
                new_wrap = self._wrap
            if new_wrap != self._wrap:
                self._wrap = new_wrap
                changed = True
        if fg is not None:
            self._fg = fg
            self._text.configure(fg=fg)
            self._apply_markdown_formatting()
            changed = True
        if bg is not None:
            self._fill = bg
            self._text.configure(bg=bg)
            self._apply_markdown_formatting()
            changed = True
        if font is not None:
            self._font = font
            self._text.configure(font=font)
            self._apply_markdown_formatting()
            self._size_cache_key = None
            self._size_cache_val = None
            changed = True
        if line_spacing is not None:
            try:
                spacing = max(0, min(12, int(line_spacing)))
            except (TypeError, ValueError):
                spacing = self._line_spacing
            if spacing != self._line_spacing:
                self._line_spacing = spacing
                self._text.configure(spacing3=spacing)
                self._size_cache_key = None
                self._size_cache_val = None
                changed = True
        if new_text is not None:
            value = str(new_text)
            if self._collapsible:
                self._full_text = value
                self._set_text(self._collapsed_preview() if self._collapsed else value)
            else:
                self._set_text(value)
            changed = True
        if changed:
            self._layout()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            if self._collapsible and self._full_text is not None:
                return self._full_text
            return self._text.get("1.0", "end-1c")
        if key in ("fg", "foreground"):
            return self._fg
        if key in ("bg", "background"):
            return self._fill
        if key == "font":
            return self._font
        if key == "line_spacing":
            return self._line_spacing
        return super().cget(key)


# 延迟提醒可选时长（中文标签 → 分钟）。
SNOOZE_OPTIONS = [("5 分钟", 5), ("10 分钟", 10), ("30 分钟", 30), ("1 小时", 60), ("2 小时", 120)]


class _ReminderBubble(tk.Canvas):
    """询问框上方的计划提醒气泡：⏰ + 文字（+ 可点地点/文件超链接）+ 延迟提醒 + × 关闭。"""

    PAD_X = 14
    PAD_Y = 10

    def __init__(self, parent, *, text: str, wraplength: int, on_close, app_font,
                 links=None, on_snooze=None) -> None:
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self._fill = "#fffbeb"
        self._border = "#f59e0b"
        self._on_close = on_close
        links = list(links or [])
        c = tk.Frame(self, bg=self._fill)
        self._content = c

        top = tk.Frame(c, bg=self._fill)
        top.pack(fill=tk.X)
        tk.Label(top, text="⏰", bg=self._fill, fg="#b45309", bd=0, font=app_font(11)).pack(side=tk.LEFT)
        close = tk.Label(top, text="×", bg=self._fill, fg="#9a3412", cursor="hand2",
                         bd=0, font=app_font(13, "bold"))
        close.pack(side=tk.RIGHT)
        close.bind("<Button-1>", lambda _e: self._on_close())
        close.bind("<Enter>", lambda _e: close.configure(fg="#dc2626"))
        close.bind("<Leave>", lambda _e: close.configure(fg="#9a3412"))
        tk.Label(top, text=text, justify=tk.LEFT, anchor=tk.W, wraplength=wraplength,
                 bg=self._fill, fg="#7c2d12", bd=0, font=app_font(10)).pack(side=tk.LEFT, padx=(6, 8))

        # 第二行：多个地点/文件超链接 + 延迟提醒。
        if links or on_snooze:
            row = tk.Frame(c, bg=self._fill)
            row.pack(fill=tk.X, pady=(6, 0))
            for label, command in links:
                self._make_link(row, label, command, app_font)
            if on_snooze:
                btn = tk.Label(row, text="延迟提醒", bg="#fde68a", fg="#7c2d12", cursor="hand2",
                               bd=0, padx=10, pady=3, font=app_font(9, "bold"))
                btn.pack(side=tk.RIGHT)
                combo = ttk.Combobox(row, state="readonly", width=7, font=app_font(9),
                                     values=[label for label, _m in SNOOZE_OPTIONS])
                combo.set(SNOOZE_OPTIONS[1][0])
                combo.pack(side=tk.RIGHT, padx=(0, 8))

                def do_snooze(_e=None):
                    label = combo.get()
                    minutes = dict(SNOOZE_OPTIONS).get(label, 10)
                    try:
                        on_snooze(minutes)
                    finally:
                        self._on_close()
                btn.bind("<Button-1>", do_snooze)
        self._layout()

    def _make_link(self, parent, text, command, app_font) -> None:
        link = tk.Label(parent, text=text, bg=self._fill, fg="#2563eb", cursor="hand2",
                        bd=0, font=app_font(10, "underline"))
        link.pack(side=tk.LEFT, padx=(0, 12))
        link.bind("<Button-1>", lambda _e: command())
        link.bind("<Enter>", lambda _e: link.configure(fg="#1d4ed8"))
        link.bind("<Leave>", lambda _e: link.configure(fg="#2563eb"))

    def _layout(self) -> None:
        self._content.update_idletasks()
        cw = max(1, self._content.winfo_reqwidth())
        ch = max(1, self._content.winfo_reqheight())
        width = cw + 2 * self.PAD_X
        height = ch + 2 * self.PAD_Y
        super().configure(width=width, height=height)
        self.delete("all")
        _rounded_rectangle(self, 1, 1, width - 1, height - 1, 12, fill=self._fill, outline=self._border)
        self.create_window(self.PAD_X, self.PAD_Y, window=self._content, anchor=tk.NW)
