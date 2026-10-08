from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

class AnnotationModel:
    """Stores annotation strokes in image-space coordinates."""

    def __init__(self) -> None:
        self.strokes: list[dict] = []

    def add(self, stroke: dict) -> None:
        self.strokes.append(stroke)

    def undo(self):
        return self.strokes.pop() if self.strokes else None

    def clear(self) -> None:
        self.strokes.clear()

    def is_empty(self) -> bool:
        return not self.strokes


class AnnotationController:
    """Freehand pen + rectangle + arrow + text drawing bound to a tk.Canvas.

    Strokes are stored in image-space via ``get_transform`` which returns
    ``(scale, offset_x, offset_y)`` so that ``canvas = image * scale + offset``.
    The same model renders to the live canvas and (via ``render_to_pil``) onto a
    PIL image for copy/save/pin.
    """

    TOOLS = ("pen", "rect", "arrow", "text")

    def __init__(self, canvas, get_transform, *, model=None, on_change=None,
                 color: str = ANNOTATION_DEFAULT_COLOR, width: int = ANNOTATION_PEN_WIDTH):
        self.canvas = canvas
        self.get_transform = get_transform
        self.model = model or AnnotationModel()
        self.on_change = on_change
        self.color = color
        self.width = width
        self.tool = "pen"
        self.enabled = False
        self.render_hook = None  # if set, render() delegates to it (multi-page hosts)
        self._drawing = False
        self._current: dict | None = None
        self._text_entry: tk.Entry | None = None
        self._text_win = None
        self._text_editor: tk.Toplevel | None = None
        self._text_pos = (0.0, 0.0)

    # -- configuration -------------------------------------------------
    def set_enabled(self, flag: bool) -> None:
        self.enabled = bool(flag)
        if not flag:
            self._cancel_text()

    def set_tool(self, tool: str) -> None:
        if tool in self.TOOLS:
            self._commit_text()
            self.tool = tool

    def set_color(self, color: str) -> None:
        self.color = color
        if self._text_entry is not None:
            try:
                self._text_entry.configure(fg=color, insertbackground=color)
            except Exception:
                pass

    # -- coordinate helpers -------------------------------------------
    def to_image(self, x, y) -> tuple[float, float]:
        scale, ox, oy = self.get_transform()
        scale = scale or 1.0
        return (x - ox) / scale, (y - oy) / scale

    def to_canvas(self, ix, iy) -> tuple[float, float]:
        scale, ox, oy = self.get_transform()
        return ix * scale + ox, iy * scale + oy

    # -- pointer events (return True if consumed) ---------------------
    def on_press(self, event) -> bool:
        if not self.enabled:
            return False
        if self.tool == "text":
            self._begin_text(event.x, event.y)
            return True
        self._commit_text()
        ix, iy = self.to_image(event.x, event.y)
        self._drawing = True
        self._current = {
            "type": self.tool,
            "color": self.color,
            "width": self.width,
            "points": [(ix, iy)],
        }
        return True

    def on_drag(self, event) -> bool:
        if not self.enabled or not self._drawing or not self._current:
            return False
        ix, iy = self.to_image(event.x, event.y)
        points = self._current["points"]
        if self.tool == "pen":
            points.append((ix, iy))
        elif len(points) == 1:
            points.append((ix, iy))
        else:
            points[1] = (ix, iy)
        self._draw_preview()
        return True

    def on_release(self, event) -> bool:
        if not self.enabled or not self._drawing:
            return False
        self._drawing = False
        current = self._current
        self._current = None
        self._clear_preview()
        if not current:
            return True
        if current["type"] in ("rect", "arrow") and len(current["points"]) < 2:
            return True
        self.model.add(current)
        self.render()
        self._changed()
        return True

    # -- text input ----------------------------------------------------
    def _begin_text(self, cx, cy) -> None:
        self._commit_text()
        self._text_pos = self.to_image(cx, cy)
        transparent_key = "#010203"
        editor = tk.Toplevel(self.canvas)
        editor.withdraw()
        editor.overrideredirect(True)
        editor.configure(bg=transparent_key)
        try:
            editor.transient(self.canvas.winfo_toplevel())
            editor.attributes("-topmost", True)
            if sys.platform == "win32":
                editor.attributes("-transparentcolor", transparent_key)
        except Exception:
            pass
        entry = tk.Entry(
            editor,
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            selectborderwidth=0,
            bg=transparent_key if sys.platform == "win32" else self.canvas.cget("bg"),
            fg=self.color,
            insertbackground=self.color,
            insertwidth=2,
            font=app_font(ANNOTATION_TEXT_SIZE),
        )
        entry.pack(fill=tk.BOTH, expand=True)
        self._text_entry = entry
        self._text_editor = editor
        editor.update_idletasks()
        editor_width = max(320, min(720, self.canvas.winfo_width() - int(cx) - 8))
        editor_height = max(entry.winfo_reqheight(), ANNOTATION_TEXT_SIZE + 12)
        place_toplevel_absolute(
            editor,
            editor_width,
            editor_height,
            self.canvas.winfo_rootx() + int(cx),
            self.canvas.winfo_rooty() + int(cy),
        )
        editor.deiconify()
        editor.lift()

        def commit(event=None):
            self._commit_text()
            return "break"

        def cancel(event=None):
            self._cancel_text()
            return "break"

        entry.bind("<Return>", commit)
        entry.bind("<KP_Enter>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", lambda event: self._commit_text())
        entry.focus_force()

    def _commit_text(self) -> None:
        if self._text_entry is None:
            return
        text = self._text_entry.get().strip()
        position = self._text_pos
        self._destroy_text_entry()
        if text:
            self.model.add({
                "type": "text",
                "color": self.color,
                "width": self.width,
                "points": [position],
                "text": text,
                "size": ANNOTATION_TEXT_SIZE,
            })
            self.render()
            self._changed()

    def _cancel_text(self) -> None:
        self._destroy_text_entry()

    def _destroy_text_entry(self) -> None:
        text_win = self._text_win
        text_editor = self._text_editor
        text_entry = self._text_entry
        self._text_entry = None
        self._text_win = None
        self._text_editor = None
        if text_win is not None:
            try:
                self.canvas.delete(text_win)
            except Exception:
                pass
        if text_editor is not None:
            try:
                text_editor.destroy()
            except Exception:
                pass
        elif text_entry is not None:
            try:
                text_entry.destroy()
            except Exception:
                pass

    # -- editing actions ----------------------------------------------
    def undo(self) -> None:
        self._commit_text()
        if self.model.undo() is not None:
            self.render()
            self._changed()

    def clear(self) -> None:
        self._commit_text()
        if self.model.strokes:
            self.model.clear()
            self.render()
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    # -- rendering -----------------------------------------------------
    def render(self) -> None:
        if self.render_hook is not None:
            self.render_hook()
            return
        self.canvas.delete("anno")
        for stroke in self.model.strokes:
            self._render_stroke(stroke, "anno")

    def _draw_preview(self) -> None:
        self.canvas.delete("anno_preview")
        if self._current:
            self._render_stroke(self._current, "anno_preview")

    def _clear_preview(self) -> None:
        self.canvas.delete("anno_preview")

    def _render_stroke(self, stroke: dict, tag: str) -> None:
        scale, _ox, _oy = self.get_transform()
        scale = scale or 1.0
        width = max(1, int(round(stroke["width"] * scale)))
        color = stroke["color"]
        kind = stroke["type"]
        points = [self.to_canvas(ix, iy) for (ix, iy) in stroke["points"]]
        if kind == "pen":
            if len(points) >= 2:
                flat = [coord for point in points for coord in point]
                self.canvas.create_line(
                    *flat, fill=color, width=width, capstyle="round",
                    joinstyle="round", smooth=True, tags=tag,
                )
            elif points:
                x, y = points[0]
                r = max(1, width / 2)
                self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=color, outline=color, tags=tag)
        elif kind == "rect" and len(points) >= 2:
            (x0, y0), (x1, y1) = points[0], points[1]
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=width, tags=tag)
        elif kind == "arrow" and len(points) >= 2:
            (x0, y0), (x1, y1) = points[0], points[1]
            self.canvas.create_line(
                x0, y0, x1, y1, fill=color, width=width, arrow="last",
                arrowshape=(max(10, 3 * width), max(12, 4 * width), max(5, 2 * width)),
                capstyle="round", tags=tag,
            )
        elif kind == "text" and points:
            x, y = points[0]
            size = max(8, int(round(stroke.get("size", ANNOTATION_TEXT_SIZE) * scale)))
            self.canvas.create_text(x, y, text=stroke.get("text", ""), fill=color, anchor="nw", font=app_font(size), tags=tag)

    def render_to_pil(self, image: "Image.Image", offset: tuple[float, float] = (0.0, 0.0)) -> "Image.Image":
        out = image.convert("RGBA").copy()
        draw = ImageDraw.Draw(out)
        ox, oy = offset
        for stroke in self.model.strokes:
            color = _hex_to_rgb(stroke["color"])
            width = max(1, int(round(stroke["width"])))
            points = [(ix - ox, iy - oy) for (ix, iy) in stroke["points"]]
            kind = stroke["type"]
            if kind == "pen" and len(points) >= 2:
                draw.line(points, fill=color, width=width, joint="curve")
                r = width / 2
                for (x, y) in (points[0], points[-1]):
                    draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
            elif kind == "pen" and points:
                x, y = points[0]
                r = max(1, width / 2)
                draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
            elif kind == "rect" and len(points) >= 2:
                x0, y0 = points[0]
                x1, y1 = points[1]
                draw.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), outline=color, width=width)
            elif kind == "arrow" and len(points) >= 2:
                _draw_arrow_pil(draw, points[0], points[1], color, width)
            elif kind == "text" and points:
                font = load_font(int(stroke.get("size", ANNOTATION_TEXT_SIZE)))
                draw.text(points[0], stroke.get("text", ""), fill=color, font=font)
        return out


def make_annotation_toolbar(parent, controller, *, on_extra=None, bg=TITLE_BG) -> tk.Frame:
    """Build a shared annotation tool strip (pen/rect/arrow/text + colors + undo/clear).

    ``on_extra`` is an optional list of ``(label, command)`` tuples appended at the end
    (e.g. 保存 / 复制 / 完成).
    """
    bar = tk.Frame(parent, bg=bg)
    tool_buttons: dict[str, tk.Button] = {}

    def refresh_tools() -> None:
        for name, button in tool_buttons.items():
            active = (name == controller.tool)
            button.configure(bg=TITLE_BUTTON_HOVER if active else TITLE_BUTTON_BG,
                             fg="#ffffff" if active else "#cdd8ec")

    def choose_tool(name: str) -> None:
        controller.set_tool(name)
        refresh_tools()

    def tool_button(label: str, name: str) -> tk.Button:
        button = tk.Button(
            bar, text=label, command=lambda n=name: choose_tool(n), bd=0, padx=10, pady=5,
            bg=TITLE_BUTTON_BG, fg="#cdd8ec", activebackground=TITLE_BUTTON_HOVER, activeforeground="#ffffff",
            font=app_font(9), cursor="hand2",
        )
        tool_buttons[name] = button
        return button

    for label, name in (("画笔", "pen"), ("矩形", "rect"), ("箭头", "arrow"), ("文字", "text")):
        tool_button(label, name).pack(side=tk.LEFT, padx=(0, 4))

    swatches = tk.Frame(bar, bg=bg)
    swatches.pack(side=tk.LEFT, padx=(8, 8))
    swatch_widgets: list[tk.Label] = []

    def refresh_colors() -> None:
        for widget in swatch_widgets:
            chosen = (widget.swatch_color == controller.color)  # type: ignore[attr-defined]
            widget.configure(highlightbackground="#ffffff" if chosen else bg,
                             highlightcolor="#ffffff" if chosen else bg)

    def choose_color(color: str) -> None:
        controller.set_color(color)
        refresh_colors()

    for color in ANNOTATION_COLORS:
        dot = tk.Label(swatches, bg=color, width=2, height=1, cursor="hand2",
                       highlightthickness=2, highlightbackground=bg, bd=0)
        dot.swatch_color = color  # type: ignore[attr-defined]
        dot.bind("<Button-1>", lambda e, c=color: choose_color(c))
        dot.pack(side=tk.LEFT, padx=2)
        swatch_widgets.append(dot)

    def plain_button(label: str, command) -> tk.Button:
        return tk.Button(
            bar, text=label, command=command, bd=0, padx=10, pady=5,
            bg=TITLE_BUTTON_BG, fg="#cdd8ec", activebackground=TITLE_BUTTON_HOVER, activeforeground="#ffffff",
            font=app_font(9), cursor="hand2",
        )

    plain_button("撤销", controller.undo).pack(side=tk.LEFT, padx=(0, 4))
    plain_button("清除", controller.clear).pack(side=tk.LEFT, padx=(0, 4))

    for label, command in (on_extra or []):
        plain_button(label, command).pack(side=tk.LEFT, padx=(0, 4))

    refresh_tools()
    refresh_colors()
    bar.refresh_tools = refresh_tools  # type: ignore[attr-defined]
    bar.refresh_colors = refresh_colors  # type: ignore[attr-defined]
    return bar


# 图片编辑导出（压缩 / 格式转换）支持的目标格式 -> 扩展名。键即 Pillow 保存格式名。
IMAGE_EXPORT_EXT = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "GIF": ".gif",
}


