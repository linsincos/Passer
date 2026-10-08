"""Passer built-in clipboard viewer with an in-memory, opt-in session history."""

from __future__ import annotations

import hashlib
import io
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox


MAX_HISTORY = 40
POLL_MS = 700


def _snapshot_signature(snapshot: dict) -> str:
    kind = str(snapshot.get("kind") or "")
    if kind == "image":
        payload = bytes(snapshot.get("png") or b"")
    elif kind == "files":
        payload = "\n".join(snapshot.get("files") or ()).encode("utf-8", "replace")
    else:
        payload = str(snapshot.get("text") or "").encode("utf-8", "replace")
    return kind + ":" + hashlib.sha256(payload).hexdigest()


def read_clipboard_snapshot(root: tk.Misc) -> dict | None:
    """Read one clipboard value without persisting or sending it anywhere."""
    try:
        text = root.clipboard_get()
    except tk.TclError:
        text = None
    if text is not None:
        value = str(text)
        if value:
            return {"kind": "text", "text": value}

    try:
        from PIL import Image, ImageGrab

        value = ImageGrab.grabclipboard()
        if isinstance(value, list):
            files = [str(Path(path)) for path in value if str(path).strip()]
            return {"kind": "files", "files": files} if files else None
        if isinstance(value, Image.Image):
            image = value.copy()
            stream = io.BytesIO()
            image.save(stream, "PNG")
            return {
                "kind": "image",
                "png": stream.getvalue(),
                "size": tuple(image.size),
                "mode": str(image.mode),
            }
    except (ImportError, OSError, ValueError):
        pass
    return None


def put_image_on_clipboard(png: bytes) -> bool:
    if os.name != "nt":
        return False
    try:
        from PIL import Image
        import win32clipboard

        with Image.open(io.BytesIO(png)) as image:
            output = io.BytesIO()
            image.convert("RGB").save(output, "BMP")
        dib = output.getvalue()[14:]
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


