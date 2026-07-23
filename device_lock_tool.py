from __future__ import annotations

import ctypes
import base64
import re
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox
from typing import Callable


PASSWORD_RE = re.compile(r"^[A-Za-z0-9]{2,8}$")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect_password(password: str) -> str:
    """Encrypt a remembered password for the current Windows user via DPAPI."""
    if not password or sys.platform != "win32":
        return ""
    raw = password.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    protected = _DataBlob()
    try:
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(
            ctypes.byref(source), "Passer Device Lock", None, None, None, 0x1,
            ctypes.byref(protected),
        ):
            return ""
        encrypted = ctypes.string_at(protected.pbData, protected.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        return ""
    finally:
        if protected.pbData:
            ctypes.windll.kernel32.LocalFree(protected.pbData)


def unprotect_password(value: str) -> str:
    if not value or not value.startswith("dpapi:") or sys.platform != "win32":
        return ""
    try:
        raw = base64.b64decode(value[6:].encode("ascii"), validate=True)
        buffer = ctypes.create_string_buffer(raw)
        source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        clear = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(clear)
        ):
            return ""
        return ctypes.string_at(clear.pbData, clear.cbData).decode("utf-8")
    except Exception:
        return ""
    finally:
        if "clear" in locals() and clear.pbData:
            ctypes.windll.kernel32.LocalFree(clear.pbData)


