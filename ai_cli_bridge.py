from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


OUTPUT_LIMIT = 12000
WPSCLI_SUBCOMMANDS = frozenset({
    "version", "pdf2word", "pdf2excel", "pdf2ppt", "pdf2md", "pdf2txt",
    "pdf2imgpdf", "pdf2cad", "pdf2photo", "word2pdf", "excel2pdf",
    "ppt2pdf", "txt2pdf", "photo2pdf", "cad2pdf", "caj2pdf", "pdfsplit",
    "pdfmerge", "pdfcompress", "pdfinfo", "pdfwatermark",
    "pdfremovewatermark", "pdfencrypt",
})


def _decode_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for encoding in ("utf-8-sig", "gb18030", "mbcs"):
        try:
            return bytes(value).decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(value).decode("utf-8", errors="replace")


def _clip_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= OUTPUT_LIMIT:
        return cleaned
    return cleaned[:OUTPUT_LIMIT] + "\n...（输出已截断）"


def _timeout(value, default: int, maximum: int = 900) -> int:
    try:
        return max(5, min(maximum, int(value or default)))
    except (TypeError, ValueError):
        return default


def _run_cli(command: list[str], timeout: int = 120) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = "\n".join(filter(None, (_decode_output(exc.stdout), _decode_output(exc.stderr))))
        message = f"命令运行超过 {timeout} 秒，已停止等待。"
        if partial.strip():
            message += "\n" + partial.strip()
        return 124, _clip_output(message)
    except OSError as exc:
        return 127, f"无法启动命令：{exc}"
    output = "\n".join(filter(None, (
        _decode_output(completed.stdout).strip(),
        _decode_output(completed.stderr).strip(),
    )))
    return completed.returncode, _clip_output(output)


def _drive_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        try:
            mask = int(ctypes.windll.kernel32.GetLogicalDrives())
            for index in range(26):
                if mask & (1 << index):
                    roots.append(Path(f"{chr(65 + index)}:/"))
        except Exception:
            pass
    if not roots:
        roots.append(Path(Path.cwd().anchor or "/"))
    return roots


def _first_existing(candidates) -> Path | None:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
            key = str(path).casefold()
        except (OSError, ValueError, TypeError):
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def _version_sort_key(path: Path) -> tuple[int, ...]:
    versions = re.findall(r"\d+(?:\.\d+){2,}", str(path.parent))
    if not versions:
        return ()
    return tuple(int(value) for value in versions[-1].split("."))


def find_wps_cli() -> Path | None:
    direct = [os.environ.get("WPSCLI_PATH")]
    for name in ("wpscli.exe", "wpscli"):
        direct.append(shutil.which(name))
    found = _first_existing(direct)
    if found is not None:
        return found

    bases: list[Path] = []
    for value in (
        os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA"),
    ):
        if value:
            root = Path(value)
            bases.extend((root / "Kingsoft" / "WPS Office", root / "WPS Office"))
    for drive in _drive_roots():
        bases.extend((
            drive / "Application" / "WPS Office",
            drive / "WPS",
            drive / "Kingsoft" / "WPS Office",
            drive / "Program Files" / "WPS Office",
            drive / "Program Files (x86)" / "Kingsoft" / "WPS Office",
        ))

    matches: list[Path] = []
    for base in bases:
        try:
            matches.extend(base.glob("*/clitool/wpscli.exe"))
            matches.extend(base.glob("*/office6/wpscli.exe"))
            matches.extend(base.glob("clitool/wpscli.exe"))
            matches.extend(base.glob("office6/wpscli.exe"))
        except OSError:
            continue
    matches = [path for path in matches if path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda path: (_version_sort_key(path), "clitool" in str(path).lower()), reverse=True)
    return matches[0].resolve()


