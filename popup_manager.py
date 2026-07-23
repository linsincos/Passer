from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable, Sequence


MonitorTuple = tuple[bool, int, int, int, int]
WorkAreaTuple = tuple[int, int, int, int]


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class MonitorWorkArea:
    primary: bool
    left: int
    top: int
    right: int
    bottom: int

    @property
    def area(self) -> WorkAreaTuple:
        return self.left, self.top, self.right, self.bottom

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def visible_size(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        visible_w = min(x + width, self.right) - max(x, self.left)
        visible_h = min(y + height, self.bottom) - max(y, self.top)
        return max(0, visible_w), max(0, visible_h)

    def distance_to_point(self, x: int, y: int) -> int:
        cx = self.left + self.width // 2
        cy = self.top + self.height // 2
        return (cx - x) ** 2 + (cy - y) ** 2


def _coerce_monitor(value: MonitorWorkArea | MonitorTuple) -> MonitorWorkArea | None:
    if isinstance(value, MonitorWorkArea):
        return value
    try:
        primary, left, top, right, bottom = value
        return MonitorWorkArea(bool(primary), int(left), int(top), int(right), int(bottom))
    except Exception:
        return None


def get_windows_work_areas() -> list[MonitorTuple]:
    if sys.platform != "win32":
        return []

    monitors: list[MonitorTuple] = []
    try:
        user32 = ctypes.windll.user32
        monitor_enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(_Rect),
            wintypes.LPARAM,
        )

        def callback(hmonitor, hdc, rect, data):
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                work = info.rcWork
                monitors.append((
                    bool(info.dwFlags & 1),
                    int(work.left),
                    int(work.top),
                    int(work.right),
                    int(work.bottom),
                ))
            return 1

        user32.EnumDisplayMonitors(None, None, monitor_enum_proc(callback), 0)
    except Exception:
        return []

    return monitors


def tk_geometry(width: int, height: int, x: int, y: int) -> str:
    return f"{int(width)}x{int(height)}{int(x):+d}{int(y):+d}"