class DeviceLockController:
    """Windows low-level input hooks with an always-live password recognizer."""

    WH_KEYBOARD_LL = 13
    WH_MOUSE_LL = 14
    HC_ACTION = 0
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104
    WM_QUIT = 0x0012
    VK_BACK = 0x08
    VK_NUMPAD0 = 0x60
    VK_NUMPAD9 = 0x69

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    def __init__(self) -> None:
        self.disable_keyboard = False
        self.disable_mouse = False
        self._password = ""
        self._typed = ""
        self._active = threading.Event()
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._keyboard_hook = None
        self._mouse_hook = None
        self._keyboard_proc = None
        self._mouse_proc = None
        self.error: str | None = None
        self.unlocked_by_password = False

    @property
    def active(self) -> bool:
        return self._active.is_set()

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    def start(self, *, disable_keyboard: bool, disable_mouse: bool, password: str) -> bool:
        if sys.platform != "win32":
            self.error = "设备锁仅支持 Windows。"
            return False
        if self.active:
            return True
        self.disable_keyboard = bool(disable_keyboard)
        self.disable_mouse = bool(disable_mouse)
        self._password = password.lower()
        self._typed = ""
        self.error = None
        self.unlocked_by_password = False
        self._ready.clear()
        self._finished.clear()
        self._active.set()
        self._thread = threading.Thread(target=self._run, name="Passer-DeviceLock", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0):
            self.error = "输入锁启动超时。"
            self.stop()
            return False
        return self.active and not self.error

    def stop(self) -> None:
        self._active.clear()
        if self._thread_id and sys.platform == "win32":
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
            except Exception:
                pass

    @staticmethod
    def _vk_character(vk: int) -> str:
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if DeviceLockController.VK_NUMPAD0 <= vk <= DeviceLockController.VK_NUMPAD9:
            return str(vk - DeviceLockController.VK_NUMPAD0)
        return ""

    def _consume_vk(self, vk: int) -> bool:
        """Record one key-down; return True as soon as the password is matched."""
        if vk == self.VK_BACK:
            self._typed = self._typed[:-1]
            return False
        char = self._vk_character(vk)
        if not char:
            return False
        self._typed = (self._typed + char)[-max(64, len(self._password)):]
        return bool(self._password and self._typed.endswith(self._password))

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        lresult = ctypes.c_ssize_t
        hook_proc_type = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def keyboard_proc(code, wparam, lparam):
            if code == self.HC_ACTION and int(wparam) in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                data = ctypes.cast(lparam, ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
                if self._consume_vk(int(data.vkCode)):
                    self.unlocked_by_password = True
                    self._password = ""
                    self._active.clear()
                    user32.PostQuitMessage(0)
                    return 1
            if code == self.HC_ACTION and self._active.is_set() and self.disable_keyboard:
                return 1
            return user32.CallNextHookEx(self._keyboard_hook, code, wparam, lparam)

        def mouse_proc(code, wparam, lparam):
            if code == self.HC_ACTION and self._active.is_set() and self.disable_mouse:
                return 1
            return user32.CallNextHookEx(self._mouse_hook, code, wparam, lparam)

        self._keyboard_proc = hook_proc_type(keyboard_proc)
        self._mouse_proc = hook_proc_type(mouse_proc)
        try:
            self._thread_id = int(kernel32.GetCurrentThreadId())
            module = kernel32.GetModuleHandleW(None)
            self._keyboard_hook = user32.SetWindowsHookExW(
                self.WH_KEYBOARD_LL, self._keyboard_proc, module, 0
            )
            if not self._keyboard_hook:
                raise ctypes.WinError()
            if self.disable_mouse:
                self._mouse_hook = user32.SetWindowsHookExW(
                    self.WH_MOUSE_LL, self._mouse_proc, module, 0
                )
                if not self._mouse_hook:
                    raise ctypes.WinError()
            self._ready.set()
            msg = wintypes.MSG()
            while self._active.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self.error = str(exc)
        finally:
            self._active.clear()
            if self._mouse_hook:
                user32.UnhookWindowsHookEx(self._mouse_hook)
            if self._keyboard_hook:
                user32.UnhookWindowsHookEx(self._keyboard_hook)
            self._mouse_hook = None
            self._keyboard_hook = None
            self._password = ""
            self._typed = ""
            self._ready.set()
            self._finished.set()


class DeviceLockWindow:
    """Small configuration dialog that hands off to a background lock session."""

    def __init__(self, app, *, on_locked: Callable[[DeviceLockController], None],
                 initial_password: str = "", initial_keyboard: bool = True,
                 initial_mouse: bool = False) -> None:
        self.app = app
        self.on_locked = on_locked
        self.closed = False
        self.window = tk.Toplevel(app.root)
        self.window.title("设备锁")
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.resizable(False, False)
        try:
            theme = app.clicker_theme()
        except Exception:
            theme = None
        border = getattr(theme, "border", "#cbd5e1")
        title_bg = getattr(theme, "title_bg", "#111827")
        title_button_bg = getattr(theme, "title_button_bg", title_bg)
        accent = getattr(theme, "accent", "#2563eb")
        accent_hover = getattr(theme, "accent_hover", "#1d4ed8")
        accent_soft = getattr(theme, "accent_soft", "#e8f1ff")
        self.window.configure(bg=border)
        self.window.attributes("-topmost", bool(app.topmost_var.get()))
        self._move_state = None

        self.keyboard_var = tk.BooleanVar(value=bool(initial_keyboard))
        self.mouse_var = tk.BooleanVar(value=bool(initial_mouse))
        self.password_var = tk.StringVar(value=initial_password)
        self.confirm_var = tk.StringVar(value=initial_password)

        body = tk.Frame(self.window, bg="#ffffff", highlightthickness=1, highlightbackground=border)
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        titlebar = tk.Frame(body, bg=title_bg, height=52)
        titlebar.pack(fill=tk.X)
        titlebar.pack_propagate(False)
        title_label = tk.Label(
            titlebar, text="设备锁", bg=title_bg, fg="#ffffff",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        title_label.pack(side=tk.LEFT, padx=18)
        close_button = tk.Button(
            titlebar, text="×", command=self.close, width=3, bd=0, relief=tk.FLAT,
            bg=title_button_bg, fg="#ffffff", activebackground="#ef4444",
            activeforeground="#ffffff", font=("Microsoft YaHei UI", 12, "bold"),
            cursor="hand2",
        )
        close_button.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=8)
        for widget in (titlebar, title_label):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._move)

        content = tk.Frame(body, bg="#ffffff")
        content.pack(fill=tk.BOTH, expand=True, padx=22, pady=18)
        tk.Label(content, text="选择要禁用的设备", bg="#ffffff", fg="#111827",
                 anchor=tk.W, font=("Microsoft YaHei UI", 10, "bold")).pack(fill=tk.X)
        tk.Checkbutton(content, text="禁用键盘", variable=self.keyboard_var, bg="#ffffff",
                       fg="#334155", activebackground="#ffffff", activeforeground="#111827",
                       selectcolor=accent_soft, bd=0,
                       font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W, pady=(10, 2))
        tk.Checkbutton(content, text="禁用鼠标", variable=self.mouse_var, bg="#ffffff",
                       fg="#334155", activebackground="#ffffff", activeforeground="#111827",
                       selectcolor=accent_soft, bd=0,
                       font=("Microsoft YaHei UI", 10)).pack(anchor=tk.W)

        tk.Label(content, text="解锁密码（2–8 位英文或数字，不区分大小写）", bg="#ffffff", fg="#334155",
                 anchor=tk.W, font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, pady=(16, 6))
        self.password_entry = tk.Entry(content, textvariable=self.password_var, show="•", bd=0,
                                       bg="#f1f5f9", fg="#111827", insertbackground="#111827",
                                       highlightthickness=1, highlightbackground=border,
                                       highlightcolor=accent, font=("Microsoft YaHei UI", 10))
        self.password_entry.pack(fill=tk.X, ipady=7)
        tk.Label(content, text="确认密码", bg="#ffffff", fg="#334155", anchor=tk.W,
                 font=("Microsoft YaHei UI", 9)).pack(fill=tk.X, pady=(12, 6))
        confirm_entry = tk.Entry(content, textvariable=self.confirm_var, show="•", bd=0,
                                 bg="#f1f5f9", fg="#111827", insertbackground="#111827",
                                 highlightthickness=1, highlightbackground=border,
                                 highlightcolor=accent, font=("Microsoft YaHei UI", 10))
        confirm_entry.pack(fill=tk.X, ipady=7)

        tk.Label(content, text="锁定后直接在键盘输入密码即可自动解锁。",
                 bg="#ffffff", fg="#64748b", anchor=tk.W,
                 font=("Microsoft YaHei UI", 8)).pack(fill=tk.X, pady=(12, 0))

        actions = tk.Frame(body, bg="#f8fafc")
        actions.pack(fill=tk.X)
        tk.Button(actions, text="确认锁定", command=self.confirm, bd=0, padx=18, pady=8,
                  bg=accent, fg="#ffffff", activebackground=accent_hover,
                  activeforeground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"),
                  cursor="hand2").pack(side=tk.RIGHT, padx=(8, 16), pady=12)
        tk.Button(actions, text="取消", command=self.close, bd=0, padx=18, pady=8,
                  bg="#e2e8f0", fg="#334155", activebackground="#cbd5e1",
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side=tk.RIGHT, pady=12)

        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.bind("<Return>", lambda _event: self.confirm())
        self.window.update_idletasks()
        width, height = 440, self.window.winfo_reqheight()
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - width) // 2)
        y = app.root.winfo_rooty() + max(0, (app.root.winfo_height() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.deiconify()
        self.window.lift()
        self.password_entry.focus_set()

    def _start_move(self, event) -> None:
        self._move_state = (
            event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y()
        )

    def _move(self, event) -> None:
        if self._move_state is None:
            return
        start_x, start_y, window_x, window_y = self._move_state
        self.window.geometry(f"+{window_x + event.x_root - start_x}+{window_y + event.y_root - start_y}")

    def confirm(self) -> None:
        if not self.keyboard_var.get() and not self.mouse_var.get():
            messagebox.showinfo("设备锁", "请至少选择禁用键盘或禁用鼠标。", parent=self.window)
            return
        password = self.password_var.get()
        if not PASSWORD_RE.fullmatch(password):
            messagebox.showinfo("设备锁", "密码必须是 2–8 位英文字母或数字。", parent=self.window)
            return
        if password.lower() != self.confirm_var.get().lower():
            messagebox.showinfo("设备锁", "两次输入的密码不一致。", parent=self.window)
            return
        controller = DeviceLockController()
        if not controller.start(
            disable_keyboard=self.keyboard_var.get(),
            disable_mouse=self.mouse_var.get(),
            password=password,
        ):
            messagebox.showerror("设备锁启动失败", controller.error or "无法安装输入钩子。", parent=self.window)
            return
        self.password_var.set("")
        self.confirm_var.set("")
        try:
            self.app.remember_device_lock_settings(
                password,
                self.keyboard_var.get(),
                self.mouse_var.get(),
            )
        except Exception:
            pass
        self.on_locked(controller)
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "device_lock_window", None) is self:
            self.app.device_lock_window = None
        try:
            self.window.destroy()
        except Exception:
            pass