def _find_node_command(name: str) -> Path | None:
    candidates = [shutil.which(f"{name}.cmd"), shutil.which(f"{name}.exe"), shutil.which(name)]
    node = shutil.which("node.exe") or shutil.which("node")
    if node:
        parent = Path(node).parent
        candidates.extend((parent / f"{name}.cmd", parent / f"{name}.exe"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / f"{name}.cmd")
    return _first_existing(candidates)


def find_openclaw_cli() -> Path | None:
    direct = _first_existing((
        os.environ.get("OPENCLAW_CLI_PATH"),
        shutil.which("openclaw.cmd"), shutil.which("openclaw.exe"), shutil.which("openclaw"),
    ))
    if direct is not None:
        return direct
    npm = _find_node_command("npm")
    if npm is not None:
        code, output = _run_cli([str(npm), "prefix", "-g"], timeout=15)
        if code == 0 and output:
            prefix = Path(output.splitlines()[-1].strip())
            found = _first_existing((prefix / "openclaw.cmd", prefix / "openclaw.exe"))
            if found is not None:
                return found
    return None


def configure_openclaw_passer_mcp(*, enabled: bool, command: str,
                                  args: list[str], cwd: str) -> tuple[bool, str]:
    """Add/update Passer's narrow MCP bridge in the local OpenClaw registry."""
    openclaw = find_openclaw_cli()
    if openclaw is None:
        return False, (
            "未找到 OpenClaw CLI。本地 Passer 桥已按设置切换，但还不能连接微信；"
            "请先安装 OpenClaw，再重新切换一次“启用 OpenClaw”。"
        )
    definition = {
        "command": str(command),
        "args": [str(value) for value in args],
        "cwd": str(cwd),
        "enabled": bool(enabled),
        "timeout": 45,
        "connectTimeout": 5,
        "supportsParallelToolCalls": False,
        "toolFilter": {"include": ["passer_control"]},
    }
    code, output = _run_cli(
        [str(openclaw), "mcp", "set", "passer", json.dumps(definition, ensure_ascii=False)],
        timeout=60,
    )
    if code != 0:
        return False, f"OpenClaw MCP 配置同步失败（退出码 {code}）：\n{output or '（无输出）'}"

    state = "启用" if enabled else "停用"
    lines = [f"已在 OpenClaw 中{state} Passer 受控桥。"]
    # Drop only OpenClaw's cached MCP runtimes.  This avoids interrupting active
    # channel work while ensuring the next agent runtime sees the new bridge.
    reload_code, reload_output = _run_cli(
        [str(openclaw), "mcp", "reload"], timeout=30,
    )
    if reload_code == 0:
        lines.append("OpenClaw MCP 缓存已刷新，新配置会在下一次运行时载入。")
    elif enabled:
        lines.append(
            "MCP 配置已保存，但缓存未能自动刷新；可手动运行 openclaw mcp reload。"
        )
        if reload_output:
            lines.append(_clip_output(reload_output))
    return True, "\n".join(lines)


def find_wechat_desktop() -> Path | None:
    direct = [
        os.environ.get("WECHAT_PATH"), shutil.which("Weixin.exe"), shutil.which("WeChat.exe"),
    ]
    if os.name == "nt":
        try:
            import winreg

            uninstall_roots = (
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            )
            for hive, root_name in uninstall_roots:
                try:
                    with winreg.OpenKey(hive, root_name) as root:
                        for index in range(winreg.QueryInfoKey(root)[0]):
                            try:
                                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                                    display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                                    if not re.search(r"微信|WeChat|Weixin", display, re.I):
                                        continue
                                    try:
                                        icon = str(winreg.QueryValueEx(entry, "DisplayIcon")[0]).strip('"')
                                        direct.append(icon.split(",", 1)[0])
                                    except OSError:
                                        pass
                                    try:
                                        location = str(winreg.QueryValueEx(entry, "InstallLocation")[0]).strip('"')
                                        direct.extend((Path(location) / "Weixin.exe", Path(location) / "WeChat.exe"))
                                    except OSError:
                                        pass
                            except OSError:
                                continue
                except OSError:
                    continue
        except (ImportError, OSError):
            pass
    for drive in _drive_roots():
        direct.extend((
            drive / "Application" / "Weixin" / "Weixin.exe",
            drive / "Tencent" / "WeChat" / "WeChat.exe",
            drive / "Program Files" / "Tencent" / "WeChat" / "WeChat.exe",
            drive / "Program Files (x86)" / "Tencent" / "WeChat" / "WeChat.exe",
        ))
    return _first_existing(direct)


def _argument_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("args 必须是 JSON 字符串数组，不能传入整段 Shell 命令。")
    if len(value) > 64:
        raise ValueError("args 最多允许 64 项。")
    result: list[str] = []
    for item in value:
        text = str(item)
        if "\x00" in text or len(text) > 4096:
            raise ValueError("args 含有无效或过长参数。")
        result.append(text)
    return result


def run_wps_action(spec: dict, action: str) -> str:
    executable = find_wps_cli()
    if executable is None:
        return "WPS CLI 未找到。请安装或更新 WPS Office，或设置 WPSCLI_PATH 指向 wpscli.exe。"
    if action in {"wps_cli_status", "wpscli_status"}:
        return f"WPS CLI 已接入：{executable}\n首次执行时 WPS 可能自动下载官方 kpdfcli 组件。"

    subcommand = str(
        spec.get("subcommand") or spec.get("command") or spec.get("operation") or ""
    ).strip().lower()
    if action in {"wps_cli_help", "wpscli_help"}:
        command = [str(executable)]
        if subcommand:
            if subcommand not in WPSCLI_SUBCOMMANDS:
                return f"不支持的 WPS CLI 子命令：{subcommand}"
            command.extend((subcommand, "--help"))
        else:
            command.append("--help")
        code, output = _run_cli(command, timeout=_timeout(spec.get("timeout"), 300))
        return f"WPS CLI 帮助（退出码 {code}）：\n{output or '（无输出）'}"

    if subcommand not in WPSCLI_SUBCOMMANDS:
        supported = "、".join(sorted(WPSCLI_SUBCOMMANDS))
        return f"不支持的 WPS CLI 子命令：{subcommand or '（空）'}。允许：{supported}"
    try:
        arguments = _argument_list(spec.get("args"))
    except ValueError as exc:
        return f"WPS CLI 参数无效：{exc}"
    command = [str(executable), subcommand, *arguments]
    if bool(spec.get("json", True)) and "--json" not in arguments:
        command.append("--json")
    code, output = _run_cli(command, timeout=_timeout(spec.get("timeout"), 300))
    state = "完成" if code == 0 else "失败"
    return f"WPS CLI {subcommand} {state}（退出码 {code}）：\n{output or '（无输出）'}"


def _wechat_status() -> str:
    desktop = find_wechat_desktop()
    openclaw = find_openclaw_cli()
    npm = _find_node_command("npm")
    lines = [
        f"微信桌面端：{desktop or '未找到'}",
        f"微信 CLI 命令依赖：{openclaw or '未安装'}（仅按需运行，不注册后台自启动）",
        f"Node/npm：{npm or '未找到'}",
    ]
    if openclaw is None:
        lines.append("腾讯微信 CLI 通道尚未安装；可在无瑕授权下执行 wechat_cli_install。")
        return "\n".join(lines)
    code, output = _run_cli(
        [str(openclaw), "channels", "status", "--channel", "openclaw-weixin", "--json"],
        timeout=45,
    )
    lines.append(f"微信通道状态（退出码 {code}）：{output or '（无输出）'}")
    return "\n".join(lines)


def _install_wechat_cli(spec: dict) -> str:
    npm = _find_node_command("npm")
    if npm is None:
        return "未找到 npm。请先安装 Node.js 22.19+、23.11+ 或 24+。"
    outputs: list[str] = []
    openclaw = find_openclaw_cli()
    timeout = _timeout(spec.get("timeout"), 900)
    if openclaw is None:
        code, output = _run_cli([str(npm), "install", "-g", "openclaw@latest"], timeout=timeout)
        outputs.append(f"安装 OpenClaw（退出码 {code}）：\n{output or '（无输出）'}")
        if code != 0:
            return "\n".join(outputs)
        openclaw = find_openclaw_cli()
        if openclaw is None:
            outputs.append("OpenClaw 安装命令已结束，但仍未找到 openclaw.cmd；请检查 npm 全局目录。")
            return "\n".join(outputs)
    code, output = _run_cli(
        [str(openclaw), "plugins", "install", "@tencent-weixin/openclaw-weixin@latest"],
        timeout=timeout,
    )
    outputs.append(f"安装腾讯微信通道（退出码 {code}）：\n{output or '（无输出）'}")
    if code != 0 and not re.search(r"already|已安装|exists", output, re.I):
        return "\n".join(outputs)
    enable_code, enable_output = _run_cli(
        [str(openclaw), "config", "set", "plugins.entries.openclaw-weixin.enabled", "true"],
        timeout=60,
    )
    outputs.append(f"启用腾讯微信通道（退出码 {enable_code}）：\n{enable_output or '（无输出）'}")
    if enable_code == 0:
        outputs.append(
            "微信 CLI 通道已安装并启用。下一步执行 wechat_cli_login 打开一次性扫码窗口；"
            "不会安装或启动常驻后台网关。"
        )
    return "\n".join(outputs)


def _spawn_wechat_login(openclaw: Path) -> str:
    command = [str(openclaw), "channels", "login", "--channel", "openclaw-weixin"]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        if os.name == "nt" and openclaw.suffix.lower() in {".cmd", ".bat"}:
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            command_line = subprocess.list2cmdline(command)
            subprocess.Popen([comspec, "/d", "/s", "/c", command_line], creationflags=flags)
        else:
            subprocess.Popen(command, creationflags=flags)
    except OSError as exc:
        return f"无法打开微信 CLI 扫码登录窗口：{exc}"
    return "已打开微信 CLI 扫码登录窗口；请使用手机微信扫码并确认授权。"


def run_wechat_action(spec: dict, action: str) -> str:
    if action in {"wechat_cli_status", "weixin_cli_status"}:
        return _wechat_status()
    if action in {"wechat_cli_install", "weixin_cli_install"}:
        return _install_wechat_cli(spec)

    openclaw = find_openclaw_cli()
    if openclaw is None:
        return "OpenClaw CLI 未安装。请先在无瑕授权下执行 wechat_cli_install。"
    if action in {"wechat_cli_login", "weixin_cli_login"}:
        return _spawn_wechat_login(openclaw)
    if action in {"wechat_cli_logs", "weixin_cli_logs"}:
        try:
            lines = max(10, min(500, int(spec.get("lines") or 100)))
        except (TypeError, ValueError):
            lines = 100
        code, output = _run_cli(
            [str(openclaw), "channels", "logs", "--channel", "openclaw-weixin", "--lines", str(lines), "--json"],
            timeout=60,
        )
        return f"微信 CLI 日志（退出码 {code}）：\n{output or '（无输出）'}"
    if action in {"wechat_cli_contacts", "weixin_cli_contacts"}:
        code, output = _run_cli(
            [str(openclaw), "directory", "peers", "list", "--channel", "openclaw-weixin", "--json"],
            timeout=60,
        )
        return f"微信 CLI 联系人查询（退出码 {code}）：\n{output or '该通道可能不支持联系人目录查询。'}"
    if action in {"wechat_cli_send", "weixin_cli_send"}:
        target = str(spec.get("target") or spec.get("to") or "").strip()
        message = str(spec.get("message") or spec.get("text") or "").strip()
        if not target or not message:
            return "wechat_cli_send 必须同时提供明确的 target 和 message。"
        if len(message) > 8000:
            return "wechat_cli_send 的 message 不能超过 8000 个字符。"
        command = [
            str(openclaw), "message", "send", "--channel", "openclaw-weixin",
            "--target", target, "--message", message,
        ]
        media = str(spec.get("media") or spec.get("file") or "").strip()
        if media:
            media_path = Path(media).expanduser()
            if not media_path.is_file():
                return f"微信待发送附件不存在：{media}"
            command.extend(("--media", str(media_path.resolve())))
        command.append("--json")
        code, output = _run_cli(command, timeout=_timeout(spec.get("timeout"), 120))
        return f"微信 CLI 发送{'完成' if code == 0 else '失败'}（退出码 {code}）：\n{output or '（无输出）'}"
    return f"不支持的微信 CLI 动作：{action}"