class ImageViewer:
    MIN_SCALE = 0.05
    MAX_SCALE = 12.0

    CHROME_TOP = 46
    CHROME_BOTTOM = 16

    def __init__(self, app, items: list[DockItem], index: int):
        self.app = app
        self.items = items
        self.index = max(0, min(index, len(items) - 1))
        self.image = None
        self.photo = None
        self._display_cache_key = None
        self._display_cache = None
        self.scale = 1.0
        self.fit_mode = False
        self.image_center = (0.0, 0.0)
        self.pan_start = None
        self.closed = False
        self.edit_mode = False
        self._sized = False
        self.move_start = None
        self.resize_start = None
        # 非批注类编辑（裁剪等）会直接改写 self.image，用 dirty 标记以便「保存」生效。
        self.dirty = False
        self.crop_mode = False
        self.crop_anchor = None
        self.crop_rect_id = None
        self.crop_box = None
        # 图片文字层：查看模式下后台 OCR，像微信一样直接拖选并复制；
        # 编辑模式不响应文字选择，避免与批注手势冲突。
        self.ocr_busy = False
        self.ocr_words: list = []
        self.ocr_image_key = None
        self.ocr_sel_anchor = None
        self.ocr_sel_focus = None
        self.ocr_selecting = False
        self.ocr_hover_index = None
        self._ocr_generation = 0
        self._ocr_results = queue.Queue()
        self._ocr_poll_after_id = None

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(420, 320)

        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)

        left = tk.Frame(self.toolbar, bg=TITLE_BG)
        left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(left, "适应", self.fit_image).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "原始", self.actual_size).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self.edit_button = self._button(left, "编辑", self.toggle_edit)
        self.edit_button.pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "另存为新版本", self.save_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "上一张", self.previous_image).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "下一张", self.next_image).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self.photoshop_button = self._button(left, "使用PS打开", self.open_current_with_photoshop)

        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        self.info_var = tk.StringVar()
        self.info_label = tk.Label(self.toolbar, textvariable=self.info_var, bg=TITLE_BG, fg="#dbe7ff", anchor=tk.W)
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))

        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self.grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        self.grip.pack(side=tk.RIGHT, padx=(0, 6))
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        self.canvas = tk.Canvas(self.shell, bg="#111827", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.annotator = AnnotationController(self.canvas, self._image_transform)
        self.edit_bar = make_annotation_toolbar(
            self.shell,
            self.annotator,
            on_extra=[("复制", self.copy_to_clipboard), ("保存", self.save_edits), ("完成", self.toggle_edit)],
        )
        # 第二条编辑工具栏：裁剪 / 压缩 / 格式转换。
        self.edit_ops_bar = tk.Frame(self.shell, bg=TITLE_BG)
        self.crop_button = self._ops_button(self.edit_ops_bar, "裁剪", self.toggle_crop)
        self.crop_button.pack(side=tk.LEFT, padx=(8, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "↺左转", lambda: self.rotate_image(90)).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "↻右转", lambda: self.rotate_image(-90)).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "⇄水平翻转", lambda: self.flip_image(True)).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "⇅垂直翻转", lambda: self.flip_image(False)).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "调整尺寸", self.resize_image).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "自动增强", self.auto_enhance).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "压缩", self.compress_image).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "转换格式", self.convert_format).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._ops_button(self.edit_ops_bar, "撤销", self.undo_edit).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self._edit_undo: list = []

        for widget in (self.toolbar, self.info_label, left):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.canvas.bind("<Motion>", self._ocr_on_motion)
        self.canvas.bind("<Leave>", self._ocr_on_leave)
        self.canvas.bind("<Button-3>", self._ocr_context_menu)
        self.canvas.bind("<Control-c>", self._ocr_copy_shortcut)
        self.canvas.bind("<Control-C>", self._ocr_copy_shortcut)
        self.window.bind("<Control-c>", self._ocr_copy_shortcut)
        self.window.bind("<Control-C>", self._ocr_copy_shortcut)
        self.window.bind("<Control-a>", self._ocr_select_all_shortcut)
        self.window.bind("<Control-A>", self._ocr_select_all_shortcut)
        self.window.bind("<Escape>", self.on_escape)
        self.window.bind("<Left>", lambda event: self.previous_image())
        self.window.bind("<Right>", lambda event: self.next_image())
        self.window.bind("<plus>", lambda event: self.zoom_by(1.2))
        self.window.bind("<KP_Add>", lambda event: self.zoom_by(1.2))
        self.window.bind("<minus>", lambda event: self.zoom_by(1 / 1.2))
        self.window.bind("<KP_Subtract>", lambda event: self.zoom_by(1 / 1.2))
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        # 始终显示在 Passer 主窗口之上（即便主窗口被点击/置顶）。
        app.keep_window_above_main(self.window)
        self.window.update_idletasks()
        self.window.pack_propagate(False)  # keep window size fixed when the edit bar toggles
        self.load_current()

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=app_font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=TITLE_BUTTON_BG))
        return button

    def _ops_button(self, parent, text: str, command) -> tk.Button:
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=10, pady=5,
            bg=TITLE_BUTTON_BG, fg="#cdd8ec", activebackground=TITLE_BUTTON_HOVER, activeforeground="#ffffff",
            font=app_font(9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=TITLE_BUTTON_HOVER))
        button.bind("<Leave>", lambda event: button.configure(
            bg=(ACCENT if (self.crop_mode and button is self.crop_button) else TITLE_BUTTON_BG)))
        return button

    def current_item(self) -> DockItem:
        return self.items[self.index]

    # -- window chrome -------------------------------------------------
    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), 420), max(self.window.winfo_height(), 320),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        width = max(420, sw + event.x_root - sx)
        height = max(320, sh + event.y_root - sy)
        self.window.geometry(f"{width}x{height}")

    # -- loading / sizing ---------------------------------------------
    def load_current(self) -> None:
        item = self.current_item()
        path = Path(item.target)
        try:
            image = Image.open(path)
            image = ImageOps.exif_transpose(image)
            image.load()
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            self.image = image
        except Exception as exc:
            messagebox.showinfo("图片打开失败", f"无法打开图片：\n{path}\n\n{exc}", parent=self.window)
            self.close()
            return

        self.update_photoshop_button()
        self.annotator.model.clear()
        # 切换图片后清空上一张的文字层与选区；新图在渲染完成后后台识别。
        self.ocr_words = []
        self.ocr_image_key = None
        self.ocr_sel_anchor = None
        self.ocr_sel_focus = None
        self.ocr_selecting = False
        self.ocr_hover_index = None
        self.canvas.configure(cursor="")
        self.scale = 1.0
        self.fit_mode = False
        if not self._sized:
            self._size_window_to_image()
            self._sized = True
        self._center_image()
        self.render()
        self._ensure_ocr_for_current_image()

    def _size_window_to_image(self) -> None:
        if self.image is None:
            return
        content_w = max(1, int(self.image.width))
        content_h = max(1, int(self.image.height))
        win_w = content_w + 2
        win_h = content_h + self.CHROME_TOP + self.CHROME_BOTTOM + 2
        area = root_monitor_work_area(self.app.root)
        if area:
            left, top, right, bottom = area
            win_w = min(win_w, int((right - left) * 0.9))
            win_h = min(win_h, int((bottom - top) * 0.9))
        win_w = max(420, win_w)
        win_h = max(320, win_h)
        x, y = center_over_root(self.app.root, win_w, win_h)
        place_toplevel_absolute(self.window, win_w, win_h, x, y)

    def _center_image(self) -> None:
        canvas_width, canvas_height = self.canvas_size()
        self.image_center = (canvas_width / 2, canvas_height / 2)

    def canvas_size(self) -> tuple[int, int]:
        self.window.update_idletasks()
        return max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)

    def _image_transform(self) -> tuple[float, float, float]:
        if self.image is None:
            return (self.scale, 0.0, 0.0)
        cx, cy = self.image_center
        return (self.scale, cx - self.image.width * self.scale / 2, cy - self.image.height * self.scale / 2)

    # -- zoom / fit ----------------------------------------------------
    def fit_scale(self) -> float:
        if self.image is None:
            return 1.0
        canvas_width, canvas_height = self.canvas_size()
        image_width, image_height = self.image.size
        if image_width <= 0 or image_height <= 0:
            return 1.0
        return max(self.MIN_SCALE, min(canvas_width / image_width, canvas_height / image_height, 1.0))

    def fit_image(self) -> None:
        if self.image is None:
            return
        self.fit_mode = True
        self.scale = self.fit_scale()
        self._center_image()
        self.render()

    def actual_size(self) -> None:
        if self.image is None:
            return
        self.fit_mode = False
        self.scale = 1.0
        self._center_image()
        self.render()

    def set_scale(self, scale: float, focus: tuple[float, float] | None = None) -> None:
        if self.image is None:
            return
        old_scale = self.scale
        self.scale = max(self.MIN_SCALE, min(self.MAX_SCALE, scale))
        self.fit_mode = False
        if focus and old_scale > 0:
            fx, fy = focus
            cx, cy = self.image_center
            ratio = self.scale / old_scale
            self.image_center = (fx - (fx - cx) * ratio, fy - (fy - cy) * ratio)
        self.render()

    def zoom_by(self, factor: float, focus: tuple[float, float] | None = None) -> None:
        self.set_scale(self.scale * factor, focus)

    def on_mouse_wheel(self, event) -> None:
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        self.zoom_by(factor, (event.x, event.y))

    # -- pointer (pan vs. annotate) -----------------------------------
    def on_press(self, event) -> None:
        if self.crop_mode:
            self._crop_on_press(event)
            return
        if self.edit_mode and self.annotator.on_press(event):
            return
        if not self.edit_mode and self._ocr_on_press(event):
            return
        if not self.edit_mode and self._ocr_selected_range() is not None:
            self.ocr_sel_anchor = None
            self.ocr_sel_focus = None
            self.render()
        self.pan_start = (event.x, event.y, self.image_center[0], self.image_center[1])

    def on_drag(self, event) -> None:
        if self.ocr_selecting:
            self._ocr_on_drag(event)
            return
        if self.crop_mode:
            self._crop_on_drag(event)
            return
        if self.edit_mode and self.annotator.on_drag(event):
            return
        if not self.pan_start:
            return
        start_x, start_y, center_x, center_y = self.pan_start
        self.fit_mode = False
        self.image_center = (center_x + event.x - start_x, center_y + event.y - start_y)
        self.render()

    def on_release(self, event) -> None:
        if self.ocr_selecting:
            self._ocr_on_release(event)
            return
        if self.crop_mode:
            self._crop_on_release(event)
            return
        if self.edit_mode:
            self.annotator.on_release(event)
        self.pan_start = None

    def on_double_click(self, event) -> None:
        if not self.edit_mode and self._ocr_word_at(event, nearest=False) is not None:
            self._ocr_on_press(event)
            self._ocr_on_release(event)
            return
        if not self.edit_mode:
            self.fit_image()

    def on_escape(self, event=None) -> None:
        if not self.edit_mode and self._ocr_selected_range() is not None:
            self.ocr_sel_anchor = None
            self.ocr_sel_focus = None
            self.ocr_selecting = False
            self.render()
        elif self.crop_mode:
            self._exit_crop_mode()
            self.app.write_status("已取消裁剪。")
        elif self.edit_mode:
            self.toggle_edit()
        else:
            self.close()

    def on_canvas_configure(self, event) -> None:
        if self.fit_mode:
            self.fit_image()
        else:
            self._center_image()
            self.render()

    def previous_image(self) -> None:
        if not self.items or self.edit_mode:
            return
        self.index = (self.index - 1) % len(self.items)
        self.load_current()

    def next_image(self) -> None:
        if not self.items or self.edit_mode:
            return
        self.index = (self.index + 1) % len(self.items)
        self.load_current()

    # -- edit mode -----------------------------------------------------
    def toggle_edit(self) -> None:
        self.edit_mode = not self.edit_mode
        self.annotator.set_enabled(self.edit_mode)
        if self.edit_mode:
            self.ocr_sel_anchor = None
            self.ocr_sel_focus = None
            self.ocr_selecting = False
            self.ocr_hover_index = None
            self.edit_bar.pack(side=tk.TOP, fill=tk.X, before=self.canvas)
            self.edit_ops_bar.pack(side=tk.TOP, fill=tk.X, before=self.canvas)
            self.canvas.configure(cursor="pencil")
        else:
            self._exit_crop_mode()
            self.edit_bar.pack_forget()
            self.edit_ops_bar.pack_forget()
            self.canvas.configure(cursor="")
        self.render()
        if not self.edit_mode:
            self._ensure_ocr_for_current_image()

    def flattened_image(self) -> "Image.Image":
        if self.annotator.model.is_empty():
            return self.image
        return self.annotator.render_to_pil(self.image)

    def copy_to_clipboard(self) -> None:
        if self.image is None:
            return
        if copy_image_to_clipboard(self.flattened_image()):
            self.app.write_status("已复制图片到剪贴板。")
        else:
            self.app.write_status("复制图片失败。")

    def save_as_new_version(self) -> None:
        if self.image is None:
            return
        item = self.current_item()
        source = Path(item.target)
        suffix = ".png" if source.suffix.lower() == ".psd" else (source.suffix or ".png")

        def writer(destination: Path) -> None:
            flat = self.flattened_image()
            ext = destination.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                self._flatten_alpha(flat).save(destination, "JPEG", quality=95)
            elif ext == ".png":
                flat.save(destination, "PNG")
            else:
                flat.save(destination)

        self.app.save_item_as_new_version(item, writer=writer, suffix=suffix)

    def save_edits(self) -> None:
        if self.image is None:
            return
        if self.annotator.model.is_empty() and not self.dirty:
            self.app.write_status("没有可保存的修改。")
            return
        path = Path(self.current_item().target)
        is_psd = path.suffix.lower() == ".psd"
        output_path = unique_path(path.parent, f"{path.stem}_编辑", ".png") if is_psd else path
        prompt = (
            f"PSD 分层文件不会被覆盖，编辑结果将另存为 PNG：\n\n{output_path}"
            if is_psd
            else f"将编辑（批注/裁剪）合并并覆盖原图片？\n\n{path}"
        )
        if not messagebox.askyesno("保存编辑", prompt, parent=self.window):
            return
        flat = self.flattened_image()
        try:
            if is_psd:
                flat.save(output_path, "PNG")
            elif path.suffix.lower() in (".jpg", ".jpeg"):
                self._flatten_alpha(flat).save(output_path, "JPEG", quality=95)
            else:
                flat.save(output_path)
        except Exception as exc:
            messagebox.showinfo("保存失败", f"无法保存图片：\n{output_path}\n\n{exc}", parent=self.window)
            return
        self.image = flat.convert("RGBA")
        self.annotator.model.clear()
        self.dirty = False
        self.render()
        self.app.write_status(f"已保存：{output_path.name}")

    # -- 裁剪 ----------------------------------------------------------
    def toggle_crop(self) -> None:
        if self.image is None:
            return
        if self.crop_mode:
            self.apply_crop()
        else:
            self._enter_crop_mode()

    def _enter_crop_mode(self) -> None:
        self.crop_mode = True
        self.crop_anchor = None
        self.crop_box = None
        if self.crop_rect_id is not None:
            self.canvas.delete(self.crop_rect_id)
            self.crop_rect_id = None
        self.crop_button.configure(text="应用裁剪", bg=ACCENT, fg="#ffffff")
        self.canvas.configure(cursor="crosshair")
        self.app.write_status("在图片上拖动框选裁剪区域，再点「应用裁剪」（Esc 取消）。")

    def _exit_crop_mode(self) -> None:
        self.crop_mode = False
        self.crop_anchor = None
        self.crop_box = None
        if self.crop_rect_id is not None:
            self.canvas.delete(self.crop_rect_id)
            self.crop_rect_id = None
        if getattr(self, "crop_button", None) is not None:
            self.crop_button.configure(text="裁剪", bg=TITLE_BUTTON_BG, fg="#cdd8ec")
        if self.edit_mode:
            self.canvas.configure(cursor="pencil")

    def _crop_on_press(self, event) -> None:
        self.crop_anchor = (event.x, event.y)
        if self.crop_rect_id is not None:
            self.canvas.delete(self.crop_rect_id)
        self.crop_rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#38bdf8", width=2, dash=(5, 3))

    def _crop_on_drag(self, event) -> None:
        if self.crop_anchor is None or self.crop_rect_id is None:
            return
        x0, y0 = self.crop_anchor
        self.canvas.coords(self.crop_rect_id, x0, y0, event.x, event.y)

    def _crop_on_release(self, event) -> None:
        if self.crop_anchor is None:
            return
        x0, y0 = self.crop_anchor
        scale, ox, oy = self._image_transform()
        if scale <= 0:
            return

        def to_img(cx, cy):
            return ((cx - ox) / scale, (cy - oy) / scale)

        ix0, iy0 = to_img(x0, y0)
        ix1, iy1 = to_img(event.x, event.y)
        l, r = sorted((ix0, ix1))
        t, b = sorted((iy0, iy1))
        l = max(0, min(l, self.image.width))
        r = max(0, min(r, self.image.width))
        t = max(0, min(t, self.image.height))
        b = max(0, min(b, self.image.height))
        if r - l < 2 or b - t < 2:
            self.crop_box = None
            self.app.write_status("选区太小，请重新框选。")
            return
        self.crop_box = (int(round(l)), int(round(t)), int(round(r)), int(round(b)))
        self.app.write_status(
            f"已框选 {self.crop_box[2] - self.crop_box[0]}×{self.crop_box[3] - self.crop_box[1]}，"
            "点「应用裁剪」确认。")

    # -- 图片文字层 (OCR) ---------------------------------------------
    def _ensure_ocr_for_current_image(self, *, force: bool = False) -> None:
        """在后台准备当前图片的文字层，不阻塞图片窗口首次显示。"""
        if self.closed or self.image is None or self.edit_mode:
            return
        key = id(self.image)
        if not force and self.ocr_image_key == key:
            return
        self._start_ocr(key)

    def _start_ocr(self, key) -> None:
        image = self.image
        if image is None:
            return
        self._ocr_generation += 1
        generation = self._ocr_generation
        self.ocr_busy = True
        self.app.write_status("正在后台识别图片文字…")

        def work() -> None:
            status = "ready"
            words = []
            try:
                module = _ensure_ocr()
                if module is None or not module.ocr_available():
                    status = "unavailable"
                else:
                    words = module.recognize_words(image)
            except Exception:
                status = "error"
                words = []
            # Tk 非线程安全：工作线程只把结果放入队列，控件更新由主线程轮询。
            self._ocr_results.put((generation, key, words, status))

        threading.Thread(target=work, daemon=True).start()
        self._schedule_ocr_poll()

    def _schedule_ocr_poll(self) -> None:
        if self.closed or self._ocr_poll_after_id is not None:
            return
        self._ocr_poll_after_id = self.window.after(80, self._ocr_poll)

    def _ocr_poll(self) -> None:
        self._ocr_poll_after_id = None
        if self.closed:
            return
        current = None
        while True:
            try:
                result = self._ocr_results.get_nowait()
            except queue.Empty:
                break
            if result[0] == self._ocr_generation:
                current = result
        if current is not None:
            generation, key, words, status = current
            self._ocr_ready(generation, key, words, status)
        if self.ocr_busy:
            self._schedule_ocr_poll()

    def _ocr_ready(self, generation, key, words, status: str) -> None:
        if generation != self._ocr_generation:
            return
        self.ocr_busy = False
        if self.closed or self.image is None or id(self.image) != key:
            return
        self.ocr_words = list(words)
        self.ocr_image_key = key
        self.ocr_sel_anchor = None
        self.ocr_sel_focus = None
        self.ocr_selecting = False
        self.ocr_hover_index = None
        if status == "ready" and words:
            self.app.write_status("可直接拖选图片中的文字，按 Ctrl+C 复制。")
        elif status == "error":
            self.app.write_status("图片文字识别失败，可右键重新识别。")
        if not self.edit_mode:
            self.render()

    def _refresh_ocr(self) -> None:
        if self.image is None or self.edit_mode:
            return
        self.ocr_sel_anchor = None
        self.ocr_sel_focus = None
        self.ocr_selecting = False
        self.ocr_hover_index = None
        self._ensure_ocr_for_current_image(force=True)

    def _ocr_word_at(self, event, nearest: bool = False):
        """画布坐标 → 命中的文字单元索引；nearest=True 时返回最近的单元。"""
        if not self.ocr_words:
            return None
        scale, ox, oy = self._image_transform()
        if scale <= 0:
            return None
        ix = (event.x - ox) / scale
        iy = (event.y - oy) / scale
        best = None
        best_dist = None
        for index, word in enumerate(self.ocr_words):
            if word.x <= ix <= word.x + word.w and word.y <= iy <= word.y + word.h:
                return index
            if nearest:
                dx = max(word.x - ix, 0.0, ix - (word.x + word.w))
                dy = max(word.y - iy, 0.0, iy - (word.y + word.h))
                dist = dx * dx + dy * dy
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best = index
        return best if nearest else None

    def _ocr_on_press(self, event) -> bool:
        index = self._ocr_word_at(event, nearest=False)
        if index is None:
            return False
        self.ocr_sel_anchor = index
        self.ocr_sel_focus = index
        self.ocr_selecting = True
        self.pan_start = None
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass
        # The borderless viewer can receive its click before Windows has fully
        # reactivated it.  Reuse Passer's focus recovery so Ctrl+C is delivered
        # to the OCR canvas after selecting text, including on the first click
        # after switching back from another application.
        focus_manager = getattr(self.app, "focus_manager", None)
        claim_focus = getattr(focus_manager, "claim", None)
        if callable(claim_focus):
            claim_focus(self.window, self.canvas)
        self.render()
        return True

    def _ocr_on_drag(self, event) -> None:
        index = self._ocr_word_at(event, nearest=True)
        if index is None:
            return
        if self.ocr_sel_anchor is None:
            self.ocr_sel_anchor = index
        self.ocr_sel_focus = index
        self.render()

    def _ocr_on_release(self, event) -> None:
        self._ocr_on_drag(event)
        self.ocr_selecting = False
        self.pan_start = None

    def _ocr_on_motion(self, event) -> None:
        if self.edit_mode or self.crop_mode or self.ocr_selecting or self.pan_start:
            return
        index = self._ocr_word_at(event, nearest=False)
        if index == self.ocr_hover_index:
            return
        self.ocr_hover_index = index
        try:
            self.canvas.configure(cursor="xterm" if index is not None else "")
        except tk.TclError:
            pass

    def _ocr_on_leave(self, _event=None) -> None:
        if self.edit_mode or self.crop_mode or self.ocr_selecting:
            return
        self.ocr_hover_index = None
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass

    def _ocr_selected_range(self):
        if self.ocr_sel_anchor is None or self.ocr_sel_focus is None:
            return None
        return (min(self.ocr_sel_anchor, self.ocr_sel_focus),
                max(self.ocr_sel_anchor, self.ocr_sel_focus))

    def _ocr_select_all(self) -> None:
        if self.edit_mode or not self.ocr_words:
            return
        self.ocr_sel_anchor = 0
        self.ocr_sel_focus = len(self.ocr_words) - 1
        self.render()

    def _draw_ocr_overlay(self) -> None:
        selection = self._ocr_selected_range()
        if self.edit_mode or not self.ocr_words or selection is None:
            return
        scale, ox, oy = self._image_transform()
        for index in range(selection[0], selection[1] + 1):
            word = self.ocr_words[index]
            x0 = word.x * scale + ox
            y0 = word.y * scale + oy
            x1 = (word.x + word.w) * scale + ox
            y1 = (word.y + word.h) * scale + oy
            self.canvas.create_rectangle(
                x0, y0, x1, y1, fill="#38bdf8", outline="#0ea5e9",
                width=1, stipple="gray50", tags="ocrov")

    def _ocr_selected_text(self, all_text: bool = False) -> str:
        module = _ensure_ocr()
        if module is None or not self.ocr_words:
            return ""
        selection = self._ocr_selected_range()
        if all_text:
            words = list(self.ocr_words)
        elif selection is not None:
            words = self.ocr_words[selection[0]:selection[1] + 1]
        else:
            return ""
        return module.join_words(words)

    def _set_clipboard_text(self, text: str) -> None:
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.window.update_idletasks()
        except Exception:
            pass

    def _ocr_copy(self, all_text: bool = False) -> None:
        if not self.ocr_words:
            self.app.write_status("没有可复制的文字。")
            return
        text = self._ocr_selected_text(all_text=all_text)
        if not text:
            self.app.write_status("没有可复制的文字。")
            return
        self._set_clipboard_text(text)
        self.app.write_status(f"已复制 {len(text)} 个字符到剪贴板。")

    def _ocr_copy_shortcut(self, event=None):
        if self.edit_mode or self._ocr_selected_range() is None:
            return None
        self._ocr_copy(all_text=False)
        return "break"

    def _ocr_select_all_shortcut(self, event=None):
        if self.edit_mode or not self.ocr_words:
            return None
        self._ocr_select_all()
        return "break"

    def _ocr_context_menu(self, event) -> None:
        if self.edit_mode or self.crop_mode:
            return
        has_selection = self._ocr_selected_range() is not None
        has_words = bool(self.ocr_words)
        menu = tk.Menu(self.window, tearoff=0)
        menu.add_command(
            label="复制所选文字", command=lambda: self._ocr_copy(False),
            state=(tk.NORMAL if has_selection else tk.DISABLED))
        menu.add_command(
            label="复制全部文字", command=lambda: self._ocr_copy(True),
            state=(tk.NORMAL if has_words else tk.DISABLED))
        menu.add_separator()
        menu.add_command(
            label="全选文字", command=self._ocr_select_all,
            state=(tk.NORMAL if has_words else tk.DISABLED))
        menu.add_command(
            label=("正在识别文字…" if self.ocr_busy else "重新识别文字"),
            command=self._refresh_ocr,
            state=(tk.DISABLED if self.ocr_busy else tk.NORMAL),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _push_image_undo(self) -> None:
        """编辑（裁剪/旋转/翻转）前快照当前图像，供「撤销」逐步回退。"""
        if self.image is None:
            return
        self._edit_undo.append(self.image.copy())
        if len(self._edit_undo) > 12:
            self._edit_undo.pop(0)

    def undo_edit(self) -> None:
        if not getattr(self, "_edit_undo", None):
            self.app.write_status("没有可撤销的编辑。")
            return
        if self.crop_mode:
            self._exit_crop_mode()
        self.image = self._edit_undo.pop()
        self.annotator.model.clear()
        self.dirty = bool(self._edit_undo)
        self.fit_image()
        self.app.write_status("已撤销一步编辑。")

    def apply_crop(self) -> None:
        if self.crop_box is None:
            self._exit_crop_mode()
            self.app.write_status("未框选区域，已退出裁剪。")
            return
        self._push_image_undo()
        try:
            self.image = self.flattened_image().crop(self.crop_box).convert("RGBA")
        except Exception as exc:
            self._edit_undo.pop()
            messagebox.showinfo("裁剪失败", str(exc), parent=self.window)
            return
        self.annotator.model.clear()
        self.dirty = True
        self._exit_crop_mode()
        self.fit_image()
        self.app.write_status(f"已裁剪为 {self.image.width}×{self.image.height}（保存以写入文件）。")

    # -- 旋转 / 翻转 --------------------------------------------------
    def rotate_image(self, degrees: int) -> None:
        if self.image is None:
            return
        if self.crop_mode:
            self._exit_crop_mode()
        self._push_image_undo()
        # PIL.rotate(expand=True) 按逆时针旋转；传入 90=左转、-90=右转。
        try:
            self.image = self.flattened_image().rotate(degrees, expand=True).convert("RGBA")
        except Exception as exc:
            self._edit_undo.pop()
            messagebox.showinfo("旋转失败", str(exc), parent=self.window)
            return
        self.annotator.model.clear()
        self.dirty = True
        self.fit_image()
        self.app.write_status(f"已旋转（保存以写入文件）。")

    def flip_image(self, horizontal: bool) -> None:
        if self.image is None:
            return
        if self.crop_mode:
            self._exit_crop_mode()
        self._push_image_undo()
        method = Image.FLIP_LEFT_RIGHT if horizontal else Image.FLIP_TOP_BOTTOM
        try:
            self.image = self.flattened_image().transpose(method).convert("RGBA")
        except Exception as exc:
            self._edit_undo.pop()
            messagebox.showinfo("翻转失败", str(exc), parent=self.window)
            return
        self.annotator.model.clear()
        self.dirty = True
        self.fit_image()
        self.app.write_status(f"已{'水平' if horizontal else '垂直'}翻转（保存以写入文件）。")

    def _ask_image_size(self) -> tuple[int, int] | None:
        if self.image is None:
            return None
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title("调整尺寸")
        dialog.configure(bg=SURFACE_BG)
        dialog.transient(self.window)
        dialog.resizable(False, False)
        apply_app_icon(dialog)

        width_var = tk.StringVar(value=str(self.image.width))
        height_var = tk.StringVar(value=str(self.image.height))
        result: dict[str, tuple[int, int] | None] = {"value": None}

        tk.Label(
            dialog, text=f"当前尺寸：{self.image.width} × {self.image.height}",
            bg=SURFACE_BG, fg="#111827", anchor=tk.W, font=app_font(10, "bold"),
        ).pack(fill=tk.X, padx=20, pady=(18, 12))

        fields = tk.Frame(dialog, bg=SURFACE_BG)
        fields.pack(fill=tk.X, padx=20)

        def size_entry(row: int, label: str, variable: tk.StringVar) -> tk.Entry:
            tk.Label(fields, text=label, bg=SURFACE_BG, fg="#334155", anchor=tk.W,
                     width=8, font=app_font(9)).grid(row=row, column=0, sticky="w", pady=5)
            entry = tk.Entry(
                fields, textvariable=variable, bd=0, relief=tk.FLAT, bg="#f1f5f9", fg="#111827",
                insertbackground="#111827", highlightthickness=1, highlightbackground=BORDER,
                highlightcolor=ACCENT, justify=tk.RIGHT, font=app_font(10),
            )
            entry.grid(row=row, column=1, sticky="ew", pady=5, ipady=6)
            tk.Label(fields, text="像素", bg=SURFACE_BG, fg="#64748b",
                     font=app_font(9)).grid(row=row, column=2, sticky="w", padx=(8, 0))
            return entry

        fields.columnconfigure(1, weight=1)
        width_entry = size_entry(0, "宽度", width_var)
        size_entry(1, "高度", height_var)

        actions = tk.Frame(dialog, bg="#f8fafc", height=54)
        actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))
        actions.pack_propagate(False)

        def cancel() -> None:
            dialog.destroy()

        def confirm() -> None:
            try:
                width, height = int(width_var.get().strip()), int(height_var.get().strip())
            except ValueError:
                messagebox.showinfo("尺寸无效", "宽度和高度必须是整数。", parent=dialog)
                return
            if not (1 <= width <= 50000 and 1 <= height <= 50000):
                messagebox.showinfo("尺寸无效", "宽度和高度必须在 1 到 50000 像素之间。", parent=dialog)
                return
            result["value"] = (width, height)
            dialog.destroy()

        tk.Button(
            actions, text="确定", command=confirm, bd=0, padx=18, pady=7,
            bg=ACCENT, fg="#ffffff", activebackground=ACCENT_HOVER, activeforeground="#ffffff",
            cursor="hand2", font=app_font(9, "bold"),
        ).pack(side=tk.RIGHT, padx=(8, 18), pady=10)
        tk.Button(
            actions, text="取消", command=cancel, bd=0, padx=16, pady=7,
            bg="#e2e8f0", fg="#334155", activebackground="#cbd5e1", activeforeground="#1f2937",
            cursor="hand2", font=app_font(9),
        ).pack(side=tk.RIGHT, pady=10)

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.update_idletasks()
        dialog_width, dialog_height = 370, 238
        x, y = center_over_root(self.window, dialog_width, dialog_height)
        place_toplevel_absolute(dialog, dialog_width, dialog_height, x, y)
        dialog.deiconify()
        dialog.lift(self.window)
        dialog.grab_set()
        width_entry.focus_set()
        width_entry.selection_range(0, tk.END)
        self.window.wait_window(dialog)
        return result["value"]

    def resize_image(self) -> None:
        """按用户指定的宽度和高度精确调整图片尺寸。"""
        if self.image is None:
            return
        size = self._ask_image_size()
        if size is None:
            return
        width, height = size
        if (width, height) == self.image.size:
            return
        if self.crop_mode:
            self._exit_crop_mode()
        self._push_image_undo()
        try:
            self.image = self.flattened_image().resize((width, height), resize_filter()).convert("RGBA")
        except Exception as exc:
            self._edit_undo.pop()
            messagebox.showinfo("调整失败", str(exc), parent=self.window)
            return
        self.annotator.model.clear()
        self.dirty = True
        self.fit_image()
        self.app.write_status(f"已调整为 {width}×{height}（保存以写入文件）。")

    def auto_enhance(self) -> None:
        """温和地拉伸对比度并略微增强色彩，保留 alpha 通道。"""
        if self.image is None:
            return
        self._push_image_undo()
        source = self.flattened_image().convert("RGBA")
        alpha = source.getchannel("A")
        rgb = ImageOps.autocontrast(source.convert("RGB"), cutoff=1)
        rgb = ImageEnhance.Color(rgb).enhance(1.06)
        enhanced = rgb.convert("RGBA")
        enhanced.putalpha(alpha)
        self.image = enhanced
        self.annotator.model.clear()
        self.dirty = True
        self.render()
        self.app.write_status("已自动增强对比度与色彩（可撤销）。")

    # -- 压缩 / 格式转换 ----------------------------------------------
    @staticmethod
    def _flatten_alpha(img: "Image.Image") -> "Image.Image":
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        return img.convert("RGB")

    def _prepare_for_format(self, img: "Image.Image", fmt: str, quality: int):
        """返回（待保存图像, Pillow 保存参数）。fmt 为大写格式名。"""
        if fmt == "JPEG":
            return self._flatten_alpha(img), {"quality": int(quality), "optimize": True}
        if fmt == "WEBP":
            return img, {"quality": int(quality)}
        if fmt == "PNG":
            return img, {"optimize": True}
        if fmt in ("BMP", "GIF"):
            return (img if img.mode in ("RGB", "P", "L") else img.convert("RGB")), {}
        return img, {}

    def compress_image(self) -> None:
        if self.image is None:
            return
        suffix = Path(self.current_item().target).suffix.lower()
        default_fmt = "JPEG" if suffix in (".jpg", ".jpeg") else "PNG"
        opts = self._ask_export_options("压缩图片", show_resize=True, default_fmt=default_fmt)
        if opts:
            self._do_export(opts, tag="_压缩")

    def convert_format(self) -> None:
        if self.image is None:
            return
        opts = self._ask_export_options("转换格式", show_resize=False, default_fmt="PNG")
        if opts:
            self._do_export(opts, tag="")

    def _do_export(self, opts: dict, tag: str) -> None:
        fmt = opts["fmt"]
        ext = IMAGE_EXPORT_EXT[fmt]
        out = self.flattened_image()
        max_edge = opts.get("max_edge") or 0
        if max_edge > 0:
            longest = max(out.width, out.height)
            if longest > max_edge:
                ratio = max_edge / longest
                out = out.resize((max(1, int(out.width * ratio)), max(1, int(out.height * ratio))),
                                 resize_filter())
        out, params = self._prepare_for_format(out, fmt, opts.get("quality", 90))
        src = Path(self.current_item().target)
        initial = f"{src.stem}{tag}{ext}"
        path = filedialog.asksaveasfilename(
            title="保存为", defaultextension=ext, initialdir=str(src.parent), initialfile=initial,
            filetypes=[(fmt, f"*{ext}"), ("所有文件", "*.*")], parent=self.window)
        if not path:
            return
        try:
            out.save(path, fmt, **params)
        except Exception as exc:
            messagebox.showinfo("保存失败", f"无法保存图片：\n{path}\n\n{exc}", parent=self.window)
            return
        out_path = Path(path)
        try:
            self.app.add_entries([new_item("image", str(out_path), out_path.name)], force_new=True)
        except Exception:
            pass
        try:
            size = out_path.stat().st_size
            size_text = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        except OSError:
            size_text = "?"
        self.app.write_status(f"已导出：{out_path.name}　{out.width}×{out.height}　{size_text}")

    def _ask_export_options(self, title: str, *, show_resize: bool, default_fmt: str) -> dict | None:
        result: dict = {}
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title(title)
        dialog.transient(self.window)
        dialog.configure(bg=SURFACE_BG)
        dialog.resizable(False, False)
        frame = tk.Frame(dialog, bg=SURFACE_BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)

        fmt_var = tk.StringVar(value=default_fmt)
        quality_var = tk.IntVar(value=85)
        maxedge_var = tk.StringVar(value="")

        row1 = tk.Frame(frame, bg=SURFACE_BG)
        row1.pack(fill=tk.X, pady=(0, 10))
        tk.Label(row1, text="格式", bg=SURFACE_BG, fg="#111827", font=app_font(10), width=6,
                 anchor=tk.W).pack(side=tk.LEFT)
        menu = tk.OptionMenu(row1, fmt_var, *IMAGE_EXPORT_EXT.keys())
        menu.configure(bd=0, bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4", highlightthickness=1,
                       highlightbackground=BORDER, font=app_font(9), cursor="hand2")
        menu.pack(side=tk.LEFT)

        row2 = tk.Frame(frame, bg=SURFACE_BG)
        row2.pack(fill=tk.X, pady=(0, 10))
        tk.Label(row2, text="质量", bg=SURFACE_BG, fg="#111827", font=app_font(10), width=6,
                 anchor=tk.W).pack(side=tk.LEFT)
        tk.Scale(row2, from_=1, to=100, orient=tk.HORIZONTAL, variable=quality_var, length=220,
                 bg=SURFACE_BG, fg="#111827", highlightthickness=0, troughcolor="#dbe3ef",
                 font=app_font(8)).pack(side=tk.LEFT)
        tk.Label(frame, text="（质量仅对 JPEG / WebP 生效）", bg=SURFACE_BG, fg="#94a3b8",
                 font=app_font(8), anchor=tk.W).pack(fill=tk.X, pady=(0, 8))

        if show_resize:
            row3 = tk.Frame(frame, bg=SURFACE_BG)
            row3.pack(fill=tk.X, pady=(0, 12))
            tk.Label(row3, text="最长边", bg=SURFACE_BG, fg="#111827", font=app_font(10), width=6,
                     anchor=tk.W).pack(side=tk.LEFT)
            tk.Entry(row3, textvariable=maxedge_var, width=8, bd=1, relief=tk.SOLID,
                     font=app_font(10), justify=tk.CENTER).pack(side=tk.LEFT, ipady=3)
            tk.Label(row3, text="px（留空=原尺寸）", bg=SURFACE_BG, fg="#94a3b8",
                     font=app_font(8)).pack(side=tk.LEFT, padx=(6, 0))

        actions = tk.Frame(frame, bg=SURFACE_BG)
        actions.pack(fill=tk.X, pady=(4, 0))

        def confirm() -> None:
            raw = maxedge_var.get().strip()
            max_edge = 0
            if raw:
                try:
                    max_edge = int(raw)
                except ValueError:
                    messagebox.showinfo("参数错误", "最长边必须是整数像素。", parent=dialog)
                    return
            result.update(fmt=fmt_var.get(), quality=quality_var.get(), max_edge=max_edge)
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        tk.Button(actions, text="取消", command=cancel, bd=0, padx=14, pady=6, bg="#eef2f7",
                  fg="#1f2937", activebackground="#e2e8f0", font=app_font(9)).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(actions, text="保存为…", command=confirm, bd=0, padx=14, pady=6, bg=ACCENT,
                  fg="white", activebackground=ACCENT_HOVER, activeforeground="white",
                  font=app_font(9, "bold")).pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda event: confirm())
        dialog.bind("<Escape>", lambda event: cancel())
        self.window.update_idletasks()
        dialog.update_idletasks()
        w = max(360, dialog.winfo_reqwidth())
        h = dialog.winfo_reqheight()
        x, y = center_over_root(self.app.root, w, h)
        place_toplevel_absolute(dialog, w, h, x, y)
        dialog.attributes("-topmost", True)
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result or None

    def update_photoshop_button(self) -> None:
        is_psd = Path(self.current_item().target).suffix.lower() == ".psd"
        if is_psd and not self.photoshop_button.winfo_manager():
            self.photoshop_button.pack(side=tk.LEFT, padx=(6, 0), pady=8)
        elif not is_psd and self.photoshop_button.winfo_manager():
            self.photoshop_button.pack_forget()

    def open_current_with_photoshop(self) -> None:
        path = Path(self.current_item().target)
        if path.suffix.lower() != ".psd" or not path.exists():
            return
        executable = find_photoshop_executable()
        try:
            if executable:
                subprocess.Popen([executable, str(path)], cwd=str(path.parent), close_fds=True)
                self.app.write_status(f"已使用 Photoshop 打开：{path.name}")
                return
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
                self.app.write_status(f"未定位 Photoshop，已使用 PSD 关联程序打开：{path.name}")
                return
            raise FileNotFoundError("未找到 Photoshop。")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法使用 Photoshop 打开：\n{path}\n\n{exc}", parent=self.window)
        if is_psd:
            self.app.add_entries([new_item("image", str(output_path), output_path.name)], force_new=True)
        self.app.write_status(f"已保存批注：{output_path.name}")

    # -- rendering -----------------------------------------------------
    def render(self) -> None:
        if self.image is None:
            return
        width = max(1, int(self.image.width * self.scale))
        height = max(1, int(self.image.height * self.scale))
        canvas_w, canvas_h = self.canvas_size()
        draw_x, draw_y, anchor = self.image_center[0], self.image_center[1], tk.CENTER
        # 高倍缩放时不生成整张超大位图，只采样画布内可见部分。
        if width * height > 24_000_000:
            ox = self.image_center[0] - width / 2
            oy = self.image_center[1] - height / 2
            sx0 = max(0, int((-ox) / self.scale) - 1)
            sy0 = max(0, int((-oy) / self.scale) - 1)
            sx1 = min(self.image.width, int((canvas_w - ox) / self.scale) + 2)
            sy1 = min(self.image.height, int((canvas_h - oy) / self.scale) + 2)
            if sx1 <= sx0 or sy1 <= sy0:
                self.canvas.delete("all")
                self.annotator.render()
                return
            cache_key = (id(self.image), width, height, sx0, sy0, sx1, sy1)
            if cache_key != self._display_cache_key or self._display_cache is None:
                visible = self.image.crop((sx0, sy0, sx1, sy1))
                target = (max(1, round((sx1 - sx0) * self.scale)),
                          max(1, round((sy1 - sy0) * self.scale)))
                self._display_cache = visible.resize(target, resize_filter())
                self._display_cache_key = cache_key
                self.photo = ImageTk.PhotoImage(self._display_cache)
            draw_x, draw_y, anchor = ox + sx0 * self.scale, oy + sy0 * self.scale, tk.NW
        else:
            # 拖动平移时缩放比没变，复用已缩放的位图。
            cache_key = (id(self.image), width, height)
            if cache_key != self._display_cache_key or self._display_cache is None:
                self._display_cache = self.image.resize((width, height), resize_filter())
                self._display_cache_key = cache_key
                self.photo = ImageTk.PhotoImage(self._display_cache)
        self.canvas.delete("all")
        self.canvas.create_image(draw_x, draw_y, image=self.photo, anchor=anchor)
        self.annotator.render()
        self._draw_ocr_overlay()
        item = self.current_item()
        suffix = "   [编辑中]" if self.edit_mode else ("   [正在识别文字]" if self.ocr_busy else "")
        self.info_var.set(
            f"{Path(item.target).name}    {self.image.width} x {self.image.height}    {int(self.scale * 100)}%{suffix}"
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._ocr_poll_after_id is not None:
            try:
                self.window.after_cancel(self._ocr_poll_after_id)
            except tk.TclError:
                pass
            self._ocr_poll_after_id = None
        try:
            if self in self.app.image_viewers:
                self.app.image_viewers.remove(self)
        except Exception:
            pass
        self.window.destroy()


class PdfViewer:
    """Frameless, continuously-scrolling PDF reader with per-page annotation."""

    MIN_SCALE = 0.1
    MAX_SCALE = 8.0
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    RENDER_TARGET_WIDTH = 1600
    PAGE_GAP = 14
    MARGIN = 16

    def __init__(self, app, item: DockItem, preview_pdf: Path | None = None):
        self.app = app
        self.item = item
        self.source_path = Path(item.target)
        self.path = Path(preview_pdf) if preview_pdf is not None else self.source_path
        self.doc = None
        self.page_count = 0
        self.display_scale = 1.0
        self.fit_width = True
        self.closed = False
        self.edit_mode = False
        self._sized = False
        self.dirty = False
        self.move_start = None
        self.resize_start = None
        self.models: dict[int, AnnotationModel] = {}

        self._base_sizes: list[tuple[float, float]] = []
        self._widest_base_w = 1.0
        self._layout: dict[int, tuple[float, float, float, float]] = {}
        self._content_height = 1
        self._page_items: dict[int, int] = {}
        self._page_photos: dict[int, "ImageTk.PhotoImage"] = {}
        self._visible_pages: set[int] = set()
        self._active_page: int | None = None
        self._current_page = 0

        try:
            if not _ensure_pdfium():
                raise RuntimeError(f"PDF 组件不可用：{PDFIUM_IMPORT_ERROR}")
            self.doc = pdfium.PdfDocument(str(self.path))
            self.page_count = len(self.doc)
            if self.page_count <= 0:
                raise RuntimeError("PDF 没有可显示的页面。")
            for i in range(self.page_count):
                width_pt, height_pt = self.doc.get_page_size(i)
                base_scale = max(1.0, min(3.0, self.RENDER_TARGET_WIDTH / max(1.0, width_pt)))
                self._base_sizes.append((width_pt * base_scale, height_pt * base_scale))
            self._widest_base_w = max((w for w, _h in self._base_sizes), default=1.0)
        except Exception as exc:
            messagebox.showinfo("PDF 打开失败", f"无法打开预览：\n{self.source_path}\n\n{exc}", parent=app.root)
            self.closed = True
            return

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(420, 320)

        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)

        left = tk.Frame(self.toolbar, bg=TITLE_BG)
        left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(left, "适应", self.fit_to_width).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "原始", self.actual_size).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "放大", lambda: self.zoom_by(1.15)).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "缩小", lambda: self.zoom_by(1 / 1.15)).pack(side=tk.LEFT, padx=(0, 12), pady=8)
        self.edit_button = self._button(left, "编辑", self.toggle_edit)
        self.edit_button.pack(side=tk.LEFT, padx=(0, 12), pady=8)
        self._button(left, "页面", self.show_page_menu).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "默认程序打开", self.open_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "另存为新版本", self.save_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        self.info_var = tk.StringVar()
        self.info_label = tk.Label(self.toolbar, textvariable=self.info_var, bg=TITLE_BG, fg="#dbe7ff", anchor=tk.W)
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))

        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self.grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        self.grip.pack(side=tk.RIGHT, padx=(0, 6))
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        body = tk.Frame(self.shell, bg="#1f2733")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(body, bg="#1f2733", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self._on_scrollbar)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.annotator = AnnotationController(self.canvas, self._active_transform, on_change=self._mark_dirty)
        self.annotator.render_hook = self.render_all_annotations
        self.edit_bar = make_annotation_toolbar(
            self.shell,
            self.annotator,
            on_extra=[("复制本页", self.copy_current_page), ("保存批注PDF", self.save_edits), ("完成", self.toggle_edit)],
        )

        for widget in (self.toolbar, self.info_label, left):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.window.bind("<Escape>", self.on_escape)
        self.window.bind("<Up>", lambda e: self.scroll_pixels(-80))
        self.window.bind("<Down>", lambda e: self.scroll_pixels(80))
        self.window.bind("<Prior>", lambda e: self.scroll_pixels(-self._viewport_height()))
        self.window.bind("<Next>", lambda e: self.scroll_pixels(self._viewport_height()))
        self.window.bind("<space>", lambda e: self.scroll_pixels(self._viewport_height() * 0.9))
        self.window.bind("<Home>", lambda e: self._scroll_to_fraction(0.0))
        self.window.bind("<End>", lambda e: self._scroll_to_fraction(1.0))
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._size_window_to_page()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        # 始终显示在 Passer 主窗口之上（即便主窗口被点击/置顶）。
        app.keep_window_above_main(self.window)
        self.window.update_idletasks()
        self.window.pack_propagate(False)  # keep window size fixed when the edit bar toggles
        self.relayout()

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=app_font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=TITLE_BUTTON_BG))
        return button

    # -- window chrome -------------------------------------------------
    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), 420), max(self.window.winfo_height(), 320),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(420, sw + event.x_root - sx)}x{max(320, sh + event.y_root - sy)}")

    def _size_window_to_page(self) -> None:
        if not self._base_sizes:
            return
        page_w, page_h = self._base_sizes[0]
        aspect = page_w / max(1, page_h)
        area = root_monitor_work_area(self.app.root)
        work_w, work_h = (1200, 900)
        if area:
            left, top, right, bottom = area
            work_w, work_h = right - left, bottom - top
        content_h = int(work_h * 0.88) - (self.CHROME_TOP + self.CHROME_BOTTOM)
        content_w = int(content_h * aspect) + 18
        max_w = int(work_w * 0.9)
        if content_w > max_w:
            content_w = max_w
        win_w = max(420, content_w + 2)
        win_h = max(320, content_h + self.CHROME_TOP + self.CHROME_BOTTOM + 2)
        x, y = center_over_root(self.app.root, win_w, win_h)
        place_toplevel_absolute(self.window, win_w, win_h, x, y)

    # -- rendering / layout -------------------------------------------
    def render_page_image(self, index: int) -> "Image.Image":
        page = self.doc[index]
        try:
            page_width = max(1.0, page.get_size()[0])
            scale = max(1.0, min(3.0, self.RENDER_TARGET_WIDTH / page_width))
            return page.render(scale=scale).to_pil().convert("RGBA")
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _viewport_height(self) -> int:
        return max(self.canvas.winfo_height(), 1)

    def relayout(self) -> None:
        self.window.update_idletasks()
        canvas_w = max(self.canvas.winfo_width(), 1)
        if self.fit_width and self._widest_base_w > 0:
            self.display_scale = max(
                self.MIN_SCALE, min(self.MAX_SCALE, (canvas_w - 2 * self.MARGIN) / self._widest_base_w)
            )
        # discard cached page bitmaps/items (scale changed)
        for item in list(self._page_items.values()):
            self.canvas.delete(item)
        self._page_items.clear()
        self._page_photos.clear()

        content_w = canvas_w
        for base_w, _base_h in self._base_sizes:
            content_w = max(content_w, base_w * self.display_scale + 2 * self.MARGIN)

        layout: dict[int, tuple[float, float, float, float]] = {}
        y = self.MARGIN
        for index, (base_w, base_h) in enumerate(self._base_sizes):
            disp_w = base_w * self.display_scale
            disp_h = base_h * self.display_scale
            px = max(self.MARGIN, (content_w - disp_w) / 2)
            layout[index] = (px, y, disp_w, disp_h)
            y += disp_h + self.PAGE_GAP
        total = int(y - self.PAGE_GAP + self.MARGIN)

        self._layout = layout
        self._content_height = max(total, 1)
        self.canvas.configure(scrollregion=(0, 0, content_w, total))
        self.update_visible()

    def update_visible(self) -> None:
        if not self._layout:
            return
        viewport_h = self._viewport_height()
        top = self.canvas.canvasy(0)
        bottom = top + viewport_h
        buffer = viewport_h * 0.5
        visible = [
            i for i, (_px, py, _dw, dh) in self._layout.items()
            if (py + dh) >= (top - buffer) and py <= (bottom + buffer)
        ]
        self._visible_pages = set(visible)

        for index in list(self._page_items.keys()):
            if index not in self._visible_pages:
                self.canvas.delete(self._page_items.pop(index))
                self._page_photos.pop(index, None)

        for index in visible:
            if index not in self._page_items:
                px, py, _dw, _dh = self._layout[index]
                photo = self._page_photo(index)
                self._page_items[index] = self.canvas.create_image(
                    px, py, image=photo, anchor="nw", tags=("page", f"page{index}")
                )
        self.render_all_annotations()
        self._update_info()

    def _page_photo(self, index: int) -> "ImageTk.PhotoImage":
        photo = self._page_photos.get(index)
        if photo is not None:
            return photo
        base = self.render_page_image(index)
        _px, _py, disp_w, disp_h = self._layout[index]
        display = base.resize((max(1, int(disp_w)), max(1, int(disp_h))), resize_filter())
        photo = ImageTk.PhotoImage(display)
        self._page_photos[index] = photo
        return photo

    def render_all_annotations(self) -> None:
        self.canvas.delete("anno")
        saved_model = self.annotator.model
        saved_active = self._active_page
        try:
            for index in sorted(self._visible_pages):
                model = self.models.get(index)
                if not model or model.is_empty():
                    continue
                self._active_page = index
                self.annotator.model = model
                for stroke in model.strokes:
                    self.annotator._render_stroke(stroke, "anno")
        finally:
            self.annotator.model = saved_model
            self._active_page = saved_active

    def _active_transform(self) -> tuple[float, float, float]:
        index = self._active_page
        if index is None or index not in self._layout:
            return (self.display_scale, 0.0, 0.0)
        px, py, _dw, _dh = self._layout[index]
        return (self.display_scale, px, py)

    def _update_info(self) -> None:
        center = self.canvas.canvasy(0) + self._viewport_height() / 2
        current = 0
        for index, (_px, py, _dw, dh) in self._layout.items():
            if py <= center <= py + dh:
                current = index
                break
            if center > py:
                current = index
        self._current_page = current
        suffix = "   [编辑中]" if self.edit_mode else ""
        self.info_var.set(
            f"{self.source_path.name}    第 {current + 1} / {self.page_count} 页    {int(self.display_scale * 100)}%{suffix}"
        )

    # -- scrolling -----------------------------------------------------
    def _on_scrollbar(self, *args) -> None:
        self.canvas.yview(*args)
        self.update_visible()

    def _scroll_to_fraction(self, fraction: float) -> None:
        self.canvas.yview_moveto(max(0.0, min(1.0, fraction)))
        self.update_visible()

    def scroll_pixels(self, dy: float) -> None:
        total = max(1, self._content_height)
        top = self.canvas.canvasy(0) + dy
        top = max(0, min(top, max(0, total - self._viewport_height())))
        self.canvas.yview_moveto(top / total)
        self.update_visible()

    def on_mouse_wheel(self, event) -> None:
        if event.state & CTRL_MASK:
            self.zoom_by(1.15 if event.delta > 0 else 1 / 1.15)
            return
        self.scroll_pixels(-event.delta)

    # -- zoom ----------------------------------------------------------
    def fit_to_width(self) -> None:
        self.fit_width = True
        self.relayout()

    def actual_size(self) -> None:
        self.fit_width = False
        self.display_scale = 1.0
        self.relayout()

    def zoom_by(self, factor: float) -> None:
        self.fit_width = False
        self.display_scale = max(self.MIN_SCALE, min(self.MAX_SCALE, self.display_scale * factor))
        self.relayout()

    # -- pointer (scroll-pan vs. annotate) ----------------------------
    def _content_event(self, event):
        return types.SimpleNamespace(x=self.canvas.canvasx(event.x), y=self.canvas.canvasy(event.y),
                                     state=getattr(event, "state", 0))

    def _page_at(self, cx, cy) -> int | None:
        for index, (px, py, dw, dh) in self._layout.items():
            if px <= cx <= px + dw and py <= cy <= py + dh:
                return index
        return None

    def on_press(self, event) -> None:
        if self.edit_mode:
            shim = self._content_event(event)
            page = self._page_at(shim.x, shim.y)
            if page is not None:
                self._active_page = page
                self.annotator.model = self.models.setdefault(page, AnnotationModel())
                self.annotator.on_press(shim)
            return
        self.canvas.scan_mark(event.x, event.y)

    def on_drag(self, event) -> None:
        if self.edit_mode:
            if self._active_page is not None:
                self.annotator.on_drag(self._content_event(event))
            return
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self.update_visible()

    def on_release(self, event) -> None:
        if self.edit_mode and self._active_page is not None:
            self.annotator.on_release(self._content_event(event))

    def on_escape(self, event=None) -> None:
        if self.edit_mode:
            self.toggle_edit()
        else:
            self.close()

    def on_canvas_configure(self, event) -> None:
        self.relayout()

    # -- edit mode -----------------------------------------------------
    def toggle_edit(self) -> None:
        self.edit_mode = not self.edit_mode
        self.annotator.set_enabled(self.edit_mode)
        if self.edit_mode:
            self.edit_bar.pack(side=tk.TOP, fill=tk.X, after=self.toolbar)
            self.canvas.configure(cursor="pencil")
        else:
            self.edit_bar.pack_forget()
            self.canvas.configure(cursor="")
        self._update_info()

    def _flatten(self, model: AnnotationModel, image: "Image.Image", index: int) -> "Image.Image":
        saved_model = self.annotator.model
        saved_active = self._active_page
        # render onto a 1:1 page bitmap, so use a transform with scale 1 and no offset
        self.annotator.model = model
        try:
            return self.annotator.render_to_pil(image)
        finally:
            self.annotator.model = saved_model
            self._active_page = saved_active

    def copy_current_page(self) -> None:
        index = self._current_page
        base = self.render_page_image(index)
        model = self.models.get(index)
        image = base if (not model or model.is_empty()) else self._flatten(model, base, index)
        if copy_image_to_clipboard(image):
            self.app.write_status(f"已复制第 {index + 1} 页到剪贴板。")
        else:
            self.app.write_status("复制失败。")

    def has_annotations(self) -> bool:
        return any(not model.is_empty() for model in self.models.values())

    def _mark_dirty(self) -> None:
        self.dirty = True

    def _write_annotated_pdf(self, out_path: Path) -> None:
        pages: list["Image.Image"] = []
        for i in range(self.page_count):
            page_pil = self.render_page_image(i)
            model = self.models.get(i)
            if model and not model.is_empty():
                page_pil = self._flatten(model, page_pil, i)
            pages.append(page_pil.convert("RGB"))
        if not pages:
            raise ValueError("PDF 没有可保存的页面。")
        pages[0].save(out_path, "PDF", save_all=True, append_images=pages[1:])

    def save_as_new_version(self) -> None:
        if self.doc is None:
            return
        if self.has_annotations():
            self.app.save_item_as_new_version(
                self.item,
                writer=self._write_annotated_pdf,
                suffix=".pdf",
                source_path=self.source_path,
            )
        else:
            self.app.save_item_as_new_version(self.item, source_path=self.source_path)

    def save_edits(self, confirm: bool = True) -> None:
        if self.doc is None:
            return
        if not self.has_annotations():
            self.app.write_status("没有可保存的批注。")
            self.dirty = False
            return
        out_path = unique_path(self.source_path.parent, f"{self.source_path.stem}_注释", ".pdf")
        if confirm and not messagebox.askyesno(
            "保存批注 PDF",
            f"将带批注的页面导出为新的 PDF（按页转为图片）？\n\n{out_path}",
            parent=self.window,
        ):
            return
        try:
            self._write_annotated_pdf(out_path)
        except Exception as exc:
            messagebox.showinfo("保存失败", f"无法保存批注 PDF：\n{out_path}\n\n{exc}", parent=self.window)
            return
        self.dirty = False
        self.app._skip_next_undo_record = True
        self.app.add_entries([new_item("file", str(out_path), out_path.name)], force_new=True)
        self.app.write_status(f"已保存批注 PDF：{out_path.name}")

    # -- 页面操作：合并 / 拆分 / 提取 / 转图片 ------------------------
    def show_page_menu(self) -> None:
        menu = tk.Menu(self.window, tearoff=False)
        menu.add_command(label="合并其他 PDF…", command=self.merge_pdfs)
        menu.add_command(label="拆分为单页 PDF", command=self.split_pages)
        menu.add_command(label="提取页…", command=self.extract_pages)
        menu.add_separator()
        menu.add_command(label="全部转为图片 (PNG)", command=self.export_images)
        try:
            x = self.window.winfo_pointerx()
            y = self.window.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # 读取实际查看的 PDF（Office 预览时为转换后的 PDF），但输出落在源文件旁边。
    def _pdf_out(self, tag: str, suffix: str) -> Path:
        return unique_path(self.source_path.parent, f"{self.source_path.stem}{tag}", suffix)

    def merge_pdfs(self) -> None:
        others = filedialog.askopenfilenames(
            title="选择要合并到当前文档之后的 PDF",
            filetypes=[("PDF", "*.pdf"), ("所有文件", "*.*")],
            parent=self.window,
        )
        if not others:
            return
        out_path = self._pdf_out("_合并", ".pdf")
        srcs = []
        try:
            dst = pdfium.PdfDocument.new()
            cur = pdfium.PdfDocument(str(self.path))
            srcs.append(cur)
            dst.import_pages(cur)
            for other in others:
                src = pdfium.PdfDocument(str(other))
                srcs.append(src)
                dst.import_pages(src)
            dst.save(str(out_path))
            dst.close()
        except Exception as exc:
            messagebox.showinfo("合并失败", f"无法合并 PDF：\n{exc}", parent=self.window)
            return
        finally:
            for s in srcs:
                try:
                    s.close()
                except Exception:
                    pass
        self.app.add_entries([new_item("file", str(out_path), out_path.name)], force_new=True)
        self.app.write_status(f"已合并 {len(others) + 1} 个 PDF：{out_path.name}")

    def split_pages(self) -> None:
        if self.page_count <= 1:
            messagebox.showinfo("无需拆分", "当前 PDF 只有一页。", parent=self.window)
            return
        stem = self.source_path.stem
        out_dir = self._pdf_out("_拆分", "")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            src = pdfium.PdfDocument(str(self.path))
            try:
                width = len(str(self.page_count))
                for i in range(self.page_count):
                    one = pdfium.PdfDocument.new()
                    one.import_pages(src, [i])
                    one.save(str(out_dir / f"{stem}_第{str(i + 1).zfill(width)}页.pdf"))
                    one.close()
            finally:
                src.close()
        except Exception as exc:
            messagebox.showinfo("拆分失败", f"无法拆分 PDF：\n{exc}", parent=self.window)
            return
        self.app.add_entries([new_item("folder", str(out_dir), out_dir.name)], force_new=True)
        self.app.write_status(f"已拆分为 {self.page_count} 个单页 PDF：{out_dir.name}")

    def extract_pages(self) -> None:
        spec = self.app.ask_name_value(
            "提取页", f"输入页码或范围（共 {self.page_count} 页），如 1-3,5,8：", "1"
        )
        if spec is None:
            return
        pages = parse_page_ranges(spec, self.page_count)
        if not pages:
            messagebox.showinfo("提取失败", "未识别到有效页码。", parent=self.window)
            return
        out_path = self._pdf_out("_提取", ".pdf")
        src = None
        try:
            dst = pdfium.PdfDocument.new()
            src = pdfium.PdfDocument(str(self.path))
            dst.import_pages(src, [p - 1 for p in pages])
            dst.save(str(out_path))
            dst.close()
        except Exception as exc:
            messagebox.showinfo("提取失败", f"无法提取页面：\n{exc}", parent=self.window)
            return
        finally:
            if src is not None:
                try:
                    src.close()
                except Exception:
                    pass
        self.app.add_entries([new_item("file", str(out_path), out_path.name)], force_new=True)
        self.app.write_status(f"已提取 {len(pages)} 页：{out_path.name}")

    def export_images(self) -> None:
        stem = self.source_path.stem
        out_dir = self._pdf_out("_图片", "")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            width = len(str(self.page_count))
            for i in range(self.page_count):
                img = self.render_page_image(i).convert("RGB")
                img.save(out_dir / f"{stem}_第{str(i + 1).zfill(width)}页.png", "PNG")
        except Exception as exc:
            messagebox.showinfo("导出失败", f"无法导出图片：\n{exc}", parent=self.window)
            return
        self.app.add_entries([new_item("folder", str(out_dir), out_dir.name)], force_new=True)
        self.app.write_status(f"已将 {self.page_count} 页导出为 PNG：{out_dir.name}")

    def open_external(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(self.source_path))  # type: ignore[attr-defined]
            else:
                open_target(self.item)
            self.app.write_status(f"已用默认程序打开：{self.source_path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用默认程序打开：\n{self.source_path}\n\n{exc}", parent=self.window)

    def close(self) -> None:
        if self.closed:
            return
        if self.dirty and self.has_annotations():
            answer = messagebox.askyesnocancel(
                "未保存的批注",
                f"“{self.source_path.name}” 有未保存的批注，是否导出为 PDF 后再关闭？",
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                self.save_edits(confirm=False)
                if self.dirty:
                    return
        self.closed = True
        try:
            if self.doc is not None:
                self.doc.close()
        except Exception:
            pass
        try:
            if self in self.app.pdf_viewers:
                self.app.pdf_viewers.remove(self)
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


class TextViewer:
    """Frameless built-in text/code viewer with optional editing and saving."""

    CHROME_TOP = 46
    CHROME_BOTTOM = 16

    def __init__(self, app, item: DockItem):
        self.app = app
        self.item = item
        self.path = Path(item.target)
        self.is_code = is_code_path(self.path)
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.font_size = 11
        self.wrap = not self.is_code
        self.read_encoding = "utf-8-sig"
        self.truncated = False
        self.line_numbers = None
        self.h_scrollbar = None
        self._highlight_after = None
        self.font_family = self._viewer_font_family()
        self._autosave_after = None
        self._draft_path = self._text_draft_path()
        self._restored_draft = False

        content = self._read_text()
        if content is None:
            messagebox.showinfo("打开失败", f"无法读取文本文件：\n{self.path}", parent=app.root)
            self.closed = True
            return
        content, self._restored_draft = self._restore_text_draft(content)

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(420, 300)

        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)

        left = tk.Frame(self.toolbar, bg=TITLE_BG)
        left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(left, "A-", lambda: self.change_font(-1)).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "A+", lambda: self.change_font(1)).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "自动换行", self.toggle_wrap).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "保存", self.save).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "另存为新版本", self.save_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        if self.is_code:
            self._button(left, "VS Code", self.open_vscode).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "默认程序打开", self.open_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        self.info_var = tk.StringVar()
        self.info_label = tk.Label(self.toolbar, textvariable=self.info_var, bg=TITLE_BG, fg="#dbe7ff", anchor=tk.W)
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))

        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self.grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        self.grip.pack(side=tk.RIGHT, padx=(0, 6))
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        body = tk.Frame(self.shell, bg=SURFACE_BG)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        if self.is_code:
            self.h_scrollbar = ttk.Scrollbar(body, orient=tk.HORIZONTAL)
            self.h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        if self.is_code:
            self.line_numbers = tk.Text(
                body, width=5, bd=0, padx=7, pady=12, bg="#f8fafc", fg="#94a3b8",
                highlightthickness=0, takefocus=False, wrap=tk.NONE, cursor="arrow",
                font=(self.font_family, self.font_size),
            )
            self.line_numbers.insert("1.0", "1")
            self.line_numbers.configure(state=tk.DISABLED)
            self.line_numbers.pack(side=tk.LEFT, fill=tk.Y)
        text_options = {
            "wrap": tk.WORD if self.wrap else tk.NONE,
            "bd": 0,
            "padx": 14,
            "pady": 12,
            "bg": SURFACE_BG,
            "fg": "#1f2937",
            "insertbackground": "#1f2937",
            "insertontime": 600,
            "insertofftime": 300,
            "highlightthickness": 0,
            "takefocus": True,
            "undo": True,
            "font": (self.font_family, self.font_size),
            "yscrollcommand": self._on_text_yview,
        }
        if self.h_scrollbar is not None:
            text_options["xscrollcommand"] = self.h_scrollbar.set
        self.text = tk.Text(body, **text_options)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.configure(command=self._yview)
        if self.h_scrollbar is not None:
            self.h_scrollbar.configure(command=self.text.xview)
        self.text.insert("1.0", content)
        if self.is_code:
            self._setup_code_tags()
            self._refresh_line_numbers()
            self._apply_syntax_highlighting(content)
        self.text.edit_modified(bool(self._restored_draft))
        if self._restored_draft:
            try:
                self.app.write_status(f"已恢复未保存草稿：{self.path.name}")
            except Exception:
                pass
        self.text_context_menu = tk.Menu(self.window, tearoff=False)
        self.text_context_menu.add_command(
            label="剪切", command=lambda: self._run_text_context_action("<<Cut>>", changed=True)
        )
        self.text_context_menu.add_command(
            label="复制", command=lambda: self._run_text_context_action("<<Copy>>")
        )
        self.text_context_menu.add_command(
            label="粘贴", command=lambda: self._run_text_context_action("<<Paste>>", changed=True)
        )
        self.text.bind("<MouseWheel>", self._on_mouse_wheel)
        self.text.bind("<KeyRelease>", self._on_text_changed)
        self.text.bind("<ButtonPress-1>", self._on_text_pointer, add="+")
        self.text.bind("<Button-3>", self._show_text_context_menu, add="+")
        self.text.bind("<FocusIn>", self._restore_text_caret, add="+")
        self.text.bind("<ButtonRelease-1>", self._sync_line_numbers)
        self.text.bind("<Configure>", self._sync_line_numbers)
        if self.line_numbers is not None:
            self.line_numbers.bind("<MouseWheel>", self._on_mouse_wheel)

        for widget in (self.toolbar, self.info_label, left):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        self.window.bind("<Escape>", lambda e: self.close())
        self.window.bind("<Control-s>", lambda e: (self.save(), "break")[1])
        self.window.bind("<Control-S>", lambda e: (self.save(), "break")[1])
        self.window.bind("<FocusIn>", self._on_window_focus_in, add="+")
        self.window.bind("<Activate>", self._on_window_activate, add="+")
        self.window.bind("<Map>", self._on_window_map, add="+")
        self.window.bind("<KeyPress>", self._rescue_first_text_key, add="+")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        lines = content.count("\n") + 1
        viewer_name = "代码查看器" if self.is_code else "文本查看器"
        self.info_var.set(f"{self.path.name}    {lines} 行    {viewer_name}")
        self._size_window()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        # Establish the native owner before the final focus handoff.  Windows may
        # recreate an overrideredirect HWND while its owner is being attached;
        # focusing first would leave a visible caret on the retired child HWND.
        app.keep_window_above_main(self.window)
        self.window.deiconify()
        self.window.focus_force()
        self._restore_text_focus()

    def _restore_text_caret(self, event=None) -> None:
        try:
            self.text.configure(state=tk.NORMAL, insertbackground="#1f2937", insertontime=600, insertofftime=300)
        except Exception:
            pass

    def _native_text_focus(self) -> None:
        """Keep the Win32 keyboard focus and Tk focus on the same widget."""
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            window_hwnd = self.window.winfo_id()
            window_root = user32.GetAncestor(window_hwnd, 2) or window_hwnd
            foreground = user32.GetForegroundWindow()
            foreground_root = user32.GetAncestor(foreground, 2) or foreground
            if foreground_root == window_root:
                user32.SetFocus(self.text.winfo_id())
        except Exception:
            pass

    def _reactivate_text_window(self) -> None:
        try:
            self.window.deiconify()
            self.window.lift()
        except Exception:
            pass
        try:
            if sys.platform == "win32":
                user32 = ctypes.windll.user32
                widget_hwnd = self.window.winfo_id()
                hwnd = user32.GetAncestor(widget_hwnd, 2) or widget_hwnd
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _restore_text_focus(self, event=None, *, reactivate: bool = True) -> None:
        if self.closed:
            return
        self._restore_text_caret()
        focus_manager = getattr(self.app, "focus_manager", None)
        if focus_manager is not None:
            focus_manager.claim(self.window, self.text, activate=reactivate)
            return
        if reactivate:
            self._reactivate_text_window()

        def focus_text() -> None:
            if self.closed:
                return
            try:
                self.text.configure(state=tk.NORMAL, takefocus=True)
                self.text.focus_set()
            except Exception:
                pass
            try:
                if self.window.focus_get() is not self.text:
                    self.text.focus_force()
            except Exception:
                pass
            self._native_text_focus()
            self._restore_text_caret()

        focus_text()
        try:
            self.window.after_idle(focus_text)
        except Exception:
            pass

    def _on_text_pointer(self, event=None) -> None:
        """A real click already activates the window; never reactivate it again."""
        self._restore_text_focus(reactivate=False)

    def _show_text_context_menu(self, event=None):
        if self.closed or event is None:
            return "break"
        self._restore_text_focus(reactivate=False)
        try:
            clicked_index = self.text.index(f"@{event.x},{event.y}")
            selection = self.text.tag_ranges(tk.SEL)
            clicked_selection = bool(
                selection
                and self.text.compare(clicked_index, ">=", selection[0])
                and self.text.compare(clicked_index, "<", selection[-1])
            )
            if not clicked_selection:
                self.text.tag_remove(tk.SEL, "1.0", tk.END)
                self.text.mark_set(tk.INSERT, clicked_index)
        except Exception:
            pass

        has_selection = bool(self.text.tag_ranges(tk.SEL))
        try:
            self.text.clipboard_get()
            can_paste = True
        except tk.TclError:
            can_paste = False
        self.text_context_menu.entryconfigure(0, state=tk.NORMAL if has_selection else tk.DISABLED)
        self.text_context_menu.entryconfigure(1, state=tk.NORMAL if has_selection else tk.DISABLED)
        self.text_context_menu.entryconfigure(2, state=tk.NORMAL if can_paste else tk.DISABLED)
        try:
            self.text_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.text_context_menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def _run_text_context_action(self, virtual_event: str, *, changed: bool = False) -> None:
        self._restore_text_focus(reactivate=False)
        try:
            self.text.event_generate(virtual_event)
        except tk.TclError:
            return
        if changed:
            try:
                self.window.after_idle(self._after_text_context_edit)
            except tk.TclError:
                pass

    def _after_text_context_edit(self) -> None:
        if self.closed:
            return
        self._sync_line_numbers()
        self._on_text_changed()

    def _on_window_map(self, event=None) -> None:
        if getattr(event, "widget", None) is self.window:
            self._restore_text_focus()

    def _on_window_focus_in(self, event=None) -> None:
        if getattr(event, "widget", None) is self.window:
            self._restore_text_focus(reactivate=False)

    def _on_window_activate(self, event=None) -> None:
        if getattr(event, "widget", None) is self.window:
            self._restore_text_focus(reactivate=False)

    def _rescue_first_text_key(self, event=None):
        """Forward the first key if Windows activated the viewer before Tk focused Text."""
        if self.closed or event is None:
            return None
        try:
            if self.window.focus_get() is self.text:
                return None
        except Exception:
            return None
        if event.keysym in {
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock", "Scroll_Lock",
        }:
            return None
        self._restore_text_focus(reactivate=False)
        try:
            self.text.event_generate("<KeyPress>", keysym=event.keysym, state=event.state)
        except Exception:
            return None
        return "break"

    def _viewer_font_family(self) -> str:
        if not self.is_code:
            return APP_FONT_FAMILY
        try:
            families = {str(name).casefold() for name in tkfont.families(self.app.root)}
        except Exception:
            families = set()
        for family in ("Cascadia Mono", "Consolas", "Courier New"):
            if not families or family.casefold() in families:
                return family
        return APP_FONT_FAMILY

    def _setup_code_tags(self) -> None:
        self.text.tag_configure("code_comment", foreground="#64748b")
        self.text.tag_configure("code_string", foreground="#047857")
        self.text.tag_configure("code_keyword", foreground=ACCENT)
        self.text.tag_configure("code_number", foreground="#b45309")
        self.text.tag_configure("code_func", foreground="#7c3aed")
        self.text.tag_configure("code_type", foreground="#0f766e")

    def _language_keywords(self) -> tuple[set[str], set[str], tuple[str, ...]]:
        suffix = self.path.suffix.lower()
        name = self.path.name.casefold()
        common = {
            "if", "else", "elif", "for", "while", "do", "switch", "case", "break", "continue",
            "return", "try", "catch", "except", "finally", "throw", "raise", "import", "from",
            "as", "with", "class", "struct", "enum", "interface", "extends", "implements",
            "public", "private", "protected", "static", "final", "const", "let", "var", "def",
            "function", "lambda", "async", "await", "yield", "new", "delete", "true", "false",
            "null", "none", "nil", "this", "self", "super", "in", "is", "not", "and", "or",
        }
        types = {
            "int", "float", "double", "decimal", "bool", "boolean", "char", "string", "str",
            "void", "object", "dict", "list", "tuple", "set", "map", "any", "number",
        }
        if suffix in {".py", ".pyw"}:
            return common | {"pass", "global", "nonlocal", "assert", "del", "match", "case"}, types, ("#",)
        if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".kt", ".kts", ".swift",
                      ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cs", ".go", ".rs", ".php",
                      ".scala", ".dart", ".gd", ".shader", ".glsl", ".hlsl", ".wgsl"}:
            return common | {"namespace", "using", "package", "module", "export", "default", "typeof",
                             "instanceof", "operator", "template", "typename", "impl", "trait", "where",
                             "mut", "fn", "pub", "use", "defer", "go", "select", "chan"}, types, ("//",)
        if suffix in {".rb", ".lua", ".r", ".jl", ".pl", ".pm", ".sh", ".bash", ".zsh", ".fish",
                      ".ps1"}:
            return common | {"then", "fi", "done", "local", "begin", "end", "elsif", "unless", "require"}, types, ("#",)
        if suffix in {".sql"}:
            return {
                "select", "from", "where", "join", "left", "right", "inner", "outer", "on", "group",
                "by", "order", "having", "insert", "update", "delete", "create", "alter", "drop",
                "table", "view", "index", "primary", "key", "foreign", "values", "into", "limit",
                "offset", "distinct", "union", "case", "when", "then", "else", "end", "as",
            }, types, ("--",)
        if suffix in {".html", ".htm", ".xml", ".vue", ".svelte"}:
            return common | {"html", "head", "body", "script", "style", "template", "div", "span"}, types, ("<!--",)
        if suffix in {".css", ".scss", ".sass", ".less"}:
            return {"import", "media", "supports", "keyframes", "font-face", "root", "var", "calc"}, types, ("/*",)
        if name in {"dockerfile", "containerfile"}:
            return {"from", "run", "cmd", "entrypoint", "copy", "add", "workdir", "env", "arg", "expose",
                    "volume", "user", "label", "shell", "healthcheck"}, types, ("#",)
        return common, types, ("#", "//", "--")

    def _apply_syntax_highlighting(self, content: str | None = None) -> None:
        if not self.is_code:
            return
        content = content if content is not None else self.text.get("1.0", "end-1c")
        for tag in ("code_comment", "code_string", "code_keyword", "code_number", "code_func", "code_type"):
            self.text.tag_remove(tag, "1.0", tk.END)
        if len(content) > 1_200_000:
            return
        lines = content.split("\n")
        if len(lines) > 20000:
            return
        keywords, types, comment_prefixes = self._language_keywords()
        keyword_re = re.compile(r"\b(" + "|".join(re.escape(word) for word in sorted(keywords, key=len, reverse=True)) + r")\b", re.I)
        type_re = re.compile(r"\b(" + "|".join(re.escape(word) for word in sorted(types, key=len, reverse=True)) + r")\b", re.I)
        string_re = re.compile(r"(?P<q>['\"`])(?:\\.|(?!\1).)*\1")
        number_re = re.compile(r"\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b")
        func_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()")
        for line_no, line in enumerate(lines, start=1):
            line_start = f"{line_no}.0"
            comment_at = None
            for prefix in comment_prefixes:
                pos = line.find(prefix)
                if pos >= 0 and (comment_at is None or pos < comment_at):
                    comment_at = pos
            scan_part = line if comment_at is None else line[:comment_at]
            for regex, tag in (
                (string_re, "code_string"),
                (keyword_re, "code_keyword"),
                (type_re, "code_type"),
                (number_re, "code_number"),
                (func_re, "code_func"),
            ):
                for match in regex.finditer(scan_part):
                    start = match.start(1) if tag == "code_func" else match.start()
                    end = match.end(1) if tag == "code_func" else match.end()
                    self.text.tag_add(tag, f"{line_start}+{start}c", f"{line_start}+{end}c")
            if comment_at is not None:
                self.text.tag_add("code_comment", f"{line_start}+{comment_at}c", f"{line_no}.end")

    def _refresh_line_numbers(self) -> None:
        if self.line_numbers is None:
            return
        try:
            count = max(1, int(self.text.index("end-1c").split(".", 1)[0]))
            numbers = "\n".join(str(index) for index in range(1, count + 1))
            self.line_numbers.configure(state=tk.NORMAL, width=max(4, len(str(count)) + 1))
            self.line_numbers.delete("1.0", tk.END)
            self.line_numbers.insert("1.0", numbers)
            self.line_numbers.configure(state=tk.DISABLED)
            self.line_numbers.yview_moveto(self.text.yview()[0])
        except Exception:
            pass

    def _sync_line_numbers(self, event=None):
        self._refresh_line_numbers()

    def _schedule_code_refresh(self) -> None:
        if not self.is_code:
            return
        if self._highlight_after is not None:
            try:
                self.window.after_cancel(self._highlight_after)
            except Exception:
                pass
        self._highlight_after = self.window.after(220, self._refresh_code_view)

    def _refresh_code_view(self) -> None:
        self._highlight_after = None
        self._refresh_line_numbers()
        self._apply_syntax_highlighting()

    def _text_draft_path(self) -> Path:
        key = hashlib.sha256(str(self.path.resolve()).casefold().encode("utf-8", "ignore")).hexdigest()
        return DATA_DIR / "TextDrafts" / f"{key}.json"

    def _restore_text_draft(self, original: str) -> tuple[str, bool]:
        try:
            data = json.loads(self._draft_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return original, False
        if not isinstance(data, dict):
            return original, False
        if str(data.get("path") or "") != str(self.path):
            return original, False
        draft_content = data.get("content")
        if not isinstance(draft_content, str) or draft_content == original:
            return original, False
        try:
            current_mtime = float(self.path.stat().st_mtime)
            source_mtime = float(data.get("source_mtime") or 0)
        except (OSError, TypeError, ValueError):
            current_mtime = 0.0
            source_mtime = 0.0
        if current_mtime and source_mtime and current_mtime > source_mtime + 1.0:
            return original, False
        return draft_content, True

    def _schedule_text_autosave(self) -> None:
        if self.truncated or self.closed:
            return
        if self._autosave_after is not None:
            try:
                self.window.after_cancel(self._autosave_after)
            except Exception:
                pass
        try:
            self._autosave_after = self.window.after(700, self._write_text_autosave)
        except Exception:
            self._autosave_after = None

    def _write_text_autosave(self) -> None:
        self._autosave_after = None
        if self.truncated or self.closed:
            return
        try:
            if not self.text.edit_modified():
                self._delete_text_autosave()
                return
            content = self.text.get("1.0", "end-1c")
            source_mtime = float(self.path.stat().st_mtime)
        except Exception:
            return
        payload = {
            "path": str(self.path),
            "content": content,
            "source_mtime": source_mtime,
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._draft_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._draft_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._draft_path)
        except (OSError, UnicodeError) as exc:
            try:
                LOGGER.warning("Unable to save text draft: %s", exc)
            except Exception:
                pass

    def _delete_text_autosave(self) -> None:
        try:
            self._draft_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _on_text_changed(self, event=None):
        if self.is_code:
            self._schedule_code_refresh()
        self._schedule_text_autosave()

    def _on_text_yview(self, *args) -> None:
        self.scrollbar.set(*args)
        if self.line_numbers is not None and args:
            try:
                self.line_numbers.yview_moveto(float(args[0]))
            except Exception:
                pass

    def _yview(self, *args):
        self.text.yview(*args)
        self._refresh_line_numbers()

    def _on_mouse_wheel(self, event):
        self.text.yview_scroll(int(-event.delta / 40), "units")
        self._refresh_line_numbers()
        return "break"

    def _read_text(self) -> str | None:
        try:
            data = self.path.read_bytes()
        except Exception:
            return None
        truncated = False
        if len(data) > TEXT_VIEWER_MAX_BYTES:
            data = data[:TEXT_VIEWER_MAX_BYTES]
            truncated = True
        if data.startswith(b"\xef\xbb\xbf"):
            self.read_encoding = "utf-8-sig"
        elif data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
            self.read_encoding = "utf-16"
        else:
            self.read_encoding = "utf-8"
            for encoding in ("utf-8", "gbk", "latin-1"):
                try:
                    data.decode(encoding)
                    self.read_encoding = encoding
                    break
                except Exception:
                    continue
        text = data.decode(self.read_encoding, "replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.truncated = truncated
        if truncated:
            text += "\n\n……（文件过大，仅显示前 4 MB）"
        return text

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=app_font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=TITLE_BUTTON_BG))
        return button

    def change_font(self, delta: int) -> None:
        self.font_size = max(7, min(40, self.font_size + delta))
        font_spec = (self.font_family, self.font_size)
        self.text.configure(font=font_spec)
        if self.line_numbers is not None:
            self.line_numbers.configure(font=font_spec)
            self._refresh_line_numbers()

    def toggle_wrap(self) -> None:
        self.wrap = not self.wrap
        self.text.configure(wrap=tk.WORD if self.wrap else tk.NONE)

    def save(self) -> None:
        if self.truncated:
            messagebox.showinfo("无法保存", "文件过大，仅载入了前 4 MB，已禁止保存以免截断内容。", parent=self.window)
            return
        content = self.text.get("1.0", "end-1c")
        encoding = self.read_encoding if self.read_encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16") else "utf-8"
        try:
            self.path.write_text(content, encoding=encoding)
        except Exception as exc:
            messagebox.showinfo("保存失败", f"无法保存文件：\n{self.path}\n\n{exc}", parent=self.window)
            return
        self.text.edit_modified(False)
        self._delete_text_autosave()
        self.app.write_status(f"已保存：{self.path.name}")

    def save_as_new_version(self) -> None:
        if self.truncated:
            messagebox.showinfo(
                "无法创建版本",
                "文件过大，当前只载入了前 4 MB；为避免生成截断版本，已停止保存。",
                parent=self.window,
            )
            return
        content = self.text.get("1.0", "end-1c")
        encoding = self.read_encoding if self.read_encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16") else "utf-8"
        self.app.save_item_as_new_version(
            self.item,
            writer=lambda destination: destination.write_text(content, encoding=encoding),
            suffix=self.path.suffix,
            source_path=self.path,
        )

    def open_external(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(self.path))  # type: ignore[attr-defined]
            else:
                open_target(self.item)
            self.app.write_status(f"已用默认程序打开：{self.path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用默认程序打开：\n{self.path}\n\n{exc}", parent=self.window)

    def open_vscode(self) -> None:
        try:
            if launch_vscode_file(self.path):
                self.app.write_status(f"已用 VS Code 打开：{self.path.name}")
                return
            messagebox.showinfo(
                "未找到 VS Code",
                "未找到 VS Code 可执行程序。请安装 VS Code，或确认 code 命令已加入 PATH。",
                parent=self.window,
            )
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用 VS Code 打开：\n{self.path}\n\n{exc}", parent=self.window)

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), 420), max(self.window.winfo_height(), 300),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(420, sw + event.x_root - sx)}x{max(300, sh + event.y_root - sy)}")

    def _size_window(self) -> None:
        area = root_monitor_work_area(self.app.root)
        win_w, win_h = 1312, 1024
        if area:
            left, top, right, bottom = area
            win_w = min(win_w, int((right - left) * 0.9))
            win_h = min(win_h, int((bottom - top) * 0.9))
        win_w = max(420, win_w)
        win_h = max(300, win_h)
        x, y = center_over_root(self.app.root, win_w, win_h)
        place_toplevel_absolute(self.window, win_w, win_h, x, y)

    def _has_unsaved(self) -> bool:
        if self.truncated:
            return False
        try:
            return bool(self.text.edit_modified())
        except Exception:
            return False

    def close(self) -> None:
        if self.closed:
            return
        if self._has_unsaved():
            self._write_text_autosave()
            answer = messagebox.askyesnocancel(
                "未保存的更改",
                f"“{self.path.name}” 有未保存的更改，是否保存？",
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                self.save()
                if self._has_unsaved():
                    return
            else:
                self._delete_text_autosave()
        else:
            self._delete_text_autosave()
        self.closed = True
        if self._autosave_after is not None:
            try:
                self.window.after_cancel(self._autosave_after)
            except Exception:
                pass
            self._autosave_after = None
        if self._highlight_after is not None:
            try:
                self.window.after_cancel(self._highlight_after)
            except Exception:
                pass
            self._highlight_after = None
        try:
            if self in self.app.text_viewers:
                self.app.text_viewers.remove(self)
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


class ExcelViewer:
    """Read-only workbook viewer with sheet tabs and a scrollable cell grid."""

    CHROME_TOP = 46
    CHROME_BOTTOM = 24
    MAX_ROWS = 2000
    MAX_COLUMNS = 100

    def __init__(self, app, item: DockItem):
        self.app = app
        self.item = item
        self.source_path = Path(item.target)
        self.path = self.source_path
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.loaded_sheets: set[int] = set()
        self.loading_sheets: set[int] = set()
        self.sheet_views: list[tuple[ttk.Treeview, tk.StringVar]] = []

        try:
            if not _ensure_openpyxl():
                raise RuntimeError(f"Excel 组件不可用：{OPENPYXL_IMPORT_ERROR}")
            if self.source_path.suffix.lower() not in EXCEL_NATIVE_EXTS:
                self.path = convert_excel_to_xlsx(self.source_path)
            self.workbook = openpyxl.load_workbook(
                self.path,
                read_only=True,
                data_only=False,
                keep_links=False,
            )
        except Exception as exc:
            messagebox.showinfo("Excel 打开失败", f"无法读取工作簿：\n{self.source_path}\n\n{exc}", parent=app.root)
            self.closed = True
            return

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(620, 400)

        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)
        left = tk.Frame(self.toolbar, bg=TITLE_BG)
        left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(left, "默认程序打开", self.open_external).pack(side=tk.LEFT, padx=(0, 8), pady=8)
        self._button(left, "另存为新版本", self.save_as_new_version).pack(side=tk.LEFT, padx=(0, 8), pady=8)
        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        self.info_var = tk.StringVar(value=self.source_path.name)
        self.info_label = tk.Label(
            self.toolbar, textvariable=self.info_var, bg=TITLE_BG, fg="#dbe7ff", anchor=tk.W
        )
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))

        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self.status_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.status_var, bg=TITLE_BG, fg="#9fb3d4", anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 8)
        )
        self.grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        self.grip.pack(side=tk.RIGHT, padx=(0, 6))
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        self.notebook = ttk.Notebook(self.shell)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        for sheet in self.workbook.worksheets:
            frame = tk.Frame(self.notebook, bg=SURFACE_BG)
            frame.grid_rowconfigure(0, weight=1)
            frame.grid_columnconfigure(0, weight=1)
            tree = ttk.Treeview(frame, show="tree headings", selectmode="browse")
            vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
            horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            state = tk.StringVar()
            self.sheet_views.append((tree, state))
            self.notebook.add(frame, text=sheet.title)

        for widget in (self.toolbar, self.info_label, left):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)
        self.notebook.bind("<<NotebookTabChanged>>", self.on_sheet_changed)
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._size_window()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        # 始终显示在 Passer 主窗口之上（即便主窗口被置顶）。
        app.keep_window_above_main(self.window)
        self.window.after_idle(self.on_sheet_changed)

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=app_font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=TITLE_BUTTON_BG))
        return button

    @staticmethod
    def cell_text(value) -> str:
        if value is None:
            return ""
        return str(value).replace("\r", " ").replace("\n", " ")

    def on_sheet_changed(self, event=None) -> None:
        try:
            index = self.notebook.index(self.notebook.select())
        except Exception:
            return
        self.load_sheet(index)

    def load_sheet(self, index: int) -> None:
        if (
            index in self.loaded_sheets
            or index in self.loading_sheets
            or not (0 <= index < len(self.workbook.worksheets))
        ):
            return
        sheet = self.workbook.worksheets[index]
        tree, _state = self.sheet_views[index]
        max_rows = min(max(1, sheet.max_row), self.MAX_ROWS)
        max_columns = min(max(1, sheet.max_column), self.MAX_COLUMNS)
        columns = [f"c{number}" for number in range(1, max_columns + 1)]
        tree.configure(columns=columns)
        tree.heading("#0", text="行")
        tree.column("#0", width=58, minwidth=45, stretch=False, anchor=tk.E)
        for number, column in enumerate(columns, 1):
            tree.heading(column, text=get_column_letter(number))
            tree.column(column, width=120, minwidth=56, stretch=False, anchor=tk.W)
        truncated = sheet.max_row > self.MAX_ROWS or sheet.max_column > self.MAX_COLUMNS
        self.info_var.set(f"{self.source_path.name}    {sheet.title}")
        self.status_var.set(f"正在加载 {sheet.title}：0 / {max_rows} 行")
        rows = iter(
            enumerate(
                sheet.iter_rows(
                    min_row=1,
                    max_row=max_rows,
                    min_col=1,
                    max_col=max_columns,
                    values_only=True,
                ),
                1,
            )
        )
        self.loading_sheets.add(index)
        self._load_sheet_batch(index, rows, max_rows, truncated, sheet.max_row, sheet.max_column)

    def _load_sheet_batch(
        self,
        index: int,
        rows,
        max_rows: int,
        truncated: bool,
        total_rows: int,
        total_columns: int,
    ) -> None:
        if self.closed or index not in self.loading_sheets:
            return
        tree, _state = self.sheet_views[index]
        last_row = 0
        try:
            for _ in range(100):
                row_number, values = next(rows)
                last_row = row_number
                tree.insert("", tk.END, text=str(row_number), values=[self.cell_text(value) for value in values])
        except StopIteration:
            self.loading_sheets.discard(index)
            self.loaded_sheets.add(index)
            suffix = "，内容过大，当前显示前 2000 行和 100 列" if truncated else ""
            self.status_var.set(f"{total_rows} 行 × {total_columns} 列{suffix}")
            return

        self.status_var.set(f"正在加载：{last_row} / {max_rows} 行")
        self.window.after(
            1,
            lambda: self._load_sheet_batch(
                index,
                rows,
                max_rows,
                truncated,
                total_rows,
                total_columns,
            ),
        )

    def open_external(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(self.source_path))  # type: ignore[attr-defined]
            else:
                open_target(self.item)
            self.app.write_status(f"已用默认程序打开：{self.source_path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用默认程序打开：\n{self.source_path}\n\n{exc}", parent=self.window)

    def save_as_new_version(self) -> None:
        self.app.save_item_as_new_version(self.item, source_path=self.source_path)

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), 620), max(self.window.winfo_height(), 400),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(620, sw + event.x_root - sx)}x{max(400, sh + event.y_root - sy)}")

    def _size_window(self) -> None:
        area = root_monitor_work_area(self.app.root)
        win_w, win_h = 1180, 760
        if area:
            left, top, right, bottom = area
            win_w = min(win_w, int((right - left) * 0.9))
            win_h = min(win_h, int((bottom - top) * 0.88))
        x, y = center_over_root(self.app.root, max(620, win_w), max(400, win_h))
        place_toplevel_absolute(self.window, max(620, win_w), max(400, win_h), x, y)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.workbook.close()
        except Exception:
            pass
        try:
            if self in self.app.excel_viewers:
                self.app.excel_viewers.remove(self)
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


