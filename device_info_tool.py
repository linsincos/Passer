from __future__ import annotations

import json
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from clicker_tool import ClickerTheme


# PowerShell 脚本：一次性查询 CPU / 主板 / 内存 / 硬盘 / GPU。
# 返回 JSON，避免多次启动 PowerShell。
_PS_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
function Pick($v) {
  if ($null -eq $v) { return '' }
  return ($v | Out-String).Trim()
}

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed
$board = Get-CimInstance Win32_BaseBoard | Select-Object -First 1 Manufacturer, Product, Version

$memTypes = @{
  20='DDR'; 21='DDR2'; 22='DDR2 FB-DIMM'; 24='DDR3'; 26='DDR4'; 34='DDR5'
}
$memArr = @()
foreach ($m in (Get-CimInstance Win32_PhysicalMemory)) {
  $tp = ''
  if ($m.SMBIOSMemoryType -and $memTypes.ContainsKey([int]$m.SMBIOSMemoryType)) { $tp = $memTypes[[int]$m.SMBIOSMemoryType] }
  elseif ($m.MemoryType -and $memTypes.ContainsKey([int]$m.MemoryType)) { $tp = $memTypes[[int]$m.MemoryType] }
  $memArr += [pscustomobject]@{
    Manufacturer = Pick $m.Manufacturer
    PartNumber   = Pick $m.PartNumber
    Capacity     = [int64]$m.Capacity
    Speed        = [int]$m.Speed
    Type         = $tp
    Slot         = Pick $m.DeviceLocator
  }
}

$memArrayInfo = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1
$memSlotsTotal = if ($memArrayInfo) { [int]$memArrayInfo.MemoryDevices } else { 0 }

$diskArr = @()
foreach ($d in (Get-CimInstance Win32_DiskDrive | Sort-Object Index)) {
  $diskArr += [pscustomobject]@{
    Model     = Pick $d.Model
    Size      = [int64]$d.Size
    Interface = Pick $d.InterfaceType
    Media     = Pick $d.MediaType
  }
}

# 系统插槽：用来检测空的 M.2/NVMe 槽位（设计上很多 OEM 不上报，能拿到就拿）。
$slotArr = @()
foreach ($s in (Get-CimInstance Win32_SystemSlot)) {
  $slotArr += [pscustomobject]@{
    Designation = Pick $s.SlotDesignation
    Usage       = [int]$s.CurrentUsage
    Width       = [int]$s.MaxDataWidth
  }
}

# GPU：Win32_VideoController.AdapterRAM 受 4GB UINT32 限制不准；
# 再从注册表读取 HardwareInformation.qwMemorySize（DWORD 或 QWORD）作为大显存的真实值。
$regBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
$regGpus = @()
if (Test-Path $regBase) {
  foreach ($k in (Get-ChildItem $regBase -ErrorAction SilentlyContinue)) {
    $p = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
    if ($p -and $p.DriverDesc) {
      $vram = 0
      if ($p.'HardwareInformation.qwMemorySize') {
        try { $vram = [int64]$p.'HardwareInformation.qwMemorySize' } catch { $vram = 0 }
      }
      if ($vram -le 0 -and $p.'HardwareInformation.MemorySize') {
        try {
          $raw = $p.'HardwareInformation.MemorySize'
          if ($raw -is [byte[]]) {
            $vram = [BitConverter]::ToUInt32($raw, 0)
          } else {
            $vram = [int64]$raw
          }
        } catch { $vram = 0 }
      }
      $regGpus += [pscustomobject]@{ Name = Pick $p.DriverDesc; Vram = [int64]$vram }
    }
  }
}

$gpuArr = @()
foreach ($g in (Get-CimInstance Win32_VideoController)) {
  $name = Pick $g.Name
  $vram = 0
  try { $vram = [int64]$g.AdapterRAM } catch { $vram = 0 }
  foreach ($r in $regGpus) {
    if ($r.Name -and ($r.Name -eq $name -or $name.StartsWith($r.Name) -or $r.Name.StartsWith($name))) {
      if ($r.Vram -gt $vram) { $vram = $r.Vram }
    }
  }
  $gpuArr += [pscustomobject]@{
    Name   = $name
    Vram   = $vram
    Driver = Pick $g.DriverVersion
  }
}

