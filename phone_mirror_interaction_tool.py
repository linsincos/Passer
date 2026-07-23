from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable
from urllib.parse import unquote

try:
    from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP

    TKDND_AVAILABLE = True
except Exception:  # pragma: no cover - optional drag/drop dependency
    COPY = "copy"
    DND_FILES = None
    REFUSE_DROP = "refuse_drop"
    TKDND_AVAILABLE = False

try:
    from clicker_tool import ClickerTheme
except Exception:  # pragma: no cover - allows standalone import for tests
    ClickerTheme = Any  # type: ignore


MODULE_NAME = "手机投屏交互"
DEFAULT_WINDOW_KEYWORDS = (
    "scrcpy",
    "qtscrcpy",
    "投屏",
    "手机",
    "移动设备",
    "无线显示",
    "多屏协同",
    "跨屏互联",
    "手机互联",
    "智慧互联",
    "电脑管家",
    "phone",
    "phone link",
    "link to windows",
    "your phone",
    "android",
    "mirror",
    "mirroring",
    "screen mirror",
    "screen mirroring",
    "cast",
    "dex",
    "samsung dex",
    "samsung flow",
    "smart view",
    "huawei share",
    "harmonyos",
    "honor",
    "xiaomi",
    "miui",
    "miui+",
    "hyperconnect",
    "vivo",
    "oppo",
    "coloros",
    "realme",
    "oneplus",
    "motorola",
    "ready for",
    "smart connect",
    "lenovo",
    "airdroid",
    "apowermirror",
    "letsview",
    "vysor",
    "wormhole",
)
SCRCPY_HELP_CACHE: dict[str, str] = {}
FPS_RE = re.compile(r"(?i)(?:(?:^|\s)(\d+(?:\.\d+)?)\s*fps\b|\bfps[:=\s]+(\d+(?:\.\d+)?))")
MOUSE_MODE_LABELS = {
    "seamless": "无缝（推荐）",
    "uhid": "UHID 高级",
}
MOUSE_MODE_KEYS = {label: key for key, label in MOUSE_MODE_LABELS.items()}
AUDIO_MODE_LABELS = {
    "sync": "同步",
    "pc_only": "仅电脑",
    "phone_only": "仅手机",
}
AUDIO_MODE_KEYS = {label: key for key, label in AUDIO_MODE_LABELS.items()}
DEFAULT_PHONE_TRANSFER_DIR = "/sdcard/Download/Passer"


def normalize_phone_mirror_settings(settings: dict | None, spec: dict | None = None) -> dict:
    """Validate and merge the persisted phone-mirror settings."""
    current = dict(settings or {})
    values = dict(spec or {})

    mouse_explicit = "mouse_mode" in values or "phone_mirror_mouse_mode" in values
    mouse_raw = str(
        values.get("mouse_mode") or values.get("phone_mirror_mouse_mode")
        or current.get("phone_mirror_mouse_mode") or "seamless"
    ).strip().casefold()
    mouse_aliases = {
        "seamless": "seamless", "sdk": "seamless", "无缝": "seamless", "无缝模式": "seamless",
        "uhid": "uhid", "advanced": "uhid", "高级": "uhid", "高级模式": "uhid",
    }
    if mouse_raw not in mouse_aliases:
        if mouse_explicit:
            raise ValueError("mouse_mode 仅支持 seamless（无缝）或 uhid（高级）。")
        mouse_raw = "seamless"

    audio_explicit = "audio_mode" in values or "phone_mirror_audio_mode" in values
    audio_raw = str(
        values.get("audio_mode") or values.get("phone_mirror_audio_mode")
        or current.get("phone_mirror_audio_mode") or "sync"
    ).strip().casefold()
    audio_aliases = {
        "sync": "sync", "同步": "sync", "both": "sync",
        "pc_only": "pc_only", "pc": "pc_only", "仅电脑": "pc_only", "电脑": "pc_only",
        "phone_only": "phone_only", "phone": "phone_only", "仅手机": "phone_only", "手机": "phone_only",
    }
    if audio_raw not in audio_aliases:
        if audio_explicit:
            raise ValueError("audio_mode 仅支持 sync、pc_only 或 phone_only。")
        audio_raw = "sync"

    sensitivity_raw = (
        values.get("mouse_sensitivity")
        if values.get("mouse_sensitivity") is not None
        else values.get("phone_mirror_mouse_sensitivity")
    )
    if sensitivity_raw is None:
        sensitivity_raw = current.get("phone_mirror_mouse_sensitivity", 8)
    sensitivity = int(sensitivity_raw)
    if not 1 <= sensitivity <= 15:
        raise ValueError("mouse_sensitivity 必须在 1 到 15 之间。")

    transfer_path = str(
        values.get("transfer_path") or values.get("phone_mirror_transfer_path")
        or current.get("phone_mirror_transfer_path") or DEFAULT_PHONE_TRANSFER_DIR
    ).strip().replace("\\", "/")
    if not transfer_path.startswith("/") or any(char in transfer_path for char in "\r\n\0"):
        raise ValueError("transfer_path 必须是以 / 开头的 Android 绝对路径。")
    transfer_path = transfer_path.rstrip("/") or DEFAULT_PHONE_TRANSFER_DIR

    if "ip" in values:
        wireless_ip = str(values.get("ip") or "").strip()
    elif "wireless_ip" in values:
        wireless_ip = str(values.get("wireless_ip") or "").strip()
    else:
        wireless_ip = str(current.get("phone_mirror_wireless_ip") or "").strip()
    embedded_port = ""
    if wireless_ip.count(":") == 1:
        wireless_ip, embedded_port = wireless_ip.rsplit(":", 1)
    if wireless_ip and (re.search(r"\s", wireless_ip) or "/" in wireless_ip):
        raise ValueError("wireless_ip 格式无效。")
    port_raw = (
        values.get("port") or values.get("wireless_port") or embedded_port
        or current.get("phone_mirror_wireless_port") or 5555
    )
    port = int(port_raw)
    if not 1 <= port <= 65535:
        raise ValueError("wireless_port 必须在 1 到 65535 之间。")
    if "serial" in values:
        preferred_serial = str(values.get("serial") or "").strip()
    elif "device_serial" in values:
        preferred_serial = str(values.get("device_serial") or "").strip()
    else:
        preferred_serial = str(current.get("phone_mirror_device_serial") or "").strip()

    return {
        "phone_mirror_mouse_mode": mouse_aliases[mouse_raw],
        "phone_mirror_mouse_sensitivity": sensitivity,
        "phone_mirror_audio_mode": audio_aliases[audio_raw],
        "phone_mirror_transfer_path": transfer_path,
        "phone_mirror_wireless_ip": wireless_ip,
        "phone_mirror_wireless_port": port,
        "phone_mirror_device_serial": preferred_serial,
    }


def phone_mirror_status(module_dir: Path | str, settings: dict | None = None) -> str:
    config = normalize_phone_mirror_settings(settings)
    module_path = Path(module_dir)
    adb_path = find_adb_candidates(module_path)[0]
    scrcpy_path = find_scrcpy_path(module_path)
    try:
        devices = list_adb_devices(adb_path)
        device_payload = [
            {"serial": item.serial, "state": item.state, "label": item.label}
            for item in devices
        ]
        error = ""
    except Exception as exc:
        device_payload = []
        error = str(exc)
    return json.dumps(
        {
            "adb_path": adb_path,
            "scrcpy_path": scrcpy_path,
            "scrcpy_available": bool(scrcpy_path),
            "devices": device_payload,
            "device_error": error,
            "settings": config,
        },
        ensure_ascii=False,
        indent=2,
    )


def run_phone_mirror_network_action(
    module_dir: Path | str,
    spec: dict,
    action: str,
    settings: dict | None = None,
) -> str:
    config = normalize_phone_mirror_settings(settings, spec)
    adb_path = str(spec.get("adb_path") or find_adb_candidates(Path(module_dir))[0])
    action = str(action or "").strip().lower()
    host = config["phone_mirror_wireless_ip"]
    port = config["phone_mirror_wireless_port"]
    target = f"{host}:{port}" if host else ""
    if action in {"phone_mirror_connect", "phone_connect"}:
        if not host:
            raise ValueError("phone_mirror_connect 需要 ip，可选 port（默认 5555）。")
        command = [adb_path, "connect", target]
        label = f"已连接无线 ADB：{target}"
    elif action in {"phone_mirror_disconnect", "phone_disconnect"}:
        if not host:
            raise ValueError("phone_mirror_disconnect 需要 ip，可选 port。")
        command = [adb_path, "disconnect", target]
        label = f"已断开无线 ADB：{target}"
    elif action in {"phone_mirror_pair", "phone_pair"}:
        address = str(spec.get("pair_address") or spec.get("address") or target).strip()
        code = re.sub(r"\s+", "", str(spec.get("pair_code") or spec.get("code") or ""))
        if not PhoneMirrorWindow._valid_host_port(address):
            raise ValueError("phone_mirror_pair 需要 address，格式为 IP:端口。")
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("phone_mirror_pair 需要手机显示的 6 位 pair_code。")
        command = [adb_path, "pair", address, code]
        label = f"已完成无线 ADB 配对：{address}"
    else:
        raise ValueError(f"不支持的手机投屏网络动作：{action}")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=_no_window_flags(),
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if len(command) >= 4 and action in {"phone_mirror_pair", "phone_pair"}:
        output = output.replace(command[-1], "******")
    lowered = output.casefold()
    if completed.returncode != 0 or "failed" in lowered or "unable" in lowered:
        raise RuntimeError(output or f"ADB 命令失败（{completed.returncode}）。")
    return label + (f"；{output}" if output else "。")


ROTATION_LABELS = {
    0: "0",
    90: "90",
    180: "180",
    270: "270",
}
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
SW_MINIMIZE = 6
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
VK_ALIASES = {
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "OPTION": 0x12,
    "WIN": 0x5B,
    "WINDOWS": 0x5B,
    "CMD": 0x5B,
    "COMMAND": 0x5B,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
    "SPACE": 0x20,
    "BACKSPACE": 0x08,
    "BKSP": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PGUP": 0x21,
    "PAGEDOWN": 0x22,
    "PGDN": 0x22,
    "UP": 0x26,
    "ARROWUP": 0x26,
    "DOWN": 0x28,
    "ARROWDOWN": 0x28,
    "LEFT": 0x25,
    "ARROWLEFT": 0x25,
    "RIGHT": 0x27,
    "ARROWRIGHT": 0x27,
    "PRINTSCREEN": 0x2C,
    "PRTSC": 0x2C,
    "CAPSLOCK": 0x14,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
    "PAUSE": 0x13,
    "MENU": 0x5D,
}
for _i in range(1, 25):
    VK_ALIASES[f"F{_i}"] = 0x70 + _i - 1


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _terminate_process(proc: subprocess.Popen | None, timeout: float = 3.0) -> bool:
    """Terminate a child process and guarantee it is reaped before returning."""
    if proc is None or proc.poll() is not None:
        return True
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=1.5)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def quote_adb_text(text: str) -> str:
    """Encode text for `adb shell input text`.

    Android's `input text` accepts percent-escaped spaces. Most other shell
    special characters are escaped with a backslash and kept best-effort.
    Unicode input support depends on the connected device keyboard/IME.
    """
    escaped = []
    for char in text:
        if char == " ":
            escaped.append("%s")
        elif char in r"""\"'`$&|;<>(){}[]!*?#~""":
            escaped.append("\\" + char)
        elif char == "\n":
            escaped.append("%n")
        else:
            escaped.append(char)
    return "".join(escaped)


def join_adb_shell_args(args: list[str]) -> str:
    return " ".join(str(item) for item in args)


def parse_first_size(text: str) -> tuple[int, int] | None:
    matches = []
    for match in re.finditer(r"(?<!\d)(\d{3,5})\s*(?:x|\*)\s*(\d{3,5})(?!\d)", text, re.I):
        width, height = int(match.group(1)), int(match.group(2))
        if 240 <= width <= 10000 and 240 <= height <= 10000:
            matches.append((width, height))
    if not matches:
        spaced = re.search(r"\b(?:real|app|logical|cur|init)\s+(\d{3,5})\s+x\s+(\d{3,5})\b", text, re.I)
        if spaced:
            return int(spaced.group(1)), int(spaced.group(2))
        return None
    return max(matches, key=lambda size: size[0] * size[1])