class FolderViewer:
    """Frameless built-in folder viewer with in-window navigation."""

    CHROME_TOP = 46
    CHROME_BOTTOM = 24

    def __init__(self, app, item: DockItem):
        self.app = app
        self.item = item
        self.path = Path(item.target)
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.history: list[Path] = []
        self.tile_paths: dict[str, Path] = {}
        self.tile_widgets: dict[str, tuple[tk.Canvas, int]] = {}
        self.tile_photo_refs = []
        self.selected_tile_ids: set[str] = set()
        self.tile_font = tkfont.Font(family=APP_FONT_FAMILY, size=9)

        if not self.path.exists() or not self.path.is_dir():
            messagebox.showinfo("打开失败", f"无法读取文件夹：\n{self.path}", parent=app.root)
            self.closed = True
            return

        self.current_path = self.path.resolve()

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(520, 360)

        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)

        left = tk.Frame(self.toolbar, bg=TITLE_BG)
        left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(left, "后退", self.go_back).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "上一级", self.go_up).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "刷新", self.refresh).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "添加到 Passer", self.add_selected_to_passer).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(left, "资源管理器", self.open_current_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        self.info_var = tk.StringVar()
        self.info_label = tk.Label(self.toolbar, textvariable=self.info_var, bg=TITLE_BG, fg="#dbe7ff", anchor=tk.W)
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))

        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        self.status_var = tk.StringVar()
        tk.Label(bottom, textvariable=self.status_var, bg=TITLE_BG, fg="#9fb3d4", anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 8)
        )
        self.grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        self.grip.pack(side=tk.RIGHT, padx=(0, 6))
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        body = tk.Frame(self.shell, bg=APP_BG)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, bg=APP_BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = tk.Frame(self.canvas, bg=APP_BG)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.menu = tk.Menu(self.window, tearoff=False)
        self.menu.add_command(label="打开", command=self.open_selected)
        self.menu.add_command(label="添加到 Passer", command=self.add_selected_to_passer)
        self.menu.add_command(label="在资源管理器中显示", command=self.reveal_selected)
        self.menu.add_command(label="复制文件", command=self.copy_selected_files)
        self.menu.add_command(label="复制路径", command=self.copy_selected_paths)
        self.menu.add_separator()
        self.menu.add_command(label="刷新", command=self.refresh)
        self.blank_menu = tk.Menu(self.window, tearoff=False)
        self.blank_menu.add_command(label="粘贴", command=self.paste_into_current_folder)
        self.blank_menu.add_command(label="刷新", command=self.refresh)

        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<Button-1>", self.clear_selection)
        self.canvas.bind("<Button-3>", self.show_blank_menu)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.content.bind("<Button-1>", self.clear_selection)
        self.content.bind("<Button-3>", self.show_blank_menu)
        self.content.bind("<MouseWheel>", self.on_mouse_wheel)
        for widget in (self.window, self.shell, body, self.canvas, self.content):
            self.register_drop_target(widget)

        for widget in (self.toolbar, self.info_label, left):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        self.window.bind("<Escape>", lambda e: self.close())
        self.window.bind("<Return>", lambda event: (self.open_selected(), "break")[1])
        self.window.bind("<BackSpace>", lambda event: (self.go_up(), "break")[1])
        self.window.bind("<F5>", lambda e: (self.refresh(), "break")[1])
        self.window.bind("<Alt-Left>", lambda e: (self.go_back(), "break")[1])
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._size_window()
        self.refresh()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        # 始终显示在 Passer 主窗口之上（即便主窗口被置顶）。
        app.keep_window_above_main(self.window)
        self.canvas.focus_set()

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=app_font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda event: button.configure(bg=hover))
        button.bind("<Leave>", lambda event: button.configure(bg=TITLE_BUTTON_BG))
        return button

    def _entry_type(self, path: Path) -> str:
        try:
            if path.is_dir():
                return "文件夹"
        except OSError:
            return "不可访问"
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            return "图片"
        if suffix in PDF_EXTS:
            return "PDF"
        if is_code_path(path):
            return "代码"
        if suffix in TEXT_EXTS:
            return "文本"
        return f"{suffix[1:].upper()} 文件" if suffix else "文件"

    def _entry_size(self, path: Path) -> str:
        try:
            if path.is_dir():
                return ""
            size = path.stat().st_size
        except OSError:
            return ""
        units = ("B", "KB", "MB", "GB", "TB")
        value = float(size)
        unit_index = 0
        while value >= 1024 and unit_index < len(units) - 1:
            value /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(value)} {units[unit_index]}"
        return f"{value:.1f} {units[unit_index]}"

    def _entry_modified(self, path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""

    def _sorted_children(self) -> list[Path] | None:
        try:
            children = list(self.current_path.iterdir())
        except Exception as exc:
            self.status_var.set(f"无法读取文件夹：{exc}")
            return None

        def sort_key(path: Path):
            try:
                is_file = not path.is_dir()
            except OSError:
                is_file = True
            return (is_file, path.name.casefold())

        return sorted(children, key=sort_key)

    def refresh(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self.tile_paths.clear()
        self.tile_widgets.clear()
        self.tile_photo_refs.clear()
        self.selected_tile_ids.clear()

        children = self._sorted_children()
        self.info_var.set(str(self.current_path))
        if children is None:
            return

        folder_count = 0
        file_count = 0
        for index, child in enumerate(children):
            try:
                is_dir = child.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                folder_count += 1
            else:
                file_count += 1
            tile_id = f"tile_{index}"
            self.tile_paths[tile_id] = child
            self.render_tile(tile_id, child, index)

        self.info_var.set(str(self.current_path))
        self.status_var.set(f"{folder_count} 个文件夹，{file_count} 个文件")
        self.update_content_size()
        try:
            self.window.title(f"Passer - {self.current_path.name or self.current_path}")
        except Exception:
            pass

    def visible_columns(self) -> int:
        width = self.canvas.winfo_width() if hasattr(self, "canvas") else 900
        if width <= 1:
            width = 900
        return max(1, width // TILE_COLUMN_WIDTH)

    def update_content_size(self) -> None:
        count = len(self.tile_paths)
        columns = self.visible_columns()
        rows = max(1, math.ceil(count / columns))
        canvas_width = max(1, self.canvas.winfo_width())
        content_width = max(canvas_width, TILE_PAD * 2 + TILE_WIDTH + max(0, columns - 1) * TILE_COLUMN_WIDTH)
        content_height = TILE_PAD * 2 + TILE_HEIGHT + max(0, rows - 1) * TILE_ROW_HEIGHT
        self.content.configure(width=content_width, height=content_height)
        self.canvas.configure(scrollregion=(0, 0, content_width, content_height))

    def on_canvas_configure(self, event) -> None:
        for index, tile_id in enumerate(list(self.tile_paths.keys())):
            widgets = self.tile_widgets.get(tile_id)
            if not widgets:
                continue
            col = index % self.visible_columns()
            row = index // self.visible_columns()
            widgets[0].place(x=TILE_PAD + col * TILE_COLUMN_WIDTH, y=TILE_PAD + row * TILE_ROW_HEIGHT)
        self.update_content_size()

    def render_tile(self, tile_id: str, path: Path, index: int) -> None:
        col = index % self.visible_columns()
        row = index // self.visible_columns()
        tile = tk.Canvas(
            self.content,
            width=TILE_WIDTH,
            height=TILE_HEIGHT,
            bg=APP_BG,
            bd=0,
            relief=tk.FLAT,
            highlightthickness=0,
            takefocus=0,
        )
        tile.place(x=TILE_PAD + col * TILE_COLUMN_WIDTH, y=TILE_PAD + row * TILE_ROW_HEIGHT, width=TILE_WIDTH, height=TILE_HEIGHT)
        border_id = create_round_rect(tile, 1, 1, TILE_WIDTH - 2, TILE_HEIGHT - 2, radius=12, outline=BORDER, width=1, fill=SURFACE_BG)

        item = self.item_for_path(path) or new_item("folder" if path.is_dir() else "file", str(path))
        item.title = path.name
        photo = image_for_item(item)
        if photo is not None:
            self.tile_photo_refs.append(photo)
            tile.create_image(TILE_WIDTH // 2, 50, image=photo, anchor=tk.CENTER)
        else:
            tile.create_text(
                TILE_WIDTH // 2,
                50,
                text=self._entry_type(path),
                fill=MUTED_FG,
                font=app_font(9),
            )

        display_title = fit_text_lines(path.name, self.tile_font, TILE_WIDTH - 16, max_lines=2)
        tile.create_text(
            TILE_WIDTH // 2,
            104,
            text=display_title,
            fill="#111827",
            justify=tk.CENTER,
            width=TILE_WIDTH - 14,
            font=app_font(9),
        )
        self.tile_widgets[tile_id] = (tile, border_id)

        tile.bind("<ButtonPress-1>", lambda event, tid=tile_id: self.select_tile(tid, event))
        tile.bind("<Double-Button-1>", lambda event, tid=tile_id: self.open_tile(tid))
        tile.bind("<Button-3>", lambda event, tid=tile_id: self.show_tile_menu(event, tid))
        tile.bind("<MouseWheel>", self.on_mouse_wheel)
        self.register_drag_source(tile, tile_id)
        self.register_drop_target(tile, path if path.is_dir() else None)

    def selected_paths(self) -> list[Path]:
        return [
            path
            for tile_id, path in self.tile_paths.items()
            if tile_id in self.selected_tile_ids
        ]

    def register_drag_source(self, widget, tile_id: str) -> None:
        if not TKDND_AVAILABLE:
            return
        try:
            widget.drag_source_register(1, DND_FILES)
            widget.dnd_bind(
                "<<DragInitCmd>>",
                lambda event, tid=tile_id: self.start_path_drag(event, tid),
            )
            widget.dnd_bind(
                "<<DragEndCmd>>",
                lambda event, source=widget, tid=tile_id: self.end_path_drag(event, source, tid),
            )
        except Exception:
            pass

    def end_path_drag(self, event, widget, tile_id: str):
        action = getattr(event, "action", "")
        if action == COPY:
            self.status_var.set("已完成拖拽复制。")
        elif not self.closed:
            self.status_var.set("已结束拖拽。")

        # Reset the native source after Tcl finishes the completed drag callback.
        def reset_source() -> None:
            try:
                if self.closed or not widget.winfo_exists() or tile_id not in self.tile_paths:
                    return
                widget.drag_source_unregister()
                self.register_drag_source(widget, tile_id)
            except Exception:
                pass

        try:
            self.window.after_idle(reset_source)
        except Exception:
            pass
        return action or COPY

    def register_drop_target(self, widget, destination: Path | None = None) -> None:
        if not TKDND_AVAILABLE:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", lambda event: COPY)
            widget.dnd_bind("<<DropPosition>>", lambda event: COPY)
            widget.dnd_bind(
                "<<Drop>>",
                lambda event, target=destination: self.handle_path_drop(event, target),
            )
        except Exception:
            pass

    def start_path_drag(self, event, tile_id: str):
        if tile_id not in self.selected_tile_ids:
            self.selected_tile_ids = {tile_id}
            self.update_selection_styles()
        paths = []
        for path in self.selected_paths():
            try:
                if path.exists():
                    paths.append(str(path.resolve()))
            except Exception:
                continue
        if not paths:
            self.status_var.set("没有可拖出的文件或文件夹。")
            return REFUSE_DROP
        self.status_var.set(f"正在拖出复制 {len(paths)} 项。")
        return ((COPY,), (DND_FILES,), tuple(paths))

    def available_drop_destination(self, folder: Path, source: Path) -> Path:
        candidate = folder / source.name
        if not candidate.exists():
            return candidate
        suffix = source.suffix if source.is_file() else ""
        stem = source.name[:-len(suffix)] if suffix else source.name
        candidate = folder / f"{stem}_副本{suffix}"
        index = 2
        while candidate.exists():
            candidate = folder / f"{stem}_副本_{index}{suffix}"
            index += 1
        return candidate

    def copy_dropped_paths(self, paths: list[str], destination: Path) -> tuple[int, list[str]]:
        copied = 0
        failures = []
        try:
            destination = destination.resolve()
            destination.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return 0, [f"{destination}\n{exc}"]

        for raw_path in paths:
            source = Path(raw_path)
            try:
                if not source.exists():
                    raise FileNotFoundError(str(source))
                source = source.resolve()
                if source.parent == destination:
                    continue
                if source.is_dir() and (destination == source or source in destination.parents):
                    raise ValueError("不能把文件夹复制到自身内部。")
                target = self.available_drop_destination(destination, source)
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
                copied += 1
            except Exception as exc:
                failures.append(f"{source}\n{exc}")
        return copied, failures

    def handle_path_drop(self, event, destination: Path | None = None):
        raw_data = getattr(event, "data", "")
        try:
            paths = [str(path) for path in self.window.tk.splitlist(raw_data)]
        except Exception:
            paths = [str(raw_data)] if raw_data else []
        if not paths:
            return COPY

        target = destination or self.current_path
        copied, failures = self.copy_dropped_paths(paths, target)
        if copied:
            if target.resolve() == self.current_path.resolve():
                self.refresh()
            self.status_var.set(f"已拖入复制 {copied} 项。")
        elif not failures:
            self.status_var.set("所选项目已在当前文件夹中。")
        if failures:
            messagebox.showinfo(
                "部分项目复制失败",
                "\n\n".join(failures[:5]),
                parent=self.window,
            )
        return COPY

    def update_selection_styles(self) -> None:
        for tile_id, widgets in self.tile_widgets.items():
            selected = tile_id in self.selected_tile_ids
            widgets[0].itemconfigure(widgets[1], outline=ACCENT if selected else BORDER, width=2 if selected else 1)

    def select_tile(self, tile_id: str, event=None):
        state = event.state if event is not None else 0
        if state & CTRL_MASK:
            if tile_id in self.selected_tile_ids:
                self.selected_tile_ids.remove(tile_id)
            else:
                self.selected_tile_ids.add(tile_id)
        else:
            self.selected_tile_ids = {tile_id}
        self.update_selection_styles(check_broken=not defer_icons)
        self.canvas.focus_set()
        return "break"

    def clear_selection(self, event=None):
        self.selected_tile_ids.clear()
        self.update_selection_styles()
        self.canvas.focus_set()
        return None

    def navigate_to(self, path: Path, remember: bool = True) -> None:
        try:
            target = path.resolve()
            if not target.exists() or not target.is_dir():
                raise FileNotFoundError(str(path))
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法打开文件夹：\n{path}\n\n{exc}", parent=self.window)
            return
        if remember and target != self.current_path:
            self.history.append(self.current_path)
        self.current_path = target
        self.refresh()

    def go_back(self) -> None:
        if not self.history:
            self.status_var.set("没有可返回的位置。")
            return
        previous = self.history.pop()
        self.navigate_to(previous, remember=False)

    def go_up(self) -> None:
        parent = self.current_path.parent
        if parent == self.current_path:
            self.status_var.set("已经在最上一级。")
            return
        self.navigate_to(parent)

    def item_for_path(self, path: Path) -> DockItem | None:
        item = item_from_link_or_path(str(path))
        if item:
            return item
        if path.exists():
            return new_item("folder" if path.is_dir() else "file", str(path.resolve()))
        return None

    def open_path(self, path: Path) -> None:
        try:
            if path.is_dir():
                self.navigate_to(path)
                return
        except OSError as exc:
            messagebox.showinfo("打开失败", f"无法打开：\n{path}\n\n{exc}", parent=self.window)
            return
        item = self.item_for_path(path)
        if not item:
            messagebox.showinfo("打开失败", f"无法打开：\n{path}", parent=self.window)
            return
        self.app.open_item(item)

    def open_selected(self) -> None:
        paths = self.selected_paths()
        if paths:
            self.open_path(paths[0])

    def open_tile(self, tile_id: str):
        self.selected_tile_ids = {tile_id}
        self.update_selection_styles()
        path = self.tile_paths.get(tile_id)
        if path:
            self.open_path(path)
        return "break"

    def add_selected_to_passer(self) -> None:
        paths = self.selected_paths()
        if not paths:
            self.status_var.set("请选择要添加的项目。")
            return
        entries = entries_from_paths([str(path) for path in paths])
        if not entries:
            self.status_var.set("没有可添加的项目。")
            return
        self.app.add_entries(entries)
        self.status_var.set(f"已添加 {len(entries)} 项到 Passer。")

    def reveal_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        path = paths[0]
        try:
            item = new_item("folder" if path.is_dir() else "file", str(path.resolve()))
            reveal_target(item)
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法在资源管理器中显示：\n{path}\n\n{exc}", parent=self.window)

    def copy_selected_files(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        existing = []
        for path in paths:
            try:
                if path.exists():
                    existing.append(str(path.resolve()))
            except OSError:
                continue
        if existing and copy_paths_to_clipboard(existing):
            self.status_var.set(f"已复制 {len(existing)} 个文件/文件夹，可粘贴到其他位置。")
        else:
            self.status_var.set("复制文件失败。")

    def copy_selected_paths(self) -> None:
        paths = self.selected_paths()
        if not paths:
            return
        text = "\n".join(str(path) for path in paths)
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.status_var.set(f"已复制 {len(paths)} 个路径。")

    def paste_into_current_folder(self) -> None:
        clipboard_data = None
        if PIL_AVAILABLE:
            try:
                clipboard_data = ImageGrab.grabclipboard()
            except Exception:
                clipboard_data = None

        if isinstance(clipboard_data, Image.Image):
            path = unique_path(self.current_path, f"图片_{now_stamp()}", ".png")
            try:
                image = clipboard_data
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")
                image.save(path, "PNG")
            except Exception as exc:
                messagebox.showinfo("粘贴失败", f"无法保存剪贴板图片：\n{path}\n\n{exc}", parent=self.window)
                return
            self.refresh()
            self.status_var.set(f"已粘贴图片：{path.name}")
            return

        if isinstance(clipboard_data, list) and clipboard_data:
            paths = [str(path) for path in clipboard_data if isinstance(path, (str, os.PathLike))]
            if paths:
                copied, failures = self.copy_dropped_paths(paths, self.current_path)
                if copied:
                    self.refresh()
                    self.status_var.set(f"已粘贴 {copied} 个文件/文件夹。")
                elif not failures:
                    self.status_var.set("所选项目已在当前文件夹中。")
                if failures:
                    messagebox.showinfo("部分项目粘贴失败", "\n\n".join(failures[:5]), parent=self.window)
                return

        try:
            text = self.window.clipboard_get()
        except tk.TclError:
            self.status_var.set("剪贴板里没有可粘贴的内容。")
            return
        if not text:
            self.status_var.set("剪贴板里没有可粘贴的内容。")
            return

        lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text) if line.strip()]
        recognized = [item_from_link_or_path(line) for line in lines]
        if lines and all(item is not None for item in recognized):
            local_paths = [item.target for item in recognized if item is not None and item.kind != "url"]
            urls = [item.target for item in recognized if item is not None and item.kind == "url"]
            copied = 0
            failures = []
            if local_paths:
                copied, failures = self.copy_dropped_paths(local_paths, self.current_path)
            for url in urls:
                stem = sanitize_filename_piece(title_for("url", url), 48) or f"网址_{now_stamp()}"
                target = unique_path(self.current_path, stem, ".url")
                try:
                    target.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8-sig")
                    copied += 1
                except Exception as exc:
                    failures.append(f"{target}\n{exc}")
            if copied:
                self.refresh()
                self.status_var.set(f"已粘贴 {copied} 项。")
            if failures:
                messagebox.showinfo("部分项目粘贴失败", "\n\n".join(failures[:5]), parent=self.window)
            return

        stem = first_text_hint(text, limit=30) or f"文本_{now_stamp()}"
        path = unique_path(self.current_path, stem, ".txt")
        try:
            path.write_text(text, encoding="utf-8-sig")
        except Exception as exc:
            messagebox.showinfo("粘贴失败", f"无法保存剪贴板文本：\n{path}\n\n{exc}", parent=self.window)
            return
        self.refresh()
        self.status_var.set(f"已粘贴文本：{path.name}")

    def open_current_external(self) -> None:
        try:
            item = new_item("folder", str(self.current_path))
            open_target(item)
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用资源管理器打开：\n{self.current_path}\n\n{exc}", parent=self.window)

    def show_tile_menu(self, event, tile_id: str):
        if tile_id not in self.selected_tile_ids:
            self.selected_tile_ids = {tile_id}
            self.update_selection_styles()
        self.menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def show_blank_menu(self, event):
        self.blank_menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def on_mouse_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, max(self.window.winfo_width(), 520), max(self.window.winfo_height(), 360),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(520, sw + event.x_root - sx)}x{max(360, sh + event.y_root - sy)}")

    def _size_window(self) -> None:
        area = root_monitor_work_area(self.app.root)
        win_w, win_h = 1440, 1024
        if area:
            left, top, right, bottom = area
            win_w = min(win_w, int((right - left) * 0.9))
            win_h = min(win_h, int((bottom - top) * 0.9))
        win_w = max(520, win_w)
        win_h = max(360, win_h)
        x, y = center_over_root(self.app.root, win_w, win_h)
        place_toplevel_absolute(self.window, win_w, win_h, x, y)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self in self.app.folder_viewers:
                self.app.folder_viewers.remove(self)
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


class _FramelessViewer:
    """精简的无边框查看器基类：标题栏（拖动/关闭）+ 右下角缩放手柄。"""

    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 460
    MIN_H = 360

    def _build_frame(self, app, title: str) -> tk.Frame:
        self.app = app
        self.closed = False
        self.move_start = None
        self.resize_start = None
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=BORDER)
        self.window.minsize(self.MIN_W, self.MIN_H)
        self.shell = tk.Frame(self.window, bg=APP_BG, highlightthickness=1, highlightbackground=BORDER)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.toolbar = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_TOP)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.toolbar.pack_propagate(False)
        self.toolbar_left = tk.Frame(self.toolbar, bg=TITLE_BG)
        self.toolbar_left.pack(side=tk.LEFT, padx=(10, 6))
        self._button(self.toolbar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        self.info_var = tk.StringVar(value=title)
        self.info_label = tk.Label(
            self.toolbar,
            textvariable=self.info_var,
            bg=TITLE_BG,
            fg="#dbe7ff",
            anchor=tk.W,
            cursor="fleur",
        )
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 10))
        for widget in (self.toolbar, self.toolbar_left, self.info_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)
        bottom = tk.Frame(self.shell, bg=TITLE_BG, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=TITLE_BG, fg="#8aa0c0", cursor="size_nw_se")
        grip.pack(side=tk.RIGHT, padx=(0, 6))
        grip.bind("<ButtonPress-1>", self.start_resize)
        grip.bind("<B1-Motion>", self.do_resize)
        body = tk.Frame(self.shell, bg=SURFACE_BG)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        return body

    def _finish_frame(self) -> None:
        self.window.bind("<Escape>", lambda _e: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = center_over_root(self.app.root, self.MIN_W, self.MIN_H)
        place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", self.app.topmost_var.get())
        self.app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()
        self.app.keep_window_above_main(self.window)
        self.window.update_idletasks()
        self.window.pack_propagate(False)

    def _button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else TITLE_BUTTON_HOVER
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                           bg=TITLE_BUTTON_BG, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
                           font=app_font(10 if close else 9), cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=TITLE_BUTTON_BG))
        return button

    def save_source_as_new_version(self) -> None:
        item = getattr(self, "item", None)
        path = getattr(self, "path", None)
        if item is None or path is None:
            return
        self.app.save_item_as_new_version(item, source_path=path)

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(self.window, max(self.window.winfo_width(), self.MIN_W),
                                max(self.window.winfo_height(), self.MIN_H),
                                wx + event.x_root - sx, wy + event.y_root - sy)

    def start_resize(self, event) -> None:
        self.resize_start = (event.x_root, event.y_root, self.window.winfo_width(), self.window.winfo_height())

    def do_resize(self, event) -> None:
        if not self.resize_start:
            return
        sx, sy, sw, sh = self.resize_start
        self.window.geometry(f"{max(self.MIN_W, sw + event.x_root - sx)}x{max(self.MIN_H, sh + event.y_root - sy)}")

    def _remove_from(self, viewers) -> None:
        try:
            if self in viewers:
                viewers.remove(self)
        except Exception:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._remove_from(self._viewer_list())
        try:
            self.window.destroy()
        except Exception:
            pass

    def _viewer_list(self):
        return []