$payload = [pscustomobject]@{
  Cpu            = $cpu
  Board          = $board
  Memory         = $memArr
  MemorySlots    = $memSlotsTotal
  Disks          = $diskArr
  Slots          = $slotArr
  Gpus           = $gpuArr
}
$payload | ConvertTo-Json -Depth 5 -Compress
"""


def _format_bytes(num: int) -> str:
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "未知"
    if value <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}" if unit not in ("B", "KB") else f"{value:.0f} {unit}"
        value /= 1024.0
    return f"{value:.2f} EB"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def collect_device_info(timeout: float = 25.0) -> dict:
    """同步执行 PowerShell 查询并返回解析后的 dict。耗时操作，需在后台线程调用。"""
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _PS_QUERY,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"error": "查询超时，请稍后重试。"}
    except FileNotFoundError:
        return {"error": "未找到 PowerShell，无法获取设备信息。"}
    except Exception as exc:
        return {"error": f"查询失败：{exc}"}

    raw = (result.stdout or "").strip()
    if not raw:
        err = (result.stderr or "").strip() or "PowerShell 未返回数据。"
        return {"error": err[:400]}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"error": f"解析失败：{exc}\n\n{raw[:400]}"}


class DeviceInfoWindow:
    """内置设备检测：显示 CPU / 主板 / 内存 / 硬盘 / GPU 信息。"""

    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    MIN_W = 900
    MIN_H = 900
    CARD_LABEL_WIDTH = 90

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.busy = False
        self._info: dict | None = None
        self._copy_lines: list[str] = []

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.minsize(self.MIN_W, self.MIN_H)

        self.shell = tk.Frame(
            self.window,
            bg=theme.app_bg,
            highlightthickness=1,
            highlightbackground=theme.border,
        )
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._build_chrome()
        self._build_body()

        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        x, y = theme.center_over_root(app.root, self.MIN_W, self.MIN_H)
        theme.place_toplevel_absolute(self.window, self.MIN_W, self.MIN_H, x, y)
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()

        # 打开即自动检测一次，避免用户多点一次。
        self.window.after(120, self.refresh)

    # -- chrome --------------------------------------------------------
    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _build_chrome(self) -> None:
        t = self.theme
        bar = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_TOP)
        bar.pack(side=tk.TOP, fill=tk.X)
        bar.pack_propagate(False)

        title = tk.Label(
            bar, text=t.title, bg=t.title_bg, fg="#dbe7ff",
            anchor=tk.W, font=self._font(10, "bold"),
        )
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(14, 8))
        self._chrome_button(bar, "×", self.close, close=True).pack(side=tk.RIGHT, padx=(0, 8), pady=8)
        for widget in (bar, title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.shell, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        actions = tk.Frame(body, bg=t.surface_bg)
        actions.pack(side=tk.TOP, fill=tk.X, padx=24, pady=(18, 8))

        self.status_var = tk.StringVar(value="点击 `刷新` 重新检测设备。")
        status = tk.Label(
            actions, textvariable=self.status_var,
            bg=t.surface_bg, fg="#475569", anchor=tk.W, font=self._font(10),
        )
        status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.copy_button = tk.Button(
            actions, text="复制", command=self.copy_all, bd=0, padx=20, pady=7,
            bg="#eef2f9", fg="#1f2937", activebackground="#e2e8f4",
            activeforeground="#111827", cursor="hand2", font=self._font(10, "bold"),
        )
        self.copy_button.bind("<Enter>", lambda _e: self.copy_button.configure(bg="#e2e8f4"))
        self.copy_button.bind("<Leave>", lambda _e: self.copy_button.configure(bg="#eef2f9"))
        self.copy_button.pack(side=tk.RIGHT, padx=(10, 0))

        self.refresh_button = tk.Button(
            actions, text="刷新", command=self.refresh, bd=0, padx=20, pady=7,
            bg=t.accent, fg="#ffffff", activebackground=t.accent_hover,
            activeforeground="#ffffff", cursor="hand2", font=self._font(10, "bold"),
        )
        self.refresh_button.pack(side=tk.RIGHT)

        list_wrap = tk.Frame(body, bg=t.surface_bg)
        list_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=20, pady=(6, 14))

        self.canvas = tk.Canvas(
            list_wrap, bg=t.surface_bg, bd=0, highlightthickness=0,
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vscroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        self.cards_frame = tk.Frame(self.canvas, bg=t.surface_bg)
        self._cards_window = self.canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._cards_window, width=e.width),
        )
        self._bind_mousewheel(self.canvas)
        self._bind_mousewheel(self.cards_frame)

        self._render_placeholder("正在读取设备信息…")

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _bind_mousewheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel)
        widget.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        widget.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def _on_mousewheel(self, event) -> None:
        try:
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta * max(1, abs(int(event.delta / 120))), "units")
        except Exception:
            pass

    def _clear_cards(self) -> None:
        for child in self.cards_frame.winfo_children():
            child.destroy()
        self._copy_lines: list[str] = []

    def _render_placeholder(self, message: str) -> None:
        self._clear_cards()
        tk.Label(
            self.cards_frame, text=message, bg=self.theme.surface_bg,
            fg="#64748b", anchor=tk.W, font=self._font(11),
        ).pack(fill=tk.X, padx=4, pady=14)

    def _make_card(self, label: str, value_lines: list[str]) -> None:
        """绘制一行卡片：左边类目，右边详情；样式仿截图中的圆角白底带描边卡片。"""
        t = self.theme
        outer = tk.Frame(
            self.cards_frame, bg="#e2e8f4",
            highlightthickness=0, bd=0,
        )
        outer.pack(fill=tk.X, padx=2, pady=6)

        inner = tk.Frame(outer, bg="#ffffff")
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        row = tk.Frame(inner, bg="#ffffff")
        row.pack(fill=tk.X, padx=22, pady=18)

        tk.Label(
            row, text=label, bg="#ffffff", fg="#0f172a",
            anchor=tk.W, font=self._font(12, "bold"),
            width=self.CARD_LABEL_WIDTH // 12,
        ).pack(side=tk.LEFT, padx=(0, 18))

        values = tk.Frame(row, bg="#ffffff")
        values.pack(side=tk.LEFT, fill=tk.X, expand=True)

        if not value_lines:
            value_lines = ["未检测到"]
        for index, line in enumerate(value_lines):
            lbl = tk.Label(
                values, text=line, bg="#ffffff", fg="#1f2937",
                anchor=tk.W, justify=tk.LEFT, font=self._font(11),
                wraplength=max(200, self.MIN_W - 260),
            )
            lbl.pack(fill=tk.X, pady=(0 if index == 0 else 4, 0))
            self._bind_mousewheel(lbl)

        for widget in (outer, inner, row, values):
            self._bind_mousewheel(widget)

        self._copy_lines.append(f"{label}：")
        for line in value_lines:
            self._copy_lines.append(f"  {line}")

    # -- chrome widgets ------------------------------------------------
    def _chrome_button(self, parent, text: str, command, close: bool = False) -> tk.Button:
        hover = "#ef4444" if close else self.theme.title_button_hover
        button = tk.Button(
            parent, text=text, command=command, bd=0, padx=11, pady=5,
            bg=self.theme.title_button_bg, fg="#e7eefc", activebackground=hover, activeforeground="#ffffff",
            font=self._font(10 if close else 9), cursor="hand2",
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=self.theme.title_button_bg))
        return button

    # -- window dragging ----------------------------------------------
    def start_move(self, event) -> None:
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event) -> None:
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.theme.place_toplevel_absolute(
            self.window,
            max(self.window.winfo_width(), self.MIN_W),
            max(self.window.winfo_height(), self.MIN_H),
            wx + event.x_root - sx,
            wy + event.y_root - sy,
        )

    # -- actions -------------------------------------------------------
    def refresh(self) -> None:
        if self.closed or self.busy:
            return
        self.busy = True
        self.refresh_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self.status_var.set("正在读取设备信息…")
        self._render_placeholder("正在读取设备信息…")

        def work() -> None:
            info = collect_device_info()
            try:
                self.window.after(0, lambda: self._on_done(info))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name="Passer-DeviceInfo").start()

    def _on_done(self, info: dict) -> None:
        if self.closed:
            return
        self.busy = False
        self.refresh_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.NORMAL)
        self._info = info
        if "error" in info:
            self.status_var.set("读取失败")
            self._render_placeholder(info["error"])
            return
        self.status_var.set("读取完成。")
        self._render(info)

    @staticmethod
    def _empty_storage_slots(slots: list[dict]) -> list[str]:
        """从 Win32_SystemSlot 里挑出明确标注为 M.2/NVMe 且未占用的槽位。
        CurrentUsage=3 表示 Available；OEM 通常只在 SlotDesignation 写明 M.2_x 时才可识别。
        识别不到就返回空列表，不强行编造空槽。"""
        result: list[str] = []
        pattern = re.compile(r"m[\W_]*2|nvme|ngff", re.IGNORECASE)
        for slot in slots:
            designation = _clean(slot.get("Designation"))
            usage = int(slot.get("Usage") or 0)
            if usage != 3 or not designation:
                continue
            if pattern.search(designation):
                result.append(designation)
        return result

    def _render(self, info: dict) -> None:
        self._clear_cards()

        cpu = info.get("Cpu") or {}
        cpu_lines: list[str] = [_clean(cpu.get("Name")) or "未知型号"]
        cores = cpu.get("NumberOfCores")
        logical = cpu.get("NumberOfLogicalProcessors")
        clock = cpu.get("MaxClockSpeed")
        bits: list[str] = []
        if cores:
            bits.append(f"{cores} 核")
        if logical:
            bits.append(f"{logical} 线程")
        if clock:
            try:
                bits.append(f"{float(clock) / 1000:.2f} GHz")
            except (TypeError, ValueError):
                pass
        if bits:
            cpu_lines.append(" · ".join(bits))
        self._make_card("CPU", cpu_lines)

        board = info.get("Board") or {}
        product = _clean(board.get("Product"))
        manufacturer = _clean(board.get("Manufacturer"))
        version = _clean(board.get("Version"))
        board_lines: list[str] = [" ".join(s for s in (manufacturer, product) if s) or "未知型号"]
        if version:
            board_lines.append(f"版本：{version}")
        self._make_card("主板", board_lines)

        mem_list = info.get("Memory") or []
        total = sum(int(m.get("Capacity") or 0) for m in mem_list)
        slots_total = int(info.get("MemorySlots") or 0)
        installed = len(mem_list)
        empty_slots = max(0, slots_total - installed)
        header_slot_text = (
            f"，共 {slots_total} 槽 / 已用 {installed} / 空 {empty_slots}"
            if slots_total
            else f"，共 {installed} 条"
        )
        mem_lines: list[str] = [f"总容量：{_format_bytes(total)}{header_slot_text}"]
        for index, mod in enumerate(mem_list, 1):
            part = _clean(mod.get("PartNumber")) or "未知型号"
            maker = _clean(mod.get("Manufacturer"))
            cap = _format_bytes(int(mod.get("Capacity") or 0))
            tp = _clean(mod.get("Type"))
            speed = mod.get("Speed")
            slot = _clean(mod.get("Slot"))
            head = f"#{index}"
            if slot:
                head += f" [{slot}]"
            details = [part]
            if maker:
                details.append(maker)
            details.append(cap)
            if tp:
                details.append(tp)
            if speed:
                details.append(f"{speed} MHz")
            mem_lines.append(f"{head}  " + " · ".join(details))
        # 空槽：仅展示「插槽 · 未插入」，不编造具体名字。
        for offset in range(empty_slots):
            mem_lines.append(f"#{installed + offset + 1}  插槽 · 未插入")
        self._make_card("内存", mem_lines)

        disks = info.get("Disks") or []
        empty_disk_slots = self._empty_storage_slots(info.get("Slots") or [])
        disk_lines: list[str] = []
        for index, disk in enumerate(disks, 1):
            model = _clean(disk.get("Model")) or "未知型号"
            size = _format_bytes(int(disk.get("Size") or 0))
            iface = _clean(disk.get("Interface"))
            media = _clean(disk.get("Media"))
            tail_bits = [size]
            if iface:
                tail_bits.append(iface)
            if media and media.lower() != "fixed hard disk media":
                tail_bits.append(media)
            disk_lines.append(f"#{index}  {model} · " + " · ".join(tail_bits))
        for offset, designation in enumerate(empty_disk_slots):
            tag = designation or "M.2 / NVMe"
            disk_lines.append(f"#{len(disks) + offset + 1}  {tag} · 未插入")
        self._make_card("硬盘", disk_lines)

        gpus = info.get("Gpus") or []
        gpu_lines: list[str] = []
        for index, gpu in enumerate(gpus, 1):
            name = _clean(gpu.get("Name")) or "未知型号"
            vram = int(gpu.get("Vram") or 0)
            driver = _clean(gpu.get("Driver"))
            tail = [name]
            if vram > 0:
                tail.append(f"显存 {_format_bytes(vram)}")
            if driver:
                tail.append(f"驱动 {driver}")
            gpu_lines.append(f"#{index}  " + " · ".join(tail))
        self._make_card("GPU", gpu_lines)

        self.canvas.yview_moveto(0)

    def copy_all(self) -> None:
        if not self._info or "error" in self._info:
            return
        text = "\n".join(getattr(self, "_copy_lines", []))
        if not text:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.status_var.set("已复制到剪贴板。")
        except Exception:
            pass

    def show(self) -> None:
        if self.closed:
            return
        try:
            self.window.deiconify()
            self.window.lift(self.app.root)
            self.window.focus_force()
        except Exception:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "device_info_window", None) is self:
            self.app.device_info_window = None
        try:
            self.window.destroy()
        except Exception:
            pass
