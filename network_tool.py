from __future__ import annotations

import re
import subprocess
import threading
import time
import tkinter as tk
import urllib.request

from clicker_tool import ClickerTheme


PING_HOSTS = ("www.baidu.com", "223.5.5.5", "1.1.1.1")
# 测速优先用国内 CDN 大文件（在国内更快更准），再回退到国际源。
SPEED_CANDIDATES = (
    "https://mirrors.tuna.tsinghua.edu.cn/anaconda/archive/Anaconda3-2023.09-0-Windows-x86_64.exe",
    "https://mirrors.aliyun.com/anaconda/archive/Anaconda3-2023.09-0-Windows-x86_64.exe",
    "https://mirror.nju.edu.cn/anaconda/archive/Anaconda3-2023.09-0-Windows-x86_64.exe",
    "https://speed.cloudflare.com/__down?bytes=200000000",
)
SPEED_URLS = (
    "https://speed.cloudflare.com/__down?bytes=25000000",
    "https://cachefly.cachefly.net/10mb.test",
)


class NetworkWindow:
    CHROME_TOP = 46
    CHROME_BOTTOM = 16
    WIDTH = 520
    HEIGHT = 400

    def __init__(self, app, theme: ClickerTheme):
        self.app = app
        self.theme = theme
        self.closed = False
        self.move_start = None
        self.busy = False

        self.download_var = tk.StringVar(value="下载速度：--")
        self.latency_var = tk.StringVar(value="延迟：--")
        self.loss_var = tk.StringVar(value="丢包率：--")

        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=theme.border)
        self.window.resizable(False, False)
        self.window.minsize(self.WIDTH, self.HEIGHT)
        self.window.maxsize(self.WIDTH, self.HEIGHT)

        self.shell = tk.Frame(
            self.window,
            width=self.WIDTH - 2,
            height=self.HEIGHT - 2,
            bg=theme.app_bg,
            highlightthickness=1,
            highlightbackground=theme.border,
        )
        self.shell.pack(fill=tk.BOTH, expand=False, padx=1, pady=1)
        self.shell.pack_propagate(False)

        self._build_chrome()
        self._build_body()
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._place_on_passer()
        self.window.attributes("-topmost", app.topmost_var.get())
        app.apply_window_transparency(self.window)
        self.window.deiconify()
        self.window.focus_force()

    def _font(self, size: int = 9, weight: str = "normal"):
        return self.theme.app_font(size, weight)

    def _place_on_passer(self) -> None:
        try:
            x, y = self.theme.center_over_root(self.app.root, self.WIDTH, self.HEIGHT)
            self.theme.place_toplevel_absolute(self.window, self.WIDTH, self.HEIGHT, x, y)
        except Exception:
            self._force_fixed_geometry()

    def _force_fixed_geometry(self) -> None:
        try:
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except Exception:
            pass

    def _build_chrome(self) -> None:
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

    def _build_body(self) -> None:
        t = self.theme
        body_h = self.HEIGHT - self.CHROME_TOP - self.CHROME_BOTTOM - 2
        body = tk.Frame(self.shell, width=self.WIDTH - 2, height=body_h, bg=t.surface_bg)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=False)
        body.pack_propagate(False)

        result = tk.Frame(body, bg=t.surface_bg, height=210)
        result.pack(fill=tk.X, padx=22, pady=(28, 8))
        result.pack_propagate(False)

        for var in (self.download_var, self.latency_var, self.loss_var):
            label = tk.Label(
                result,
                textvariable=var,
                bg="#f8fafc",
                fg="#111827",
                anchor=tk.W,
                font=self._font(13, "bold"),
                padx=16,
                pady=0,
                height=2,
                highlightthickness=1,
                highlightbackground=t.border,
            )
            label.pack(fill=tk.X, pady=5)

        actions = tk.Frame(body, bg=t.surface_bg, height=52)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(0, 14))
        actions.pack_propagate(False)
        self.start_button = self._button(actions, "开始检测", self.start_detection, primary=True)
        self.start_button.pack(side=tk.RIGHT, pady=7)

        bottom = tk.Frame(self.shell, bg=t.title_bg, height=self.CHROME_BOTTOM)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        bottom.pack_propagate(False)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=12,
            bd=0,
            padx=0,
            pady=8,
            bg=(self.theme.accent if primary else "#eef2f9"),
            fg=("#ffffff" if primary else "#1f2937"),
            disabledforeground=self.theme.accent_soft_hover,
            activebackground=(self.theme.accent_hover if primary else "#e2e8f4"),
            activeforeground=("#ffffff" if primary else "#111827"),
            cursor="hand2",
            font=self._font(10, "bold" if primary else "normal"),
        )

    def _chrome_button(self, parent, text, command, close=False):
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

    def start_detection(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._force_fixed_geometry()
        self.start_button.configure(state=tk.DISABLED)
        self.download_var.set("下载速度：检测中…")
        self.latency_var.set("延迟：检测中…")
        self.loss_var.set("丢包率：检测中…")

        def work():
            # 并行测延迟与测速，缩短总时长。
            box: dict = {}
            ping_thread = threading.Thread(
                target=lambda: box.__setitem__("ping", self.measure_ping()),
                daemon=True, name="Passer-Ping",
            )
            ping_thread.start()
            speed_mbps = self.measure_download_speed()
            ping_thread.join(timeout=15)
            latency_ms, loss_percent = box.get("ping", (None, None))

            def done():
                self.busy = False
                self._force_fixed_geometry()
                self.start_button.configure(state=tk.NORMAL)
                self.latency_var.set(f"延迟：{latency_ms:.0f} ms" if latency_ms is not None else "延迟：检测失败")
                self.loss_var.set(f"丢包率：{loss_percent:.0f}%" if loss_percent is not None else "丢包率：检测失败")
                self.download_var.set(
                    f"下载速度：{speed_mbps:.2f} Mbps" if speed_mbps is not None else "下载速度：检测失败"
                )
                self._force_fixed_geometry()

            try:
                self.window.after(0, done)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name="Passer-NetworkCheck").start()

    @staticmethod
    def measure_ping() -> tuple[float | None, float | None]:
        # 并行 ping 多个目标，取最先返回的有效结果，兼顾速度与稳健。
        results: dict = {}
        done = threading.Event()

        def ping_one(host: str) -> None:
            try:
                result = subprocess.run(
                    ["ping", "-n", "5", host],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = result.stdout or result.stderr or ""
                loss = NetworkWindow._parse_loss(output)
                avg = NetworkWindow._parse_avg_latency(output)
                if avg is not None:
                    results[host] = (avg, loss)
                    done.set()
            except Exception:
                pass

        threads = [threading.Thread(target=ping_one, args=(h,), daemon=True) for h in PING_HOSTS]
        for t in threads:
            t.start()
        done.wait(timeout=9)
        for t in threads:
            t.join(timeout=0.1)
        if results:
            # 选延迟最低（通常最近/最准）的目标。
            avg, loss = min(results.values(), key=lambda v: v[0])
            return avg, loss
        return None, None

    @staticmethod
    def _measure_ping_legacy() -> tuple[float | None, float | None]:
        for host in PING_HOSTS:
            try:
                result = subprocess.run(
                    ["ping", "-n", "4", host],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                output = (result.stdout or result.stderr or "")
                loss = NetworkWindow._parse_loss(output)
                avg = NetworkWindow._parse_avg_latency(output)
                if loss is not None or avg is not None:
                    return avg, loss
            except Exception:
                continue
        return None, None

    @staticmethod
    def _parse_loss(output: str) -> float | None:
        patterns = (
            r"(\d+(?:\.\d+)?)%\s*丢失",
            r"\((\d+(?:\.\d+)?)%\s*loss\)",
            r"Lost\s*=\s*\d+\s*\((\d+(?:\.\d+)?)%",
        )
        for pattern in patterns:
            match = re.search(pattern, output, re.I)
            if match:
                return float(match.group(1))
        return None

    @staticmethod
    def _parse_avg_latency(output: str) -> float | None:
        patterns = (
            r"平均\s*=\s*(\d+(?:\.\d+)?)ms",
            r"Average\s*=\s*(\d+(?:\.\d+)?)ms",
        )
        for pattern in patterns:
            match = re.search(pattern, output, re.I)
            if match:
                return float(match.group(1))
        raw_times = re.findall(r"时间[=<]\s*(\d+(?:\.\d+)?)ms|time[=<]\s*(\d+(?:\.\d+)?)ms", output, re.I)
        times = [float(value) for pair in raw_times for value in pair if value]
        return sum(times) / len(times) if times else None

    @staticmethod
    def _pick_speed_url() -> str | None:
        """探测候选测速源，返回第一个能快速返回数据的 URL。"""
        for url in SPEED_CANDIDATES:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Passer Network Tool"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.read(65536):
                        return url
            except Exception:
                continue
        return None

    @staticmethod
    def measure_download_speed(measure_secs: float = 4.0, warmup_secs: float = 1.0,
                               streams: int = 4) -> float | None:
        """多连接并发下载、剔除 TCP 慢启动预热段后测稳定吞吐，更快也更准。"""
        url = NetworkWindow._pick_speed_url()
        if not url:
            return NetworkWindow._measure_download_legacy()
        stop = threading.Event()
        measuring = threading.Event()
        counters = [0] * streams

        def stream(i: int) -> None:
            while not stop.is_set():  # 数据流耗尽则重连，保证持续占满带宽。
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Passer Network Tool"})
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        while not stop.is_set():
                            chunk = resp.read(1024 * 128)
                            if not chunk:
                                break
                            if measuring.is_set():
                                counters[i] += len(chunk)
                except Exception:
                    if stop.is_set():
                        break
                    time.sleep(0.2)

        threads = [threading.Thread(target=stream, args=(i,), daemon=True) for i in range(streams)]
        for t in threads:
            t.start()
        time.sleep(warmup_secs)          # 预热：跳过慢启动
        measuring.set()
        t0 = time.perf_counter()
        time.sleep(measure_secs)         # 稳定测量窗口
        elapsed = time.perf_counter() - t0
        stop.set()
        total = sum(counters)
        if total > 0 and elapsed > 0:
            return total * 8 / elapsed / 1_000_000
        return NetworkWindow._measure_download_legacy()

    @staticmethod
    def _measure_download_legacy() -> float | None:
        best_mbps: float | None = None
        for url in SPEED_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Passer Network Tool"})
                total = 0
                started = time.perf_counter()
                last_mark = started
                last_total = 0
                with urllib.request.urlopen(req, timeout=15) as resp:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        now = time.perf_counter()
                        elapsed = now - started
                        if now - last_mark >= 0.5:
                            interval_bytes = total - last_total
                            interval = max(0.001, now - last_mark)
                            best_mbps = max(best_mbps or 0.0, interval_bytes * 8 / interval / 1_000_000)
                            last_mark = now
                            last_total = total
                        if elapsed >= 8 or total >= 25_000_000:
                            break
                elapsed = max(0.001, time.perf_counter() - started)
                avg_mbps = total * 8 / elapsed / 1_000_000
                best_mbps = max(best_mbps or 0.0, avg_mbps)
                if best_mbps:
                    break
            except Exception:
                continue
        return best_mbps

    def start_move(self, event):
        self.move_start = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def do_move(self, event):
        if not self.move_start:
            return
        sx, sy, wx, wy = self.move_start
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

    def show(self):
        self.window.deiconify()
        self._place_on_passer()
        self.window.lift()
        self.window.focus_force()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self.app, "network_window", None) is self:
            self.app.network_window = None
        self.window.destroy()