class VersionHistoryManagerWindow(_FramelessViewer):
    """Passer-styled, item-specific history version management window."""

    MIN_W = 780
    MIN_H = 520

    def __init__(self, app, item: DockItem):
        self.item = app.history_origin_item(item)
        body = self._build_frame(app, f"版本管理 · {self.item.display_title}")
        self._button(
            self.toolbar_left,
            "保存当前为新版本",
            self.save_current_version,
        ).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        header = tk.Frame(body, bg=SURFACE_BG)
        header.pack(fill=tk.X, padx=18, pady=(16, 10))
        tk.Label(
            header,
            text=self.item.display_title,
            bg=SURFACE_BG,
            fg="#0f172a",
            anchor=tk.W,
            font=app_font(12, "bold"),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.count_var = tk.StringVar()
        tk.Label(
            header,
            textvariable=self.count_var,
            bg=SURFACE_BG,
            fg=MUTED_FG,
            anchor=tk.E,
            font=app_font(9),
        ).pack(side=tk.RIGHT)
        tk.Label(
            body,
            text=str(self.item.target),
            bg=SURFACE_BG,
            fg=MUTED_FG,
            anchor=tk.W,
            font=app_font(8),
        ).pack(fill=tk.X, padx=18, pady=(0, 10))

        holder = tk.Frame(body, bg=SURFACE_BG)
        holder.pack(fill=tk.BOTH, expand=True, padx=(18, 8), pady=(0, 14))
        self.list_canvas = tk.Canvas(holder, bg=SURFACE_BG, highlightthickness=0, bd=0)
        self.list_scrollbar = ttk.Scrollbar(
            holder,
            orient=tk.VERTICAL,
            command=self.list_canvas.yview,
        )
        self.list_canvas.configure(yscrollcommand=self.list_scrollbar.set)
        self.list_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list_frame = tk.Frame(self.list_canvas, bg=SURFACE_BG)
        self.list_window = self.list_canvas.create_window(
            (0, 0), window=self.list_frame, anchor=tk.NW
        )
        self.list_frame.bind("<Configure>", self._sync_scroll_region)
        self.list_canvas.bind("<Configure>", self._fit_list_width)
        self.window.bind("<MouseWheel>", self._on_mouse_wheel)

        self.refresh()
        self._finish_frame()

    def _viewer_list(self):
        return self.app.version_manager_windows

    def _sync_scroll_region(self, _event=None) -> None:
        try:
            self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _fit_list_width(self, event) -> None:
        try:
            self.list_canvas.itemconfigure(self.list_window, width=max(1, event.width))
        except tk.TclError:
            pass

    def _on_mouse_wheel(self, event) -> None:
        try:
            self.list_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    @staticmethod
    def _created_label(record: dict) -> str:
        created = str(record.get("created_at") or "")
        try:
            return datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return created

    def _action_button(self, parent, text: str, command, *, primary=False, danger=False):
        if danger:
            bg, fg, hover = "#fee2e2", "#b91c1c", "#fecaca"
        elif primary:
            bg, fg, hover = ACCENT, "#ffffff", ACCENT_HOVER
        else:
            bg, fg, hover = "#eaf0f8", "#334155", "#dbe5f2"
        return tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            relief=tk.FLAT,
            padx=11,
            pady=6,
            bg=bg,
            fg=fg,
            activebackground=hover,
            activeforeground=fg,
            cursor="hand2",
            font=app_font(8),
        )

    def refresh(self) -> None:
        if getattr(self, "closed", False):
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        records = self.app.version_history_records(self.item)
        self.count_var.set(f"共 {len(records)} 个历史版本")
        if not records:
            empty = tk.Frame(
                self.list_frame,
                bg="#f8fafc",
                highlightthickness=1,
                highlightbackground=BORDER,
            )
            empty.pack(fill=tk.X, padx=(0, 10), pady=4)
            tk.Label(
                empty,
                text="暂无历史版本\n可点击标题栏中的“保存当前为新版本”创建第一个版本。",
                bg="#f8fafc",
                fg=MUTED_FG,
                justify=tk.CENTER,
                font=app_font(9),
            ).pack(fill=tk.X, padx=20, pady=34)
            return
        for record in reversed(records):
            self._build_record_card(record)

    def _build_record_card(self, record: dict) -> None:
        path = history_record_path(record)
        card = tk.Frame(
            self.list_frame,
            bg="#f8fafc",
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        card.pack(fill=tk.X, padx=(0, 10), pady=4)
        actions = tk.Frame(card, bg="#f8fafc")
        actions.pack(side=tk.RIGHT, padx=12, pady=12)
        self._action_button(
            actions,
            "打开",
            lambda rec=dict(record): self.app.open_history_version(self.item, rec),
            primary=True,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._action_button(
            actions,
            "删除",
            lambda rec=dict(record): self.delete_record(rec),
            danger=True,
        ).pack(side=tk.LEFT)

        details = tk.Frame(card, bg="#f8fafc")
        details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=14, pady=10)
        tk.Label(
            details,
            text=str(record.get("label") or "历史版本"),
            bg="#f8fafc",
            fg="#0f172a",
            anchor=tk.W,
            font=app_font(10, "bold"),
        ).pack(fill=tk.X)
        metadata = self._created_label(record)
        if path is not None:
            metadata = f"{metadata}  ·  {path.name}" if metadata else path.name
        tk.Label(
            details,
            text=metadata,
            bg="#f8fafc",
            fg=MUTED_FG,
            anchor=tk.W,
            font=app_font(8),
        ).pack(fill=tk.X, pady=(4, 0))

    def save_current_version(self) -> None:
        if self.app.save_item_as_new_version(self.item) is not None:
            self.refresh()

    def delete_record(self, record: dict) -> None:
        label = str(record.get("label") or "该历史版本")
        if not messagebox.askyesno(
            "删除历史版本",
            f"确定永久删除“{label}”吗？\n\n此操作不会删除当前文件。",
            parent=self.window,
        ):
            return
        key = str(self.item.id)
        original = list(self.app.version_history.get(key, ()))
        record_id = str(record.get("id") or "")
        updated = [
            entry for entry in original
            if (str(entry.get("id") or "") != record_id if record_id else entry != record)
        ]
        path = history_record_path(record)
        if updated:
            self.app.version_history[key] = updated
        else:
            self.app.version_history.pop(key, None)
        try:
            save_version_history(self.app.version_history)
            if path is not None:
                make_path_writable(path)
                path.unlink(missing_ok=True)
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        except Exception as exc:
            self.app.version_history[key] = original
            try:
                save_version_history(self.app.version_history)
            except Exception:
                pass
            self.app._log_unexpected(
                exc,
                module="versions",
                action="delete_history_version",
                target_path=path or self.item.target,
                expected=(OSError, ValueError),
            )
            messagebox.showinfo(
                "删除失败",
                f"无法删除历史版本：\n{path or label}\n\n{exc}",
                parent=self.window,
            )
            return
        self.app.tile_signatures.pop(key, None)
        self.app.schedule_render()
        self.app.write_status(f"已删除历史版本：{label}")
        self.refresh()


class ShellPreviewHandlerViewer(_FramelessViewer):
    """Host Microsoft's Office Preview Handler for instant Office previews."""

    MIN_W = 880
    MIN_H = 620

    def __init__(self, app, item: DockItem):
        self.item = item
        self.path = Path(item.target)
        self._preview = None
        self._initializer = None
        self._host_ready = False
        self._comtypes = None
        self._com_initialized = False
        self._clsid = office_fast_preview_clsid_for_path(self.path)
        if not self._clsid:
            self.closed = True
            return

        body = self._build_frame(app, self.path.name)
        self._button(self.toolbar_left, "默认程序打开", self.open_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(self.toolbar_left, "另存为新版本", self.save_source_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self.host = tk.Frame(body, bg="#ffffff")
        self.host.pack(fill=tk.BOTH, expand=True)
        self.host.bind("<Configure>", self._on_host_configure)
        self.host.bind("<Button-1>", lambda _event: self.focus_preview())
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._size_window()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.update_idletasks()
        try:
            self._start_preview()
        except Exception:
            self._destroy_preview()
            self.closed = True
            try:
                self.window.destroy()
            except Exception:
                pass
            return

        self.window.deiconify()
        self.window.focus_force()
        app.keep_window_above_main(self.window)
        self._host_ready = True
        self._set_preview_rect()

    def _viewer_list(self):
        return getattr(self.app, "shell_preview_viewers", [])

    def _size_window(self) -> None:
        area = root_monitor_work_area(self.app.root)
        win_w, win_h = 1280, 860
        if area:
            left, top, right, bottom = area
            win_w = min(win_w, int((right - left) * 0.9))
            win_h = min(win_h, int((bottom - top) * 0.88))
        x, y = center_over_root(self.app.root, max(self.MIN_W, win_w), max(self.MIN_H, win_h))
        place_toplevel_absolute(self.window, max(self.MIN_W, win_w), max(self.MIN_H, win_h), x, y)

    def _preview_rect(self) -> _Rect:
        try:
            width = max(1, int(self.host.winfo_width()))
            height = max(1, int(self.host.winfo_height()))
        except Exception:
            width, height = 1, 1
        return _Rect(0, 0, width, height)

    def _start_preview(self) -> None:
        clsid = self._clsid
        if not clsid:
            raise RuntimeError("未找到 Microsoft Office 系统预览器。")
        comtypes, IInitializeWithFile, IPreviewHandler = _preview_com_interfaces()
        import comtypes.client as cc

        comtypes.CoInitialize()
        self._comtypes = comtypes
        self._com_initialized = True
        preview = cc.CreateObject(comtypes.GUID(clsid), interface=IPreviewHandler)
        initializer = preview.QueryInterface(IInitializeWithFile)
        initializer.Initialize(str(self.path), 0)
        self._preview = preview
        self._initializer = initializer
        rect = self._preview_rect()
        preview.SetWindow(wintypes.HWND(int(self.host.winfo_id())), ctypes.byref(rect))
        preview.DoPreview()

    def _set_preview_rect(self) -> None:
        if self.closed or self._preview is None:
            return
        try:
            rect = self._preview_rect()
            self._preview.SetRect(ctypes.byref(rect))
        except Exception:
            pass

    def _on_host_configure(self, _event=None) -> None:
        if self._host_ready:
            self._set_preview_rect()

    def focus_preview(self) -> None:
        try:
            if self._preview is not None:
                self._preview.SetFocus()
        except Exception:
            pass

    def open_external(self) -> None:
        try:
            open_target(self.item)
            self.app.write_status(f"已用默认程序打开：{self.path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法用默认程序打开：\n{self.path}\n\n{exc}", parent=self.window)

    def _destroy_preview(self) -> None:
        preview = self._preview
        self._preview = None
        self._initializer = None
        if preview is not None:
            try:
                preview.Unload()
            except Exception:
                pass
        # 与 _start_preview 里的 CoInitialize 配对，避免每次预览泄漏一次 COM 初始化。
        # 创建与销毁都在主线程，apartment 一致；只反初始化一次。
        if self._com_initialized:
            self._com_initialized = False
            try:
                if self._comtypes is not None:
                    self._comtypes.CoUninitialize()
            except Exception:
                pass
            self._comtypes = None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._destroy_preview()
        self._remove_from(self._viewer_list())
        try:
            self.window.destroy()
        except Exception:
            pass


class MediaViewer(_FramelessViewer):
    """音视频「预览器」：资源管理器同款缩略图 + 元数据（时长/分辨率/比特率/标签）。

    不内嵌播放（无媒体后端），提供「用默认播放器打开」。
    """

    MIN_W = 760
    MIN_H = 520

    def __init__(self, app, item: DockItem):
        self.item = item
        self.path = Path(item.target)
        self.thumb_photo = None
        body = self._build_frame(app, self.path.name)
        is_audio = self.path.suffix.lower() in AUDIO_EXTS
        self.is_audio = is_audio
        self._button(self.toolbar_left, "▶ 播放", self.open_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(self.toolbar_left, "另存为新版本", self.save_source_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        if is_audio:
            self._button(self.toolbar_left, "编辑音频", self.open_audio_editor).pack(side=tk.LEFT, padx=(0, 6), pady=8)
            self._button(self.toolbar_left, "转换格式", self.convert_format).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        else:
            self._button(self.toolbar_left, "剪辑", self.trim_video).pack(side=tk.LEFT, padx=(0, 6), pady=8)
            self._button(self.toolbar_left, "转换格式", self.convert_format).pack(side=tk.LEFT, padx=(0, 6), pady=8)
            self._button(self.toolbar_left, "静音副本", self.mute_video).pack(side=tk.LEFT, padx=(0, 6), pady=8)
            self._button(self.toolbar_left, "提取音频", self.extract_audio).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._ff_queue = None

        thumb_wrap = tk.Frame(body, bg="#1f2733", height=280)
        thumb_wrap.pack(side=tk.TOP, fill=tk.X)
        thumb_wrap.pack_propagate(False)
        self.thumb_label = tk.Label(thumb_wrap, bg="#1f2733", fg="#94a3b8",
                                    font=app_font(40), text=("♪" if is_audio else "▶"))
        self.thumb_label.pack(fill=tk.BOTH, expand=True)

        info_wrap = tk.Frame(body, bg=SURFACE_BG)
        info_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=14, pady=12)
        try:
            size = self.path.stat().st_size
            size_text = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        except OSError:
            size_text = "?"
        rows = [("类型", "音频" if is_audio else "视频"), ("文件大小", size_text)]
        rows += shell_media_metadata(self.path)
        grid = tk.Frame(info_wrap, bg=SURFACE_BG)
        grid.pack(fill=tk.X, anchor=tk.N)
        for r, (k, v) in enumerate(rows):
            tk.Label(grid, text=k, bg=SURFACE_BG, fg="#64748b", font=app_font(9), anchor=tk.W,
                     width=12).grid(row=r, column=0, sticky=tk.W, pady=2)
            tk.Label(grid, text=v, bg=SURFACE_BG, fg="#111827", font=app_font(9), anchor=tk.W,
                     justify=tk.LEFT, wraplength=360).grid(row=r, column=1, sticky=tk.W, pady=2)

        self._finish_frame()
        self.window.after_idle(self._load_thumbnail)

    def _load_thumbnail(self) -> None:
        img = shell_thumbnail(self.path, 256)
        if img is None or ImageTk is None or self.closed:
            return
        try:
            img.thumbnail((360, 270))
            self.thumb_photo = ImageTk.PhotoImage(img)
            self.thumb_label.configure(image=self.thumb_photo, text="")
        except Exception:
            pass

    def open_external(self) -> None:
        try:
            open_target(self.item)
            self.app.write_status(f"已用默认播放器打开：{self.path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法打开：\n{self.path}\n\n{exc}", parent=self.window)

    def open_audio_editor(self) -> None:
        if self.path.suffix.lower() == ".wav":
            self._open_audio_editor_path(self.path)
            return
        ffmpeg = self._ensure_ffmpeg()
        if not ffmpeg:
            return
        out_path = unique_path(self.path.parent, f"{self.path.stem}_编辑", ".wav")
        cmd = [ffmpeg, "-y", "-i", str(self.path), "-vn", "-c:a", "pcm_s16le", str(out_path)]
        self._run_ffmpeg(
            cmd, out_path, f"正在准备音频编辑：{self.path.name} …", "已准备 WAV", "准备失败",
            on_done=lambda: self._open_audio_editor_path(out_path))

    def _open_audio_editor_path(self, path: Path) -> None:
        editor = AudioEditorWindow(self.app, path, self.item)
        if not editor.closed:
            self.app.audio_editors.append(editor)

    # -- 视频剪辑（ffmpeg 流复制，快速无损）---------------------------
    def _media_duration_seconds(self) -> float:
        for name, value in shell_media_metadata(self.path):
            if name.casefold() in ("时长", "长度", "length", "duration"):
                # Shell 常返回 "00:01:23"，可能含不可见的 LTR 标记。
                cleaned = "".join(ch for ch in value if ch.isdigit() or ch in ":.")
                sec = parse_time_to_seconds(cleaned)
                if sec:
                    return sec
        return 0.0

    def _ensure_ffmpeg(self):
        """返回可用的 ffmpeg 路径；找不到则引导用户指定并记住。取消返回 None。"""
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            return ffmpeg
        if not messagebox.askyesno(
            "未找到 ffmpeg",
            "该功能需要 ffmpeg。\n\n是否现在指定 ffmpeg.exe 的位置？\n"
            "（也可把 ffmpeg.exe 放到程序目录的 tools\\ffmpeg\\ 下，或加入系统 PATH）",
            parent=self.window,
        ):
            return None
        picked = filedialog.askopenfilename(
            title="选择 ffmpeg.exe", filetypes=[("ffmpeg", "ffmpeg.exe"), ("可执行文件", "*.exe")],
            parent=self.window,
        )
        if not picked:
            return None
        if not ffmpeg_usable(picked):
            messagebox.showinfo("无效", "该文件不是可用的 ffmpeg。", parent=self.window)
            return None
        remember_ffmpeg_path(picked)
        return picked

    def _run_ffmpeg(self, cmd: list, out_path: Path, busy: str, done: str, fail: str, on_done=None) -> None:
        """后台执行 ffmpeg，完成后把产物加入 Passer；不阻塞 UI。"""
        if getattr(self, "_ff_running", False):
            self.app.write_status("已有一个媒体任务正在运行，请稍候。")
            return
        self._ff_running = True
        self._ff_busy = busy
        self._ff_tick = 0
        self.info_var.set(busy)
        # 不等待标准输入，且只收集错误；减少转码期间的管道与日志开销。
        cmd = [cmd[0], "-nostdin", "-hide_banner", "-loglevel", "error", *cmd[1:]]
        import queue as _queue
        self._ff_queue = _queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                si = None
                if sys.platform == "win32":
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                r = subprocess.run(cmd, capture_output=True, startupinfo=si)
                ok = r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
                err = (r.stderr or b"").decode("utf-8", "ignore")[-500:]
                self._ff_queue.put((ok, err))
            except Exception as exc:
                self._ff_queue.put((False, str(exc)))

        threading.Thread(target=worker, name="Passer-ffmpeg", daemon=True).start()
        self._poll_ffmpeg(out_path, done, fail, on_done)

    def _poll_ffmpeg(self, out_path: Path, done: str, fail: str, on_done=None) -> None:
        try:
            ok, err = self._ff_queue.get_nowait()
        except Exception:
            if not self.closed:
                # 处理中：循环省略号，给出“正在工作”的反馈（转码可能较久）。
                self._ff_tick = (self._ff_tick + 1) % 4
                self.info_var.set(self._ff_busy.rstrip("…. ") + "…" + "·" * self._ff_tick)
                self.window.after(400, lambda: self._poll_ffmpeg(out_path, done, fail, on_done))
            return
        self._ff_running = False
        if ok:
            try:
                self.app.add_entries([new_item("file", str(out_path), out_path.name)], force_new=True)
            except Exception:
                pass
            self.info_var.set(f"{done}：{out_path.name}")
            self.app.write_status(f"{done}：{out_path.name}")
            if on_done is not None:
                self.window.after_idle(on_done)
        else:
            self.info_var.set(self.path.name)
            messagebox.showinfo(fail, f"ffmpeg 执行失败：\n\n{err or '未知错误'}", parent=self.window)

    def trim_video(self) -> None:
        ffmpeg = self._ensure_ffmpeg()
        if not ffmpeg:
            return
        rng = self._ask_trim_range(self._media_duration_seconds())
        if not rng:
            return
        start, end = rng
        out_path = unique_path(self.path.parent, f"{self.path.stem}_剪辑", self.path.suffix)
        cmd = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(self.path)]
        if end is not None and end > start:
            cmd += ["-t", f"{end - start:.3f}"]
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(out_path)]
        self._run_ffmpeg(cmd, out_path, f"正在剪辑：{self.path.name} …", "已剪辑", "剪辑失败")

    def convert_format(self) -> None:
        ffmpeg = self._ensure_ffmpeg()
        if not ffmpeg:
            return
        choice = self._ask_target_format()
        if not choice:
            return
        ext, extra = choice
        out_path = unique_path(self.path.parent, self.path.stem, ext)
        cmd = [ffmpeg, "-y", "-i", str(self.path), *extra, str(out_path)]
        self._run_ffmpeg(cmd, out_path, f"正在转换为 {ext[1:].upper()} …", "已转换", "转换失败")

    def mute_video(self) -> None:
        ffmpeg = self._ensure_ffmpeg()
        if not ffmpeg:
            return
        out_path = unique_path(self.path.parent, f"{self.path.stem}_静音", self.path.suffix)
        cmd = [ffmpeg, "-y", "-i", str(self.path), "-map", "0:v", "-c:v", "copy", "-an", str(out_path)]
        self._run_ffmpeg(cmd, out_path, "正在生成静音副本 …", "已生成静音副本", "静音失败")

    def extract_audio(self) -> None:
        ffmpeg = self._ensure_ffmpeg()
        if not ffmpeg:
            return
        out_path = unique_path(self.path.parent, f"{self.path.stem}_音频", ".mp3")
        cmd = [ffmpeg, "-y", "-i", str(self.path), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out_path)]
        self._run_ffmpeg(cmd, out_path, "正在提取音频 …", "已提取音频", "提取失败")

    # 目标格式 -> (扩展名, ffmpeg 额外参数)。容器类让 ffmpeg 按容器自动选编码器。
    VIDEO_CONVERT_FORMATS = (
        ("MP4 (H.264)", ".mp4", ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]),
        ("MKV", ".mkv", []),
        ("MOV", ".mov", []),
        ("AVI", ".avi", []),
        ("WebM (VP9)", ".webm", ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4",
                                      "-row-mt", "1", "-c:a", "libopus"]),
        ("GIF 动图", ".gif", ["-vf", "fps=12,scale=480:-1:flags=lanczos"]),
        ("MP3 (仅音频)", ".mp3", ["-vn", "-q:a", "2"]),
        ("WAV (仅音频)", ".wav", ["-vn"]),
    )

    AUDIO_CONVERT_FORMATS = (
        ("MP3", ".mp3", ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]),
        ("WAV (PCM)", ".wav", ["-vn", "-c:a", "pcm_s16le"]),
        ("M4A (AAC)", ".m4a", ["-vn", "-c:a", "aac", "-b:a", "192k"]),
        ("FLAC", ".flac", ["-vn", "-c:a", "flac"]),
        ("OGG (Vorbis)", ".ogg", ["-vn", "-c:a", "libvorbis", "-q:a", "5"]),
    )

    def _ask_target_format(self):
        """弹出目标格式选择，返回 (ext, extra_args) 或 None。"""
        formats = self.AUDIO_CONVERT_FORMATS if self.is_audio else self.VIDEO_CONVERT_FORMATS
        labels = [f[0] for f in formats]
        result = {}
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title("转换格式")
        dialog.transient(self.window)
        dialog.configure(bg=SURFACE_BG)
        dialog.resizable(False, False)
        frame = tk.Frame(dialog, bg=SURFACE_BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)
        tk.Label(frame, text="选择目标格式：", bg=SURFACE_BG, fg="#111827", anchor=tk.W,
                 font=app_font(10)).pack(fill=tk.X, pady=(0, 10))
        var = tk.StringVar(value=labels[0])
        menu = tk.OptionMenu(frame, var, *labels)
        menu.configure(bd=0, bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4", highlightthickness=1,
                       highlightbackground=BORDER, font=app_font(9), cursor="hand2")
        menu.pack(fill=tk.X)
        tk.Label(frame, text="WebM/GIF 转码较慢，请耐心等待。", bg=SURFACE_BG, fg="#94a3b8",
                 font=app_font(8), anchor=tk.W).pack(fill=tk.X, pady=(8, 0))
        actions = tk.Frame(frame, bg=SURFACE_BG)
        actions.pack(fill=tk.X, pady=(12, 0))

        def confirm() -> None:
            for label, ext, extra in formats:
                if label == var.get():
                    result["choice"] = (ext, extra)
                    break
            dialog.destroy()

        tk.Button(actions, text="取消", command=dialog.destroy, bd=0, padx=14, pady=6, bg="#eef2f7",
                  fg="#1f2937", font=app_font(9)).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(actions, text="开始转换", command=confirm, bd=0, padx=14, pady=6, bg=ACCENT,
                  fg="white", activebackground=ACCENT_HOVER, activeforeground="white",
                  font=app_font(9, "bold")).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Return>", lambda _e: confirm())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self.window.update_idletasks()
        dialog.update_idletasks()
        w = max(320, dialog.winfo_reqwidth())
        h = dialog.winfo_reqheight()
        x, y = center_over_root(self.app.root, w, h)
        place_toplevel_absolute(dialog, w, h, x, y)
        dialog.attributes("-topmost", True)
        dialog.deiconify()
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result.get("choice")

    def _ask_trim_range(self, total: float):
        """弹出起止时间对话框，返回 (start_sec, end_sec|None) 或 None（取消）。"""
        def fmt(sec: float) -> str:
            sec = max(0, int(round(sec)))
            return f"{sec // 60}:{sec % 60:02d}"

        result = {}
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.title("剪辑视频")
        dialog.transient(self.window)
        dialog.configure(bg=SURFACE_BG)
        dialog.resizable(False, False)
        frame = tk.Frame(dialog, bg=SURFACE_BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)
        hint = f"总时长 {fmt(total)}。" if total else ""
        tk.Label(frame, text=f"{hint}填写起止时间（秒 或 mm:ss / hh:mm:ss）。", bg=SURFACE_BG,
                 fg="#111827", anchor=tk.W, font=app_font(10)).pack(fill=tk.X, pady=(0, 10))
        start_var = tk.StringVar(value="0:00")
        end_var = tk.StringVar(value=fmt(total) if total else "")
        for label, var, tip in (("起始", start_var, ""), ("结束", end_var, "（留空=到结尾）")):
            row = tk.Frame(frame, bg=SURFACE_BG)
            row.pack(fill=tk.X, pady=4)
            tk.Label(row, text=label, bg=SURFACE_BG, fg="#334155", font=app_font(10),
                     width=5, anchor=tk.W).pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, width=12, bd=1, relief=tk.SOLID,
                     font=app_font(10), justify=tk.CENTER).pack(side=tk.LEFT, ipady=3)
            if tip:
                tk.Label(row, text=tip, bg=SURFACE_BG, fg="#94a3b8", font=app_font(8)).pack(side=tk.LEFT, padx=(6, 0))
        actions = tk.Frame(frame, bg=SURFACE_BG)
        actions.pack(fill=tk.X, pady=(12, 0))

        def confirm() -> None:
            start = parse_time_to_seconds(start_var.get()) or 0.0
            end_raw = end_var.get().strip()
            end = parse_time_to_seconds(end_raw) if end_raw else None
            if end is not None and end <= start:
                messagebox.showinfo("时间无效", "结束时间需大于起始时间。", parent=dialog)
                return
            result["range"] = (start, end)
            dialog.destroy()

        tk.Button(actions, text="取消", command=dialog.destroy, bd=0, padx=14, pady=6, bg="#eef2f7",
                  fg="#1f2937", font=app_font(9)).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(actions, text="开始剪辑", command=confirm, bd=0, padx=14, pady=6, bg=ACCENT,
                  fg="white", activebackground=ACCENT_HOVER, activeforeground="white",
                  font=app_font(9, "bold")).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Return>", lambda _e: confirm())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        self.window.update_idletasks()
        dialog.update_idletasks()
        w = max(340, dialog.winfo_reqwidth())
        h = dialog.winfo_reqheight()
        x, y = center_over_root(self.app.root, w, h)
        place_toplevel_absolute(dialog, w, h, x, y)
        dialog.attributes("-topmost", True)
        dialog.deiconify()
        dialog.grab_set()
        self.window.wait_window(dialog)
        return result.get("range")

    def _viewer_list(self):
        return getattr(self.app, "media_viewers", [])