def parse_rotation(text: str) -> int:
    patterns = (
        r"SurfaceOrientation:\s*([0-3])",
        r"orientation\s*=\s*([0-3])",
        r"mCurrentRotation\s*=\s*ROTATION_(0|90|180|270)",
        r"mRotation\s*=\s*ROTATION_(0|90|180|270)",
        r"mRotation\s*=\s*([0-3])",
        r"rotation\s*([0-3])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = int(match.group(1))
        return {0: 0, 1: 90, 2: 180, 3: 270}.get(value, value)
    return 0


def choose_size_for_window(
    sizes: list[tuple[int, int]],
    projection_window: WindowInfo | None,
) -> tuple[int, int] | None:
    unique: list[tuple[int, int]] = []
    for width, height in sizes:
        if width <= 0 or height <= 0:
            continue
        pair = (int(width), int(height))
        if pair not in unique:
            unique.append(pair)
    if not unique:
        return None
    if projection_window is None:
        return max(unique, key=lambda size: size[0] * size[1])
    view_w = projection_window.client_width or projection_window.width
    view_h = projection_window.client_height or projection_window.height
    if view_w <= 0 or view_h <= 0:
        return max(unique, key=lambda size: size[0] * size[1])
    view_ratio = view_w / max(1, view_h)

    def score(size: tuple[int, int]) -> float:
        width, height = size
        ratios = (width / max(1, height), height / max(1, width))
        ratio_score = min(abs(view_ratio - ratios[0]), abs(view_ratio - ratios[1]))
        area_bonus = -0.00000001 * width * height
        return ratio_score + area_bonus

    return min(unique, key=score)


def vk_from_token(token: str) -> int | None:
    value = re.sub(r"\s+", "", token or "").upper()
    if not value:
        return None
    if value in VK_ALIASES:
        return VK_ALIASES[value]
    if len(value) == 1 and "A" <= value <= "Z":
        return ord(value)
    if len(value) == 1 and "0" <= value <= "9":
        return ord(value)
    if value.startswith("VK_"):
        try:
            return int(value[3:], 16)
        except ValueError:
            return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def parse_hotkey(text: str) -> list[int]:
    parts = [part for part in re.split(r"\s*(?:\+|,|，|、)\s*", text or "") if part]
    keys = [vk_from_token(part) for part in parts]
    return [key for key in keys if key is not None]


def focus_window(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def set_window_topmost(hwnd: int, enabled: bool) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST if enabled else HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE,
        )
        return True
    except Exception:
        return False


def minimize_window(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True
    except Exception:
        return False


def send_virtual_keys(keys: list[int], target_hwnd: int = 0, restore_focus: bool = True, delay: float = 0.025) -> bool:
    if sys.platform != "win32" or not keys:
        return False
    if target_hwnd and restore_focus:
        focus_window(target_hwnd)
        time.sleep(0.05)
    user32 = ctypes.windll.user32
    pressed: list[int] = []
    try:
        for key in keys:
            user32.keybd_event(int(key), 0, 0, 0)
            pressed.append(int(key))
            time.sleep(delay)
        for key in reversed(pressed):
            user32.keybd_event(int(key), 0, KEYEVENTF_KEYUP, 0)
            time.sleep(delay)
        return True
    except Exception:
        for key in reversed(pressed):
            try:
                user32.keybd_event(int(key), 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
        return False


@dataclass
class AdbDevice:
    serial: str
    state: str
    product: str = ""
    model: str = ""
    device: str = ""

    @property
    def label(self) -> str:
        name = self.model or self.product or self.device
        return f"{self.serial} ({name})" if name else self.serial


@dataclass
class DeviceDisplayInfo:
    width: int
    height: int
    rotation: int = 0
    density: str = ""
    manufacturer: str = ""
    brand: str = ""
    model: str = ""
    android_version: str = ""
    source: str = ""
    display_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        name = " ".join(part for part in (self.brand or self.manufacturer, self.model) if part).strip()
        prefix = f"{name}，" if name else ""
        density = f"，{self.density} dpi" if self.density else ""
        android = f"，Android {self.android_version}" if self.android_version else ""
        displays = f"，显示ID {'/'.join(self.display_ids)}" if len(self.display_ids) > 1 else ""
        return f"{prefix}{self.width}x{self.height}，旋转 {self.rotation}°{density}{android}{displays}"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    left: int
    top: int
    right: int
    bottom: int
    client_left: int
    client_top: int
    client_right: int
    client_bottom: int
    pid: int = 0

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def client_width(self) -> int:
        return max(0, self.client_right - self.client_left)

    @property
    def client_height(self) -> int:
        return max(0, self.client_bottom - self.client_top)

    @property
    def label(self) -> str:
        size = f"{self.client_width}x{self.client_height}" if self.client_width else f"{self.width}x{self.height}"
        return f"{self.title}  [{size}]"


def projection_transfer_dock_geometry(window: WindowInfo, dock_height: int = 46) -> tuple[int, int, int, int]:
    """Return a compact dock rectangle inside the bottom of a projection client area."""

    client_width = window.client_width or window.width
    left = window.client_left if window.client_width else window.left
    bottom = window.client_bottom if window.client_height else window.bottom
    dock_width = min(client_width, max(120, min(520, client_width - 16)))
    x = left + max(0, (client_width - dock_width) // 2)
    y = bottom - dock_height - 8
    return dock_width, dock_height, x, y


class AdbClient:
    def __init__(self, adb_path: str = "adb", serial: str = ""):
        self.adb_path = adb_path or "adb"
        self.serial = serial

    def base_command(self) -> list[str]:
        command = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        return command

    def run(self, args: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.base_command() + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_no_window_flags(),
        )

    def shell(self, args: list[str], timeout: float = 12.0) -> str:
        result = self.run(["shell", *args], timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "ADB command failed").strip())
        return result.stdout.strip()

    def tap(self, x: int, y: int) -> str:
        return self.shell(["input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        return self.shell(["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(max(1, int(duration_ms)))])

    def keyevent(self, key: str | int) -> str:
        return self.shell(["input", "keyevent", str(key)])

    def text(self, value: str) -> str:
        return self.shell(["input", "text", quote_adb_text(value)])

    def wm_size(self) -> tuple[int, int] | None:
        output = self.shell(["wm", "size"])
        return parse_first_size(output)

    def getprop(self, name: str) -> str:
        try:
            return self.shell(["getprop", name], timeout=4.0).strip()
        except Exception:
            return ""

    def wm_density(self) -> str:
        try:
            output = self.shell(["wm", "density"], timeout=4.0)
        except Exception:
            return ""
        match = re.search(r"(\d+)", output)
        return match.group(1) if match else ""

    def display_info(self, projection_window: WindowInfo | None = None) -> DeviceDisplayInfo | None:
        manufacturer = self.getprop("ro.product.manufacturer")
        brand = self.getprop("ro.product.brand")
        model = self.getprop("ro.product.model")
        android_version = self.getprop("ro.build.version.release")
        density = self.wm_density()
        sizes: list[tuple[int, int]] = []
        notes: list[str] = []
        display_ids: set[str] = set()

        for label, command, timeout in (
            ("wm size", ["wm", "size"], 5.0),
            ("dumpsys display", ["dumpsys", "display"], 8.0),
            ("dumpsys window", ["dumpsys", "window"], 8.0),
            ("dumpsys SurfaceFlinger", ["dumpsys", "SurfaceFlinger", "--display-id"], 8.0),
        ):
            try:
                output = self.shell(command, timeout=timeout)
            except Exception:
                continue
            size = parse_first_size(output)
            if size:
                sizes.append(size)
                notes.append(label)
            display_ids.update(re.findall(r"\bdisplayId\s+(-?\d+)\b", output))
            display_ids.update(re.findall(r"\bDisplay\s+(-?\d+)\b", output))

        size = choose_size_for_window(sizes, projection_window)
        if size is None:
            return None
        rotation_text = ""
        for command in (["dumpsys", "input"], ["dumpsys", "window"], ["dumpsys", "display"]):
            try:
                rotation_text += "\n" + self.shell(command, timeout=6.0)
            except Exception:
                pass
        rotation = parse_rotation(rotation_text)
        return DeviceDisplayInfo(
            width=size[0],
            height=size[1],
            rotation=rotation,
            density=density,
            manufacturer=manufacturer,
            brand=brand,
            model=model,
            android_version=android_version,
            source=", ".join(notes[:3]),
            display_ids=tuple(sorted(display_ids, key=lambda value: int(value) if value.lstrip("-").isdigit() else 999)),
            notes=tuple(notes),
        )

    def current_focus(self) -> str:
        try:
            return self.shell(["dumpsys", "window", "windows"], timeout=7.0)
        except Exception:
            return ""


@dataclass
class AdbShellTask:
    adb_path: str
    serial: str
    args: list[str]
    label: str
    on_done: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None


class FastAdbShell:
    """Reuse one interactive `adb shell` for latency-sensitive input commands."""

    def __init__(self) -> None:
        self.tasks: "queue.Queue[AdbShellTask | None]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen[str] | None = None
        self.proc_key: tuple[str, str] | None = None
        self.lock = threading.Lock()

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._run, daemon=True, name="Passer-FastAdbShell")
        self.worker.start()

    def enqueue(self, task: AdbShellTask) -> None:
        self.start()
        self.tasks.put(task)

    def stop(self) -> None:
        self.stop_event.set()
        self.tasks.put(None)
        self._close_proc()

    def _base_command(self, adb_path: str, serial: str) -> list[str]:
        command = [adb_path or "adb"]
        if serial:
            command.extend(["-s", serial])
        return command

    def _ensure_proc(self, adb_path: str, serial: str) -> subprocess.Popen[str]:
        key = (adb_path or "adb", serial or "")
        proc = self.proc
        if proc is not None and self.proc_key == key and proc.poll() is None and proc.stdin is not None:
            return proc
        self._close_proc()
        self.proc = subprocess.Popen(
            self._base_command(*key) + ["shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_no_window_flags(),
        )
        self.proc_key = key
        return self.proc

    def _close_proc(self) -> None:
        with self.lock:
            proc = self.proc
            self.proc = None
            self.proc_key = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write("exit\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    def _run_fallback(self, task: AdbShellTask) -> None:
        result = subprocess.run(
            self._base_command(task.adb_path, task.serial) + ["shell", *task.args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8.0,
            creationflags=_no_window_flags(),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "ADB command failed").strip())

    def _run(self) -> None:
        while not self.stop_event.is_set():
            task = self.tasks.get()
            if task is None:
                break
            try:
                proc = self._ensure_proc(task.adb_path, task.serial)
                if proc.stdin is None:
                    raise RuntimeError("ADB shell stdin is not available")
                proc.stdin.write(join_adb_shell_args(task.args) + "\n")
                proc.stdin.flush()
                if task.on_done:
                    task.on_done(task.label)
            except Exception as first_error:
                self._close_proc()
                try:
                    self._run_fallback(task)
                    if task.on_done:
                        task.on_done(task.label)
                except Exception as fallback_error:
                    if task.on_error:
                        task.on_error(str(fallback_error or first_error))
        self._close_proc()


def find_adb_candidates(module_dir: Path | None = None) -> list[str]:
    candidates: list[str] = []

    def add(path: str | Path) -> None:
        value = str(path)
        if value and value not in candidates:
            candidates.append(value)

    env_adb = os.environ.get("ADB")
    if env_adb:
        add(env_adb)
    if module_dir:
        add(module_dir / "tools" / "scrcpy" / "adb.exe")
        add(module_dir / "tools" / "scrcpy" / "platform-tools" / "adb.exe")
        add(module_dir / "tools" / "platform-tools" / "adb.exe")
        add(module_dir / "platform-tools" / "adb.exe")
        add(module_dir / "adb.exe")
    for found in ("adb", shutil.which("adb") or ""):
        if found:
            add(found)
    return candidates


def list_adb_devices(adb_path: str = "adb", timeout: float = 8.0) -> list[AdbDevice]:
    result = subprocess.run(
        [adb_path, "devices", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_no_window_flags(),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "adb devices failed").strip())
    devices: list[AdbDevice] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        attrs = {}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                attrs[key] = value
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                product=attrs.get("product", ""),
                model=attrs.get("model", "").replace("_", " "),
                device=attrs.get("device", ""),
            )
        )
    return devices


def find_scrcpy_path(module_dir: Path | None = None) -> str:
    found = shutil.which("scrcpy")
    if found:
        return found
    if module_dir:
        for path in (
            module_dir / "tools" / "scrcpy" / "scrcpy.exe",
            module_dir / "scrcpy" / "scrcpy.exe",
            module_dir / "scrcpy.exe",
        ):
            if path.exists():
                return str(path)
    return ""


def scrcpy_help(scrcpy_path: str) -> str:
    cached = SCRCPY_HELP_CACHE.get(scrcpy_path)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [scrcpy_path, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
            creationflags=_no_window_flags(),
        )
        text = (result.stdout or "") + "\n" + (result.stderr or "")
    except Exception:
        text = ""
    SCRCPY_HELP_CACHE[scrcpy_path] = text
    return text


def scrcpy_supports(scrcpy_path: str, option: str) -> bool:
    return option in scrcpy_help(scrcpy_path)


def enable_scrcpy_clipboard_sync(command: list[str], scrcpy_path: str) -> bool:
    """Keep Ctrl available to Android while retaining scrcpy's default clipboard autosync."""
    if scrcpy_supports(scrcpy_path, "--shortcut-mod"):
        command.append("--shortcut-mod=lalt,lsuper")
    return scrcpy_supports(scrcpy_path, "--no-clipboard-autosync")


def enumerate_windows() -> list[WindowInfo]:
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    windows: list[WindowInfo] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = _clean_space(buffer.value)
            if not title:
                return True

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True

            client = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(client))
            pt1 = wintypes.POINT(client.left, client.top)
            pt2 = wintypes.POINT(client.right, client.bottom)
            user32.ClientToScreen(hwnd, ctypes.byref(pt1))
            user32.ClientToScreen(hwnd, ctypes.byref(pt2))
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))

            info = WindowInfo(
                hwnd=int(hwnd),
                title=title,
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
                client_left=int(pt1.x),
                client_top=int(pt1.y),
                client_right=int(pt2.x),
                client_bottom=int(pt2.y),
                pid=int(process_id.value),
            )
            if info.width >= 120 and info.height >= 120:
                windows.append(info)
        except Exception:
            return True
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    windows.sort(key=lambda item: (not _looks_like_phone_window(item.title), item.title.casefold()))
    return windows


def _looks_like_phone_window(title: str) -> bool:
    lower = title.casefold()
    return any(keyword.casefold() in lower for keyword in DEFAULT_WINDOW_KEYWORDS)


def map_window_point_to_device(
    screen_x: int,
    screen_y: int,
    window: WindowInfo,
    device_w: int,
    device_h: int,
    mode: str = "contain",
    rotate: int = 0,
) -> tuple[int, int] | None:
    if device_w <= 0 or device_h <= 0:
        return None
    rotate = rotate % 360
    view_device_w, view_device_h = (
        (device_h, device_w) if rotate in (90, 270) else (device_w, device_h)
    )
    left, top = window.client_left, window.client_top
    view_w, view_h = window.client_width, window.client_height
    if view_w <= 0 or view_h <= 0:
        left, top = window.left, window.top
        view_w, view_h = window.width, window.height
    local_x = int(screen_x) - left
    local_y = int(screen_y) - top
    if local_x < 0 or local_y < 0 or local_x > view_w or local_y > view_h:
        return None

    if mode == "stretch":
        nx = local_x / max(1, view_w)
        ny = local_y / max(1, view_h)
    else:
        scale = min(view_w / view_device_w, view_h / view_device_h)
        content_w = view_device_w * scale
        content_h = view_device_h * scale
        offset_x = (view_w - content_w) / 2
        offset_y = (view_h - content_h) / 2
        if local_x < offset_x or local_y < offset_y or local_x > offset_x + content_w or local_y > offset_y + content_h:
            return None
        nx = (local_x - offset_x) / max(1.0, content_w)
        ny = (local_y - offset_y) / max(1.0, content_h)

    x = int(round(nx * view_device_w))
    y = int(round(ny * view_device_h))
    x, y = max(0, min(view_device_w - 1, x)), max(0, min(view_device_h - 1, y))
    if rotate == 90:
        x, y = device_w - 1 - y, x
    elif rotate == 180:
        x, y = device_w - 1 - x, device_h - 1 - y
    elif rotate == 270:
        x, y = y, device_h - 1 - x
    return x, y


class HookPoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MouseHookStruct(ctypes.Structure):
    _fields_ = [
        ("pt", HookPoint),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class MouseTapBridge:
    """Global mouse hook that converts projection-window clicks into ADB taps."""

    WH_MOUSE_LL = 14
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_MBUTTONUP = 0x0208

    def __init__(self, callback: Callable[[int, int, str], None]):
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.thread_id = 0
        self.hook = None
        self._proc = None

    @property
    def active(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self.active:
            return True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="Passer-PhoneMirrorMouseHook")
        self.thread.start()
        for _ in range(20):
            if self.hook:
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.05)
        return bool(self.hook)

    def stop(self) -> None:
        self.stop_event.set()
        if sys.platform == "win32" and self.thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            except Exception:
                pass

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()

        LowLevelMouseProc = ctypes.WINFUNCTYPE(
            wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        def proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code >= 0 and w_param in (self.WM_LBUTTONUP, self.WM_RBUTTONUP, self.WM_MBUTTONUP):
                info = ctypes.cast(l_param, ctypes.POINTER(MouseHookStruct)).contents
                button = {
                    self.WM_LBUTTONUP: "left",
                    self.WM_RBUTTONUP: "right",
                    self.WM_MBUTTONUP: "middle",
                }.get(int(w_param), "left")
                try:
                    self.callback(int(info.pt.x), int(info.pt.y), button)
                except Exception:
                    pass
            return user32.CallNextHookEx(self.hook, n_code, w_param, l_param)

        self._proc = LowLevelMouseProc(proc)
        self.hook = user32.SetWindowsHookExW(self.WH_MOUSE_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self.hook:
            return
        try:
            msg = wintypes.MSG()
            while not self.stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                user32.UnhookWindowsHookEx(self.hook)
            except Exception:
                pass
            self.hook = None
            self.thread_id = 0


class PhoneMirrorAdvancedWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 760
    MIN_H = 860

    def __init__(
        self,
        app: Any,
        theme: ClickerTheme,
        on_close: Callable[[], None] | None = None,
    ):
        self.app = app
        self.theme = theme
        self.on_close = on_close
        self.closed = False
        self.move_start = None
        self.devices: list[AdbDevice] = []
        self.windows: list[WindowInfo] = []
        self.scrcpy_process: subprocess.Popen[str] | None = None
        self.scrcpy_output_threads: list[threading.Thread] = []
        self.active_fps_limit = 0
        self.tap_bridge = MouseTapBridge(self._mouse_hook_event)
        self.fast_shell = FastAdbShell()

        module_dir = Path(__file__).resolve().parent
        self.adb_var = tk.StringVar(value=find_adb_candidates(module_dir)[0])
        self.device_var = tk.StringVar(value="")
        self.window_var = tk.StringVar(value="")
        self.size_var = tk.StringVar(value="1080x2400")
        self.display_id_var = tk.StringVar(value="")
        self.mode_var = tk.StringVar(value="contain")
        self.rotate_var = tk.StringVar(value="0")
        self.coord_var = tk.StringVar(value="")
        self.text_var = tk.StringVar(value="")
        self.pc_hotkey_var = tk.StringVar(value="Ctrl+V")
        self.pc_topmost_var = tk.BooleanVar(value=False)
        self.window_topmost_var = tk.BooleanVar(value=bool(app.topmost_var.get()))
        self.swipe_duration_var = tk.StringVar(value="320")
        self.audio_var = tk.BooleanVar(value=True)
        self.low_latency_var = tk.BooleanVar(value=True)
        self.video_buffer_var = tk.StringVar(value="0")
        self.audio_buffer_var = tk.StringVar(value="40")
        self.show_fps_var = tk.BooleanVar(value=True)
        self.fps_limit_var = tk.StringVar(value="0")
        self.fps_var = tk.StringVar(value="FPS: --")
        self.bridge_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="连接 ADB 设备，并选择投屏窗口后即可交互。")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)

        self.shell = tk.Frame(self.window, bg=theme.app_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.geometry(f"{self.MIN_W}x{self.MIN_H}+{x}+{y}")
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_set()
        self.window.after(120, self.refresh_all)

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text=getattr(t, "title", MODULE_NAME), bg=t.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        self.topmost_button = self._chrome_button(bar, "置顶", self.toggle_window_topmost)
        self.topmost_button.pack(side=tk.RIGHT, padx=(0, 6), pady=8)
        self._refresh_window_topmost_button()
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        content = tk.Frame(body, bg=t.surface_bg)
        content.grid(row=0, column=0, sticky="nsew", padx=18, pady=(16, 10))
        content.grid_columnconfigure(0, weight=1, uniform="phone_mirror_cols")
        content.grid_columnconfigure(1, weight=1, uniform="phone_mirror_cols")
        content.grid_rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=t.surface_bg)
        right = tk.Frame(content, bg=t.surface_bg)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 9))
        right.grid(row=0, column=1, sticky="nsew", padx=(9, 0))

        connection = self._panel(left, "\u8fde\u63a5")
        self._row(
            connection,
            "ADB",
            lambda row: self._entry(row, self.adb_var),
            lambda row: self._button(row, "\u5237\u65b0", self.refresh_devices),
        )

        def make_device_combo(row: tk.Widget) -> ttk.Combobox:
            self.device_combo = self._combo(row, self.device_var)
            return self.device_combo

        self._row(
            connection,
            "\u8bbe\u5907",
            make_device_combo,
            lambda row: self._button(row, "\u8bc6\u522b\u5c3a\u5bf8", self.detect_device_size),
        )
        self._row(
            connection,
            "\u5c3a\u5bf8",
            lambda row: self._entry(row, self.size_var, width=14),
            lambda row: self._button(row, "\u6d4b\u8bd5", self.test_adb),
        )
        self._row(connection, "\u663e\u793aID", lambda row: self._entry(row, self.display_id_var, width=8))

        projection = self._panel(left, "\u6295\u5c4f\u7a97\u53e3")

        def make_window_combo(row: tk.Widget) -> ttk.Combobox:
            self.window_combo = self._combo(row, self.window_var)
            return self.window_combo

        self._row(
            projection,
            "\u7a97\u53e3",
            make_window_combo,
            lambda row: self._button(row, "\u5237\u65b0", self.refresh_windows),
        )
        self._row(
            projection,
            "\u6620\u5c04",
            lambda row: self._combo(row, self.mode_var, values=("contain", "stretch")),
            lambda row: self._combo(row, self.rotate_var, values=("0", "90", "180", "270")),
        )
        self.bridge_check = self._check(
            projection,
            "\u6865\u63a5\u9f20\u6807\u5de6\u952e\uff1a\u70b9\u6295\u5c4f\u7a97\u53e3\u65f6\u8f6c\u53d1\u4e3a\u624b\u673a\u70b9\u51fb",
            self.bridge_var,
            self.toggle_bridge,
        )
        self.bridge_check.pack(fill=tk.X, pady=(8, 2))

        scrcpy_panel = self._panel(left, "scrcpy")
        scrcpy_row = tk.Frame(scrcpy_panel, bg=t.surface_bg)
        scrcpy_row.pack(fill=tk.X, pady=(0, 8))
        self._button(scrcpy_row, "\u542f\u52a8\u6295\u5c4f", self.start_scrcpy, primary=True).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(scrcpy_row, "\u505c\u6b62", self.stop_scrcpy).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        audio_row = tk.Frame(scrcpy_panel, bg=t.surface_bg)
        audio_row.pack(fill=tk.X, pady=(0, 6))
        self._check(audio_row, "\u97f3\u9891\u63a5\u5165", self.audio_var).pack(side=tk.LEFT)
        self._check(audio_row, "\u4f4e\u5ef6\u8fdf\u53c2\u6570", self.low_latency_var).pack(side=tk.LEFT, padx=(12, 0))

        buffer_row = tk.Frame(scrcpy_panel, bg=t.surface_bg)
        buffer_row.pack(fill=tk.X, pady=4)
        tk.Label(buffer_row, text="\u89c6\u9891\u7f13\u51b2", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT)
        self._entry(buffer_row, self.video_buffer_var, width=6).pack(side=tk.LEFT, padx=(6, 4))
        tk.Label(buffer_row, text="ms", bg=t.surface_bg, fg="#64748b", font=self._font(9)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(buffer_row, text="\u97f3\u9891\u7f13\u51b2", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT)
        self._entry(buffer_row, self.audio_buffer_var, width=6).pack(side=tk.LEFT, padx=(6, 4))
        tk.Label(buffer_row, text="ms", bg=t.surface_bg, fg="#64748b", font=self._font(9)).pack(side=tk.LEFT)

        fps_row = tk.Frame(scrcpy_panel, bg=t.surface_bg)
        fps_row.pack(fill=tk.X, pady=4)
        self._check(fps_row, "\u663e\u793a FPS", self.show_fps_var).pack(side=tk.LEFT)
        tk.Label(fps_row, text="\u76ee\u6807", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT, padx=(12, 4))
        self._entry(fps_row, self.fps_limit_var, width=6).pack(side=tk.LEFT)
        tk.Label(fps_row, text="FPS", bg=t.surface_bg, fg="#64748b", font=self._font(9)).pack(side=tk.LEFT, padx=(4, 10))
        tk.Label(
            fps_row,
            textvariable=self.fps_var,
            bg="#f8fafc",
            fg="#0f172a",
            anchor=tk.CENTER,
            font=self._font(9, "bold"),
            padx=8,
            pady=3,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        actions_panel = self._panel(right, "\u57fa\u7840\u52a8\u4f5c")
        self._button_grid(
            actions_panel,
            [
                ("\u8fd4\u56de", lambda: self.keyevent("BACK")),
                ("\u4e3b\u9875", lambda: self.keyevent("HOME")),
                ("\u4efb\u52a1", lambda: self.keyevent("APP_SWITCH")),
                ("\u7535\u6e90", lambda: self.keyevent("POWER")),
                ("\u97f3\u91cf+", lambda: self.keyevent("VOLUME_UP")),
                ("\u97f3\u91cf-", lambda: self.keyevent("VOLUME_DOWN")),
            ],
            columns=3,
        )

        tap_panel = self._panel(right, "\u5750\u6807\u70b9\u51fb")
        self._row(
            tap_panel,
            "\u5750\u6807",
            lambda row: self._entry(row, self.coord_var, width=16),
            lambda row: self._button(row, "\u70b9\u51fb", self.tap_coord, primary=True),
        )
        tk.Label(
            tap_panel,
            text="\u683c\u5f0f\uff1ax,y\uff1b\u5f00\u542f\u6865\u63a5\u540e\u4e5f\u53ef\u76f4\u63a5\u70b9\u6295\u5c4f\u7a97\u53e3\u3002",
            bg=t.surface_bg,
            fg="#64748b",
            anchor=tk.W,
            justify=tk.LEFT,
            font=self._font(9),
            wraplength=310,
        ).pack(fill=tk.X, pady=(0, 4))

        swipe_panel = self._panel(right, "\u6ed1\u52a8")
        self._button_grid(
            swipe_panel,
            [
                ("\u4e0a\u6ed1", lambda: self.swipe_direction("up")),
                ("\u4e0b\u6ed1", lambda: self.swipe_direction("down")),
                ("\u5de6\u6ed1", lambda: self.swipe_direction("left")),
                ("\u53f3\u6ed1", lambda: self.swipe_direction("right")),
            ],
            columns=2,
        )
        self._row(swipe_panel, "\u65f6\u957f ms", lambda row: self._entry(row, self.swipe_duration_var, width=10))

        text_panel = self._panel(right, "\u6587\u5b57")
        text_row = tk.Frame(text_panel, bg=t.surface_bg)
        text_row.pack(fill=tk.X)
        self._entry(text_row, self.text_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(text_row, "\u8f93\u5165", self.input_text, primary=True).pack(side=tk.LEFT, padx=(8, 0))

        pc_panel = self._panel(right, "\u7535\u8111\u64cd\u4f5c")
        self._button_grid(
            pc_panel,
            [
                ("\u805a\u7126", self.focus_projection_window),
                ("\u7f6e\u9876", self.toggle_projection_topmost),
                ("\u6700\u5c0f\u5316", self.minimize_projection_window),
                ("\u590d\u5236", lambda: self.send_pc_hotkey("Ctrl+C")),
                ("\u7c98\u8d34", lambda: self.send_pc_hotkey("Ctrl+V")),
                ("\u5168\u9009", lambda: self.send_pc_hotkey("Ctrl+A")),
                ("\u64a4\u9500", lambda: self.send_pc_hotkey("Ctrl+Z")),
                ("\u5237\u65b0", lambda: self.send_pc_hotkey("F5")),
                ("\u5168\u5c4f", lambda: self.send_pc_hotkey("F11")),
                ("\u56de\u8f66", lambda: self.send_pc_hotkey("Enter")),
                ("Tab", lambda: self.send_pc_hotkey("Tab")),
                ("Esc", lambda: self.send_pc_hotkey("Esc")),
            ],
            columns=3,
        )
        hotkey_row = tk.Frame(pc_panel, bg=t.surface_bg)
        hotkey_row.pack(fill=tk.X, pady=(8, 0))
        self._entry(hotkey_row, self.pc_hotkey_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(hotkey_row, "\u53d1\u9001\u5feb\u6377\u952e", self.send_custom_pc_hotkey, primary=True).pack(side=tk.LEFT, padx=(8, 0))

        status = tk.Label(
            body,
            textvariable=self.status_var,
            bg="#f8fafc",
            fg="#475569",
            anchor=tk.W,
            justify=tk.LEFT,
            font=self._font(9),
            padx=10,
            pady=8,
            wraplength=700,
            highlightthickness=1,
            highlightbackground="#e2e8f0",
        )
        status.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _panel(self, parent: tk.Widget, title: str) -> tk.Frame:
        panel = tk.Frame(
            parent,
            bg=self.theme.surface_bg,
            highlightthickness=1,
            highlightbackground="#e2e8f0",
        )
        panel.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            panel,
            text=title,
            bg=self.theme.surface_bg,
            fg="#0f172a",
            anchor=tk.W,
            font=self._font(10, "bold"),
        ).pack(fill=tk.X, padx=12, pady=(10, 6))
        inner = tk.Frame(panel, bg=self.theme.surface_bg)
        inner.pack(fill=tk.X, padx=12, pady=(0, 12))
        return inner


    def _row(
        self,
        parent: tk.Widget,
        label: str,
        main_factory: Callable[[tk.Widget], tk.Widget],
        action_factory: Callable[[tk.Widget], tk.Widget] | None = None,
    ) -> None:
        row = tk.Frame(parent, bg=self.theme.surface_bg)
        row.pack(fill=tk.X, pady=4)
        row.grid_columnconfigure(1, weight=1)
        tk.Label(
            row,
            text=label,
            bg=self.theme.surface_bg,
            fg="#475569",
            font=self._font(9),
            width=8,
            anchor=tk.W,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        main = main_factory(row)
        main.grid(row=0, column=1, sticky="ew")
        if action_factory is not None:
            action = action_factory(row)
            action.grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _check(
        self,
        parent: tk.Widget,
        text: str,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
    ) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
            bg=self.theme.surface_bg,
            fg="#334155",
            activebackground=self.theme.surface_bg,
            activeforeground="#111827",
            selectcolor="#f8fafc",
            anchor=tk.W,
            cursor="hand2",
            font=self._font(9),
        )

    def _button_grid(self, parent: tk.Widget, items: list[tuple[str, Callable[[], None]]], columns: int = 3) -> None:
        grid = tk.Frame(parent, bg=self.theme.surface_bg)
        grid.pack(fill=tk.X, pady=2)
        for col in range(columns):
            grid.grid_columnconfigure(col, weight=1, uniform="buttons")
        for index, (text, command) in enumerate(items):
            self._button(grid, text, command).grid(
                row=index // columns,
                column=index % columns,
                sticky="ew",
                padx=3,
                pady=3,
            )


    def _entry(self, parent: tk.Widget, variable: tk.StringVar, width: int = 20) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bd=0,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor=self.theme.accent,
            bg="#ffffff",
            fg="#0f172a",
            font=self._font(10),
            insertbackground="#0f172a",
        )

    def _combo(self, parent: tk.Widget, variable: tk.StringVar, values: tuple[str, ...] = ()) -> ttk.Combobox:
        return ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", font=self._font(10))

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None], primary: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            padx=12,
            pady=7,
            bg=(self.theme.accent if primary else "#eef2f9"),
            fg=("#ffffff" if primary else "#1f2937"),
            activebackground=(self.theme.accent_hover if primary else "#e2e8f4"),
            activeforeground=("#ffffff" if primary else "#111827"),
            cursor="hand2",
            font=self._font(9, "bold" if primary else "normal"),
        )

    def _chrome_button(self, parent: tk.Widget, text: str, command: Callable[[], None], close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            padx=11,
            pady=5,
            bg=self.theme.title_button_bg,
            fg="#e7eefc",
            activebackground=hover,
            activeforeground="#ffffff",
            font=self._font(10),
            cursor="hand2",
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=self.theme.title_button_bg))
        return button

    def refresh_all(self) -> None:
        self.refresh_devices()
        self.refresh_windows()

    def refresh_devices(self) -> None:
        def work() -> None:
            try:
                devices = list_adb_devices(self.adb_var.get().strip() or "adb")
                self.window.after(0, lambda: self._set_devices(devices, None))
            except Exception as exc:
                error = exc
                self.window.after(0, lambda error=error: self._set_devices([], error))

        self.status_var.set("正在刷新 ADB 设备...")
        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorRefreshAdb").start()

    def _set_devices(self, devices: list[AdbDevice], error: Exception | None) -> None:
        self.devices = devices
        labels = [device.label for device in devices if device.state == "device"]
        self.device_combo.configure(values=labels)
        if labels and self.device_var.get() not in labels:
            self.device_var.set(labels[0])
        if error:
            self.status_var.set(f"ADB 设备刷新失败：{error}")
        elif labels:
            self.status_var.set(f"已发现 {len(labels)} 台可用设备。")
            if len(labels) == 1:
                self.detect_device_size()
        else:
            self.status_var.set("没有发现可用设备。请确认 USB 调试、授权弹窗和 adb 环境。")

    def refresh_windows(self) -> None:
        self.windows = enumerate_windows()
        labels = [item.label for item in self.windows]
        self.window_combo.configure(values=labels)
        preferred = next((item.label for item in self.windows if _looks_like_phone_window(item.title)), "")
        if preferred:
            self.window_var.set(preferred)
        elif labels and self.window_var.get() not in labels:
            self.window_var.set(labels[0])
        if sys.platform != "win32":
            self.status_var.set("当前系统不是 Windows，无法枚举投屏窗口；ADB 基础动作仍可用。")

    def selected_serial(self) -> str:
        label = self.device_var.get()
        for device in self.devices:
            if device.label == label:
                return device.serial
        return label.split(" ", 1)[0].strip()

    def adb(self) -> AdbClient:
        return AdbClient(self.adb_var.get().strip() or "adb", self.selected_serial())

    def selected_window(self) -> WindowInfo | None:
        label = self.window_var.get()
        for item in self.windows:
            if item.label == label:
                return item
        return None

    def parse_size(self) -> tuple[int, int]:
        match = re.search(r"(\d+)\s*[xX*,，, ]\s*(\d+)", self.size_var.get())
        if not match:
            raise ValueError("手机尺寸格式应为 1080x2400")
        return int(match.group(1)), int(match.group(2))

    def test_adb(self) -> None:
        def work() -> None:
            try:
                adb = self.adb()
                brand = adb.getprop("ro.product.brand")
                manufacturer = adb.getprop("ro.product.manufacturer")
                model = adb.getprop("ro.product.model")
                version = adb.getprop("ro.build.version.release")
                name = " ".join(part for part in (brand or manufacturer, model) if part).strip()
                suffix = f"，Android {version}" if version else ""
                self.window.after(0, lambda: self.status_var.set(f"ADB 连接正常：{name or self.selected_serial()}{suffix}"))
            except Exception as exc:
                message = f"ADB 测试失败：{exc}"
                self.window.after(0, lambda message=message: self.status_var.set(message))

        self.status_var.set("正在测试 ADB...")
        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorTestAdb").start()

    def detect_device_size(self) -> None:
        def work() -> None:
            try:
                info = self.adb().display_info(self.selected_window())
                if not info:
                    size = self.adb().wm_size()
                    if not size:
                        raise RuntimeError("未能读取手机显示尺寸")
                    info = DeviceDisplayInfo(width=size[0], height=size[1], source="wm size")
                self.window.after(0, lambda: self._set_device_info(info))
            except Exception as exc:
                message = f"识别尺寸失败：{exc}"
                self.window.after(0, lambda message=message: self.status_var.set(message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorSize").start()

    def _set_device_size(self, size: tuple[int, int]) -> None:
        self.size_var.set(f"{size[0]}x{size[1]}")
        self.status_var.set(f"已识别手机分辨率：{size[0]}x{size[1]}")

    def _set_device_info(self, info: DeviceDisplayInfo) -> None:
        self.size_var.set(f"{info.width}x{info.height}")
        self.rotate_var.set(ROTATION_LABELS.get(info.rotation, "0"))
        source = f"；来源：{info.source}" if info.source else ""
        self.status_var.set(f"已识别：{info.label}{source}")

    def tap_coord(self) -> None:
        match = re.search(r"(-?\d+)\s*[,， ]\s*(-?\d+)", self.coord_var.get())
        if not match:
            self.status_var.set("请输入坐标，例如：540,1200")
            return
        self.run_adb_action(["input", "tap", match.group(1), match.group(2)], "点击")

    def keyevent(self, key: str) -> None:
        self.run_adb_action(["input", "keyevent", key], f"按键 {key}")

    def input_text(self) -> None:
        text = self.text_var.get()
        if not text:
            return
        self.run_adb_action(["input", "text", quote_adb_text(text)], "输入文字")

    def swipe_direction(self, direction: str) -> None:
        try:
            w, h = self.parse_size()
            duration = int(float(self.swipe_duration_var.get() or "320"))
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        cx, cy = w // 2, h // 2
        margin_x = max(40, w // 5)
        margin_y = max(40, h // 5)
        if direction == "up":
            points = (cx, h - margin_y, cx, margin_y)
        elif direction == "down":
            points = (cx, margin_y, cx, h - margin_y)
        elif direction == "left":
            points = (w - margin_x, cy, margin_x, cy)
        else:
            points = (margin_x, cy, w - margin_x, cy)
        self.run_adb_action(["input", "swipe", *map(str, points), str(duration)], f"{direction} swipe")

    def _with_display_id(self, args: list[str]) -> list[str]:
        display_id = self.display_id_var.get().strip()
        if not display_id or len(args) < 2 or args[0] != "input":
            return args
        if not re.fullmatch(r"-?\d+", display_id):
            self._set_status("显示 ID 必须是数字；已按默认显示执行。")
            return args
        return ["input", "-d", display_id, *args[1:]]

    def run_adb_action(self, args: list[str], label: str) -> None:
        serial = self.selected_serial()
        if not serial:
            self._set_status("请先选择 ADB 设备。")
            return
        args = self._with_display_id(args)
        self._set_status(f"已发送：{label}")
        self.fast_shell.enqueue(
            AdbShellTask(
                adb_path=self.adb_var.get().strip() or "adb",
                serial=serial,
                args=[str(item) for item in args],
                label=label,
                on_done=lambda done_label: self._set_status(f"已执行：{done_label}"),
                on_error=lambda error: self._set_status(f"{label} 失败：{error}"),
            )
        )

    def _set_status(self, text: str) -> None:
        try:
            self.window.after(0, lambda: self.status_var.set(text))
        except Exception:
            pass

    def toggle_bridge(self) -> None:
        if self.bridge_var.get():
            if not self.selected_window():
                self.bridge_var.set(False)
                self.status_var.set("请先选择投屏窗口。")
                return
            if not self.tap_bridge.start():
                self.bridge_var.set(False)
                self.status_var.set("鼠标桥接启动失败。可能需要 Windows 环境或权限。")
                return
            self.status_var.set("鼠标桥接已开启：左键点击投屏窗口会转为手机点击，右键可发送返回。")
        else:
            self.tap_bridge.stop()
            self.status_var.set("鼠标桥接已关闭。")

    def _mouse_hook_event(self, screen_x: int, screen_y: int, button: str) -> None:
        if self.closed or not self.bridge_var.get():
            return
        selected = self.selected_window()
        if selected is None:
            return
        try:
            device_w, device_h = self.parse_size()
            mapped = map_window_point_to_device(
                screen_x,
                screen_y,
                selected,
                device_w,
                device_h,
                self.mode_var.get(),
                int(self.rotate_var.get() or "0"),
            )
        except Exception:
            mapped = None
        if mapped is None:
            return
        if button == "right":
            self.run_adb_action(["input", "keyevent", "BACK"], "返回")
            return
        if button != "left":
            return
        x, y = mapped
        try:
            self.window.after(0, lambda: self.coord_var.set(f"{x},{y}"))
        except Exception:
            pass
        self.run_adb_action(["input", "tap", str(x), str(y)], f"投屏点击 {x},{y}")

    def selected_hwnd(self) -> int:
        selected = self.selected_window()
        return int(selected.hwnd) if selected else 0

    def focus_projection_window(self) -> None:
        hwnd = self.selected_hwnd()
        if not hwnd:
            self.status_var.set("请先选择投屏窗口。")
            return
        if focus_window(hwnd):
            self.status_var.set("已聚焦投屏窗口。")
        else:
            self.status_var.set("聚焦窗口失败，可能不是 Windows 环境或窗口已关闭。")

    def toggle_projection_topmost(self) -> None:
        hwnd = self.selected_hwnd()
        if not hwnd:
            self.status_var.set("请先选择投屏窗口。")
            return
        enabled = not self.pc_topmost_var.get()
        if set_window_topmost(hwnd, enabled):
            self.pc_topmost_var.set(enabled)
            self.status_var.set("投屏窗口已置顶。" if enabled else "投屏窗口已取消置顶。")
        else:
            self.status_var.set("设置窗口置顶失败。")

    def toggle_window_topmost(self) -> None:
        enabled = not bool(self.window_topmost_var.get())
        self.window_topmost_var.set(enabled)
        try:
            self.window.attributes("-topmost", enabled)
            self.status_var.set("投屏控制窗口已置顶。" if enabled else "投屏控制窗口已取消置顶。")
        except tk.TclError:
            return
        self._refresh_window_topmost_button()

    def _refresh_window_topmost_button(self) -> None:
        button = getattr(self, "topmost_button", None)
        if button is None:
            return
        enabled = bool(self.window_topmost_var.get())
        button.configure(
            text="取消置顶" if enabled else "置顶",
            bg=self.theme.accent if enabled else self.theme.title_button_bg,
        )

    def minimize_projection_window(self) -> None:
        hwnd = self.selected_hwnd()
        if not hwnd:
            self.status_var.set("请先选择投屏窗口。")
            return
        if minimize_window(hwnd):
            self.status_var.set("已最小化投屏窗口。")
        else:
            self.status_var.set("最小化窗口失败。")

    def send_pc_hotkey(self, hotkey: str) -> None:
        keys = parse_hotkey(hotkey)
        if not keys:
            self.status_var.set(f"无法识别快捷键：{hotkey}")
            return
        hwnd = self.selected_hwnd()
        ok = send_virtual_keys(keys, hwnd)
        self.status_var.set(f"已发送电脑快捷键：{hotkey}" if ok else f"发送电脑快捷键失败：{hotkey}")

    def send_custom_pc_hotkey(self) -> None:
        hotkey = self.pc_hotkey_var.get().strip()
        if not hotkey:
            self.status_var.set("请输入快捷键，例如 Ctrl+V、Alt+Tab、Win+D、F11。")
            return
        self.send_pc_hotkey(hotkey)

    def start_scrcpy(self) -> None:
        if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
            self.status_var.set("scrcpy 已在运行。")
            return
        module_dir = Path(__file__).resolve().parent
        scrcpy = find_scrcpy_path(module_dir)
        if not scrcpy:
            self.status_var.set("未找到 scrcpy。可把 scrcpy.exe 放到 tools\\scrcpy，或加入 PATH。")
            return
        command, notes = self._build_scrcpy_command(scrcpy)
        if not command:
            return
        serial = self.selected_serial()
        if serial:
            command.extend(["-s", serial])
        self.status_var.set("正在启动 scrcpy...")
        try:
            self.scrcpy_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_no_window_flags(),
            )
            self._start_scrcpy_output_readers(self.scrcpy_process)
            suffix = f"（{'; '.join(notes)}）" if notes else ""
            self.status_var.set(f"已启动 scrcpy{suffix}，稍后可刷新投屏窗口。")
            self.window.after(1500, self.refresh_windows)
        except Exception as exc:
            self.status_var.set(f"启动 scrcpy 失败：{exc}")

    def _buffer_ms(self, variable: tk.StringVar, default: int, minimum: int = 0, maximum: int = 1000) -> str:
        try:
            value = int(float(variable.get() or default))
        except Exception:
            value = default
        value = max(minimum, min(maximum, value))
        variable.set(str(value))
        return str(value)

    def _fps_limit(self) -> int:
        try:
            value = int(float(self.fps_limit_var.get() or "0"))
        except Exception:
            value = 0
        value = max(0, min(240, value))
        self.fps_limit_var.set(str(value))
        return value

    def _build_scrcpy_command(self, scrcpy: str) -> tuple[list[str], list[str]]:
        command = [scrcpy]
        notes: list[str] = []
        if enable_scrcpy_clipboard_sync(command, scrcpy):
            notes.append("双向剪贴板同步")
        if scrcpy_supports(scrcpy, "--keyboard=mode"):
            command.append("--keyboard=sdk")
        supports_audio = scrcpy_supports(scrcpy, "--audio") or scrcpy_supports(scrcpy, "--no-audio")
        supports_no_audio = scrcpy_supports(scrcpy, "--no-audio")
        audio_enabled = bool(self.audio_var.get())

        if not audio_enabled and supports_no_audio:
            command.append("--no-audio")
            notes.append("音频关闭")
        elif audio_enabled:
            if supports_audio:
                notes.append("音频接入")
            else:
                notes.append("未检测到 scrcpy 音频参数，按基础投屏启动")

        if self.low_latency_var.get():
            video_buffer = self._buffer_ms(self.video_buffer_var, 0, 0, 500)
            audio_buffer = self._buffer_ms(self.audio_buffer_var, 40, 0, 1000)
            if scrcpy_supports(scrcpy, "--video-buffer"):
                command.extend(["--video-buffer", video_buffer])
                notes.append(f"视频缓冲 {video_buffer}ms")
            if audio_enabled and scrcpy_supports(scrcpy, "--audio-buffer"):
                command.extend(["--audio-buffer", audio_buffer])
                notes.append(f"音频缓冲 {audio_buffer}ms")
            if audio_enabled and scrcpy_supports(scrcpy, "--audio-output-buffer"):
                command.extend(["--audio-output-buffer", audio_buffer])
        fps_limit = self._fps_limit()
        self.active_fps_limit = fps_limit
        if fps_limit > 0 and scrcpy_supports(scrcpy, "--max-fps"):
            command.extend(["--max-fps", str(fps_limit)])
            notes.append(f"目标 {fps_limit}FPS")
        elif fps_limit > 0:
            notes.append("目标FPS不支持")
        if self.show_fps_var.get() and scrcpy_supports(scrcpy, "--print-fps"):
            command.append("--print-fps")
            self.fps_var.set("FPS: 等待数据")
            notes.append("FPS显示")
        elif self.show_fps_var.get():
            self.fps_var.set("FPS: scrcpy不支持")
            notes.append("FPS显示不支持")
        else:
            self.fps_var.set("FPS: 关闭")
        display_id = self.display_id_var.get().strip()
        if display_id and re.fullmatch(r"-?\d+", display_id) and scrcpy_supports(scrcpy, "--display-id"):
            command.extend(["--display-id", display_id])
            notes.append(f"显示ID {display_id}")
        return command, notes

    def _start_scrcpy_output_readers(self, proc: subprocess.Popen[str]) -> None:
        self.scrcpy_output_threads = []
        for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._read_scrcpy_output,
                args=(stream,),
                daemon=True,
                name=f"Passer-scrcpy-{name}",
            )
            self.scrcpy_output_threads.append(thread)
            thread.start()

    def _read_scrcpy_output(self, stream: Any) -> None:
        try:
            for line in stream:
                if self.closed:
                    break
                self._handle_scrcpy_line(str(line))
        except Exception:
            pass

    def _handle_scrcpy_line(self, line: str) -> None:
        match = FPS_RE.search(line or "")
        if not match:
            return
        value = next((group for group in match.groups() if group), "")
        if not value:
            return
        try:
            fps = float(value)
        except ValueError:
            return
        target = int(getattr(self, "active_fps_limit", 0) or 0)
        if target > 0:
            text = f"FPS: {fps:.1f} / {target}"
        else:
            text = f"FPS: {fps:.1f}"
        self._set_status_var(self.fps_var, text)

    def _set_status_var(self, variable: tk.StringVar, text: str) -> None:
        try:
            self.window.after(0, lambda: variable.set(text))
        except Exception:
            pass

    def stop_scrcpy(self) -> None:
        proc = self.scrcpy_process
        if proc is not None and proc.poll() is None:
            if _terminate_process(proc):
                self.status_var.set("已请求停止 scrcpy。")
            else:
                self.status_var.set("停止 scrcpy 失败：进程未响应。")
        self.scrcpy_process = None
        self.scrcpy_output_threads = []
        if self.show_fps_var.get():
            self.fps_var.set("FPS: --")

    def start_move(self, event: tk.Event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event: tk.Event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def show(self) -> None:
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
            if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
                self.status_var.set("投屏仍在运行，可继续管理。")
        except Exception:
            pass

    def hide(self, notify_parent: bool = True) -> None:
        if self.closed:
            return
        self.bridge_var.set(False)
        self.tap_bridge.stop()
        self.fast_shell.stop()
        try:
            self.window.withdraw()
            if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
                self.status_var.set("控制窗口已隐藏，正在进行的投屏保持运行。")
        except Exception:
            pass
        if notify_parent and self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                pass

    def close(self) -> None:
        self.hide(notify_parent=True)


class PasserSelect(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        variable: tk.StringVar,
        values: tuple[str, ...] = (),
        font: Any = None,
        accent: str = "#2563eb",
        height: int = 36,
    ):
        super().__init__(
            parent,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            height=height,
            cursor="hand2",
        )
        self.pack_propagate(False)
        self.variable = variable
        self.values = tuple(values)
        self.accent = accent
        self.font = font
        self.value_label = tk.Label(
            self,
            textvariable=variable,
            bg="#ffffff",
            fg="#1f2937",
            anchor=tk.W,
            font=font,
            cursor="hand2",
        )
        self.value_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 4))
        self.arrow_label = tk.Label(
            self,
            text="\u25be",
            bg="#ffffff",
            fg="#64748b",
            font=font,
            cursor="hand2",
        )
        self.arrow_label.pack(side=tk.RIGHT, padx=(2, 10))
        for widget in (self, self.value_label, self.arrow_label):
            widget.bind("<Button-1>", self._open_menu)
            widget.bind("<Enter>", lambda _event: self.configure(highlightbackground=self.accent))
            widget.bind("<Leave>", lambda _event: self.configure(highlightbackground="#cbd5e1"))

    def _open_menu(self, _event: tk.Event | None = None) -> None:
        if not self.values:
            return
        menu = tk.Menu(
            self,
            tearoff=False,
            bd=1,
            bg="#ffffff",
            fg="#1f2937",
            activebackground=self.accent,
            activeforeground="#ffffff",
            font=self.font,
        )
        for option in self.values:
            menu.add_command(label=option, command=lambda value=option: self.variable.set(value))
        try:
            menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            menu.grab_release()

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if isinstance(cnf, dict):
            kwargs.update(cnf)
        if "values" in kwargs:
            self.values = tuple(kwargs.pop("values"))
        if cnf is None and not kwargs:
            return super().configure()
        return super().configure(**kwargs)

    config = configure


class PhoneMirrorWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 900
    MIN_H = 820

    def __init__(self, app: Any, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.module_dir = Path(__file__).resolve().parent
        self.adb_path = find_adb_candidates(self.module_dir)[0]
        self.devices: list[AdbDevice] = []
        self.device_label_to_serial: dict[str, str] = {}
        self.scrcpy_process: subprocess.Popen[str] | None = None
        self.projection_hwnd = 0
        self.projection_window_title = ""
        self.projection_windows_before: set[int] = set()
        self.projection_topmost_var = tk.BooleanVar(value=False)
        self.window_topmost_var = tk.BooleanVar(value=bool(app.topmost_var.get()))
        self.projection_transfer_dock: tk.Toplevel | None = None
        self.projection_dock_after_id: str | None = None
        self.advanced_window: PhoneMirrorAdvancedWindow | None = None
        self.device_var = tk.StringVar(value="")
        self.pair_address_var = tk.StringVar(value="")
        self.pair_code_var = tk.StringVar(value="")
        settings = getattr(app, "settings", {}) if isinstance(getattr(app, "settings", {}), dict) else {}
        config = normalize_phone_mirror_settings(settings)
        transfer_path = config["phone_mirror_transfer_path"]
        mode_key = config["phone_mirror_mouse_mode"]
        sensitivity = config["phone_mirror_mouse_sensitivity"]
        audio_key = config["phone_mirror_audio_mode"]
        self.ip_var = tk.StringVar(value=config["phone_mirror_wireless_ip"])
        self.port_var = tk.StringVar(value=str(config["phone_mirror_wireless_port"]))
        self.preferred_serial = config["phone_mirror_device_serial"]
        self.mouse_mode_var = tk.StringVar(value=MOUSE_MODE_LABELS[mode_key])
        self.mouse_sensitivity_var = tk.IntVar(value=max(1, min(15, sensitivity)))
        self.audio_mode_var = tk.StringVar(value=AUDIO_MODE_LABELS[audio_key])
        self.remote_transfer_var = tk.StringVar(value=transfer_path)
        self.status_var = tk.StringVar(value="Android 11+ \u53ef\u7528\u65e0\u7ebf\u8c03\u8bd5\u914d\u5bf9\uff0c\u5168\u7a0b\u4e0d\u9700\u8981 USB \u6570\u636e\u7ebf\u3002")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)
        self.shell = tk.Frame(self.window, bg=theme.surface_bg, highlightthickness=1, highlightbackground=theme.border)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.geometry(f"{self.MIN_W}x{self.MIN_H}+{x}+{y}")
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_set()
        self.window.after(120, self.refresh_devices)

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)
        title = tk.Label(bar, text="\u624b\u673a\u6295\u5c4f", bg=t.title_bg, fg="#dbe7ff", anchor=tk.W, font=self._font(10, "bold"))
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "\u00d7", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        self.window_topmost_button = self._chrome_button(bar, "置顶", self.toggle_window_topmost)
        self.window_topmost_button.pack(side=tk.RIGHT, padx=(0, 6), pady=8)
        self._refresh_window_topmost_button()
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        content = tk.Frame(body, bg=t.surface_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)
        panel = tk.Frame(content, bg=t.surface_bg)
        panel.pack(fill=tk.X, pady=(0, 16))
        inner = tk.Frame(panel, bg=t.surface_bg)
        inner.pack(fill=tk.X)
        tk.Label(inner, text="\u8bbe\u5907", bg=t.surface_bg, fg="#0f172a", font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 8))
        row = tk.Frame(inner, bg=t.surface_bg)
        row.pack(fill=tk.X)
        self.device_combo = PasserSelect(
            row,
            variable=self.device_var,
            values=(),
            font=self._font(11),
            accent=t.accent,
        )
        self.device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._button(row, "\u5237\u65b0\u8bbe\u5907", self.refresh_devices).pack(side=tk.LEFT, padx=(8, 0))

        wireless = tk.Frame(content, bg=t.surface_bg)
        wireless.pack(fill=tk.X, pady=(0, 16))
        wireless_inner = tk.Frame(wireless, bg=t.surface_bg)
        wireless_inner.pack(fill=tk.X)
        tk.Label(wireless_inner, text="\u65e0\u7ebf\u8c03\u8bd5", bg=t.surface_bg, fg="#0f172a", font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 8))
        pair_row = tk.Frame(wireless_inner, bg=t.surface_bg)
        pair_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(pair_row, text="\u914d\u5bf9\u5730\u5740", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT, padx=(0, 8))
        self._entry(pair_row, self.pair_address_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(pair_row, text="\u914d\u5bf9\u7801", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT, padx=(8, 6))
        self._entry(pair_row, self.pair_code_var, width=8).pack(side=tk.LEFT)
        self._button(pair_row, "\u914d\u5bf9", self.pair_wireless).pack(side=tk.LEFT, padx=(8, 0))
        wireless_row = tk.Frame(wireless_inner, bg=t.surface_bg)
        wireless_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(wireless_row, text="\u8fde\u63a5 IP", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT, padx=(0, 8))
        self._entry(wireless_row, self.ip_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(wireless_row, text="\u7aef\u53e3", bg=t.surface_bg, fg="#475569", font=self._font(9)).pack(side=tk.LEFT, padx=(8, 6))
        self._entry(wireless_row, self.port_var, width=7).pack(side=tk.LEFT, padx=(8, 0))
        wireless_actions = tk.Frame(wireless_inner, bg=t.surface_bg)
        wireless_actions.pack(fill=tk.X)
        self._equal_buttons(
            wireless_actions,
            (
                ("USB \u65b9\u5f0f\u542f\u7528", self.enable_wireless, False),
                ("\u8fde\u63a5\u65e0\u7ebf", self.connect_wireless, False),
                ("\u65ad\u5f00", self.disconnect_wireless, False),
            ),
        )

        actions = tk.Frame(content, bg=t.surface_bg)
        actions.pack(fill=tk.X, pady=(0, 12))
        self._equal_buttons(
            actions,
            (
                ("\u542f\u52a8\u6295\u5c4f", self.start_projection, True),
                ("\u505c\u6b62", self.stop_projection, False),
                ("投屏置顶", self.toggle_projection_topmost, False),
                ("\u8bbe\u7f6e", self.open_projection_settings, False),
            ),
        )
        tk.Label(
            content,
            textvariable=self.status_var,
            bg="#f8fafc",
            fg="#475569",
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=840,
            padx=10,
            pady=9,
            font=self._font(9),
        ).pack(fill=tk.X)
        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _ensure_projection_transfer_dock(self) -> tk.Toplevel:
        dock = self.projection_transfer_dock
        if dock is not None:
            try:
                if dock.winfo_exists():
                    return dock
            except tk.TclError:
                pass

        dock = tk.Toplevel(self.window)
        dock.withdraw()
        dock.overrideredirect(True)
        dock.configure(bg=self.theme.accent)
        dock.attributes("-topmost", True)
        shell = tk.Frame(dock, bg=self.theme.title_bg, highlightthickness=1, highlightbackground=self.theme.accent)
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        shell.grid_columnconfigure(0, weight=1, uniform="projection_transfer")
        shell.grid_columnconfigure(1, weight=1, uniform="projection_transfer")

        send_zone = self._projection_transfer_zone(shell, "↓  移入手机", primary=True)
        send_zone.grid(row=0, column=0, sticky="nsew")
        export_zone = self._projection_transfer_zone(shell, "↑  移出电脑", primary=False)
        export_zone.grid(row=0, column=1, sticky="nsew")
        for widget in self._walk_widgets(send_zone):
            self._register_transfer_drop_target(widget)
        for widget in self._walk_widgets(export_zone):
            self._register_transfer_drag_source(widget)
        if not TKDND_AVAILABLE:
            for widget in self._walk_widgets(shell):
                try:
                    widget.configure(cursor="arrow")
                except tk.TclError:
                    pass

        self.projection_transfer_dock = dock
        return dock

    def _projection_transfer_zone(self, parent: tk.Widget, text: str, primary: bool) -> tk.Frame:
        background = self.theme.accent if primary else self.theme.title_button_bg
        foreground = "#ffffff" if primary else "#dbe7ff"
        frame = tk.Frame(parent, bg=background, cursor="hand2", height=42)
        frame.grid_propagate(False)
        tk.Label(
            frame,
            text=text,
            bg=background,
            fg=foreground,
            font=self._font(9, "bold"),
            anchor=tk.CENTER,
            cursor="hand2",
        ).place(relx=0, rely=0, relwidth=1, relheight=1)
        return frame

    def _find_projection_window(self) -> WindowInfo | None:
        windows = enumerate_windows()
        if self.projection_hwnd:
            current = next((item for item in windows if item.hwnd == self.projection_hwnd), None)
            if current is not None:
                return current
        if self.projection_window_title:
            exact = next((item for item in windows if item.title == self.projection_window_title), None)
            if exact is not None:
                return exact
        proc = self.scrcpy_process
        if proc is not None:
            by_pid = [item for item in windows if item.pid == proc.pid]
            if by_pid:
                return max(by_pid, key=lambda item: item.client_width * item.client_height)
        candidates = [
            item for item in windows
            if item.hwnd not in self.projection_windows_before and _looks_like_phone_window(item.title)
        ]
        if candidates:
            return max(candidates, key=lambda item: item.client_width * item.client_height)
        return None

    def _projection_dock_should_show(self, info: WindowInfo, dock: tk.Toplevel) -> bool:
        if sys.platform != "win32":
            return True
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.GetAncestor.restype = wintypes.HWND
            if user32.IsIconic(info.hwnd) or not user32.IsWindowVisible(info.hwnd):
                return False
            dock_hwnd = int(dock.winfo_id())
            foreground = int(user32.GetForegroundWindow())
            if foreground in (info.hwnd, dock_hwnd):
                return True
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return False
            inside = info.left <= point.x < info.right and info.top <= point.y < info.bottom
            if not inside:
                return False
            target = int(user32.WindowFromPoint(point) or 0)
            root_target = int(user32.GetAncestor(target, 2) or 0) if target else 0
            return root_target in (info.hwnd, dock_hwnd)
        except Exception:
            return True

    def _track_projection_transfer_dock(self) -> None:
        self.projection_dock_after_id = None
        if self.closed:
            return
        proc = self.scrcpy_process
        if proc is None or proc.poll() is not None:
            self._hide_projection_transfer_dock()
            return
        try:
            dock = self._ensure_projection_transfer_dock()
            info = self._find_projection_window()
            if info is None:
                self._hide_projection_transfer_dock()
            else:
                self.projection_hwnd = info.hwnd
                if self.projection_topmost_var.get():
                    set_window_topmost(info.hwnd, True)
                width, height, x, y = projection_transfer_dock_geometry(info)
                dock.geometry(f"{width}x{height}+{x}+{y}")
                if self._projection_dock_should_show(info, dock):
                    if str(dock.state()) == "withdrawn":
                        dock.deiconify()
                else:
                    dock.withdraw()
        except (tk.TclError, RuntimeError):
            return
        self.projection_dock_after_id = self.window.after(160, self._track_projection_transfer_dock)

    def _start_projection_dock_tracking(self) -> None:
        # The projection-bottom transfer dock was removed from the UI.
        self._stop_projection_dock_tracking(destroy=True)

    def _hide_projection_transfer_dock(self) -> None:
        dock = self.projection_transfer_dock
        if dock is not None:
            try:
                dock.withdraw()
            except tk.TclError:
                pass

    def _stop_projection_dock_tracking(self, destroy: bool = False) -> None:
        after_id = self.projection_dock_after_id
        self.projection_dock_after_id = None
        if after_id:
            try:
                self.window.after_cancel(after_id)
            except (tk.TclError, RuntimeError):
                pass
        self._hide_projection_transfer_dock()
        self.projection_hwnd = 0
        if destroy and self.projection_transfer_dock is not None:
            try:
                self.projection_transfer_dock.destroy()
            except tk.TclError:
                pass
            self.projection_transfer_dock = None

    def _walk_widgets(self, widget: tk.Widget) -> list[tk.Widget]:
        result = [widget]
        try:
            for child in widget.winfo_children():
                result.extend(self._walk_widgets(child))
        except Exception:
            pass
        return result

    def _entry(self, parent: tk.Widget, variable: tk.StringVar, width: int = 20) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, width=width, bd=0, relief=tk.FLAT, bg="#f8fafc", fg="#0f172a", insertbackground=self.theme.accent, font=self._font(11), highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor=self.theme.accent)

    def _button(self, parent: tk.Widget, text: str, command: Callable[[], None], primary: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bd=0, padx=12, pady=8, bg=(self.theme.accent if primary else "#eef2f9"), fg=("#ffffff" if primary else "#1f2937"), activebackground=(self.theme.accent_hover if primary else "#e2e8f4"), activeforeground=("#ffffff" if primary else "#111827"), cursor="hand2", font=self._font(9, "bold" if primary else "normal"))

    def _equal_buttons(
        self,
        parent: tk.Widget,
        items: tuple[tuple[str, Callable[[], None], bool], ...],
    ) -> None:
        for index, (text, command, primary) in enumerate(items):
            column = index * 2
            parent.grid_columnconfigure(column, weight=1, uniform="phone_mirror_actions")
            self._button(parent, text, command, primary=primary).grid(row=0, column=column, sticky="ew")
            if index < len(items) - 1:
                parent.grid_columnconfigure(column + 1, minsize=8)

    def _chrome_button(self, parent: tk.Widget, text: str, command: Callable[[], None], close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(parent, text=text, command=command, bd=0, padx=11, pady=5, bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff", font=self._font(10), cursor="hand2")
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=self.theme.title_button_bg))
        return button

    def toggle_window_topmost(self) -> None:
        enabled = not bool(self.window_topmost_var.get())
        self.window_topmost_var.set(enabled)
        try:
            self.window.attributes("-topmost", enabled)
            self.status_var.set("投屏控制窗口已置顶。" if enabled else "投屏控制窗口已取消置顶。")
        except tk.TclError:
            return
        self._refresh_window_topmost_button()

    def _refresh_window_topmost_button(self) -> None:
        button = getattr(self, "window_topmost_button", None)
        if button is None:
            return
        enabled = bool(self.window_topmost_var.get())
        button.configure(
            text="取消置顶" if enabled else "置顶",
            bg=self.theme.accent if enabled else self.theme.title_button_bg,
        )

    def toggle_projection_topmost(self) -> None:
        info = self._find_projection_window()
        if info is None:
            self.status_var.set("尚未找到正在运行的投屏窗口。")
            return
        self.projection_hwnd = info.hwnd
        enabled = not bool(self.projection_topmost_var.get())
        if set_window_topmost(info.hwnd, enabled):
            self.projection_topmost_var.set(enabled)
            self.status_var.set("投屏窗口已置顶。" if enabled else "投屏窗口已取消置顶。")
        else:
            self.status_var.set("设置投屏窗口置顶失败。")

    def _register_transfer_drop_target(self, widget: tk.Widget) -> None:
        if not TKDND_AVAILABLE or not DND_FILES:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._transfer_drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._transfer_drop_enter)
            widget.dnd_bind("<<DropLeave>>", self._transfer_drop_leave)
            widget.dnd_bind("<<Drop>>", self._handle_transfer_drop)
        except Exception:
            pass

    def _register_transfer_drag_source(self, widget: tk.Widget) -> None:
        if not TKDND_AVAILABLE or not DND_FILES:
            return
        try:
            widget.drag_source_register(1, DND_FILES)
            widget.dnd_bind("<<DragInitCmd>>", self._start_transfer_drag)
        except Exception:
            pass

    def _transfer_drop_enter(self, _event=None):
        self.status_var.set("松开鼠标即可把文件发送到手机。")
        return COPY

    def _transfer_drop_leave(self, _event=None):
        return COPY

    def _parse_dropped_paths(self, event) -> list[str]:
        raw_data = getattr(event, "data", "")
        try:
            paths = [str(path) for path in self.window.tk.splitlist(raw_data)]
        except Exception:
            paths = [str(raw_data)] if raw_data else []
        result: list[str] = []
        for raw in paths:
            text = str(raw).strip()
            if text.startswith("file:///"):
                text = unquote(text[8:]).replace("/", os.sep)
            if text and Path(text).exists():
                result.append(str(Path(text)))
        return result

    def _remote_transfer_dir(self) -> str:
        value = (self.remote_transfer_var.get() or DEFAULT_PHONE_TRANSFER_DIR).strip().replace("\\", "/")
        if not value.startswith("/"):
            value = DEFAULT_PHONE_TRANSFER_DIR
        value = value.rstrip("/") or DEFAULT_PHONE_TRANSFER_DIR
        self.remote_transfer_var.set(value)
        settings = getattr(self.app, "settings", None)
        if isinstance(settings, dict):
            settings["phone_mirror_transfer_path"] = value
        save = getattr(self.app, "save", None)
        if callable(save):
            try:
                save()
            except Exception:
                pass
        return value

    def _handle_transfer_drop(self, event):
        paths = self._parse_dropped_paths(event)
        if not paths:
            self.status_var.set("没有识别到可发送的文件或文件夹。")
            return COPY
        self.push_paths_to_phone(paths)
        return COPY

    def push_paths_to_phone(self, paths: list[str]) -> None:
        serial = self.selected_serial()
        if not serial:
            self.status_var.set("请先选择或授权一台安卓设备，再拖入文件。")
            return
        valid_paths = [Path(path) for path in paths if Path(path).exists()]
        if not valid_paths:
            self.status_var.set("没有可发送的文件或文件夹。")
            return
        remote_dir = self._remote_transfer_dir()
        self.status_var.set(f"正在发送 {len(valid_paths)} 项到手机：{remote_dir}")

        def work() -> None:
            try:
                mkdir = self.run_adb(["shell", "mkdir", "-p", remote_dir], serial=serial, timeout=20.0)
                if mkdir.returncode != 0:
                    output = ((mkdir.stdout or "") + " " + (mkdir.stderr or "")).strip()
                    raise RuntimeError(output or f"adb mkdir failed: {mkdir.returncode}")
                failures: list[str] = []
                for path in valid_paths:
                    result = self.run_adb(["push", str(path), remote_dir], serial=serial, timeout=600.0)
                    if result.returncode != 0:
                        output = ((result.stdout or "") + " " + (result.stderr or "")).strip()
                        failures.append(f"{path.name}: {output or result.returncode}")
                if failures:
                    message = "；".join(failures[:3])
                    if len(failures) > 3:
                        message += f"；另有 {len(failures) - 3} 项失败"
                    self._post(lambda message=message: self.status_var.set("部分文件发送失败：" + message))
                    return
                self._post(lambda: self.status_var.set(f"已发送 {len(valid_paths)} 项到手机：{remote_dir}"))
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self.status_var.set("发送到手机失败：" + message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorPushFiles").start()

    def _phone_export_root(self) -> Path:
        root = Path(os.environ.get("TEMP") or os.environ.get("TMP") or str(self.module_dir)) / "PasserPhoneMirrorExports"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _start_transfer_drag(self, _event=None):
        paths = self.pull_transfer_path_for_drag()
        if not paths:
            return ((REFUSE_DROP,), (DND_FILES,), tuple())
        return ((COPY,), (DND_FILES,), tuple(paths))

    def pull_transfer_path_for_drag(self) -> list[str]:
        serial = self.selected_serial()
        if not serial:
            self.status_var.set("请先选择或授权一台安卓设备，再从手机拖出文件。")
            return []
        remote_dir = self._remote_transfer_dir()
        remote_name = remote_dir.rstrip("/").rsplit("/", 1)[-1] or "PhoneFiles"
        local_target = self._phone_export_root() / f"{remote_name}_{time.strftime('%Y%m%d_%H%M%S')}"
        self.status_var.set(f"正在从手机复制：{remote_dir}")
        try:
            result = self.run_adb(["pull", remote_dir, str(local_target)], serial=serial, timeout=600.0)
            output = ((result.stdout or "") + " " + (result.stderr or "")).strip()
            if result.returncode != 0:
                self.status_var.set("从手机复制失败：" + (output or str(result.returncode)))
                return []
            if not local_target.exists():
                candidates = [path for path in local_target.parent.glob(local_target.name + "*") if path.exists()]
                if candidates:
                    local_target = candidates[0]
            if not local_target.exists():
                self.status_var.set("从手机复制完成，但没有找到本地导出文件。")
                return []
            self.status_var.set(f"已复制到电脑缓存，可拖出粘贴：{local_target}")
            return [str(local_target)]
        except Exception as exc:
            self.status_var.set(f"从手机复制失败：{exc}")
            return []

    def _post(self, callback: Callable[[], None]) -> None:
        if self.closed:
            return
        try:
            self.window.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    def refresh_devices(self) -> None:
        self.status_var.set("\u6b63\u5728\u5237\u65b0\u8bbe\u5907...")
        def work() -> None:
            try:
                devices = list_adb_devices(self.adb_path)
                self._post(lambda devices=devices: self._set_devices(devices, None))
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self._set_devices([], message))
        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorLauncherRefresh").start()

    def _set_devices(self, devices: list[AdbDevice], error: str | None) -> None:
        self.devices = devices
        ready = [device for device in devices if device.state == "device"]
        self.device_label_to_serial = {device.label: device.serial for device in ready}
        labels = list(self.device_label_to_serial.keys())
        self.device_combo.configure(values=labels)
        preferred_label = next(
            (label for label, serial in self.device_label_to_serial.items()
             if serial == self.preferred_serial),
            "",
        )
        if preferred_label:
            self.device_var.set(preferred_label)
        elif labels and self.device_var.get() not in labels:
            self.device_var.set(labels[0])
        if error:
            self.status_var.set("ADB \u68c0\u6d4b\u5931\u8d25\uff1a" + error)
        elif ready:
            self.status_var.set(f"\u5df2\u53d1\u73b0 {len(ready)} \u53f0\u53ef\u7528\u5b89\u5353\u8bbe\u5907\u3002\u70b9\u51fb\u542f\u52a8\u6295\u5c4f\u3002")
        elif devices:
            states = ", ".join(f"{d.serial}: {d.state}" for d in devices)
            self.status_var.set("\u672a\u53d1\u73b0\u5df2\u6388\u6743\u8bbe\u5907\uff1a" + states + "\u3002\u8bf7\u786e\u8ba4\u65e0\u7ebf\u8c03\u8bd5\u914d\u5bf9\u6216 USB \u8c03\u8bd5\u6388\u6743\u3002")
        else:
            self.status_var.set("\u6ca1\u6709\u53d1\u73b0\u8bbe\u5907\u3002Android 11+ \u8bf7\u4f7f\u7528\u4e0a\u65b9\u65e0\u7ebf\u914d\u5bf9\uff1b\u65e7\u7248\u5b89\u5353\u53ef\u4f7f\u7528 USB \u65b9\u5f0f\u542f\u7528\u3002")

    def selected_serial(self) -> str:
        label = self.device_var.get()
        if label in self.device_label_to_serial:
            return self.device_label_to_serial[label]
        ready = [device for device in self.devices if device.state == "device"]
        return ready[0].serial if len(ready) == 1 else ""

    def selected_device(self) -> AdbDevice | None:
        serial = self.selected_serial()
        if not serial:
            return None
        return next((device for device in self.devices if device.serial == serial), None)

    def wireless_target(self) -> str:
        host = self.ip_var.get().strip()
        port = self.port_var.get().strip() or "5555"
        self.port_var.set(port)
        if not host:
            return ""
        return host if ":" in host else f"{host}:{port}"

    def run_adb(self, args: list[str], serial: str = "", timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
        command = [self.adb_path]
        if serial:
            command.extend(["-s", serial])
        command.extend(args)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_no_window_flags(),
        )

    @staticmethod
    def _valid_host_port(value: str) -> bool:
        value = value.strip()
        if not value or ":" not in value:
            return False
        host, port = value.rsplit(":", 1)
        return bool(host.strip()) and bool(re.fullmatch(r"\d{1,5}", port)) and 1 <= int(port) <= 65535

    def discover_wireless_target(self, host: str) -> str:
        try:
            result = self.run_adb(["mdns", "services"], timeout=8.0)
        except Exception:
            return ""
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        candidates: list[str] = []
        for line in output.splitlines():
            if "_adb-tls-connect._tcp" not in line:
                continue
            match = re.search(r"((?:\d{1,3}\.){3}\d{1,3}:\d{1,5})\s*$", line.strip())
            if match:
                candidates.append(match.group(1))
        same_host = next((target for target in candidates if target.rsplit(":", 1)[0] == host), "")
        return same_host or (candidates[0] if len(candidates) == 1 else "")

    def pair_wireless(self) -> None:
        address = self.pair_address_var.get().strip()
        code = re.sub(r"\s+", "", self.pair_code_var.get())
        if not self._valid_host_port(address):
            self.status_var.set("\u8bf7\u8f93\u5165\u624b\u673a\u201c\u4f7f\u7528\u914d\u5bf9\u7801\u914d\u5bf9\u8bbe\u5907\u201d\u9875\u9762\u663e\u793a\u7684 IP \u5730\u5740\u548c\u7aef\u53e3\u3002")
            return
        if not re.fullmatch(r"\d{6}", code):
            self.status_var.set("\u914d\u5bf9\u7801\u5e94\u4e3a\u624b\u673a\u4e0a\u663e\u793a\u7684 6 \u4f4d\u6570\u5b57\u3002")
            return
        host = address.rsplit(":", 1)[0]
        self.status_var.set(f"\u6b63\u5728\u65e0\u7ebf\u914d\u5bf9 {address}...")

        def work() -> None:
            try:
                result = self.run_adb(["pair", address, code], timeout=25.0)
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                lowered = output.lower()
                if "protocol fault" in lowered or "couldn't read status message" in lowered:
                    self.run_adb(["kill-server"], timeout=8.0)
                    self.run_adb(["start-server"], timeout=15.0)
                    result = self.run_adb(["pair", address, code], timeout=25.0)
                    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                lowered = output.lower()
                if result.returncode != 0 or "failed" in lowered or "unable" in lowered:
                    raise RuntimeError(output or f"adb pair {address} failed")
                target = self.discover_wireless_target(host)
                connect_output = ""
                if target:
                    connected = self.run_adb(["connect", target], timeout=15.0)
                    connect_output = ((connected.stdout or "") + "\n" + (connected.stderr or "")).strip()
                    if connected.returncode != 0 or "failed" in connect_output.lower():
                        target = ""
                self._post(
                    lambda address=address, host=host, target=target, output=output, connect_output=connect_output:
                    self._wireless_paired(address, host, target, output, connect_output)
                )
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self._wireless_pair_failed(message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorWirelessPair").start()

    def _wireless_pair_failed(self, message: str) -> None:
        lowered = message.lower()
        if "protocol fault" in lowered or "couldn't read status message" in lowered:
            self.status_var.set(
                "\u65e0\u7ebf\u914d\u5bf9\u8fde\u63a5\u88ab\u624b\u673a\u4e2d\u65ad\u3002\u8bf7\u5728\u624b\u673a\u4e0a\u91cd\u65b0\u6253\u5f00\u201c\u4f7f\u7528\u914d\u5bf9\u7801\u914d\u5bf9\u8bbe\u5907\u201d\uff0c\u7acb\u5373\u8f93\u5165\u65b0\u7684\u5730\u5740\u548c 6 \u4f4d\u914d\u5bf9\u7801\uff0c\u5e76\u786e\u4fdd\u7535\u8111\u4e0e\u624b\u673a\u5728\u540c\u4e00 Wi-Fi\u3002"
            )
            return
        self.status_var.set("\u65e0\u7ebf\u914d\u5bf9\u5931\u8d25\uff1a" + message)

    def _wireless_paired(self, address: str, host: str, target: str, output: str, connect_output: str) -> None:
        self.pair_code_var.set("")
        self.ip_var.set(host)
        if target:
            connect_host, connect_port = target.rsplit(":", 1)
            self.ip_var.set(connect_host)
            self.port_var.set(connect_port)
            self.status_var.set(f"\u5df2\u914d\u5bf9\u5e76\u8fde\u63a5 {target}\u3002\u5237\u65b0\u8bbe\u5907\u540e\u53ef\u76f4\u63a5\u542f\u52a8\u6295\u5c4f\u3002")
            self.refresh_devices()
            return
        suffix = f" {output}" if output else ""
        self.status_var.set(
            f"\u5df2\u914d\u5bf9 {address}\u3002\u8bf7\u628a\u624b\u673a\u201c\u65e0\u7ebf\u8c03\u8bd5\u201d\u4e3b\u9875\u7684 IP \u548c\u7aef\u53e3\u586b\u5230\u4e0b\u65b9\uff0c\u518d\u70b9\u51fb\u8fde\u63a5\u65e0\u7ebf\u3002{suffix}"
        )

    def detect_wireless_ip(self, serial: str) -> str:
        checks = (["shell", "ip", "route"], ["shell", "ip", "addr", "show", "wlan0"])
        for args in checks:
            try:
                result = self.run_adb(args, serial=serial, timeout=6.0)
            except Exception:
                continue
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            private_ip = r"(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})"
            source_match = re.search(rf"\bsrc\s+({private_ip})\b", output)
            if source_match:
                return source_match.group(1)
            pattern = rf"\b{private_ip}\b"
            for match in re.findall(pattern, output):
                if not match.endswith(".255"):
                    return match
        return ""

    def enable_wireless(self) -> None:
        device = self.selected_device()
        if device is None:
            self.status_var.set("\u8bf7\u5148\u7528 USB \u8fde\u63a5\u5e76\u6388\u6743\u4e00\u53f0\u5b89\u5353\u8bbe\u5907\u3002")
            return
        if ":" in device.serial:
            self.ip_var.set(device.serial.split(":", 1)[0])
            self.status_var.set("\u8be5\u8bbe\u5907\u5df2\u662f\u65e0\u7ebf ADB \u8fde\u63a5\uff0c\u53ef\u76f4\u63a5\u542f\u52a8\u6295\u5c4f\u3002")
            return
        port = self.port_var.get().strip() or "5555"
        self.port_var.set(port)
        if not re.fullmatch(r"\d{1,5}", port) or not 1 <= int(port) <= 65535:
            self.status_var.set("\u7aef\u53e3\u9700\u8981\u662f 1-65535 \u4e4b\u95f4\u7684\u6570\u5b57\uff0c\u9ed8\u8ba4 5555\u3002")
            return
        self.status_var.set("\u6b63\u5728\u5f00\u542f\u65e0\u7ebf ADB...")

        def work() -> None:
            try:
                ip = self.detect_wireless_ip(device.serial)
                result = self.run_adb(["tcpip", port], serial=device.serial)
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if result.returncode != 0:
                    raise RuntimeError(output or f"adb tcpip {port} failed")
                self._post(lambda ip=ip, port=port, output=output: self._wireless_enabled(ip, port, output))
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self.status_var.set("\u5f00\u542f\u65e0\u7ebf ADB \u5931\u8d25\uff1a" + message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorWirelessEnable").start()

    def _wireless_enabled(self, ip: str, port: str, output: str) -> None:
        if ip:
            self.ip_var.set(ip)
            self.status_var.set(f"\u5df2\u542f\u7528\u65e0\u7ebf ADB\uff1a{ip}:{port}\u3002\u70b9\u51fb\u8fde\u63a5\u65e0\u7ebf\uff0c\u6210\u529f\u540e\u53ef\u62d4\u6389 USB\u3002")
        else:
            suffix = f" {output}" if output else ""
            self.status_var.set("\u5df2\u542f\u7528\u65e0\u7ebf ADB\uff0c\u4f46\u672a\u81ea\u52a8\u8bc6\u522b IP\u3002\u8bf7\u8f93\u5165\u624b\u673a Wi-Fi IP \u540e\u70b9\u51fb\u8fde\u63a5\u65e0\u7ebf\u3002" + suffix)

    def connect_wireless(self) -> None:
        target = self.wireless_target()
        if not target:
            self.status_var.set("\u8bf7\u8f93\u5165\u624b\u673a Wi-Fi IP\uff0c\u4f8b\u5982 192.168.1.23\u3002")
            return
        self.status_var.set(f"\u6b63\u5728\u8fde\u63a5 {target}...")

        def work() -> None:
            try:
                result = self.run_adb(["connect", target])
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if result.returncode != 0 or "failed" in output.lower() or "unable" in output.lower():
                    raise RuntimeError(output or f"adb connect {target} failed")
                self._post(lambda target=target, output=output: self._wireless_connected(target, output))
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self.status_var.set("\u8fde\u63a5\u65e0\u7ebf ADB \u5931\u8d25\uff1a" + message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorWirelessConnect").start()

    def _wireless_connected(self, target: str, output: str) -> None:
        suffix = f" {output}" if output else ""
        self.status_var.set(f"\u5df2\u8fde\u63a5 {target}\u3002\u53ef\u4ee5\u62d4\u6389 USB\uff0c\u7136\u540e\u70b9\u51fb\u542f\u52a8\u6295\u5c4f\u3002{suffix}")
        self.refresh_devices()

    def disconnect_wireless(self) -> None:
        target = self.wireless_target()
        if not target:
            self.status_var.set("\u8bf7\u8f93\u5165\u8981\u65ad\u5f00\u7684\u65e0\u7ebf\u5730\u5740\u3002")
            return
        self.status_var.set(f"\u6b63\u5728\u65ad\u5f00 {target}...")

        def work() -> None:
            try:
                result = self.run_adb(["disconnect", target], timeout=8.0)
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if result.returncode != 0:
                    raise RuntimeError(output or f"adb disconnect {target} failed")
                self._post(lambda target=target, output=output: self._wireless_disconnected(target, output))
            except Exception as exc:
                message = str(exc)
                self._post(lambda message=message: self.status_var.set("\u65ad\u5f00\u65e0\u7ebf ADB \u5931\u8d25\uff1a" + message))

        threading.Thread(target=work, daemon=True, name="Passer-PhoneMirrorWirelessDisconnect").start()

    def _wireless_disconnected(self, target: str, output: str) -> None:
        suffix = f" {output}" if output else ""
        self.status_var.set(f"\u5df2\u65ad\u5f00 {target}\u3002{suffix}")
        self.refresh_devices()

    def _mouse_mode_key(self) -> str:
        return MOUSE_MODE_KEYS.get(self.mouse_mode_var.get(), "seamless")

    def _audio_mode_key(self) -> str:
        return AUDIO_MODE_KEYS.get(self.audio_mode_var.get(), "sync")

    def _save_projection_settings(self) -> None:
        settings = getattr(self.app, "settings", None)
        if isinstance(settings, dict):
            settings["phone_mirror_mouse_mode"] = self._mouse_mode_key()
            settings["phone_mirror_mouse_sensitivity"] = int(self.mouse_sensitivity_var.get())
            settings["phone_mirror_audio_mode"] = self._audio_mode_key()
            settings["phone_mirror_transfer_path"] = self._remote_transfer_dir()
            settings["phone_mirror_wireless_ip"] = self.ip_var.get().strip()
            try:
                settings["phone_mirror_wireless_port"] = int(self.port_var.get().strip() or 5555)
            except (TypeError, ValueError):
                settings["phone_mirror_wireless_port"] = 5555
            settings["phone_mirror_device_serial"] = self.selected_serial() or self.preferred_serial
        save = getattr(self.app, "save", None)
        if callable(save):
            try:
                save()
            except Exception:
                pass

    def apply_ai_configuration(self, config: dict) -> str:
        normalized = normalize_phone_mirror_settings(
            getattr(self.app, "settings", {}), config,
        )
        self.mouse_mode_var.set(MOUSE_MODE_LABELS[normalized["phone_mirror_mouse_mode"]])
        self.mouse_sensitivity_var.set(normalized["phone_mirror_mouse_sensitivity"])
        self.audio_mode_var.set(AUDIO_MODE_LABELS[normalized["phone_mirror_audio_mode"]])
        self.remote_transfer_var.set(normalized["phone_mirror_transfer_path"])
        self.ip_var.set(normalized["phone_mirror_wireless_ip"])
        self.port_var.set(str(normalized["phone_mirror_wireless_port"]))
        self.preferred_serial = normalized["phone_mirror_device_serial"]
        preferred_label = next(
            (label for label, serial in self.device_label_to_serial.items()
             if serial == self.preferred_serial),
            "",
        )
        if preferred_label:
            self.device_var.set(preferred_label)
        return "手机投屏窗口配置已同步。"

    def _apply_pointer_speed(self, serial: str) -> str:
        sensitivity = max(1, min(15, int(self.mouse_sensitivity_var.get())))
        pointer_speed = sensitivity - 8
        try:
            result = self.run_adb(
                ["shell", "settings", "put", "system", "pointer_speed", str(pointer_speed)],
                serial=serial,
                timeout=5.0,
            )
            if result.returncode != 0:
                output = ((result.stdout or "") + " " + (result.stderr or "")).strip()
                return f"\uff1b指针速度设置失败：{output or result.returncode}"
        except Exception as exc:
            return f"\uff1b指针速度设置失败：{exc}"
        return f"\uff1b鼠标灵敏度 {sensitivity}/15"

    def _apply_audio_mode(self, command: list[str], scrcpy: str) -> str:
        mode_key = self._audio_mode_key()
        if mode_key == "phone_only":
            if scrcpy_supports(scrcpy, "--no-audio"):
                command.append("--no-audio")
                return "；声音仅手机"
            return "；声音仅手机（当前 scrcpy 不支持）"
        if mode_key == "pc_only":
            if scrcpy_supports(scrcpy, "--audio-source"):
                command.append("--audio-source=output")
            return "；声音仅电脑"
        if scrcpy_supports(scrcpy, "--audio-source"):
            command.append("--audio-source=playback")
        if scrcpy_supports(scrcpy, "--audio-dup"):
            command.append("--audio-dup")
            return "；声音同步"
        return "；声音同步（当前 scrcpy 不支持双端播放）"

    def open_projection_settings(self) -> None:
        dialog = tk.Toplevel(self.window)
        dialog.withdraw()
        dialog.overrideredirect(True)
        dialog.transient(self.window)
        dialog.configure(bg=self.theme.border)
        shell = tk.Frame(dialog, bg=self.theme.surface_bg, highlightthickness=1, highlightbackground=self.theme.border)
        shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        bar = tk.Frame(shell, bg=self.theme.title_bg, height=42)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        tk.Label(bar, text="投屏设置", bg=self.theme.title_bg, fg="#dbe7ff",
                 font=self._font(10, "bold"), anchor=tk.W).pack(side=tk.LEFT, padx=14)
        body = tk.Frame(shell, bg=self.theme.surface_bg)
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

        tk.Label(body, text="鼠标模式", bg=self.theme.surface_bg, fg="#0f172a",
                 font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 6))
        mode_select = PasserSelect(
            body,
            variable=self.mouse_mode_var,
            values=tuple(MOUSE_MODE_LABELS.values()),
            font=self._font(10),
            accent=self.theme.accent,
        )
        mode_select.pack(fill=tk.X, pady=(0, 12))

        sensitivity_label = tk.Label(body, bg=self.theme.surface_bg, fg=self.theme.accent,
                                     font=self._font(10, "bold"), anchor=tk.E)
        sensitivity_label.pack(fill=tk.X)
        scale = tk.Scale(
            body,
            from_=1,
            to=15,
            orient=tk.HORIZONTAL,
            variable=self.mouse_sensitivity_var,
            showvalue=False,
            resolution=1,
            bd=0,
            highlightthickness=0,
            bg=self.theme.surface_bg,
            troughcolor="#dbe4f0",
            activebackground=self.theme.accent,
        )
        scale.pack(fill=tk.X, pady=(0, 8))

        def refresh_label(*_args) -> None:
            sensitivity_label.configure(text=f"鼠标灵敏度：{int(self.mouse_sensitivity_var.get())}/15")

        self.mouse_sensitivity_var.trace_add("write", refresh_label)
        refresh_label()
        tk.Label(
            body,
            text="无缝模式便于鼠标自由进出投屏窗口；UHID 高级模式更接近真鼠标，但可能被投屏窗口捕获。",
            bg=self.theme.surface_bg,
            fg="#64748b",
            wraplength=380,
            justify=tk.LEFT,
            font=self._font(8),
        ).pack(fill=tk.X, pady=(2, 14))

        tk.Label(body, text="声音模式", bg=self.theme.surface_bg, fg="#0f172a",
                 font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 6))
        audio_select = PasserSelect(
            body,
            variable=self.audio_mode_var,
            values=tuple(AUDIO_MODE_LABELS.values()),
            font=self._font(10),
            accent=self.theme.accent,
        )
        audio_select.pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            body,
            text="同步：电脑和手机一起出声；仅电脑：声音转到电脑；仅手机：电脑不接管声音。",
            bg=self.theme.surface_bg,
            fg="#64748b",
            wraplength=380,
            justify=tk.LEFT,
            font=self._font(8),
        ).pack(fill=tk.X, pady=(0, 14))

        tk.Label(body, text="手机传输目录", bg=self.theme.surface_bg, fg="#0f172a",
                 font=self._font(10, "bold"), anchor=tk.W).pack(fill=tk.X, pady=(0, 6))
        self._entry(body, self.remote_transfer_var).pack(fill=tk.X, pady=(0, 6), ipady=5)
        tk.Label(
            body,
            text="拖入投屏的文件会发送到此目录；从投屏底部拖出时会导出此目录。",
            bg=self.theme.surface_bg,
            fg="#64748b",
            wraplength=380,
            justify=tk.LEFT,
            font=self._font(8),
        ).pack(fill=tk.X, pady=(0, 14))

        actions = tk.Frame(body, bg=self.theme.surface_bg)
        actions.pack(fill=tk.X)

        def close(save: bool = False) -> None:
            if save:
                self._save_projection_settings()
                self.status_var.set("投屏设置已保存。")
            try:
                dialog.destroy()
            except Exception:
                pass

        self._button(actions, "保存", lambda: close(True), primary=True).pack(side=tk.RIGHT)
        self._button(actions, "取消", lambda: close(False)).pack(side=tk.RIGHT, padx=(0, 8))
        bar.bind("<ButtonPress-1>", lambda event: setattr(dialog, "_move_start", (event.x_root, event.y_root, dialog.winfo_x(), dialog.winfo_y())))
        bar.bind("<B1-Motion>", lambda event: (
            dialog.geometry(f"+{dialog._move_start[2] + event.x_root - dialog._move_start[0]}+{dialog._move_start[3] + event.y_root - dialog._move_start[1]}")
            if getattr(dialog, "_move_start", None) else None
        ))
        dialog.bind("<Escape>", lambda _event: close(False))
        dialog.update_idletasks()
        width, height = 430, max(260, shell.winfo_reqheight() + 2)
        x = self.window.winfo_rootx() + max(0, (self.window.winfo_width() - width) // 2)
        y = self.window.winfo_rooty() + max(0, (self.window.winfo_height() - height) // 2)
        self.theme.place_toplevel_absolute(dialog, width, height, x, y)
        dialog.attributes("-topmost", self.window.attributes("-topmost"))
        dialog.deiconify()
        dialog.lift(self.window)
        dialog.focus_force()

    def start_projection(self) -> None:
        if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
            self.status_var.set("scrcpy \u5df2\u5728\u8fd0\u884c\u3002")
            self._stop_projection_dock_tracking(destroy=True)
            return
        scrcpy = find_scrcpy_path(self.module_dir)
        if not scrcpy:
            self.status_var.set("\u672a\u627e\u5230 scrcpy.exe\u3002\u8bf7\u628a scrcpy \u653e\u5230 tools\\scrcpy\u3002")
            return
        serial = self.selected_serial()
        ready_count = len([device for device in self.devices if device.state == "device"])
        if not serial:
            self.status_var.set("\u8bf7\u5148\u9009\u62e9\u6216\u6388\u6743\u4e00\u53f0\u5b89\u5353\u8bbe\u5907\u3002" if ready_count else "\u8bf7\u5148\u8fde\u63a5\u5e76\u6388\u6743\u4e00\u53f0\u5b89\u5353\u8bbe\u5907\u3002")
            return
        command = [scrcpy, "--serial", serial]
        clipboard_sync = enable_scrcpy_clipboard_sync(command, scrcpy)
        self.projection_windows_before = {item.hwnd for item in enumerate_windows()}
        self.projection_window_title = f"Passer 手机投屏 - {serial}"
        if scrcpy_supports(scrcpy, "--window-title"):
            command.append(f"--window-title={self.projection_window_title}")
        else:
            self.projection_window_title = ""
        remote_dir = self._remote_transfer_dir()
        transfer_note = ""
        try:
            mkdir = self.run_adb(["shell", "mkdir", "-p", remote_dir], serial=serial, timeout=8.0)
            if mkdir.returncode != 0:
                output = ((mkdir.stdout or "") + " " + (mkdir.stderr or "")).strip()
                transfer_note = f"；传输目录初始化失败：{output or mkdir.returncode}"
        except Exception as exc:
            transfer_note = f"；传输目录初始化失败：{exc}"
        if scrcpy_supports(scrcpy, "--push-target"):
            command.append(f"--push-target={remote_dir}/")
        if scrcpy_supports(scrcpy, "--stay-awake"):
            command.append("--stay-awake")
        mode_key = self._mouse_mode_key()
        uhid_control = mode_key == "uhid"
        if mode_key == "uhid" and scrcpy_supports(scrcpy, "--mouse=mode"):
            command.append("--mouse=uhid")
        elif mode_key == "seamless" and scrcpy_supports(scrcpy, "--mouse=mode"):
            command.append("--mouse=sdk")
        if mode_key == "uhid" and scrcpy_supports(scrcpy, "--keyboard=mode"):
            command.append("--keyboard=uhid")
        elif mode_key == "seamless" and scrcpy_supports(scrcpy, "--keyboard=mode"):
            command.append("--keyboard=sdk")
        audio_note = self._apply_audio_mode(command, scrcpy)
        speed_note = self._apply_pointer_speed(serial)
        clipboard_note = "；双向剪贴板同步" if clipboard_sync else ""
        try:
            self.scrcpy_process = subprocess.Popen(command, cwd=str(Path(scrcpy).parent), creationflags=_no_window_flags())
            self._stop_projection_dock_tracking(destroy=True)
            mode = "\uff08UHID 高级控制\uff09" if uhid_control else "\uff08无缝鼠标模式\uff09"
            self.status_var.set(f"\u5df2\u542f\u52a8\u6295\u5c4f\u7a97\u53e3{mode}{audio_note}{speed_note}{clipboard_note}{transfer_note}\u3002")
        except Exception as exc:
            self._stop_projection_dock_tracking()
            self.status_var.set(f"\u542f\u52a8 scrcpy \u5931\u8d25\uff1a{exc}")

    def stop_projection(self) -> None:
        self._stop_projection_dock_tracking()
        proc = self.scrcpy_process
        if proc is not None and proc.poll() is None:
            if _terminate_process(proc):
                self.status_var.set("\u5df2\u8bf7\u6c42\u505c\u6b62\u6295\u5c4f\u3002")
            else:
                self.status_var.set("\u505c\u6b62\u6295\u5c4f\u5931\u8d25\uff1a\u8fdb\u7a0b\u672a\u54cd\u5e94\u3002")
        else:
            self.status_var.set("\u6ca1\u6709\u7531 Passer \u542f\u52a8\u7684\u6295\u5c4f\u8fdb\u7a0b\u3002")
        self.scrcpy_process = None
        self.projection_topmost_var.set(False)

    def open_advanced(self) -> None:
        if self.advanced_window is not None and not getattr(self.advanced_window, "closed", True):
            self.advanced_window.show()
        else:
            self.advanced_window = PhoneMirrorAdvancedWindow(
                self.app,
                self.theme,
                on_close=self._restore_focus_after_advanced,
            )
        try:
            self.advanced_window.adb_var.set(self.adb_path)
            if self.device_var.get():
                self.advanced_window.device_var.set(self.device_var.get())
        except Exception:
            pass

    def _restore_focus_after_advanced(self) -> None:
        if self.closed:
            return
        try:
            self.app.hide_search_results()
        except Exception:
            pass

        def restore() -> None:
            if self.closed:
                return
            try:
                self.window.deiconify()
                self.window.lift()
                self.window.focus_set()
                self.app.hide_search_results()
            except Exception:
                pass

        try:
            self.window.after_idle(restore)
        except Exception:
            pass

    def show(self) -> None:
        try:
            self.window.deiconify(); self.window.lift(); self.window.focus_set()
            if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
                self._stop_projection_dock_tracking(destroy=True)
                self.status_var.set("投屏仍在运行，可继续管理。")
        except Exception:
            pass

    def start_move(self, event: tk.Event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event: tk.Event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def close(self) -> None:
        if self.closed:
            return
        self._save_projection_settings()
        self._stop_projection_dock_tracking(destroy=False)
        if self.advanced_window is not None and not getattr(self.advanced_window, "closed", True):
            self.advanced_window.hide(notify_parent=False)
        try:
            self.window.withdraw()
            if self.scrcpy_process is not None and self.scrcpy_process.poll() is None:
                self.status_var.set("控制窗口已隐藏，正在进行的投屏保持运行。")
        except Exception:
            pass


def open_tool(context: Any) -> PhoneMirrorWindow:
    """Entry point for Passer's later BuiltinModules API."""

    from clicker_tool import ClickerTheme as _ClickerTheme

    theme = _ClickerTheme(
        title=MODULE_NAME,
        border=context.colors.get("border", "#cbd5e1"),
        app_bg=context.colors.get("app_bg", "#f8fafc"),
        surface_bg=context.colors.get("surface_bg", "#ffffff"),
        title_bg=context.colors.get("title_bg", "#172235"),
        muted_fg=context.colors.get("muted_fg", "#64748b"),
        accent=context.colors.get("accent", "#2563eb"),
        danger=context.colors.get("danger", "#ef4444"),
        app_font=context.app_font,
        center_over_root=lambda root, w, h: (
            root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2),
            root.winfo_rooty() + max(0, (root.winfo_height() - h) // 2),
        ),
        place_toplevel_absolute=lambda win, w, h, x, y: win.geometry(f"{w}x{h}+{x}+{y}"),
    )

    class _ContextApp:
        root = context.root
        topmost_var = tk.BooleanVar(value=True)
        settings = context.settings if isinstance(context.settings, dict) else {}

        @staticmethod
        def apply_window_transparency(_window: tk.Toplevel) -> None:
            return None

        @staticmethod
        def save() -> None:
            context.save_settings()

        @staticmethod
        def hide_search_results() -> None:
            return None

    controller = PhoneMirrorWindow(_ContextApp(), theme)
    context.place_window(controller)
    context.write_status("已打开手机投屏交互模块。")
    return controller


if __name__ == "__main__":
    class _StandaloneTheme:
        title = MODULE_NAME
        border = "#cbd5e1"
        app_bg = "#f8fafc"
        surface_bg = "#ffffff"
        title_bg = "#172235"
        muted_fg = "#64748b"
        accent = "#2563eb"
        danger = "#ef4444"
        accent_hover = "#1d4ed8"
        accent_soft = "#e8f1ff"
        accent_soft_hover = "#dbeafe"
        title_button_bg = "#172235"
        title_button_hover = "#223047"

        @staticmethod
        def app_font(size: int = 9, weight: str = "normal") -> tuple[str, int, str]:
            return ("Microsoft YaHei UI", size, weight)

        @staticmethod
        def center_over_root(root: tk.Tk, width: int, height: int) -> tuple[int, int]:
            root.update_idletasks()
            return (
                root.winfo_screenwidth() // 2 - width // 2,
                root.winfo_screenheight() // 2 - height // 2,
            )

        @staticmethod
        def place_toplevel_absolute(window: tk.Toplevel, width: int, height: int, x: int, y: int) -> None:
            window.geometry(f"{width}x{height}+{x}+{y}")

    class _StandaloneApp:
        def __init__(self) -> None:
            self.root = tk.Tk()
            self.root.withdraw()
            self.topmost_var = tk.BooleanVar(value=False)

        @staticmethod
        def apply_window_transparency(_window: tk.Toplevel) -> None:
            return None

    app = _StandaloneApp()
    PhoneMirrorWindow(app, _StandaloneTheme())
    app.root.mainloop()
