# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Passer's Windows bundles.

Fast fixed-runtime build:  pyinstaller Passer.spec --noconfirm
Single-file release build: set PASSER_ONEFILE=1, then run the same command.

The default build produces ``dist/Passer/Passer.exe`` plus its adjacent
``PasserRuntime`` folder.  ``PASSER_ONEFILE=1`` instead produces the standalone
``dist/Passer.exe`` used for GitHub releases.  The single file is easier to
distribute, while the fixed-runtime build starts faster and avoids per-launch
``_MEI`` extraction.

All optional features remain bundled:
  * QR generate + recognise (pyzbar + its libzbar/libiconv DLLs)
  * per-program mute        (pycaw + comtypes)
  * scientific calculator   (sympy + mpmath)
  * drag & drop             (tkinterdnd2 + tkdnd binaries)
  * PDF/image previews      (Pillow, pypdfium2)
"""

import re as _re

import PyInstaller as _PyInstaller
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules
import os as _os

_pyinstaller_version = tuple(
    int(part) for part in _re.findall(r"\d+", _PyInstaller.__version__)[:3]
)
if _pyinstaller_version < (6, 21):
    raise SystemExit(
        "Passer's supported Windows bundle requires PyInstaller >= 6.21."
    )

block_cipher = None

# --- optional dependencies: collect their binaries / data / submodules ---------
binaries = []
datas = []
hiddenimports = []


def _without_tests(names):
    """Keep runtime submodules while dropping test/demo helpers from bundled deps."""
    blocked_parts = {".tests", ".test", "._testing"}
    blocked_suffixes = (".demo", ".runtests", ".setup")
    return [
        name for name in names
        if not any(part in name for part in blocked_parts)
        and not name.endswith(blocked_suffixes)
    ]

# pyzbar ships libzbar-64.dll + libiconv.dll that are loaded via ctypes at runtime
binaries += collect_dynamic_libs("pyzbar")
hiddenimports += ["pyzbar", "pyzbar.pyzbar"]

# tkinterdnd2 carries the platform tkdnd shared library as package data
datas += collect_data_files("tkinterdnd2")
hiddenimports += ["tkinterdnd2"]

# pycaw + comtypes define the Core Audio COM interfaces used for muting
hiddenimports += _without_tests(collect_submodules("pycaw"))
hiddenimports += _without_tests(collect_submodules("comtypes"))

# 手机 Aira v2 bridge uses cryptography for ephemeral ECDH and AES-GCM.
hiddenimports += _without_tests(collect_submodules("cryptography"))

# sympy / mpmath power the calculator's symbolic engine (diff / integrate / solve)
hiddenimports += ["sympy", "mpmath"]

# qrcode is pure-python but referenced indirectly
hiddenimports += ["qrcode"]

# cnlunar powers the Plan tool's Chinese lunar calendar (农历/节气/节日); pure-python
# with data submodules, so pull them all in.
hiddenimports += _without_tests(collect_submodules("cnlunar"))
datas += collect_data_files("cnlunar")

# numpy / scipy power the built-in WAV audio editor (denoise via scipy.signal STFT);
# they are imported lazily inside methods, so list them explicitly.
hiddenimports += ["numpy", "scipy", "scipy.signal"]
hiddenimports += _without_tests(collect_submodules("scipy.signal"))

# Passer's own tool modules (imported lazily via importlib.import_module, so
# PyInstaller cannot discover them statically — every one must be listed here).
hiddenimports += [
    "calculator_tool", "qr_tool", "network_tool", "server_tool", "clipboard_tool", "screen_record_tool",
    "device_lock_tool", "device_info_tool", "clicker_tool", "ai_chat",
    "random_tool", "markdown_tool", "file_search_tool",
    "magnet_tool", "map_tool", "plan_tool", "file_share_tool",
    "shutdown_tool", "mail_tool", "ai_cli_bridge", "math_render", "popup_manager",
    "passer_module_api", "automation_tool", "aira_tool", "aira_mobile_bridge", "aira_phone_files", "aira_relay_client", "browser_bridge",
    "image_ocr", "phone_mirror_interaction_tool",
]

# Aira reads the visible Weixin conversation list through UI Automation first,
# then falls back to a non-interactive window capture plus Windows OCR.
hiddenimports += ["psutil", "win32gui", "win32ui", "win32process", "pywinauto"]
hiddenimports += _without_tests(collect_submodules("pywinauto"))

# 图片取字 OCR 后端：winsdk(Windows.Media.Ocr)。winsdk 的 WinRT 命名空间按需
# 惰性加载，collect_submodules 抓不全，故显式列出 image_ocr 实际 import 的命名空间。
hiddenimports += [
    "winsdk",
    "winsdk.windows.media.ocr",
    "winsdk.windows.globalization",
    "winsdk.windows.graphics.imaging",
    "winsdk.windows.storage.streams",
    "winsdk.windows.foundation",
]
try:
    hiddenimports += _without_tests(collect_submodules("winsdk"))
except Exception:
    pass

# Lazily imported third-party backends (importlib.import_module at call time):
#   openpyxl   -> Excel (.xlsx) preview/export
#   pypdfium2  -> PDF preview + Office→图片 渲染 (ships pdfium.dll loaded at runtime)
#   docx       -> python-docx: Word 进阶编辑/新建
#   pptx       -> python-pptx: PowerPoint 进阶编辑/新建
hiddenimports += ["openpyxl", "pypdfium2"]
hiddenimports += _without_tests(collect_submodules("openpyxl"))
hiddenimports += ["docx", "pptx", "lxml", "lxml.etree", "lxml._elementpath"]
hiddenimports += _without_tests(collect_submodules("docx"))
hiddenimports += _without_tests(collect_submodules("pptx"))
binaries += collect_dynamic_libs("pypdfium2")
datas += collect_data_files("pypdfium2")
# python-docx / python-pptx 模板：默认 .docx/.pptx 模板随包数据。
datas += collect_data_files("docx")
datas += collect_data_files("pptx")

# Official aria2 Windows backend used by the built-in magnet downloader.
binaries.append(("tools/aria2/aria2c.exe", "tools/aria2"))
# Ruffle desktop player used by Passer to open local SWF files directly.
if _os.path.exists("tools/ruffle/ruffle.exe"):
    binaries.append(("tools/ruffle/ruffle.exe", "tools/ruffle"))
    for ruffle_file in ("tools/ruffle/LICENSE.md", "tools/ruffle/README.md"):
        if _os.path.exists(ruffle_file):
            datas.append((ruffle_file, "tools/ruffle"))
for license_file in ("tools/aria2/COPYING", "tools/aria2/LICENSE.OpenSSL"):
    datas.append((license_file, "tools/aria2"))

# Official scrcpy Windows bundle used by the built-in Android phone mirror.
if _os.path.exists("tools/scrcpy/scrcpy.exe"):
    for _root, _dirs, _files in _os.walk("tools/scrcpy"):
        for _file in _files:
            _src = _os.path.join(_root, _file)
            _dest = _root
            if _file.lower().endswith((".exe", ".dll")):
                binaries.append((_src, _dest))
            else:
                datas.append((_src, _dest))
# Source-split modules loaded with exec(..., globals()) at runtime.
for source_part in (
    "passer_core.py", "passer_platform.py", "passer_viewers.py",
    "ai_chat_office.py", "ai_chat_web.py", "ai_chat_skills.py",
    "ai_chat_widgets.py", "ai_chat_llm.py",
):
    datas.append((source_part, "."))

# branding / icon assets resolved via SCRIPT_DIR at runtime
for asset in ("passer.ico", "passer.png", "passer_preview.png", "passer_source.png"):
    if _os.path.exists(asset):
        datas.append((asset, "."))

# Bundle default AI skills as read-only seed data; user-created skills remain writable in PasserData.
for _root, _dirs, _files in _os.walk(_os.path.join("PasserData", "AISkills")):
    for _f in _files:
        _src = _os.path.join(_root, _f)
        datas.append((_src, _root))


a = Analysis(
    ["Passer.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch/transformers etc. are never imported by Passer (only string literals
    # like "whisper" appear); they get dragged in transitively and bloat the exe
    # by ~2.8 GB. Exclude the heavy unused ML stack.
    excludes=[
        "torch", "torchvision", "torchaudio",
        "transformers", "sentence_transformers",
        "tensorflow", "jax", "jaxlib",
        "pandas", "matplotlib", "mpl_toolkits",
        "IPython", "jedi", "parso", "pygments",
        "scipy.signal.tests", "scipy.tests", "numpy.tests",
        "scipy._lib.array_api_compat.torch",
        "sympy.plotting", "sympy.plotting.backends.matplotlibbackend",
        "sympy.printing.pytorch", "sympy.printing.tensorflow",
        "comtypes.test", "pycaw.test", "mpmath.tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_onefile = _os.environ.get("PASSER_ONEFILE", "").strip() == "1"

if _onefile:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="Passer",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        console=True,
        hide_console="hide-early",
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="passer.ico",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Passer",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        contents_directory="PasserRuntime",
        # Keep redirected stdio available for `Passer.exe --openclaw-mcp`, while
        # hiding the owned console before Python starts during normal GUI launch.
        console=True,
        hide_console="hide-early",
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon="passer.ico",
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Passer",
    )