class PopupManager:
    """Shared geometry and placement policy for Passer popups and tool windows."""

    def __init__(
        self,
        monitor_provider: Callable[[], Sequence[MonitorWorkArea | MonitorTuple]] | None = None,
    ) -> None:
        self.monitor_provider = monitor_provider or get_windows_work_areas

    def monitors(self) -> list[MonitorWorkArea]:
        values: list[MonitorWorkArea] = []
        try:
            raw = self.monitor_provider()
        except Exception:
            raw = []
        for item in raw or []:
            monitor = _coerce_monitor(item)
            if monitor is not None and monitor.width > 0 and monitor.height > 0:
                values.append(monitor)
        return values

    def get_work_areas(self) -> list[MonitorTuple]:
        return [
            (monitor.primary, monitor.left, monitor.top, monitor.right, monitor.bottom)
            for monitor in self.monitors()
        ]

    def root_work_area(self, root: Any) -> WorkAreaTuple | None:
        monitors = self.monitors()
        if not monitors:
            return None
        x, y, width, height = self._root_rect(root)
        center_x = x + max(width, 1) // 2
        center_y = y + max(height, 1) // 2
        monitor = self._monitor_for_point(center_x, center_y, monitors)
        return monitor.area if monitor is not None else None

    def point_work_area(self, x: int, y: int) -> WorkAreaTuple | None:
        monitors = self.monitors()
        if not monitors:
            return None
        monitor = self._monitor_for_point(int(x), int(y), monitors)
        return monitor.area if monitor is not None else None

    @staticmethod
    def clamp_to_work_area(
        x: int,
        y: int,
        width: int,
        height: int,
        area: WorkAreaTuple | None,
    ) -> tuple[int, int]:
        if not area:
            return int(x), int(y)
        left, top, right, bottom = (int(v) for v in area)
        max_x = max(left, right - int(width))
        max_y = max(top, bottom - int(height))
        return min(max(int(x), left), max_x), min(max(int(y), top), max_y)

    def rect_intersects_any_work_area(self, x: int, y: int, width: int, height: int) -> bool:
        monitors = self.monitors()
        if not monitors:
            return True
        rect_right = int(x) + int(width)
        rect_bottom = int(y) + int(height)
        for monitor in monitors:
            if not (
                rect_right <= monitor.left
                or int(x) >= monitor.right
                or rect_bottom <= monitor.top
                or int(y) >= monitor.bottom
            ):
                return True
        return False

    def center_over_root(
        self,
        root: Any,
        width: int,
        height: int,
        *,
        min_width: int = 1,
        min_height: int = 1,
    ) -> tuple[int, int]:
        root_x, root_y, root_width, root_height = self._root_rect(root)
        root_width = max(root_width, int(min_width), 1)
        root_height = max(root_height, int(min_height), 1)
        x = root_x + (root_width - int(width)) // 2
        y = root_y + (root_height - int(height)) // 2
        return self.clamp_to_work_area(x, y, width, height, self.root_work_area(root))

    def startup_window_position(self, width: int, height: int, root: Any) -> tuple[int, int]:
        area = self._preferred_startup_area(root)
        return self._center_in_area(width, height, area)

    def first_run_geometry(
        self,
        root: Any,
        *,
        min_width: int,
        min_height: int,
    ) -> tuple[int, int, int, int]:
        area = self._preferred_startup_area(root)
        left, top, right, bottom = area
        area_w = max(1, right - left)
        area_h = max(1, bottom - top)
        width = max(int(min_width), area_w // 2)
        height = max(int(min_height), area_h // 2)
        x, y = self._center_in_area(width, height, area)
        return width, height, x, y

    def restored_window_position(
        self,
        width: int,
        height: int,
        root: Any,
        saved_x: Any,
        saved_y: Any,
        *,
        min_visible_width: int = 80,
        min_visible_height: int = 48,
    ) -> tuple[int, int]:
        if not isinstance(saved_x, int) or not isinstance(saved_y, int):
            return self.startup_window_position(width, height, root)

        monitors = self.monitors()
        if monitors:
            for monitor in monitors:
                visible_w, visible_h = monitor.visible_size(saved_x, saved_y, int(width), int(height))
                if visible_w >= min_visible_width and visible_h >= min_visible_height:
                    return self.clamp_to_work_area(saved_x, saved_y, width, height, monitor.area)
            return self.startup_window_position(width, height, root)

        screen_w, screen_h = self._root_screen_size(root)
        if (
            saved_x + min_visible_width > 0
            and saved_y + min_visible_height > 0
            and saved_x < screen_w
            and saved_y < screen_h
        ):
            return (
                min(max(saved_x, 0), max(0, screen_w - int(width))),
                min(max(saved_y, 0), max(0, screen_h - int(height))),
            )
        return self.startup_window_position(width, height, root)

    def available_size_for_root(
        self,
        root: Any,
        *,
        margin_x: int = 0,
        margin_y: int = 0,
        fallback_width: int = 480,
        fallback_height: int = 420,
    ) -> tuple[int, int]:
        area = self.root_work_area(root)
        if area:
            left, top, right, bottom = area
            return (
                max(int(fallback_width), int(right - left) - int(margin_x)),
                max(int(fallback_height), int(bottom - top) - int(margin_y)),
            )
        screen_w, screen_h = self._root_screen_size(root)
        return (
            max(int(fallback_width), screen_w - int(margin_x)),
            max(int(fallback_height), screen_h - int(margin_y)),
        )

    @staticmethod
    def native_window_handle(window: tk.Tk | tk.Toplevel) -> int:
        try:
            frame = window.tk.call("wm", "frame", window._w)
            return int(str(frame), 0)
        except Exception:
            return int(window.winfo_id())

    def place_absolute(self, window: tk.Tk | tk.Toplevel, width: int, height: int, x: int, y: int) -> None:
        try:
            window.update_idletasks()
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.UINT,
                ]
                user32.SetWindowPos.restype = wintypes.BOOL
                swp_no_z_order = 0x0004
                ok = user32.SetWindowPos(
                    wintypes.HWND(self.native_window_handle(window)),
                    wintypes.HWND(0),
                    int(x),
                    int(y),
                    int(width),
                    int(height),
                    swp_no_z_order,
                )
                if ok:
                    return
            except Exception:
                pass
        window.geometry(tk_geometry(width, height, x, y))

    def _preferred_startup_area(self, root: Any) -> WorkAreaTuple:
        monitors = self.monitors()
        if monitors:
            non_primary = [monitor for monitor in monitors if not monitor.primary]
            candidates = non_primary or [monitor for monitor in monitors if monitor.primary] or monitors
            monitor = sorted(candidates, key=lambda item: (item.left, item.top))[0]
            return monitor.area
        screen_w, screen_h = self._root_screen_size(root)
        return 0, 0, screen_w, screen_h

    @staticmethod
    def _center_in_area(width: int, height: int, area: WorkAreaTuple) -> tuple[int, int]:
        left, top, right, bottom = (int(v) for v in area)
        area_w = max(1, right - left)
        area_h = max(1, bottom - top)
        return (
            left + max((area_w - int(width)) // 2, 0),
            top + max((area_h - int(height)) // 2, 0),
        )

    @staticmethod
    def _monitor_for_point(
        x: int,
        y: int,
        monitors: Sequence[MonitorWorkArea],
    ) -> MonitorWorkArea | None:
        for monitor in monitors:
            if monitor.contains_point(x, y):
                return monitor
        if not monitors:
            return None
        return min(monitors, key=lambda monitor: monitor.distance_to_point(x, y))

    @staticmethod
    def _safe_call(root: Any, name: str, default: int) -> int:
        try:
            return int(getattr(root, name)())
        except Exception:
            return int(default)

    def _root_rect(self, root: Any) -> tuple[int, int, int, int]:
        try:
            root.update_idletasks()
        except Exception:
            pass
        return (
            self._safe_call(root, "winfo_rootx", 0),
            self._safe_call(root, "winfo_rooty", 0),
            max(1, self._safe_call(root, "winfo_width", 1)),
            max(1, self._safe_call(root, "winfo_height", 1)),
        )

    def _root_screen_size(self, root: Any) -> tuple[int, int]:
        return (
            max(1, self._safe_call(root, "winfo_screenwidth", 1)),
            max(1, self._safe_call(root, "winfo_screenheight", 1)),
        )


default_popup_manager = PopupManager()

