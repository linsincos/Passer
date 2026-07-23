from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from clicker_tool import ClickerTheme

try:
    from PIL import ImageGrab
except Exception:  # pragma: no cover
    ImageGrab = None


def screen_refresh_rate() -> int:
    """Best-effort primary-monitor refresh rate in Hz (falls back to 60)."""
    if sys.platform == "win32":
        try:
            import ctypes

            hdc = ctypes.windll.user32.GetDC(0)
            try:
                rate = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 116))  # VREFRESH
            finally:
                ctypes.windll.user32.ReleaseDC(0, hdc)
            if rate > 1:
                return rate
        except Exception:
            pass
    return 60


def virtual_screen_bounds() -> tuple[int, int, int, int] | None:
    """(x, y, w, h) spanning every monitor, or None if unavailable."""
    if sys.platform == "win32":
        try:
            import ctypes

            u = ctypes.windll.user32
            return (
                u.GetSystemMetrics(76),  # SM_XVIRTUALSCREEN
                u.GetSystemMetrics(77),  # SM_YVIRTUALSCREEN
                u.GetSystemMetrics(78),  # SM_CXVIRTUALSCREEN
                u.GetSystemMetrics(79),  # SM_CYVIRTUALSCREEN
            )
        except Exception:
            pass
    return None


