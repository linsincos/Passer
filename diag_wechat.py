"""Live diagnostic for the WeChat auto-click. Run it WHILE the WeChat login
window (with the green 进入微信 button) is on screen:

    python diag_wechat.py

It prints WeChat's processes, its top-level windows, and the UI Automation
control tree around any 进入微信 / 微信 control. Send the whole output back.
"""
from __future__ import annotations

import os
import ctypes
from ctypes import wintypes


def list_processes() -> set:
    import subprocess
    print("=" * 70)
    print("PROCESSES (wechat / weixin / xwechat):")
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Where-Object {$_.Name -match 'wechat|weixin|xwechat'} | "
         "ForEach-Object { try { $_.Id.ToString()+'|'+$_.Name+'|'+$_.Path } "
         "catch { $_.Id.ToString()+'|'+$_.Name+'|' } }"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")
    pids = set()
    for line in (ps.stdout or "").strip().splitlines():
        print("  ", line)
        try:
            pids.add(int(line.split("|", 1)[0]))
        except Exception:
            pass
    if not pids:
        print("  (none found — is WeChat actually running?)")
    return pids


def list_win32_windows(pids: set) -> None:
    user32 = ctypes.windll.user32
    print("=" * 70)
    print("TOP-LEVEL WINDOWS (visible):")

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = ctypes.create_unicode_buffer(256); user32.GetWindowTextW(hwnd, t, 256)
        c = ctypes.create_unicode_buffer(256); user32.GetClassNameW(hwnd, c, 256)
        blob = (t.value + c.value).lower()
        mark = "  <-- WeChat" if (pid.value in pids or "wechat" in blob
                                  or "weixin" in blob or "微信" in t.value) else ""
        if mark or pid.value in pids:
            print(f"   pid={pid.value:<7} class={c.value!r:32} title={t.value!r}{mark}")
        return True

    user32.EnumWindows(cb, 0)


def dump_uia(pids: set) -> None:
    print("=" * 70)
    print("UI AUTOMATION control dump (controls containing 进入 / 微信):")
    try:
        from pywinauto import Desktop
    except Exception as exc:
        print("  pywinauto import failed:", exc)
        return
    any_win = False
    for win in Desktop(backend="uia").windows():
        try:
            pid = win.process_id()
            title = win.window_text() or ""
            cls = win.class_name() or ""
        except Exception:
            continue
        blob = (title + cls).lower()
        if not (pid in pids or "wechat" in blob or "weixin" in blob or "微信" in title):
            continue
        any_win = True
        print("-" * 60)
        print(f"WINDOW pid={pid} class={cls!r} title={title!r}")
        try:
            for ctrl in win.descendants():
                try:
                    name = ctrl.window_text() or ""
                    ctype = ctrl.element_info.control_type
                except Exception:
                    continue
                if "进入" in name or "微信" in name or "Enter" in name:
                    print(f"    name={name!r:18} control_type={ctype}")
        except Exception as exc:
            print("    descendants() failed:", exc)
    if not any_win:
        print("  No WeChat window seen by UIA (window may not expose UI Automation).")


if __name__ == "__main__":
    pids = list_processes()
    list_win32_windows(pids)
    dump_uia(pids)
    print("=" * 70)
    print("Done. Copy everything above and send it back.")
