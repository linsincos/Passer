from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from clicker_tool import ClickerTheme

try:
    from PIL import Image, ImageDraw, ImageGrab, ImageTk
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageGrab = None
    ImageTk = None

try:
    from tkinterdnd2 import COPY, DND_FILES

    TKDND_AVAILABLE = True
except Exception:  # pragma: no cover
    COPY = "copy"
    DND_FILES = None
    TKDND_AVAILABLE = False


QR_DATA_CODEWORDS = {1: 19, 2: 34, 3: 55, 4: 80}
QR_ECC_CODEWORDS = {1: 7, 2: 10, 3: 15, 4: 20}
QR_ALIGNMENT = {1: [], 2: [18], 3: [22], 4: [26]}


def _gf_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = _gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= _gf_mul(coef, GF_EXP[i])
            nxt[j + 1] ^= coef
        poly = nxt
    return poly[:-1]


def _rs_ecc(data: list[int], degree: int) -> list[int]:
    # _rs_generator yields coefficients low-degree first; the division below
    # consumes them high-degree first, so reverse before use.
    gen = list(reversed(_rs_generator(degree)))
    result = [0] * degree
    for value in data:
        factor = value ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(gen):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _append_bits(bits: list[int], value: int, length: int) -> None:
    for i in range(length - 1, -1, -1):
        bits.append((value >> i) & 1)


def _format_bits(mask: int = 0, ecc_level: int = 1) -> int:
    data = (ecc_level << 3) | mask
    value = data << 10
    generator = 0x537
    for i in range(14, 9, -1):
        if (value >> i) & 1:
            value ^= generator << (i - 10)
    return (((data << 10) | value) ^ 0x5412) & 0x7FFF


class SimpleQr:
    def __init__(self, text: str):
        raw = text.encode("utf-8")
        self.version = next((v for v, cap in QR_DATA_CODEWORDS.items() if len(raw) <= cap - 2), None)
        if self.version is None:
            raise ValueError("纯内置二维码生成最多支持约 78 字节文本；请缩短内容。")
        self.size = 21 + (self.version - 1) * 4
        self.modules: list[list[bool | None]] = [[None] * self.size for _ in range(self.size)]
        self.function: list[list[bool]] = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        data = self._make_data_codewords(raw)
        ecc = _rs_ecc(data, QR_ECC_CODEWORDS[self.version])
        codewords = data + ecc
        self._draw_codewords(codewords)
        self._apply_mask_0()
        self._draw_format_bits()
        for y in range(self.size):
            for x in range(self.size):
                if self.modules[y][x] is None:
                    self.modules[y][x] = False

    def _set_function(self, x: int, y: int, value: bool) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            self.modules[y][x] = value
            self.function[y][x] = True

    def _draw_finder(self, x: int, y: int) -> None:
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                xx, yy = x + dx, y + dy
                if not (0 <= xx < self.size and 0 <= yy < self.size):
                    continue
                dark = (0 <= dx <= 6 and 0 <= dy <= 6 and
                        (dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4)))
                self._set_function(xx, yy, dark)

    def _draw_alignment(self, cx: int, cy: int) -> None:
        if self.function[cy][cx]:
            return
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def _draw_function_patterns(self) -> None:
        self._draw_finder(0, 0)
        self._draw_finder(self.size - 7, 0)
        self._draw_finder(0, self.size - 7)
        for i in range(8, self.size - 8):
            self._set_function(i, 6, i % 2 == 0)
            self._set_function(6, i, i % 2 == 0)
        for pos in QR_ALIGNMENT[self.version]:
            self._draw_alignment(pos, pos)
        self._set_function(8, self.size - 8, True)
        for i in range(9):
            if i != 6:
                self._set_function(8, i, False)
                self._set_function(i, 8, False)
        for i in range(8):
            self._set_function(self.size - 1 - i, 8, False)
            self._set_function(8, self.size - 1 - i, False)

    def _make_data_codewords(self, raw: bytes) -> list[int]:
        capacity = QR_DATA_CODEWORDS[self.version]
        bits: list[int] = []
        _append_bits(bits, 0b0100, 4)
        _append_bits(bits, len(raw), 8)
        for byte in raw:
            _append_bits(bits, byte, 8)
        for _ in range(min(4, capacity * 8 - len(bits))):
            bits.append(0)
        while len(bits) % 8:
            bits.append(0)
        data = [sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits), 8)]
        pads = [0xEC, 0x11]
        index = 0
        while len(data) < capacity:
            data.append(pads[index % 2])
            index += 1
        return data

    def _draw_codewords(self, codewords: list[int]) -> None:
        bits: list[int] = []
        for byte in codewords:
            _append_bits(bits, byte, 8)
        bit_index = 0
        direction = -1
        x = self.size - 1
        y = self.size - 1
        while x > 0:
            if x == 6:
                x -= 1
            while 0 <= y < self.size:
                for dx in (0, 1):
                    xx = x - dx
                    if not self.function[y][xx]:
                        value = bits[bit_index] if bit_index < len(bits) else 0
                        self.modules[y][xx] = bool(value)
                        bit_index += 1
                y += direction
            direction *= -1
            y += direction
            x -= 2

    def _apply_mask_0(self) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and (x + y) % 2 == 0:
                    self.modules[y][x] = not bool(self.modules[y][x])

    def _draw_format_bits(self) -> None:
        bits = _format_bits(0, 1)
        for i in range(15):
            bit = bool((bits >> i) & 1)
            if i < 6:
                self._set_function(8, i, bit)
            elif i < 8:
                self._set_function(8, i + 1, bit)
            else:
                self._set_function(8, self.size - 15 + i, bit)
            if i < 8:
                self._set_function(self.size - i - 1, 8, bit)
            elif i < 9:
                self._set_function(15 - i - 1, 8, bit)
            else:
                self._set_function(15 - i - 1, 8, bit)
        self._set_function(8, self.size - 8, True)

    def to_image(self, scale: int = 10, border: int = 4):
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成图片。")
        size = (self.size + border * 2) * scale
        image = Image.new("RGB", (size, size), "white")
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(self.modules):
            for x, dark in enumerate(row):
                if dark:
                    x0 = (x + border) * scale
                    y0 = (y + border) * scale
                    draw.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill="black")
        return image