class RegionSelector:
    def __init__(self, app, callback):
        self.app = app
        self.callback = callback
        self.start = None
        self.rect = None
        self.window = tk.Toplevel(app.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.25)
        self.window.configure(bg="black")
        bounds = virtual_screen_bounds()
        if bounds:
            vx, vy, vw, vh = bounds
            self.window.geometry(f"{vw}x{vh}+{vx}+{vy}")
        else:
            self.window.attributes("-fullscreen", True)
        self.canvas = tk.Canvas(self.window, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", lambda _e: self.close())
        self.window.bind("<Button-3>", lambda _e: self.close())
        self.window.bind("<Escape>", lambda _e: self.close())

    def on_press(self, event):
        self.start = (event.x_root, event.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        try:
            outline = self.app.screen_record_theme().accent
        except Exception:
            outline = "#2563eb"
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline=outline, width=3)

    def on_drag(self, event):
        if not self.start or not self.rect:
            return
        x0, y0 = self.start
        self.canvas.coords(
            self.rect,
            x0 - self.window.winfo_rootx(),
            y0 - self.window.winfo_rooty(),
            event.x_root - self.window.winfo_rootx(),
            event.y_root - self.window.winfo_rooty(),
        )

    def on_release(self, event):
        if not self.start:
            self.close()
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.close()
        if bbox[2] - bbox[0] >= 20 and bbox[3] - bbox[1] >= 20:
            self.callback(bbox)

    def close(self):
        try:
            self.window.destroy()
        except Exception:
            pass


class RegionBorder:
    """A persistent, click-through outline drawn just outside the capture region."""

    TRANSPARENT = "#010203"

    def __init__(self, app, region: tuple[int, int, int, int]):
        self.window = tk.Toplevel(app.root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=self.TRANSPARENT)
        bounds = virtual_screen_bounds()
        if bounds:
            vx, vy, vw, vh = bounds
        else:
            vx, vy = 0, 0
            vw = self.window.winfo_screenwidth()
            vh = self.window.winfo_screenheight()
        self.window.geometry(f"{vw}x{vh}+{vx}+{vy}")
        click_through = False
        try:
            self.window.attributes("-transparentcolor", self.TRANSPARENT)
            click_through = True
        except Exception:
            self.window.attributes("-alpha", 0.35)
        canvas = tk.Canvas(self.window, bg=self.TRANSPARENT, highlightthickness=0, takefocus=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        x0, y0, x1, y1 = region
        # Draw 2px outside the captured area so the border itself is never recorded.
        canvas.create_rectangle(
            x0 - vx - 2, y0 - vy - 2, x1 - vx + 2, y1 - vy + 2,
            outline="#ef4444", width=3,
        )
        if not click_through:
            # Without a transparent colour the overlay would swallow clicks; keep it
            # from stealing focus at least.
            self.window.attributes("-disabled", True)

    def close(self):
        try:
            self.window.destroy()
        except Exception:
            pass


class ScreenRecordWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 520
    MIN_H = 430

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.region: tuple[int, int, int, int] | None = None
        self.region_border: RegionBorder | None = None
        self.frames = []
        self.recording = False
        self.record_started = 0.0
        self.max_fps = screen_refresh_rate()
        self.format_var = tk.StringVar(value="GIF")
        self.fps_var = tk.StringVar(value="30")
        self.status_var = tk.StringVar(value="先框选录制区域。")

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
        form = tk.Frame(body, bg=t.surface_bg)
        form.pack(fill=tk.X, padx=18, pady=18)
        form.grid_columnconfigure(1, weight=1)
        tk.Label(form, text="格式", bg=t.surface_bg, fg="#334155", font=self._font(10)).grid(row=0, column=0, sticky=tk.W, pady=6)
        ttk.Combobox(form, textvariable=self.format_var, values=["GIF", "MP4"], state="readonly").grid(row=0, column=1, sticky=tk.EW, pady=6)
        tk.Label(form, text=f"FPS（≤{self.max_fps}）", bg=t.surface_bg, fg="#334155", font=self._font(10)).grid(row=1, column=0, sticky=tk.W, pady=6)
        tk.Entry(form, textvariable=self.fps_var, bd=0, bg="#f8fafc", fg="#111827", insertbackground="#111827",
                 highlightthickness=1, highlightbackground=t.border, highlightcolor=t.accent, font=self._font(10)).grid(
            row=1, column=1, sticky=tk.EW, pady=6
        )
        buttons = tk.Frame(body, bg=t.surface_bg)
        buttons.pack(fill=tk.X, padx=18, pady=(0, 12))
        self._button(buttons, "框选区域", self.select_region).pack(side=tk.LEFT)
        self.start_button = self._button(buttons, "开始录制", self.start_recording, True)
        self.start_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_button = self._button(buttons, "停止并保存", self.stop_recording)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(body, textvariable=self.status_var, bg=t.surface_bg, fg="#334155", anchor=tk.W,
                 justify=tk.LEFT, wraplength=470, font=self._font(10)).pack(fill=tk.X, padx=18, pady=(4, 12))
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

    def select_region(self):
        if self.recording:
            return
        RegionSelector(self.app, self.set_region)

    def set_region(self, bbox):
        self.region = tuple(int(v) for v in bbox)
        w, h = self.region[2] - self.region[0], self.region[3] - self.region[1]
        self.status_var.set(f"已选择区域：{self.region} · {w}×{h}")
        self._show_region_border()

    def _show_region_border(self):
        self._clear_region_border()
        if self.region:
            try:
                self.region_border = RegionBorder(self.app, self.region)
            except Exception:
                self.region_border = None

    def _clear_region_border(self):
        if self.region_border is not None:
            self.region_border.close()
            self.region_border = None

    def _read_fps(self) -> int:
        """Parse the FPS field, clamped to 1..screen refresh rate."""
        try:
            value = int(float(self.fps_var.get()))
        except Exception:
            value = 30
        value = max(1, min(self.max_fps, value))
        if str(value) != self.fps_var.get():
            self.fps_var.set(str(value))
        return value

    def _update_elapsed(self):
        if not self.recording:
            return
        secs = time.perf_counter() - self.record_started
        self.status_var.set(f"录制中…… {secs:.0f} 秒（{len(self.frames)} 帧），点击“停止并保存”结束。")
        self.window.after(200, self._update_elapsed)

    def start_recording(self):
        if ImageGrab is None:
            messagebox.showinfo("无法录制", "当前环境缺少 Pillow ImageGrab。", parent=self.window)
            return
        if self.recording:
            return
        if not self.region:
            self.select_region()
            return
        fps = self._read_fps()
        self.frames = []
        self.recording = True
        self.record_started = time.perf_counter()
        self._update_elapsed()

        def work():
            interval = 1 / fps
            while self.recording and len(self.frames) < fps * 120:
                started = time.perf_counter()
                try:
                    self.frames.append(ImageGrab.grab(bbox=self.region, all_screens=True).convert("RGB"))
                except Exception as exc:
                    self.window.after(0, lambda e=exc: self.status_var.set(f"录制失败：{e}"))
                    self.recording = False
                    break
                delay = interval - (time.perf_counter() - started)
                if delay > 0:
                    time.sleep(delay)

        threading.Thread(target=work, daemon=True, name="Passer-ScreenRecord").start()

    def stop_recording(self):
        if not self.recording and not self.frames:
            return
        self.recording = False
        frames = list(self.frames)
        self.frames = []
        if not frames:
            self.status_var.set("没有录到帧。")
            return
        fps = self._read_fps()
        fmt = self.format_var.get().upper()
        suffix = ".mp4" if fmt == "MP4" else ".gif"
        path = Path(self.app.store_dir) / f"录屏_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"
        self.status_var.set("正在保存……")

        def save():
            error = None
            try:
                if fmt == "MP4":
                    try:
                        import imageio.v2 as imageio  # type: ignore
                        import imageio_ffmpeg  # noqa: F401  # ensures an ffmpeg backend is present
                    except Exception as exc:
                        raise RuntimeError("当前环境缺少 MP4 编码支持（imageio/imageio-ffmpeg）。请改用 GIF。") from exc
                    # H.264 (yuv420p) requires even width/height; crop the odd edge off.
                    w, h = frames[0].size
                    cw, ch = w - (w % 2), h - (h % 2)
                    if (cw, ch) != (w, h):
                        frames = [f.crop((0, 0, cw, ch)) for f in frames]
                    imageio.mimsave(str(path), frames, fps=fps, codec="libx264", quality=8)
                else:
                    duration = int(1000 / fps)
                    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0, optimize=True)
            except Exception as exc:
                error = exc

            def done():
                if error:
                    self.status_var.set(f"保存失败：{error}")
                    return
                self.app.add_paths([str(path)])
                self.status_var.set(f"已保存并载入：{path.name}")

            self.window.after(0, done)

        threading.Thread(target=save, daemon=True, name="Passer-ScreenRecord-Save").start()

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
        self.recording = False
        self.closed = True
        self._clear_region_border()
        if getattr(self.app, "screen_record_window", None) is self:
            self.app.screen_record_window = None
        self.window.destroy()