class ClipboardWindow:
    WIDTH = 900
    HEIGHT = 620

    def __init__(self, app, theme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.paused = False
        self.history: list[dict] = []
        self.last_signature = ""
        self._poll_id = None
        self._photo = None
        self._move_start = None
        self.accent = getattr(theme, "accent", "#2563eb")
        self.border = getattr(theme, "border", "#cbd5e1")
        self.surface_bg = getattr(theme, "surface_bg", "#ffffff")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=self.border)
        self.window.minsize(720, 460)

        shell = tk.Frame(
            self.window, bg=self.surface_bg,
            highlightthickness=1, highlightbackground=self.border,
        )
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        title_bg = getattr(theme, "title_bg", "#0f172a")
        titlebar = tk.Frame(shell, bg=title_bg, height=46)
        titlebar.pack(fill=tk.X)
        titlebar.pack_propagate(False)
        title_label = tk.Label(
            titlebar, text="剪贴板 Clipboard", bg=title_bg, fg="#e7eefc",
            font=self._font(10, "bold"), anchor=tk.W,
        )
        title_label.pack(side=tk.LEFT, padx=14)
        self._button(titlebar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=7)
        for widget in (titlebar, title_label):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._move)

        toolbar = tk.Frame(shell, bg="#f8fafc", highlightthickness=0)
        toolbar.pack(fill=tk.X, padx=12, pady=(10, 6))
        self._light_button(toolbar, "立即读取", self.refresh).pack(side=tk.LEFT, padx=(0, 6))
        self._light_button(toolbar, "写回剪贴板", self.copy_selected_back).pack(side=tk.LEFT, padx=(0, 6))
        self.save_image_button = self._light_button(toolbar, "另存图片", self.save_selected_image)
        self.save_image_button.pack(side=tk.LEFT, padx=(0, 6))
        self.pause_button = self._light_button(toolbar, "暂停记录", self.toggle_pause)
        self.pause_button.pack(side=tk.LEFT, padx=(0, 6))
        self._light_button(toolbar, "清空系统剪贴板", self.clear_clipboard).pack(side=tk.RIGHT)
        self._light_button(toolbar, "清空历史", self.clear_history).pack(side=tk.RIGHT, padx=(0, 6))

        content = tk.Frame(shell, bg=self.surface_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        left = tk.Frame(content, bg="#f8fafc", highlightthickness=1, highlightbackground="#d7e0ec")
        left.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(
            left, text="本次窗口历史", bg="#f8fafc", fg="#334155",
            font=self._font(9, "bold"), anchor=tk.W,
        ).pack(fill=tk.X, padx=10, pady=(9, 6))
        list_frame = tk.Frame(left, bg="#f8fafc")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self.listbox = tk.Listbox(
            list_frame, width=27, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#1f2937",
            selectbackground="#dbeafe", selectforeground="#1d4ed8",
            activestyle="none", font=self._font(9), exportselection=False,
        )
        list_scroll = tk.Scrollbar(list_frame, command=self.listbox.yview, bd=0)
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self.show_selected)

        right = tk.Frame(content, bg=self.surface_bg, highlightthickness=1, highlightbackground="#d7e0ec")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.preview_title = tk.StringVar(value="剪贴板为空")
        tk.Label(
            right, textvariable=self.preview_title, bg=self.surface_bg, fg="#334155",
            font=self._font(9, "bold"), anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(9, 5))
        self.preview_image = tk.Label(right, bg=self.surface_bg, fg="#64748b", text="")
        self.text_frame = tk.Frame(right, bg=self.surface_bg)
        self.text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        self.text = tk.Text(
            self.text_frame, bd=0, relief=tk.FLAT, wrap=tk.WORD,
            bg=self.surface_bg, fg="#111827",
            insertbackground="#111827", selectbackground="#bfdbfe", undo=True,
            font=self._font(10), padx=10, pady=8,
        )
        text_scroll = tk.Scrollbar(self.text_frame, command=self.text.yview, bd=0)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_var = tk.StringVar(value="仅在本窗口打开时读取；历史不会写入磁盘。")
        tk.Label(
            shell, textvariable=self.status_var, bg="#f8fafc", fg="#64748b",
            anchor=tk.W, font=self._font(8),
        ).pack(fill=tk.X, padx=14, pady=(0, 8))

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        x, y = self._center_position()
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        try:
            self.window.attributes("-topmost", bool(app.topmost_var.get()))
            app.apply_window_transparency(self.window)
        except Exception:
            pass
        self.window.deiconify()
        app.keep_window_above_main(self.window)
        app.place_tool_window_on_passer(self)
        self.refresh()
        self._schedule_poll()

    def _font(self, size: int, weight: str | None = None):
        try:
            factory = self.theme.app_font
            return factory(size, weight) if weight else factory(size)
        except Exception:
            return ("Microsoft YaHei UI", size, weight or "normal")

    def _button(self, parent, text, command, *, close=False):
        bg = "#17243a"
        hover = "#ef4444" if close else "#223451"
        button = tk.Button(
            parent, text=text, command=command, bd=0, relief=tk.FLAT,
            padx=11, pady=5, bg=bg, fg="#e7eefc",
            activebackground=hover, activeforeground="#ffffff", cursor="hand2",
            font=self._font(10 if close else 9),
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=bg))
        return button

    def _light_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, bd=0, relief=tk.FLAT,
            padx=12, pady=6, bg="#e8f0fe", fg=self.accent,
            activebackground="#dbeafe", activeforeground=self.accent,
            cursor="hand2", font=self._font(9),
        )

    def _center_position(self) -> tuple[int, int]:
        try:
            self.app.root.update_idletasks()
            x = self.app.root.winfo_rootx() + (self.app.root.winfo_width() - self.WIDTH) // 2
            y = self.app.root.winfo_rooty() + (self.app.root.winfo_height() - self.HEIGHT) // 2
            return max(0, x), max(0, y)
        except Exception:
            return 120, 90

    def _start_move(self, event) -> None:
        self._move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _move(self, event) -> None:
        if not self._move_start:
            return
        sx, sy, wx, wy = self._move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def _schedule_poll(self) -> None:
        if not self.closed:
            self._poll_id = self.window.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        self._poll_id = None
        if not self.closed and not self.paused:
            self.refresh(silent=True)
        self._schedule_poll()

    def refresh(self, silent: bool = False) -> None:
        snapshot = read_clipboard_snapshot(self.window)
        if snapshot is None:
            if not silent and not self.history:
                self._show_empty()
            return
        signature = _snapshot_signature(snapshot)
        if signature == self.last_signature:
            return
        self.last_signature = signature
        snapshot["signature"] = signature
        snapshot["time"] = datetime.now().strftime("%H:%M:%S")
        self.history.insert(0, snapshot)
        del self.history[MAX_HISTORY:]
        self._refresh_list()
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(0)
        self.listbox.activate(0)
        self.show_selected()
        self.status_var.set(f"已记录 {len(self.history)} 条；只保存在内存中。")

    def _entry_label(self, entry: dict) -> str:
        kind = entry.get("kind")
        if kind == "image":
            size = entry.get("size") or (0, 0)
            summary = f"图片 {size[0]}×{size[1]}"
        elif kind == "files":
            files = entry.get("files") or []
            summary = f"文件 {len(files)} 项"
        else:
            text = str(entry.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
            summary = text[:18] + ("…" if len(text) > 18 else "")
        return f"{entry.get('time', '')}  {summary or '空文本'}"

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for entry in self.history:
            self.listbox.insert(tk.END, self._entry_label(entry))

    def _selected(self) -> dict | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return self.history[index] if 0 <= index < len(self.history) else None

    def _show_empty(self) -> None:
        self.preview_title.set("剪贴板为空")
        self.preview_image.pack_forget()
        self.text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        self.text.delete("1.0", tk.END)
        self.save_image_button.configure(state=tk.DISABLED)

    def show_selected(self, event=None) -> None:
        entry = self._selected()
        if entry is None:
            self._show_empty()
            return
        kind = entry.get("kind")
        self._photo = None
        if kind == "image":
            self.text_frame.pack_forget()
            self.preview_image.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
            try:
                from PIL import Image, ImageTk

                with Image.open(io.BytesIO(entry.get("png") or b"")) as source:
                    image = source.convert("RGBA")
                    image.thumbnail((560, 440))
                self._photo = ImageTk.PhotoImage(image)
                self.preview_image.configure(image=self._photo, text="")
            except Exception:
                self.preview_image.configure(image="", text="图片预览失败")
            size = entry.get("size") or (0, 0)
            self.preview_title.set(f"图片 · {size[0]}×{size[1]} · {entry.get('mode', '')}")
            self.save_image_button.configure(state=tk.NORMAL)
            return
        self.preview_image.pack_forget()
        self.text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        self.text.delete("1.0", tk.END)
        if kind == "files":
            files = entry.get("files") or []
            self.text.insert("1.0", "\n".join(files))
            self.preview_title.set(f"文件列表 · {len(files)} 项")
        else:
            self.text.insert("1.0", str(entry.get("text") or ""))
            self.preview_title.set(f"文本 · {len(str(entry.get('text') or ''))} 字符")
        self.save_image_button.configure(state=tk.DISABLED)

    def copy_selected_back(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if entry.get("kind") == "image":
            if not put_image_on_clipboard(bytes(entry.get("png") or b"")):
                messagebox.showinfo("写回失败", "当前环境无法把图片写回系统剪贴板。", parent=self.window)
                return
        elif entry.get("kind") == "files":
            try:
                copied = self.app.write_files_to_clipboard(entry.get("files") or ())
            except Exception:
                copied = False
            if not copied:
                messagebox.showinfo("写回失败", "无法把这些文件写回系统剪贴板。", parent=self.window)
                return
        else:
            value = self.text.get("1.0", "end-1c")
            try:
                self.window.clipboard_clear()
                self.window.clipboard_append(value)
                self.window.update()
            except tk.TclError as exc:
                messagebox.showinfo("写回失败", str(exc), parent=self.window)
                return
        self.last_signature = ""
        self.status_var.set("已写回系统剪贴板。")

    def save_selected_image(self) -> None:
        entry = self._selected()
        if entry is None or entry.get("kind") != "image":
            return
        path = filedialog.asksaveasfilename(
            title="另存剪贴板图片", defaultextension=".png",
            initialfile=f"剪贴板_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")], parent=self.window,
        )
        if not path:
            return
        try:
            Path(path).write_bytes(bytes(entry.get("png") or b""))
        except OSError as exc:
            messagebox.showinfo("保存失败", f"无法保存图片：\n{path}\n\n{exc}", parent=self.window)
            return
        self.status_var.set(f"已保存：{Path(path).name}")

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.configure(text="继续记录" if self.paused else "暂停记录")
        self.status_var.set("已暂停读取剪贴板。" if self.paused else "已继续读取剪贴板。")

    def clear_clipboard(self) -> None:
        try:
            self.window.clipboard_clear()
            self.window.update()
        except tk.TclError as exc:
            messagebox.showinfo("清空失败", str(exc), parent=self.window)
            return
        self.last_signature = ""
        self.status_var.set("系统剪贴板已清空；内存历史仍保留。")

    def clear_history(self) -> None:
        self.history.clear()
        current = read_clipboard_snapshot(self.window)
        self.last_signature = _snapshot_signature(current) if current is not None else ""
        self._refresh_list()
        self._show_empty()
        self.status_var.set("本次窗口历史已清空。")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._poll_id is not None:
            try:
                self.window.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        try:
            self.window.destroy()
        finally:
            if getattr(self.app, "clipboard_window", None) is self:
                self.app.clipboard_window = None


def open_tool(app, theme):
    existing = getattr(app, "clipboard_window", None)
    try:
        if existing is not None and not existing.closed and existing.window.winfo_exists():
            existing.window.deiconify()
            existing.window.lift(app.root)
            existing.window.focus_force()
            return existing
    except tk.TclError:
        pass
    window = ClipboardWindow(app, theme)
    app.clipboard_window = window
    return window