class AudioEditorWindow(_FramelessViewer):
    """内置 WAV 音频编辑器：降噪 / 剪辑（裁剪·删除选区）/ 拼接 / 音量增减 / 播放。

    波形上拖动可选区；多数操作作用于选区（无选区则作用于整段）。基于 numpy/scipy，
    用 winsound 即时试听，保存为 16-bit PCM WAV。
    """

    MIN_W = 760
    MIN_H = 480

    def __init__(self, app, path: Path, item: DockItem | None = None):
        import numpy as np
        self._np = np
        self.path = Path(path)
        self.item = item or new_item("file", str(self.path), self.path.name)
        self.sel = None            # (start_sample, end_sample) in self.data 坐标
        self._drag_anchor = None
        self.undo_stack: list = []
        self.wave_photo = None
        try:
            self.data, self.sr = read_wav_float(self.path)
        except Exception as exc:
            messagebox.showinfo("无法打开", f"读取 WAV 失败：\n{self.path}\n\n{exc}", parent=app.root)
            self.closed = True
            return

        body = self._build_frame(app, f"音频编辑 - {self.path.name}")

        ops = tk.Frame(body, bg=TITLE_BG)
        ops.pack(side=tk.TOP, fill=tk.X)
        effects = tk.Frame(body, bg=TITLE_BG)
        effects.pack(side=tk.TOP, fill=tk.X)
        for label, cmd in (
            ("▶ 播放", self.play), ("■ 停止", self.stop),
            ("音量＋", lambda: self.change_volume(1.26)), ("音量－", lambda: self.change_volume(0.79)),
            ("标准化", self.normalize), ("淡入", lambda: self.fade(True)), ("淡出", lambda: self.fade(False)),
        ):
            self._ops_button(ops, label, cmd).pack(side=tk.LEFT, padx=(8 if label == "▶ 播放" else 0, 4), pady=4)
        for label, cmd in (
            ("裁剪到选区", self.trim_to_selection), ("删除选区", self.delete_selection),
            ("拼接WAV…", self.concat_wav), ("降噪", self.denoise),
            ("撤销", self.undo), ("保存为WAV…", self.save_as),
            ("另存为新版本", self.save_as_new_version),
        ):
            self._ops_button(effects, label, cmd).pack(
                side=tk.LEFT, padx=(8 if label == "裁剪到选区" else 0, 4), pady=4)

        self.wave_canvas = tk.Canvas(body, bg="#0f172a", highlightthickness=0, cursor="xterm")
        self.wave_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))
        self.wave_canvas.bind("<Configure>", lambda _e: self._draw_wave())
        self.wave_canvas.bind("<ButtonPress-1>", self._sel_press)
        self.wave_canvas.bind("<B1-Motion>", self._sel_drag)
        self.wave_canvas.bind("<ButtonRelease-1>", self._sel_release)

        self.status_var = tk.StringVar()
        tk.Label(body, textvariable=self.status_var, bg=SURFACE_BG, fg="#334155",
                 anchor=tk.W, font=app_font(9)).pack(side=tk.TOP, fill=tk.X, padx=12, pady=(0, 8))

        self._finish_frame()
        self._update_status()
        self.window.after_idle(self._draw_wave)

    def _ops_button(self, parent, text, command):
        b = tk.Button(parent, text=text, command=command, bd=0, padx=10, pady=5,
                      bg=TITLE_BUTTON_BG, fg="#cdd8ec", activebackground=TITLE_BUTTON_HOVER, activeforeground="#ffffff",
                      font=app_font(9), cursor="hand2")
        b.bind("<Enter>", lambda _e: b.configure(bg=TITLE_BUTTON_HOVER))
        b.bind("<Leave>", lambda _e: b.configure(bg=TITLE_BUTTON_BG))
        return b

    # -- 时长 / 选区 ---------------------------------------------------
    def _duration(self) -> float:
        return len(self.data) / float(self.sr) if self.sr else 0.0

    def _fmt_time(self, samples: int) -> str:
        s = samples / float(self.sr) if self.sr else 0.0
        return f"{int(s // 60):d}:{s % 60:05.2f}"

    def _update_status(self) -> None:
        ch = self.data.shape[1] if self.data.ndim > 1 else 1
        msg = f"{self.sr} Hz · {ch} 声道 · 时长 {self._fmt_time(len(self.data))}"
        if self.sel:
            msg += f"　选区 {self._fmt_time(self.sel[0])}–{self._fmt_time(self.sel[1])}"
        else:
            msg += "　（在波形上拖动以选区；无选区时操作作用于整段）"
        self.status_var.set(msg)

    def _x_to_sample(self, x: float) -> int:
        w = max(1, self.wave_canvas.winfo_width())
        return int(max(0, min(len(self.data), x / w * len(self.data))))

    def _sel_press(self, event) -> None:
        self._drag_anchor = self._x_to_sample(event.x)

    def _sel_drag(self, event) -> None:
        if self._drag_anchor is None:
            return
        cur = self._x_to_sample(event.x)
        lo, hi = sorted((self._drag_anchor, cur))
        self.sel = (lo, hi) if hi - lo > 0 else None
        self._draw_selection()  # 仅重画选区矩形，波形包络不重算（拖动更流畅）

    def _sel_release(self, event) -> None:
        if self._drag_anchor is not None:
            cur = self._x_to_sample(event.x)
            lo, hi = sorted((self._drag_anchor, cur))
            self.sel = (lo, hi) if hi - lo > self.sr * 0.005 else None
        self._drag_anchor = None
        self._update_status()
        self._draw_selection()

    def _region(self):
        """当前作用区间（选区或整段）。返回 (start, end)。"""
        return self.sel if self.sel else (0, len(self.data))

    # -- 波形绘制 ------------------------------------------------------
    def _draw_wave(self) -> None:
        """重画整条波形包络（仅在缩放/数据变化时调用）+ 选区。

        包络用「单个多边形」绘制（1 个画布项），而非每像素一条线（数百个项），
        Tk 绘制开销大幅下降。选区单独成项，拖动时只重画它。
        """
        np = self._np
        c = self.wave_canvas
        c.delete("wave")
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        mid = h / 2
        c.create_line(0, mid, w, mid, fill="#1e293b", tags="wave")
        if len(self.data):
            mono = self.data.mean(axis=1) if self.data.ndim > 1 else self.data
            n = len(mono)
            if n >= w:
                # NumPy 分箱归并取代逐像素 Python 循环，长音频绘制提速明显。
                idx = np.linspace(0, n, w + 1, dtype=np.int64)
                hi = np.maximum.reduceat(mono, idx[:-1])
                lo = np.minimum.reduceat(mono, idx[:-1])
            else:
                # 样本数少于画布像素时直接插值，避免 reduceat 重复下标。
                positions = np.linspace(0, max(0, n - 1), w)
                hi = lo = np.interp(positions, np.arange(n), mono)
            xs = np.arange(w, dtype=np.float64)
            top = np.column_stack((xs, mid - hi * mid * 0.95)).ravel().tolist()
            bot = np.column_stack((xs, mid - lo * mid * 0.95)).ravel().tolist()
            # 上沿正序 + 下沿逆序 → 闭合多边形。
            poly = top + bot[::-1]
            if len(poly) >= 6:
                c.create_polygon(*poly, fill="#38bdf8", outline="", tags="wave")
        self._draw_selection()

    def _draw_selection(self) -> None:
        c = self.wave_canvas
        c.delete("sel")
        if not self.sel or len(self.data) == 0:
            return
        w = max(1, c.winfo_width())
        h = max(1, c.winfo_height())
        n = len(self.data)
        x0 = self.sel[0] / n * w
        x1 = self.sel[1] / n * w
        c.create_rectangle(x0, 0, x1, h, outline="#f59e0b", fill="#f59e0b",
                           stipple="gray25", width=1, tags="sel")

    # -- 编辑操作 ------------------------------------------------------
    def _push_undo(self) -> None:
        self.undo_stack.append((self.data.copy(), self.sel))
        if len(self.undo_stack) > 15:
            self.undo_stack.pop(0)

    def undo(self) -> None:
        if not self.undo_stack:
            self.app.write_status("没有可撤销的操作。")
            return
        self.data, self.sel = self.undo_stack.pop()
        self._draw_wave()
        self._update_status()

    def change_volume(self, gain: float) -> None:
        np = self._np
        self._push_undo()
        s, e = self._region()
        self.data[s:e] = np.clip(self.data[s:e] * gain, -1.0, 1.0)
        self._draw_wave()
        self.app.write_status(f"音量 ×{gain:.2f}{'（选区）' if self.sel else ''}")

    def normalize(self) -> None:
        """将选区（或整段）峰值标准化到 -1 dBFS。"""
        np = self._np
        s, e = self._region()
        region = self.data[s:e]
        if len(region) == 0:
            return
        peak = float(np.max(np.abs(region)))
        if peak < 1e-7:
            self.app.write_status("该区域是静音，无法标准化。")
            return
        self._push_undo()
        target = 10 ** (-1.0 / 20.0)
        self.data[s:e] = np.clip(region * (target / peak), -1.0, 1.0)
        self._draw_wave()
        self.app.write_status(f"已标准化到 -1 dBFS{'（选区）' if self.sel else ''}。")

    def fade(self, fade_in: bool) -> None:
        """对选区（或整段）应用线性淡入/淡出。"""
        np = self._np
        s, e = self._region()
        length = e - s
        if length < 2:
            return
        self._push_undo()
        ramp = np.linspace(0.0, 1.0, length, dtype=np.float32)
        if not fade_in:
            ramp = ramp[::-1]
        self.data[s:e] *= ramp[:, None]
        self._draw_wave()
        self.app.write_status(f"已应用{'淡入' if fade_in else '淡出'}{'（选区）' if self.sel else ''}。")

    def trim_to_selection(self) -> None:
        if not self.sel:
            messagebox.showinfo("裁剪", "请先在波形上拖动选择一段。", parent=self.window)
            return
        self._push_undo()
        self.data = self.data[self.sel[0]:self.sel[1]]
        self.sel = None
        self._draw_wave()
        self._update_status()
        self.app.write_status("已裁剪到选区。")

    def delete_selection(self) -> None:
        np = self._np
        if not self.sel:
            messagebox.showinfo("删除选区", "请先在波形上拖动选择一段。", parent=self.window)
            return
        self._push_undo()
        self.data = np.concatenate([self.data[:self.sel[0]], self.data[self.sel[1]:]], axis=0)
        self.sel = None
        self._draw_wave()
        self._update_status()
        self.app.write_status("已删除选区。")

    def concat_wav(self) -> None:
        np = self._np
        other = filedialog.askopenfilename(
            title="选择要拼接到末尾的 WAV", filetypes=[("WAV", "*.wav"), ("所有文件", "*.*")],
            parent=self.window,
        )
        if not other:
            return
        try:
            data2, sr2 = read_wav_float(other)
        except Exception as exc:
            messagebox.showinfo("拼接失败", f"无法读取：\n{other}\n\n{exc}", parent=self.window)
            return
        # 采样率对齐。
        if sr2 != self.sr:
            from scipy.signal import resample
            new_len = int(round(len(data2) * self.sr / sr2))
            data2 = resample(data2, new_len, axis=0).astype(np.float32)
        # 声道对齐。
        ch = self.data.shape[1]
        if data2.shape[1] != ch:
            if ch == 1:
                data2 = data2.mean(axis=1, keepdims=True)
            else:
                data2 = np.repeat(data2[:, :1], ch, axis=1)
        self._push_undo()
        self.data = np.concatenate([self.data, data2.astype(np.float32)], axis=0)
        self._draw_wave()
        self._update_status()
        self.app.write_status(f"已拼接：{Path(other).name}")

    def denoise(self) -> None:
        # 谱减法在长音频上较耗时，放到后台线程跑，避免界面卡死。
        if getattr(self, "_dn_busy", False):
            return
        self._dn_busy = True
        self._push_undo()
        sel = self.sel
        hint = "（以选区为噪声样本）" if sel else "（自动估计噪声）"
        self.status_var.set(f"正在降噪{hint}…")
        import queue as _queue
        self._dn_queue = _queue.Queue(maxsize=1)
        # _push_undo 已经保留了独立快照，直接作为工作线程输入，避免大音频再复制一遍。
        data_in, sr = self.undo_stack[-1][0], self.sr
        np = self._np

        def worker() -> None:
            try:
                self._dn_queue.put((True, denoise_audio(data_in, sr, noise_range=sel).astype(np.float32)))
            except Exception as exc:
                self._dn_queue.put((False, str(exc)))

        threading.Thread(target=worker, name="Passer-denoise", daemon=True).start()
        self._poll_denoise(hint)

    def _poll_denoise(self, hint: str) -> None:
        try:
            ok, payload = self._dn_queue.get_nowait()
        except Exception:
            if not self.closed:
                self.window.after(120, lambda: self._poll_denoise(hint))
            return
        self._dn_busy = False
        if not ok:
            if self.undo_stack:
                self.undo_stack.pop()
            messagebox.showinfo("降噪失败", str(payload), parent=self.window)
            self._update_status()
            return
        self.data = payload
        self._draw_wave()
        self._update_status()
        self.app.write_status(f"已降噪 {hint}")

    # -- 播放（winsound 内存 WAV）-------------------------------------
    def play(self) -> None:
        try:
            import winsound
            s, e = self._region()
            seg = self.data[s:e]
            if len(seg) == 0:
                return
            winsound.PlaySound(wav_bytes(seg, self.sr), winsound.SND_MEMORY | winsound.SND_ASYNC)
            self.app.write_status("播放中…" + ("（选区）" if self.sel else "（整段）"))
        except Exception as exc:
            messagebox.showinfo("播放失败", str(exc), parent=self.window)

    def stop(self) -> None:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def save_as(self) -> None:
        self.stop()
        out = filedialog.asksaveasfilename(
            title="保存为 WAV", defaultextension=".wav",
            initialdir=str(self.path.parent), initialfile=f"{self.path.stem}_编辑.wav",
            filetypes=[("WAV", "*.wav")], parent=self.window,
        )
        if not out:
            return
        try:
            write_wav_int16(str(out), self.data, self.sr)
        except Exception as exc:
            messagebox.showinfo("保存失败", f"无法保存：\n{out}\n\n{exc}", parent=self.window)
            return
        try:
            self.app.add_entries([new_item("file", str(out), Path(out).name)], force_new=True)
        except Exception:
            pass
        self.app.write_status(f"已保存：{Path(out).name}　时长 {self._fmt_time(len(self.data))}")

    def save_as_new_version(self) -> None:
        self.stop()
        self.app.save_item_as_new_version(
            self.item,
            writer=lambda destination: write_wav_int16(str(destination), self.data, self.sr),
            suffix=".wav",
            source_path=self.path,
        )

    def close(self) -> None:
        if getattr(self, "closed", False):
            return
        self.stop()
        super().close()

    def _viewer_list(self):
        return getattr(self.app, "audio_editors", [])