def decode_qr_pil(image) -> str:
    decoder_available = False
    try:
        from pyzbar.pyzbar import decode  # type: ignore

        decoder_available = True
        found = decode(image)
        if found:
            return found[0].data.decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        decoder_available = True
        rgb = np.asarray(image.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        data, _points, _straight = cv2.QRCodeDetector().detectAndDecode(bgr)
        if data:
            return data
    except Exception:
        pass
    if decoder_available:
        raise RuntimeError("未在图片中识别到二维码，请尝试更清晰、完整的图片。")
    raise RuntimeError("当前环境缺少二维码识别库（pyzbar 或 OpenCV），无法识别。")


def decode_qr_image(path: str) -> str:
    if Image is None:
        raise RuntimeError("缺少 Pillow，无法读取图片。")
    with Image.open(path) as image:
        return decode_qr_pil(image.copy())


class QRToolWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 520
    MIN_H = 520

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.text_var = tk.StringVar(value="https://example.com")
        self.result_var = tk.StringVar(value="")
        self.preview_photo = None

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

    def _font(self, size=9, weight="normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self):
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

    def _build_body(self):
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(body, text="内容", bg=t.surface_bg, fg="#334155", font=self._font(10), anchor=tk.W).pack(
            fill=tk.X, padx=18, pady=(18, 6)
        )
        entry = tk.Entry(body, textvariable=self.text_var, bd=0, bg="#f8fafc", fg="#111827",
                         insertbackground="#111827", highlightthickness=1, highlightbackground=t.border,
                         highlightcolor=t.accent, font=self._font(10))
        entry.pack(fill=tk.X, padx=18, ipady=8)
        row = tk.Frame(body, bg=t.surface_bg)
        row.pack(fill=tk.X, padx=18, pady=12)
        self._button(row, "生成并载入", self.generate, True).pack(side=tk.RIGHT)
        self._button(row, "识别图片", self.recognize).pack(side=tk.RIGHT, padx=(0, 8))
        self._button(row, "复制链接", self.copy_link).pack(side=tk.RIGHT, padx=(0, 8))
        preview_hint = "点击此处后按 Ctrl+V 识别剪贴板图片"
        if TKDND_AVAILABLE:
            preview_hint += "\n（也可拖动图片到此识别二维码）"
        self.preview = tk.Label(
            body,
            bg="#f8fafc",
            text=preview_hint,
            fg="#64748b",
            font=self._font(10),
            cursor="hand2",
            takefocus=True,
            highlightthickness=2,
            highlightbackground="#e2e8f0",
            highlightcolor=t.accent,
        )
        self.preview.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 10))
        self.preview.bind("<Button-1>", self._focus_preview)
        self.preview.bind("<Control-v>", self._paste_and_recognize)
        self.preview.bind("<Control-V>", self._paste_and_recognize)
        self.preview.bind("<FocusIn>", lambda _e: self.preview.configure(highlightbackground=t.accent))
        self.preview.bind("<FocusOut>", lambda _e: self.preview.configure(highlightbackground="#e2e8f0"))
        self._register_drop_target(self.preview)
        tk.Label(body, textvariable=self.result_var, bg=t.surface_bg, fg="#334155", wraplength=470,
                 justify=tk.LEFT, anchor=tk.W, font=self._font(9)).pack(fill=tk.X, padx=18, pady=(0, 14))
        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bd=0, padx=16, pady=8,
                         bg=(self.theme.accent if primary else "#eef2f9"),
                         fg=("#ffffff" if primary else "#1f2937"),
                         activebackground=(self.theme.accent_hover if primary else "#e2e8f4"),
                         activeforeground=("#ffffff" if primary else "#111827"),
                         cursor="hand2", font=self._font(9, "bold" if primary else "normal"))

    def _chrome_button(self, parent, text, command, close=False):
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5,
                           bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover,
                           activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=self.theme.title_button_bg))
        return button

    def generate(self):
        text = self.text_var.get()
        if not text:
            return
        try:
            image = SimpleQr(text).to_image(scale=8)
            path = Path(self.app.store_dir) / f"二维码_{text[:12].strip() or 'QR'}.png"
            safe = "".join(ch for ch in path.stem if ch not in '<>:"/\\|?*')[:40] or "二维码"
            path = self.app.store_dir / f"{safe}.png"
            counter = 1
            while path.exists():
                path = self.app.store_dir / f"{safe}_{counter}.png"
                counter += 1
            image.save(path, "PNG")
            self.app.add_paths([str(path)])
            self._show_preview(image)
            self.result_var.set(f"已生成并载入：{path.name}")
        except Exception as exc:
            messagebox.showinfo("生成失败", str(exc), parent=self.window)

    def recognize(self):
        path = filedialog.askopenfilename(
            title="选择二维码图片",
            filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"), ("所有文件", "*.*")],
            parent=self.window,
        )
        if path:
            self._recognize_path(path)

    def copy_link(self):
        text = self.text_var.get().strip()
        if not text:
            messagebox.showinfo("复制链接", "当前没有可复制的链接。", parent=self.window)
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.result_var.set(f"已复制链接：{text}")

    def _recognize_path(self, path: str) -> None:
        try:
            if Image is None:
                raise RuntimeError("缺少 Pillow，无法读取图片。")
            with Image.open(path) as image:
                self._recognize_image(image.copy())
        except Exception as exc:
            messagebox.showinfo("识别失败", str(exc), parent=self.window)

    def _focus_preview(self, _event=None):
        self.preview.focus_set()
        self.result_var.set("预览区已选中，请按 Ctrl+V 粘贴并识别剪贴板图片。")

    def _paste_and_recognize(self, _event=None):
        try:
            if ImageGrab is None:
                raise RuntimeError("缺少 Pillow，无法读取剪贴板图片。")
            clipboard_data = ImageGrab.grabclipboard()
            if Image is not None and isinstance(clipboard_data, Image.Image):
                self._recognize_image(clipboard_data.copy())
                return "break"
            if isinstance(clipboard_data, list):
                image_path = next(
                    (
                        path
                        for path in clipboard_data
                        if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
                    ),
                    None,
                )
                if image_path:
                    self._recognize_path(image_path)
                    return "break"
            raise RuntimeError("剪贴板中没有图片。请先复制截图或图片文件，再点击此处按 Ctrl+V。")
        except Exception as exc:
            messagebox.showinfo("识别失败", str(exc), parent=self.window)
        return "break"

    def _recognize_image(self, image) -> None:
        text = decode_qr_pil(image)
        self._show_preview(image)
        self.text_var.set(text)
        self.result_var.set(f"识别结果：{text}（已复制）")
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def _show_preview(self, image) -> None:
        if ImageTk is None:
            return
        preview = image.copy()
        preview.thumbnail((360, 260))
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview.configure(image=self.preview_photo, text="")

    def _register_drop_target(self, widget) -> None:
        if not TKDND_AVAILABLE or DND_FILES is None:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", lambda _e: COPY)
            widget.dnd_bind("<<DropPosition>>", lambda _e: COPY)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        raw = getattr(event, "data", "")
        try:
            paths = [str(p) for p in self.window.tk.splitlist(raw)]
        except Exception:
            paths = [str(raw)] if raw else []
        if paths:
            self._recognize_path(paths[0])
        return COPY

    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(self.window, max(self.window.winfo_width(), self.MIN_W),
                                           max(self.window.winfo_height(), self.MIN_H),
                                           wx + event.x_root - sx, wy + event.y_root - sy)

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "qr_window", None) is self:
            self.app.qr_window = None
        self.window.destroy()