class ArchiveViewer(_FramelessViewer):
    """压缩包预览器：列出 zip / tar(.gz/.bz2/.xz) 内容，支持解压全部。

    rar / 7z 缺少解压库时，显示提示并提供用默认程序打开。
    """

    MIN_W = 620
    MIN_H = 420

    def __init__(self, app, item: DockItem):
        self.item = item
        self.path = Path(item.target)
        body = self._build_frame(app, self.path.name)
        self._button(self.toolbar_left, "解压全部…", self.extract_all).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(self.toolbar_left, "默认程序打开", self.open_external).pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self._button(self.toolbar_left, "另存为新版本", self.save_source_as_new_version).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        columns = ("size", "csize", "mtime")
        self.tree = ttk.Treeview(body, columns=columns, show="tree headings")
        self.tree.heading("#0", text="名称")
        self.tree.heading("size", text="大小")
        self.tree.heading("csize", text="压缩后")
        self.tree.heading("mtime", text="修改时间")
        self.tree.column("#0", width=320, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)
        self.tree.column("csize", width=90, anchor=tk.E)
        self.tree.column("mtime", width=140, anchor=tk.W)
        vsb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._native = self.path.suffix.lower() in ARCHIVE_NATIVE_EXTS or self._is_zip()
        self._populate()
        self._finish_frame()

    @staticmethod
    def _human(n) -> str:
        try:
            n = float(n)
        except (TypeError, ValueError):
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return ""

    def _is_zip(self) -> bool:
        import zipfile
        try:
            return zipfile.is_zipfile(self.path)
        except OSError:
            return False

    def _read_entries(self) -> list[tuple[str, int, int, str]]:
        """返回 (名称, 原始大小, 压缩后大小, 修改时间)。压缩后大小未知用 -1。"""
        import zipfile
        import tarfile
        entries: list[tuple[str, int, int, str]] = []
        if self._is_zip():
            with zipfile.ZipFile(self.path) as zf:
                for zi in zf.infolist():
                    dt = ("%04d-%02d-%02d %02d:%02d" % zi.date_time[:5]) if zi.date_time else ""
                    entries.append((zi.filename, zi.file_size, zi.compress_size, dt))
            return entries
        try:
            with tarfile.open(self.path) as tf:
                for m in tf.getmembers():
                    mt = datetime.fromtimestamp(m.mtime).strftime("%Y-%m-%d %H:%M") if m.mtime else ""
                    entries.append((m.name + ("/" if m.isdir() else ""), m.size, -1, mt))
        except (tarfile.TarError, OSError):
            return []
        return entries

    def _populate(self) -> None:
        if not self._native:
            self.tree.insert("", tk.END, text=f"  {self.path.suffix.upper().lstrip('.')} 暂不支持内置预览（缺少解压库）", values=("", "", ""))
            self.tree.insert("", tk.END, text="  可点「默认程序打开」用系统解压工具查看。", values=("", "", ""))
            self.info_var.set(self.path.name)
            return
        try:
            entries = self._read_entries()
        except Exception as exc:
            self.tree.insert("", tk.END, text=f"  读取失败：{exc}", values=("", "", ""))
            return
        total = 0
        for name, size, csize, mtime in entries:
            total += max(0, size)
            self.tree.insert("", tk.END, text=name,
                             values=(self._human(size) if not name.endswith("/") else "",
                                     self._human(csize) if csize >= 0 else "",
                                     mtime))
        self.info_var.set(f"{self.path.name}　共 {len(entries)} 项，原始合计 {self._human(total)}")

    def extract_all(self) -> None:
        if not self._native:
            messagebox.showinfo("无法解压", "该格式需要系统解压工具，请用「默认程序打开」。", parent=self.window)
            return
        target = filedialog.askdirectory(
            title="选择解压到的目录", initialdir=str(self.path.parent), parent=self.window
        )
        if not target:
            return
        out_dir = unique_path(Path(target), self.path.stem, "")
        import zipfile
        import tarfile
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            if self._is_zip():
                with zipfile.ZipFile(self.path) as zf:
                    zf.extractall(out_dir)
            else:
                with tarfile.open(self.path) as tf:
                    tf.extractall(out_dir)
        except Exception as exc:
            messagebox.showinfo("解压失败", f"无法解压：\n{exc}", parent=self.window)
            return
        self.app.add_entries([new_item("folder", str(out_dir), out_dir.name)], force_new=True)
        self.app.write_status(f"已解压到：{out_dir.name}")

    def open_external(self) -> None:
        try:
            open_target(self.item)
            self.app.write_status(f"已用默认程序打开：{self.path.name}")
        except Exception as exc:
            messagebox.showinfo("打开失败", f"无法打开：\n{self.path}\n\n{exc}", parent=self.window)

    def _viewer_list(self):
        return getattr(self.app, "archive_viewers", [])


class PinnedImageWindow:
    """A borderless, always-on-top image stuck to the desktop (Snipaste-style)."""

    instances: list["PinnedImageWindow"] = []

    def __init__(self, app, image: "Image.Image", x: int, y: int):
        self.app = app
        self.image = image.convert("RGB")
        self.window = tk.Toplevel(app.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=ACCENT)
        self.photo = ImageTk.PhotoImage(self.image)
        self.label = tk.Label(self.window, image=self.photo, bd=0, highlightthickness=0, cursor="fleur")
        self.label.pack(padx=2, pady=2)
        place_toplevel_absolute(self.window, self.image.width + 4, self.image.height + 4, int(x) - 2, int(y) - 2)

        self.move_start = None
        for widget in (self.window, self.label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)
            widget.bind("<Double-Button-1>", lambda event: self.close())
            widget.bind("<Button-3>", self.popup_menu)
        self.window.bind("<Escape>", lambda event: self.close())

        self.menu = tk.Menu(self.window, tearoff=False)
        self.menu.add_command(label="复制图片", command=self.copy)
        self.menu.add_command(label="关闭", command=self.close)

        PinnedImageWindow.instances.append(self)
        self.window.focus_force()

    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        place_toplevel_absolute(
            self.window, self.window.winfo_width(), self.window.winfo_height(),
            wx + event.x_root - sx, wy + event.y_root - sy,
        )

    def popup_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def copy(self) -> None:
        copy_image_to_clipboard(self.image)

    def close(self) -> None:
        try:
            PinnedImageWindow.instances.remove(self)
        except ValueError:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass


class _ScreenOverlayBase:
    """Shared fullscreen frozen-screen canvas for annotate / screenshot overlays."""

    def __init__(self, app, cursor: str, dim: float = 0.0):
        self.app = app
        self.closed = False
        self.screen = capture_virtual_screen()
        if self.screen is None:
            raise RuntimeError("无法捕获屏幕。")
        self.origin = virtual_screen_origin()
        width, height = self.screen.size

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, width=width, height=height, highlightthickness=0, bd=0, cursor=cursor, bg="#000000")
        self.canvas.pack()

        base = self.screen
        if dim > 0:
            base = Image.blend(self.screen, Image.new("RGB", self.screen.size, (0, 0, 0)), dim)
        self.bg_photo = ImageTk.PhotoImage(base)
        self.canvas.create_image(0, 0, image=self.bg_photo, anchor="nw", tags="bg")

        place_toplevel_absolute(self.window, width, height, self.origin[0], self.origin[1])
        self.window.deiconify()
        self.window.focus_force()
        self.annotator = AnnotationController(self.canvas, lambda: (1.0, 0.0, 0.0))

    def _identity_close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        except Exception:
            pass


class FullscreenAnnotateOverlay(_ScreenOverlayBase):
    """Full-screen red-pen annotation over a frozen desktop. ESC exits."""

    def __init__(self, app):
        super().__init__(app, cursor="pencil", dim=0.0)
        self.annotator.set_enabled(True)
        self.canvas.bind("<ButtonPress-1>", self.annotator.on_press)
        self.canvas.bind("<B1-Motion>", self.annotator.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.annotator.on_release)
        self.window.bind("<Escape>", lambda event: self.close())

        bar = make_annotation_toolbar(self.canvas, self.annotator, on_extra=[("复制", self.copy), ("退出", self.close)])
        tx, ty = self.app_monitor_top_center()
        self.canvas.create_window(tx, ty, window=bar, anchor="n")
        app.fullscreen_overlay = self

    def app_monitor_top_center(self) -> tuple[int, int]:
        """Top-centre of the monitor holding the main window, in canvas coordinates."""
        area = root_monitor_work_area(self.app.root)
        ox, oy = self.origin
        if area:
            left, top, right, bottom = area
            return (left + right) // 2 - ox, top - oy + 12
        return self.screen.width // 2, 12

    def copy(self) -> None:
        image = self.screen if self.annotator.model.is_empty() else self.annotator.render_to_pil(self.screen)
        if copy_image_to_clipboard(image):
            self.app.write_status("已复制全屏批注。")

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.app.fullscreen_overlay is self:
                self.app.fullscreen_overlay = None
        except Exception:
            pass
        self._identity_close()


class ScreenshotOverlay(_ScreenOverlayBase):
    """Drag-select a region, annotate, then pin / load / copy / discard."""

    DIM_AMOUNT = 0.45

    def __init__(self, app):
        super().__init__(app, cursor="cross", dim=self.DIM_AMOUNT)
        self.sel: tuple[float, float, float, float] | None = None
        self.sel_start = None
        self.dragging = False
        self.committed = False
        self.annotating = False
        self.toolbar = None
        self.toolbar_win = None
        self.edit_strip = None
        self.edit_strip_win = None
        self.anno_btn = None
        self.selection_photo = None
        self.right_cancel_pending = False
        self._redraw_pending = False
        self.current_hex = None
        self._mag_photo = None

        # Background is already dimmed by the base overlay (dim=DIM_AMOUNT), so we
        # avoid building a second full-screen PhotoImage here — a big latency win
        # on multi-monitor / high-DPI setups.

        # Snapshot of window rectangles (in canvas coords) for WeChat-style snapping.
        ox, oy = self.origin
        exclude = [
            native_window_handle(self.window),
            self.window.winfo_id(),
            native_window_handle(self.app.root),
        ]
        self.window_rects = [
            (left - ox, top - oy, right - ox, bottom - oy)
            for (left, top, right, bottom) in enumerate_window_rects(exclude)
        ]

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress-3>", self.on_cancel_right_click)
        self.canvas.bind("<ButtonRelease-3>", self.on_cancel_right_click)
        self.canvas.bind("<Button-3>", self.on_cancel_right_click)
        self.window.bind("<ButtonPress-3>", self.on_cancel_right_click)
        self.window.bind("<ButtonRelease-3>", self.on_cancel_right_click)
        self.window.bind("<Button-3>", self.on_cancel_right_click)
        self.window.bind("<Escape>", lambda event: self.cancel())
        self.window.bind("<Control-c>", self._copy_color)
        self.window.bind("<Control-C>", self._copy_color)
        app.screenshot_overlay = self
        self.redraw_selection()  # start fully dimmed

    # -- snap-to-window ------------------------------------------------
    def window_rect_at(self, cx, cy) -> tuple[float, float, float, float] | None:
        for left, top, right, bottom in self.window_rects:
            if left <= cx <= right and top <= cy <= bottom:
                return (
                    max(0, left), max(0, top),
                    min(self.screen.width, right), min(self.screen.height, bottom),
                )
        return None

    def on_motion(self, event) -> None:
        if not self.committed and not self.annotating:
            self._update_magnifier(event.x, event.y)
        if self.committed or self.annotating or self.dragging:
            return
        rect = self.window_rect_at(event.x, event.y)
        if rect == self.sel:
            return
        self.sel = rect
        self.request_redraw_selection()

    # -- selection / annotation routing -------------------------------
    def on_press(self, event) -> None:
        if self.annotating and self.annotator.on_press(event):
            return
        if self.committed:
            self.committed = False
            self.hide_toolbar()
        self.sel_start = (event.x, event.y)
        self.dragging = False

    def on_drag(self, event) -> None:
        if self.annotating and self.annotator.on_drag(event):
            return
        if not self.sel_start:
            return
        x0, y0 = self.sel_start
        if abs(event.x - x0) > 4 or abs(event.y - y0) > 4:
            self.dragging = True
        if self.dragging:
            self.sel = (min(x0, event.x), min(y0, event.y), max(x0, event.x), max(y0, event.y))
            self.request_redraw_selection()
            if not self.annotating:
                self._update_magnifier(event.x, event.y)

    def on_release(self, event) -> None:
        if self.annotating and self.annotator.on_release(event):
            return
        if not self.sel_start:
            return
        self.sel_start = None
        self.dragging = False
        # A plain click (no drag) keeps the snapped window rect set during hover.
        if self.sel and (self.sel[2] - self.sel[0] > 4) and (self.sel[3] - self.sel[1] > 4):
            self.committed = True
            self.redraw_selection()
            self.show_toolbar()
        else:
            self.sel = None
            self.redraw_selection()

    def request_redraw_selection(self) -> None:
        """Coalesce rapid motion events into one redraw per idle cycle.

        Each redraw crops the frozen screen and builds a fresh PhotoImage, which
        is costly for large regions; collapsing a burst of B1-Motion events into
        a single idle-time redraw keeps dragging the selection smooth.
        """
        if self._redraw_pending or self.closed:
            return
        self._redraw_pending = True
        try:
            self.canvas.after_idle(self._flush_redraw_selection)
        except Exception:
            self._redraw_pending = False
            self.redraw_selection()

    def _flush_redraw_selection(self) -> None:
        self._redraw_pending = False
        if not self.closed:
            self.redraw_selection()

    def redraw_selection(self) -> None:
        self.canvas.delete("bright_sel")
        self.canvas.delete("selrect")
        self.canvas.delete("sizetag")
        width, height = self.screen.size
        if not self.sel:
            self.selection_photo = None
            self.canvas.tag_raise("anno")
            self.canvas.tag_raise("mag")
            return
        x0, y0, x1, y1 = (int(v) for v in self.sel)
        x0 = max(0, min(width, x0))
        y0 = max(0, min(height, y0))
        x1 = max(0, min(width, x1))
        y1 = max(0, min(height, y1))
        if x1 <= x0 or y1 <= y0:
            self.selection_photo = None
            self.canvas.tag_raise("anno")
            return
        self.selection_photo = ImageTk.PhotoImage(self.screen.crop((x0, y0, x1, y1)))
        self.canvas.create_image(x0, y0, image=self.selection_photo, anchor="nw", tags="bright_sel")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=ACCENT, width=2, tags="selrect")
        self.canvas.create_text(
            x0 + 5, max(2, y0 - 16), anchor="nw", fill="#ffffff",
            text=f"{x1 - x0} × {y1 - y0}", tags="sizetag", font=app_font(9, "bold"),
        )
        self.canvas.tag_raise("bright_sel")
        self.canvas.tag_raise("anno")
        self.canvas.tag_raise("selrect")
        self.canvas.tag_raise("sizetag")
        self.canvas.tag_raise("mag")

    # -- magnifier / color picker -------------------------------------
    MAG_SRC_HALF = 12   # 取色镜源区域半径（像素）
    MAG_ZOOM = 7        # 放大倍数
    MAG_PAD = 14
    MAG_LINE_H = 22

    def _hide_magnifier(self) -> None:
        self.canvas.delete("mag")
        self._mag_photo = None

    def _update_magnifier(self, x: int, y: int) -> None:
        if self.closed or self.committed or self.annotating:
            self._hide_magnifier()
            return
        w, h = self.screen.size
        if not (0 <= x < w and 0 <= y < h):
            self._hide_magnifier()
            return

        half = self.MAG_SRC_HALF
        zoom = self.MAG_ZOOM
        src = 2 * half + 1
        disp = src * zoom

        left, top = x - half, y - half
        right, bottom = left + src, top + src
        if left < 0:
            left, right = 0, src
        if top < 0:
            top, bottom = 0, src
        if right > w:
            right, left = w, w - src
        if bottom > h:
            bottom, top = h, h - src

        crop = self.screen.crop((left, top, right, bottom)).resize((disp, disp), Image.NEAREST)
        self._mag_photo = ImageTk.PhotoImage(crop)

        pixel = self.screen.getpixel((x, y))
        r, g, b = (int(c) for c in pixel[:3])
        self.current_hex = f"#{r:02X}{g:02X}{b:02X}"
        abs_x, abs_y = self.origin[0] + x, self.origin[1] + y

        pad = self.MAG_PAD
        line_h = self.MAG_LINE_H
        card_w = disp + 2 * pad
        card_h = pad + disp + 12 + line_h * 2 + 8 + pad

        # 默认放在光标右下角，贴近屏幕边缘时翻转。
        cx, cy = x + 22, y + 22
        if cx + card_w > w:
            cx = x - 22 - card_w
        if cx < 0:
            cx = 4
        if cy + card_h > h:
            cy = y - 22 - card_h
        if cy < 0:
            cy = 4

        self.canvas.delete("mag")
        self.canvas.create_rectangle(
            cx, cy, cx + card_w, cy + card_h,
            fill="#ffffff", outline="#cbd5e1", width=1, tags="mag",
        )
        img_x, img_y = cx + pad, cy + pad
        self.canvas.create_image(img_x, img_y, image=self._mag_photo, anchor="nw", tags="mag")
        self.canvas.create_rectangle(
            img_x, img_y, img_x + disp, img_y + disp,
            outline="#94a3b8", width=1, tags="mag",
        )
        # 十字准星，指向中心像素。
        ch_x = img_x + (x - left) * zoom + zoom // 2
        ch_y = img_y + (y - top) * zoom + zoom // 2
        self.canvas.create_line(img_x, ch_y, img_x + disp, ch_y, fill="#22c55e", width=1, tags="mag")
        self.canvas.create_line(ch_x, img_y, ch_x, img_y + disp, fill="#22c55e", width=1, tags="mag")

        text_x, value_x = cx + pad, cx + pad + 52
        ty = img_y + disp + 12
        self.canvas.create_text(text_x, ty, anchor="nw", fill="#64748b", text="坐标", font=app_font(9), tags="mag")
        self.canvas.create_text(
            value_x, ty, anchor="nw", fill="#0f172a",
            text=f"{abs_x}, {abs_y}", font=app_font(10, "bold"), tags="mag",
        )
        ty += line_h
        self.canvas.create_text(text_x, ty, anchor="nw", fill="#64748b", text="色值", font=app_font(9), tags="mag")
        self.canvas.create_text(
            value_x, ty, anchor="nw", fill="#0f172a",
            text=self.current_hex, font=app_font(10, "bold"), tags="mag",
        )
        ty += line_h
        self.canvas.create_text(
            text_x, ty, anchor="nw", fill="#94a3b8",
            text="按 Ctrl+C 复制色值", font=app_font(8), tags="mag",
        )

    def _copy_color(self, event=None):
        if not self.current_hex:
            return
        try:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(self.current_hex)
            self.app.write_status(f"已复制色值 {self.current_hex}")
        except Exception:
            pass

    # -- toolbars ------------------------------------------------------
    def show_toolbar(self) -> None:
        self._hide_magnifier()
        self.hide_toolbar()
        bar = tk.Frame(self.canvas, bg=TITLE_BG)

        def make(text: str, command, fg: str = "#e7eefc") -> tk.Button:
            button = tk.Button(
                bar, text=text, command=command, bd=0, padx=11, pady=5,
                bg=TITLE_BUTTON_BG, fg=fg, activebackground=TITLE_BUTTON_HOVER, activeforeground="#ffffff",
                font=app_font(11, "bold"), cursor="hand2",
            )
            button.pack(side=tk.LEFT, padx=2, pady=2)
            return button

        self.anno_btn = make("✎ 注释", self.toggle_annotate)
        make("📌 置顶", self.do_pin)
        make("⮌ 载入", self.do_load)
        make("✕", self.cancel, fg="#ff6b6b")
        make("✓", self.do_copy, fg="#51cf66")
        self.toolbar = bar

        x0, y0, x1, y1 = (int(v) for v in self.sel)
        self.window.update_idletasks()
        bw = bar.winfo_reqwidth()
        bh = bar.winfo_reqheight()
        tx = max(0, min(x1 - bw, self.screen.width - bw))
        ty = y1 + 8
        if ty + bh > self.screen.height:
            ty = max(0, y0 - bh - 8)
        self.toolbar_win = self.canvas.create_window(tx, ty, window=bar, anchor="nw")

    def hide_toolbar(self) -> None:
        if self.toolbar_win is not None:
            self.canvas.delete(self.toolbar_win)
            self.toolbar_win = None
        if self.toolbar is not None:
            self.toolbar.destroy()
            self.toolbar = None
        self.hide_edit_strip()

    def toggle_annotate(self) -> None:
        self.annotating = not self.annotating
        self.annotator.set_enabled(self.annotating)
        if self.annotating:
            self._hide_magnifier()
            self.canvas.configure(cursor="pencil")
            if self.anno_btn:
                self.anno_btn.configure(bg=TITLE_BUTTON_HOVER)
            self.show_edit_strip()
        else:
            self.canvas.configure(cursor="cross")
            if self.anno_btn:
                self.anno_btn.configure(bg=TITLE_BUTTON_BG)
            self.hide_edit_strip()

    def show_edit_strip(self) -> None:
        self.hide_edit_strip()
        if not self.sel:
            return
        strip = make_annotation_toolbar(self.canvas, self.annotator)
        self.edit_strip = strip
        x0, y0, x1, y1 = (int(v) for v in self.sel)
        self.window.update_idletasks()
        sw = strip.winfo_reqwidth()
        sh = strip.winfo_reqheight()
        tx = max(0, min(x0, self.screen.width - sw))
        ty = max(0, y0 - sh - 8)
        if ty < 2:
            ty = min(self.screen.height - sh, y1 + 48)
        self.edit_strip_win = self.canvas.create_window(tx, ty, window=strip, anchor="nw")

    def hide_edit_strip(self) -> None:
        if self.edit_strip_win is not None:
            self.canvas.delete(self.edit_strip_win)
            self.edit_strip_win = None
        if self.edit_strip is not None:
            self.edit_strip.destroy()
            self.edit_strip = None

    # -- result actions ------------------------------------------------
    def result_image(self) -> "Image.Image | None":
        if not self.sel:
            return None
        x0, y0, x1, y1 = (int(v) for v in self.sel)
        source = self.screen if self.annotator.model.is_empty() else self.annotator.render_to_pil(self.screen)
        return source.crop((x0, y0, x1, y1)).convert("RGB")

    def do_pin(self) -> None:
        image = self.result_image()
        if image is None:
            return
        x0, y0 = (int(v) for v in self.sel[:2])
        vx, vy = self.origin
        self.close()
        PinnedImageWindow(self.app, image, vx + x0, vy + y0)

    def do_load(self) -> None:
        image = self.result_image()
        if image is None:
            return
        self.close()
        self.app.add_screenshot_item(image)

    def do_copy(self) -> None:
        image = self.result_image()
        if image is None:
            return
        self.close()
        if copy_image_to_clipboard(image):
            self.app.write_status("截图已复制到剪贴板。")
        else:
            self.app.write_status("复制截图失败。")

    def cancel(self) -> None:
        self.close()

    def install_right_click_cancel_hook(self) -> None:
        return

    def uninstall_right_click_cancel_hook(self) -> None:
        return

    def on_cancel_right_click(self, event=None):
        if self.closed:
            return "break"
        self.right_cancel_pending = True
        event_type = str(getattr(event, "type", ""))
        is_release = event is None or event_type.endswith("ButtonRelease") or event_type == "5"
        if not is_release:
            return "break"
        try:
            self.window.after(10, self.cancel)
        except Exception:
            self.cancel()
        return "break"

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.app.screenshot_overlay is self:
                self.app.screenshot_overlay = None
        except Exception:
            pass
        self._identity_close()


class WindowsFileDrop:
    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, root: tk.Tk, callback, hotkey_callback=None):
        self.root = root
        self.callback = callback
        self.hotkey_callback = hotkey_callback
        self.enabled = False
        self.old_proc = None
        self.new_proc = None
        if sys.platform != "win32" or TKDND_AVAILABLE or not ENABLE_NATIVE_WM_DROPFILES:
            return
        try:
            self._install()
        except Exception:
            self.enabled = False

    def _install(self) -> None:
        self.root.update_idletasks()
        hwnd = wintypes.HWND(self.root.winfo_id())
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        lresult = ctypes.c_ssize_t
        wndproc_type = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        if ctypes.sizeof(ctypes.c_void_p) == 8:
            set_window_long = user32.SetWindowLongPtrW
        else:
            set_window_long = user32.SetWindowLongW
        set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_window_long.restype = ctypes.c_void_p

        call_window_proc = user32.CallWindowProcW
        call_window_proc.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        call_window_proc.restype = lresult

        def_window_proc = user32.DefWindowProcW
        def_window_proc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        def_window_proc.restype = lresult

        drag_accept_files = shell32.DragAcceptFiles
        drag_accept_files.argtypes = [wintypes.HWND, wintypes.BOOL]

        drag_query_file = shell32.DragQueryFileW
        drag_query_file.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        drag_query_file.restype = wintypes.UINT

        drag_finish = shell32.DragFinish
        drag_finish.argtypes = [wintypes.HANDLE]

        def read_drop(handle) -> list[str]:
            try:
                count = drag_query_file(handle, 0xFFFFFFFF, None, 0)
                paths: list[str] = []
                for index in range(count):
                    length = drag_query_file(handle, index, None, 0)
                    if length <= 0:
                        continue
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    if drag_query_file(handle, index, buffer, length + 1):
                        paths.append(buffer.value)
                return paths
            finally:
                drag_finish(handle)

        def wnd_proc(hwnd_value, msg, wparam, lparam):
            try:
                if msg == self.WM_DROPFILES:
                    paths = read_drop(wparam)
                    if paths:
                        self.root.after(0, lambda copied=list(paths): self.callback(copied))
                    return 0
                if self.old_proc:
                    return call_window_proc(self.old_proc, hwnd_value, msg, wparam, lparam)
                return def_window_proc(hwnd_value, msg, wparam, lparam)
            except Exception:
                return 0

        self.new_proc = wndproc_type(wnd_proc)
        self.old_proc = set_window_long(hwnd, self.GWLP_WNDPROC, ctypes.cast(self.new_proc, ctypes.c_void_p))
        self._accept_files = True
        drag_accept_files(hwnd, bool(self._accept_files))
        self._set_window_long = set_window_long
        self._drag_accept_files = drag_accept_files
        self._hwnd = hwnd
        self.enabled = True

    def register_hotkey(self, hotkey_id: int, modifiers: int, vk: int) -> bool:
        return False

    def unregister_hotkey(self, hotkey_id: int) -> None:
        return

    def close(self) -> None:
        if not self.enabled:
            return
        try:
            self._drag_accept_files(self._hwnd, False)
            self._set_window_long(self._hwnd, self.GWLP_WNDPROC, self.old_proc)
        except Exception:
            pass


class GroupOverlay:
    """Single-click pop-up that shows a group's members.

    Double-click a member to open it; right-click for per-member actions; or
    drag a member out onto the main grid to pull it out of the group (脱出).
    """

    MINI = 104

    def __init__(self, app, group_item: "DockItem"):
        self.app = app
        self.group_id = group_item.id
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.configure(bg=ACCENT)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.drag_member_id: str | None = None
        self.drag_started = False
        self.drag_start_root: tuple[int, int] | None = None
        self.drag_pointer_offset = (self.MINI // 2, self.MINI // 2)
        self.drag_preview_window = None
        self.drag_preview_photo = None
        self.drag_preview_member_id: str | None = None
        self.drag_target_member_id: str | None = None
        self.external_drag_active = False
        self.member_tiles: dict[str, tk.Canvas] = {}
        self.member_photo_refs: list[object] = []
        self._build(group_item)
        self._position(group_item)
        app.keep_window_above_main(self.win)
        self.win.bind("<Escape>", lambda event: self.app.close_group_overlay())

    def _build(self, group_item: "DockItem") -> None:
        outer = tk.Frame(self.win, bg=SURFACE_BG)
        outer.pack(padx=2, pady=2, fill=tk.BOTH, expand=True)

        header = tk.Frame(outer, bg=SURFACE_BG)
        header.pack(fill=tk.X, padx=10, pady=(8, 2))
        tk.Button(
            header, text="✕", command=self.app.close_group_overlay, bd=0, bg=SURFACE_BG,
            fg=MUTED_FG, activebackground=SURFACE_BG, font=app_font(10), cursor="hand2",
        ).pack(side=tk.RIGHT)
        tk.Button(
            header, text="解散组", command=self._dissolve, bd=0, padx=8, pady=2, bg="#eef2f7",
            fg="#1f2937", activebackground="#e2e8f0", font=app_font(8), cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(
            header, text="打开全部", command=self._open_all, bd=0, padx=8, pady=2, bg=ACCENT,
            fg="white", activebackground=ACCENT_HOVER, activeforeground="white",
            font=app_font(8, "bold"), cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(
            header, text="排序", command=self._sort_members, bd=0, padx=8, pady=2,
            bg="#eef2f7", fg="#1f2937", activebackground="#e2e8f0",
            font=app_font(8), cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.grid = tk.Frame(outer, bg=SURFACE_BG)
        self.grid.pack(padx=10, pady=(4, 10))
        self._refresh_member_grid()

    def _refresh_member_grid(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self.member_tiles.clear()
        self.member_photo_refs.clear()
        members = self.app.group_members(self.group_id)
        cols = max(1, min(4, len(members)))
        for index, member in enumerate(members):
            r, c = divmod(index, cols)
            tile = self._build_member_tile(self.grid, member)
            tile.grid(row=r, column=c, padx=4, pady=4)
            self.member_tiles[member.id] = tile

    def _build_member_tile(self, parent, member: "DockItem"):
        tile = tk.Canvas(
            parent, width=self.MINI, height=self.MINI, bg=SURFACE_BG, bd=0,
            highlightthickness=0,
        )
        photo = self._paint_member_tile(tile, member)
        if photo is not None:
            self.member_photo_refs.append(photo)
        tile.bind("<Double-Button-1>", lambda event, m=member: self._open_member(m))
        tile.bind("<Button-3>", lambda event, m=member: self._popup_member_menu(event, m))
        tile.bind("<ButtonPress-1>", lambda event, m=member: self._press(event, m))
        tile.bind("<B1-Motion>", self._motion)
        tile.bind("<ButtonRelease-1>", self._release)
        self._register_member_drag_source(tile, member)
        return tile

    def _paint_member_tile(self, tile, member: "DockItem"):
        tile._member_border_id = create_round_rect(
            tile, 1, 1, self.MINI - 2, self.MINI - 2,
            radius=10, outline=BORDER, width=1, fill=SURFACE_BG,
        )
        # Use a slightly smaller, fixed-size icon so the two-line name below never
        # overlaps it.
        photo = None
        if PIL_AVAILABLE:
            pil = pil_icon_for_item(member, 40)
            if pil is not None:
                photo = ImageTk.PhotoImage(pil)
        if photo is None:
            photo = image_for_item(member)
        if photo is not None:
            tile.create_image(self.MINI // 2, 34, image=photo, anchor=tk.CENTER)
        else:
            tile.create_text(self.MINI // 2, 34, text=self.app.kind_label(member.kind), fill=MUTED_FG, font=app_font(8))
        version_count = self.app.history_version_count(member)
        if version_count:
            tile.create_text(
                self.MINI - 7, 7, text=f"+{version_count}", anchor=tk.NE,
                fill=ACCENT, font=app_font(7, "bold"),
            )
        name = fit_text_lines(member.display_title, self.app.tile_font, self.MINI - 12, max_lines=2)
        tile.create_text(
            self.MINI // 2, 84, text=name, fill="#111827", justify=tk.CENTER,
            width=self.MINI - 10, font=app_font(8),
        )
        return photo

    def _register_member_drag_source(self, tile, member: "DockItem") -> None:
        if not TKDND_AVAILABLE:
            return
        try:
            tile.drag_source_register(1, DND_FILES)
            tile.dnd_bind("<<DragInitCmd>>", lambda event, m=member: self._start_external_drag(event, m))
            tile.dnd_bind("<<DragEndCmd>>", self._end_external_drag)
        except Exception:
            pass

    def _open_member(self, member: "DockItem") -> None:
        self.app.close_group_overlay()
        self.app.open_item(member)

    def _open_all(self) -> None:
        group = self.app.item_by_id(self.group_id)
        if group:
            self.app.open_group_all(group)

    def _sort_members(self) -> None:
        if self.app.sort_group_members_by_filename(self.group_id):
            self._commit_member_order("组内项目已按文件名排序。")
        else:
            self.app.write_status("组内项目已经按文件名排序。")

    def _commit_member_order(self, status: str) -> None:
        self.app.save()
        self.app.refresh_group_tile(self.group_id)
        self._refresh_member_grid()
        group = self.app.item_by_id(self.group_id)
        if group is not None:
            self._position(group)
        self.app.keep_window_above_main(self.win)
        self.app.write_status(status)

    def _dissolve(self) -> None:
        self.app.dissolve_group(self.group_id)
        self.app.save()
        self.app.render_items()
        self.app.write_status("已解散组。")

    def _popup_member_menu(self, event, member: "DockItem") -> None:
        menu = tk.Menu(self.win, tearoff=False)
        menu.add_command(label="打开", command=lambda m=member: self._open_member(m))
        menu.add_command(label="复制", command=lambda m=member: self._copy(m))
        menu.add_command(label="移出组", command=lambda m=member: self._eject(m))
        menu.add_command(label="从 Passer 移除", command=lambda m=member: self._remove(m))
        menu.tk_popup(event.x_root, event.y_root)

    def _copy(self, member: "DockItem") -> None:
        self.app.copy_items_default([member])

    def _eject(self, member: "DockItem") -> None:
        self.app.remove_from_group(member.id)
        self.app.save()
        self.app.render_items()
        self.app.write_status("已移出组。")

    def _remove(self, member: "DockItem") -> None:
        group_id = self.group_id
        self.app.items = [it for it in self.app.items if it.id != member.id]
        self.app.dissolve_group_if_needed(group_id)
        self.app.save()
        self.app.render_items()
        self.app.write_status("已移除 1 项。")

    def _press(self, event, member: "DockItem") -> None:
        self._destroy_drag_preview()
        self._set_drag_target(None)
        self.drag_member_id = member.id
        self.drag_started = False
        self.drag_start_root = (event.x_root, event.y_root)
        try:
            self.drag_pointer_offset = (
                event.x_root - event.widget.winfo_rootx(),
                event.y_root - event.widget.winfo_rooty(),
            )
        except Exception:
            self.drag_pointer_offset = (self.MINI // 2, self.MINI // 2)

    def _motion(self, event):
        if not self.drag_start_root:
            return
        sx, sy = self.drag_start_root
        if abs(event.x_root - sx) > DRAG_THRESHOLD or abs(event.y_root - sy) > DRAG_THRESHOLD:
            self.drag_started = True
        if not self.drag_started:
            return "break"

        member = self.app.item_by_id(self.drag_member_id) if self.drag_member_id else None
        inside_overlay = self._point_in_overlay(event.x_root, event.y_root)
        inside_passer = self.app.is_inside_main_window(event.x_root, event.y_root)
        if inside_overlay:
            target_id = self._member_at_point(event.x_root, event.y_root)
            self._set_drag_target(target_id if target_id != self.drag_member_id else None)
        else:
            self._set_drag_target(None)
        if member is not None and (inside_passer or inside_overlay):
            self._show_drag_preview(member, event.x_root, event.y_root)
            return "break"

        # Let TkDND take over only after the pointer has left Passer. Windows then
        # shows its native copy cursor and copies the file at the external target.
        self._hide_drag_preview()
        return None

    def _show_drag_preview(self, member: "DockItem", x_root: int, y_root: int) -> None:
        if self.drag_preview_window is None or self.drag_preview_member_id != member.id:
            self._destroy_drag_preview()
            preview = tk.Toplevel(self.win)
            preview.withdraw()
            preview.overrideredirect(True)
            preview.configure(bg=SURFACE_BG)
            try:
                preview.attributes("-topmost", True)
                preview.attributes("-alpha", 0.88)
            except Exception:
                pass
            tile = tk.Canvas(
                preview, width=self.MINI, height=self.MINI, bg=SURFACE_BG,
                bd=0, highlightthickness=0,
            )
            tile.pack(fill=tk.BOTH, expand=True)
            self.drag_preview_photo = self._paint_member_tile(tile, member)
            self.drag_preview_window = preview
            self.drag_preview_member_id = member.id

        offset_x, offset_y = self.drag_pointer_offset
        x = int(x_root - offset_x)
        y = int(y_root - offset_y)
        try:
            self.drag_preview_window.geometry(f"{self.MINI}x{self.MINI}+{x}+{y}")
            if self.drag_preview_window.state() != "normal":
                self.drag_preview_window.deiconify()
                self.drag_preview_window.lift()
        except Exception:
            pass

    def _hide_drag_preview(self) -> None:
        if self.drag_preview_window is not None:
            try:
                self.drag_preview_window.withdraw()
            except Exception:
                pass

    def _destroy_drag_preview(self) -> None:
        if self.drag_preview_window is not None:
            try:
                self.drag_preview_window.destroy()
            except Exception:
                pass
        self.drag_preview_window = None
        self.drag_preview_photo = None
        self.drag_preview_member_id = None

    def _start_external_drag(self, event, member: "DockItem"):
        x_root = getattr(event, "x_root", self.app.root.winfo_pointerx())
        y_root = getattr(event, "y_root", self.app.root.winfo_pointery())
        if self._point_in_overlay(x_root, y_root) or self.app.is_inside_main_window(x_root, y_root):
            return REFUSE_DROP
        path = self.app.drag_path_for_item(member)
        if not path:
            self.app.write_status("没有可拖出复制的文件或快捷方式。")
            return REFUSE_DROP
        self.external_drag_active = True
        self._destroy_drag_preview()
        self.app.write_status(f"正在拖出复制：{member.display_title}")
        return ((COPY,), (DND_FILES,), (path,))

    def _end_external_drag(self, event) -> None:
        action = getattr(event, "action", "")
        was_active = self.external_drag_active
        self.external_drag_active = False
        self.drag_member_id = None
        self.drag_started = False
        self.drag_start_root = None
        self._set_drag_target(None)
        self._destroy_drag_preview()
        if was_active and action == COPY:
            self.app.write_status("已拖出复制，组内文件保持不变。")

    def _release(self, event) -> None:
        member_id = self.drag_member_id
        started = self.drag_started
        target_member_id = self._member_at_point(event.x_root, event.y_root)
        self._destroy_drag_preview()
        self._set_drag_target(None)
        self.drag_member_id = None
        self.drag_started = False
        self.drag_start_root = None
        if not member_id or not started:
            return
        if self._point_in_overlay(event.x_root, event.y_root):
            if (
                target_member_id
                and target_member_id != member_id
                and self.app.move_group_member(self.group_id, member_id, target_member_id)
            ):
                self._commit_member_order("已调整组内顺序。")
            return
        if self.app.is_inside_main_window(event.x_root, event.y_root):
            cx = event.x_root - self.app.content.winfo_rootx()
            cy = event.y_root - self.app.content.winfo_rooty()
            col, row = self.app.slot_from_pixel_position(cx, cy)
            self.app.remove_from_group(member_id, col, row)
            self.app.save()
            self.app.render_items()
            self.app.write_status("已移出组。")

    def _member_at_point(self, x_root: int, y_root: int) -> str | None:
        for member_id, tile in self.member_tiles.items():
            try:
                if not tile.winfo_ismapped():
                    continue
                left = tile.winfo_rootx()
                top = tile.winfo_rooty()
                if left <= x_root < left + tile.winfo_width() and top <= y_root < top + tile.winfo_height():
                    return member_id
            except tk.TclError:
                continue
        return None

    def _set_drag_target(self, member_id: str | None) -> None:
        if self.drag_target_member_id == member_id:
            return
        self.drag_target_member_id = member_id
        for current_id, tile in self.member_tiles.items():
            border_id = getattr(tile, "_member_border_id", None)
            if border_id is None:
                continue
            try:
                active = current_id == member_id
                tile.itemconfigure(
                    border_id,
                    outline=(ACCENT if active else BORDER),
                    width=(2 if active else 1),
                )
            except tk.TclError:
                continue

    def _point_in_overlay(self, x: int, y: int) -> bool:
        try:
            left = self.win.winfo_rootx()
            top = self.win.winfo_rooty()
            right = left + self.win.winfo_width()
            bottom = top + self.win.winfo_height()
        except Exception:
            return False
        return left <= x < right and top <= y < bottom

    def _position(self, group_item: "DockItem") -> None:
        self.win.update_idletasks()
        width = self.win.winfo_reqwidth()
        height = self.win.winfo_reqheight()
        widgets = self.app.tile_widgets.get(group_item.id)
        if widgets:
            tile = widgets[0]
            x = tile.winfo_rootx()
            y = tile.winfo_rooty() + tile.winfo_height() + 6
            anchor_x = tile.winfo_rootx() + max(tile.winfo_width(), 1) // 2
            anchor_y = tile.winfo_rooty() + max(tile.winfo_height(), 1) // 2
        else:
            x = self.app.root.winfo_rootx() + 40
            y = self.app.root.winfo_rooty() + 80
            anchor_x, anchor_y = x, y
        # Clamp inside the monitor the group tile lives on. winfo_screenwidth/
        # height only describe the primary monitor, so on a secondary display
        # they would yank the pop-up back onto the primary one.
        area = point_monitor_work_area(anchor_x, anchor_y)
        if area:
            left, top, right, bottom = area
        else:
            left, top = 0, 0
            right = self.win.winfo_screenwidth()
            bottom = self.win.winfo_screenheight()
        x = max(left + 4, min(x, right - width - 4))
        y = max(top + 4, min(y, bottom - height - 4))
        self.win.geometry(f"+{x}+{y}")

    def destroy(self) -> None:
        self._destroy_drag_preview()
        self.member_photo_refs.clear()
        try:
            self.win.destroy()
        except Exception:
            pass


class MultiFilePicker:
    """Modal dialog to tick any mix of files and folders across folders at once.

    Windows' native dialogs can't multi-select files and folders together, so
    this provides a single browser where every checked item — file or folder —
    is collected, even across navigation. Returns ``self.result`` as a list of
    absolute path strings, or None on cancel.
    """

    CHECK_ON = "☑"
    CHECK_OFF = "☐"

    def __init__(self, app: "RelayDockApp", start_dir: str | None = None):
        self.app = app
        self.result: list[str] | None = None
        self.selected: dict[str, bool] = {}  # ordered set of chosen absolute paths
        try:
            start = Path(start_dir) if start_dir else Path.home()
            if not start.is_dir():
                start = Path.home()
        except Exception:
            start = Path.home()
        self.current = start

        self.win = tk.Toplevel(app.root)
        self.win.title("添加文件 / 文件夹")
        self.win.configure(bg=APP_BG)
        self.win.transient(app.root)
        w, h = 1320, 1080  # 2x the original 660x540
        # Keep it on Passer's own monitor: clamp the size to that monitor's work
        # area, centre over the Passer window, then place it there explicitly so
        # the window manager can't fling it onto another display.
        area = root_monitor_work_area(app.root)
        if area:
            left, top, right, bottom = area
            w = min(w, right - left - 40)
            h = min(h, bottom - top - 40)
        x, y = center_over_root(app.root, w, h)
        self.win.geometry(tk_geometry(w, h, x, y))
        self.win.minsize(520, 380)
        place_toplevel_absolute(self.win, w, h, x, y)

        self._build()
        self._populate()
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)
        self.win.bind("<Escape>", lambda e: self._cancel())
        self.win.after(10, self._focus_self)
        self.win.grab_set()
        app.root.wait_window(self.win)

    def _focus_self(self) -> None:
        try:
            self.win.lift()
            self.win.focus_force()
        except Exception:
            pass

    # -- UI ----------------------------------------------------------------
    def _build(self) -> None:
        nav = tk.Frame(self.win, bg=APP_BG)
        nav.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 6))

        tk.Button(nav, text="⬆ 上一级", command=self._go_up, bd=0, relief=tk.FLAT,
                  bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                  padx=10, pady=4, cursor="hand2", font=app_font(10)).pack(side=tk.LEFT)

        drives = self._drives()
        if drives:
            self.drive_var = tk.StringVar()
            combo = ttk.Combobox(nav, values=drives, width=5, state="readonly",
                                 textvariable=self.drive_var)
            combo.pack(side=tk.LEFT, padx=(8, 0))
            combo.bind("<<ComboboxSelected>>", lambda e: self._go_to(Path(self.drive_var.get())))

        self.path_var = tk.StringVar(value=str(self.current))
        entry = tk.Entry(nav, textvariable=self.path_var, font=app_font(10),
                         relief=tk.FLAT, bg="white")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=3)
        entry.bind("<Return>", lambda e: self._go_to(Path(self.path_var.get().strip())))

        mid = tk.Frame(self.win, bg="white", highlightthickness=1, highlightbackground=BORDER)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10)
        self.canvas = tk.Canvas(mid, bg="white", bd=0, highlightthickness=0)
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        # Inner frame that actually holds the icon cells.
        self.grid_frame = tk.Frame(self.canvas, bg="white")
        self._grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>",
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.grid_frame.bind("<MouseWheel>", self._on_mousewheel)

        # Small-icons grid state.
        self.cells: list[tuple[str, tk.Frame, bool]] = []  # (path, frame, is_dir)
        self._icon_cache: dict = {}
        self._cell_w = 230   # width of one small-icon cell
        self._cols = 1

        bottom = tk.Frame(self.win, bg=APP_BG)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        self.count_label = tk.Label(bottom, text="已选 0 项", bg=APP_BG, fg=MUTED_FG,
                                    font=app_font(10))
        self.count_label.pack(side=tk.LEFT)
        tk.Button(bottom, text="清空", command=self._clear, bd=0, relief=tk.FLAT,
                  bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                  padx=12, pady=5, cursor="hand2", font=app_font(10)).pack(side=tk.LEFT, padx=(10, 0))
        self.add_btn = tk.Button(bottom, text="添加", command=self._confirm, bd=0, relief=tk.FLAT,
                                 bg=ACCENT, fg="white", activebackground=ACCENT_HOVER,
                                 padx=18, pady=5, cursor="hand2", font=app_font(10, "bold"))
        self.add_btn.pack(side=tk.RIGHT)
        tk.Button(bottom, text="取消", command=self._cancel, bd=0, relief=tk.FLAT,
                  bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
                  padx=14, pady=5, cursor="hand2", font=app_font(10)).pack(side=tk.RIGHT, padx=(0, 8))

    def _drives(self) -> list[str]:
        if sys.platform != "win32":
            return []
        found = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.exists(root):
                found.append(root)
        return found

    # -- icons -------------------------------------------------------------
    def _icon_for(self, entry: Path, is_dir: bool):
        """Small Tk icon for a file/folder, cached by folder/extension/path."""
        suffix = entry.suffix.lower()
        if is_dir:
            key = "<dir>"
        elif suffix in (".exe", ".lnk", ".ico", ".url"):
            key = str(entry).lower()  # these carry their own icon
        else:
            key = suffix or "<file>"
        if key in self._icon_cache:
            return self._icon_cache[key]
        photo = None
        try:
            if is_dir:
                img = _shell_icon_image(str(entry), is_folder=True, use_attributes=False)
            elif suffix in (".exe", ".lnk", ".ico", ".url") and entry.exists():
                img = _shell_icon_image(str(entry), is_folder=False, use_attributes=False)
            else:
                img = _shell_icon_image(suffix or ".txt", is_folder=False, use_attributes=True)
            if img is not None:
                img = img.resize((20, 20), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        self._icon_cache[key] = photo
        return photo

    # -- listing / navigation ---------------------------------------------
    def _populate(self) -> None:
        self.path_var.set(str(self.current))
        for _path, frame, _is_dir in self.cells:
            try:
                frame.destroy()
            except Exception:
                pass
        self.cells = []
        try:
            entries = list(self.current.iterdir())
        except Exception:
            entries = []
        dirs = sorted([e for e in entries if self._is_dir(e)], key=lambda p: p.name.lower())
        files = sorted([e for e in entries if not self._is_dir(e)], key=lambda p: p.name.lower())
        for entry in dirs + files:
            self._make_cell(entry, self._is_dir(entry))
        self._relayout()
        self.canvas.yview_moveto(0)
        self._update_count()

    def _make_cell(self, entry: Path, is_dir: bool) -> None:
        path = str(entry)
        selected = path in self.selected
        bg = ACCENT_SOFT if selected else "white"
        frame = tk.Frame(self.grid_frame, bg=bg, width=self._cell_w, height=30,
                         highlightthickness=1,
                         highlightbackground=(ACCENT if selected else "white"))
        frame.pack_propagate(False)
        photo = self._icon_for(entry, is_dir)
        icon = tk.Label(frame, image=photo, bg=bg)
        icon.image = photo
        icon.pack(side=tk.LEFT, padx=(4, 4))
        name = tk.Label(frame, text=entry.name, bg=bg, fg="#1f2937", anchor="w",
                        font=app_font(10), justify=tk.LEFT)
        name.pack(side=tk.LEFT, fill=tk.X, expand=True)
        for widget in (frame, icon, name):
            widget.bind("<Button-1>", lambda e, p=path: self._toggle(p))
            widget.bind("<Double-Button-1>", lambda e, p=path, d=is_dir: self._activate(p, d))
            widget.bind("<Button-3>", lambda e, p=path: self._deselect(p))
            widget.bind("<MouseWheel>", self._on_mousewheel)
        self.cells.append((path, frame, is_dir))

    def _relayout(self, *_args) -> None:
        try:
            width = self.canvas.winfo_width() or self.win.winfo_width()
        except Exception:
            width = 1200
        cols = max(1, width // self._cell_w)
        self._cols = cols
        for index, (_path, frame, _is_dir) in enumerate(self.cells):
            frame.grid(row=index // cols, column=index % cols, padx=3, pady=3, sticky="ew")
        # let columns share width evenly
        for c in range(cols):
            self.grid_frame.grid_columnconfigure(c, weight=1, uniform="cell")
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        # keep the inner frame as wide as the canvas, and reflow if columns change.
        self.canvas.itemconfigure(self._grid_window, width=event.width)
        new_cols = max(1, event.width // self._cell_w)
        if new_cols != self._cols:
            self._relayout()

    def _on_mousewheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    @staticmethod
    def _is_dir(path: Path) -> bool:
        try:
            return path.is_dir()
        except Exception:
            return False

    def _go_to(self, path: Path) -> None:
        try:
            if path.is_dir():
                self.current = path
                self._populate()
        except Exception:
            pass

    def _go_up(self) -> None:
        parent = self.current.parent
        if parent != self.current:
            self._go_to(parent)

    # -- selection ---------------------------------------------------------
    def _set_cell_style(self, path: str, selected: bool) -> None:
        bg = ACCENT_SOFT if selected else "white"
        border = ACCENT if selected else "white"
        for cell_path, frame, _is_dir in self.cells:
            if cell_path == path:
                try:
                    frame.configure(bg=bg, highlightbackground=border)
                    for child in frame.winfo_children():
                        child.configure(bg=bg)
                except Exception:
                    pass
                break

    def _toggle(self, path: str) -> None:
        if path in self.selected:
            del self.selected[path]
            self._set_cell_style(path, False)
        else:
            self.selected[path] = True
            self._set_cell_style(path, True)
        self._update_count()

    def _deselect(self, path: str) -> None:
        """Right-click removes an item from the selection (no-op if not selected)."""
        if path in self.selected:
            del self.selected[path]
            self._set_cell_style(path, False)
            self._update_count()

    def _activate(self, path: str, is_dir: bool) -> None:
        # Double-click: enter folders (undo the single-click toggle first), else
        # leave the file toggled.
        if is_dir:
            if path in self.selected:
                del self.selected[path]
                self._update_count()
            self._go_to(Path(path))

    def _clear(self) -> None:
        for path in list(self.selected.keys()):
            self._set_cell_style(path, False)
        self.selected.clear()
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.selected)
        self.count_label.config(text=f"已选 {n} 项")
        self.add_btn.config(text=f"添加 ({n})" if n else "添加")

    # -- finish ------------------------------------------------------------
    def _confirm(self) -> None:
        self.result = list(self.selected.keys())
        self._destroy()

    def _cancel(self) -> None:
        self.result = None
        self._destroy()

    def _destroy(self) -> None:
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


def rounded_polygon_points(x1: int, y1: int, x2: int, y2: int, radius: int) -> list[int]:
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    return [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """Small-radius canvas button used by the transparent native header layer."""

    def __init__(
        self,
        parent,
        text: str,
        command,
        normal_bg: str,
        hover_bg: str,
        fg: str,
        font,
        padx: int = 13,
        pady: int = 7,
        width: int | None = None,
        radius: int = 6,
    ):
        self._text = text
        self._command = command
        self._normal_bg = normal_bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._font_spec = font
        self._radius = radius
        self._hover = False
        self._pressed = False
        font_obj = tkfont.Font(root=parent, font=font)
        button_width = width or max(28, font_obj.measure(text) + padx * 2)
        button_height = max(28, font_obj.metrics("linespace") + pady * 2)
        super().__init__(
            parent,
            width=button_width,
            height=button_height,
            bg=parent.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self._button_width = button_width
        self._button_height = button_height
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self._draw()

    def _draw(self) -> None:
        self.delete("surface")
        fill = self._hover_bg if self._hover or self._pressed else self._normal_bg
        points = rounded_polygon_points(1, 1, self._button_width - 1, self._button_height - 1, self._radius)
        self.create_polygon(points, smooth=True, splinesteps=12, fill=fill, outline=fill, tags="surface")
        self.create_text(
            self._button_width / 2,
            self._button_height / 2,
            text=self._text,
            fill=self._fg,
            font=self._font_spec,
            tags="surface",
        )

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        self._pressed = True
        self.focus_set()
        self._draw()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        inside = event is None or (
            0 <= event.x < self._button_width and 0 <= event.y < self._button_height
        )
        self._hover = inside
        self._draw()
        if was_pressed and inside:
            self.invoke()

    def set_palette(self, normal_bg: str, hover_bg: str, fg: str) -> None:
        self._normal_bg = normal_bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._draw()

    def set_metrics(
        self,
        *,
        font=None,
        padx: int = 13,
        pady: int = 7,
        width: int | None = None,
        radius: int | None = None,
    ) -> None:
        """Resize an existing canvas button without rebuilding its bindings."""
        if font is not None:
            self._font_spec = font
        if radius is not None:
            self._radius = max(2, int(radius))
        font_obj = tkfont.Font(root=self, font=self._font_spec)
        self._button_width = int(width or max(28, font_obj.measure(self._text) + int(padx) * 2))
        self._button_height = max(26, font_obj.metrics("linespace") + int(pady) * 2)
        tk.Canvas.configure(self, width=self._button_width, height=self._button_height)
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        normal_bg = kwargs.pop("bg", kwargs.pop("background", None))
        hover_bg = kwargs.pop("activebackground", None)
        fg = kwargs.pop("fg", kwargs.pop("foreground", None))
        kwargs.pop("activeforeground", None)
        if normal_bg is not None:
            self._normal_bg = normal_bg
        if hover_bg is not None:
            self._hover_bg = hover_bg
        if fg is not None:
            self._fg = fg
        result = super().configure(**kwargs) if kwargs else None
        self._draw()
        return result

    config = configure

    def cget(self, key: str):
        if key in ("bg", "background"):
            return self._normal_bg
        if key in ("fg", "foreground"):
            return self._fg
        if key == "activebackground":
            return self._hover_bg
        if key == "text":
            return self._text
        if key == "font":
            return self._font_spec
        if key == "command":
            return self._command
        return super().cget(key)

    def invoke(self):
        if callable(self._command):
            return self._command()
        return None
