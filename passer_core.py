from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

def _load_symbol(module_name: str, symbol_name: str):
    return getattr(importlib.import_module(module_name), symbol_name)


def make_path_writable(path: str | os.PathLike) -> Path:
    """Clear a copied file's read-only bit without changing its source file."""
    target = Path(path)
    try:
        mode = target.stat().st_mode
    except FileNotFoundError:
        return target
    target.chmod(mode | stat.S_IWRITE)
    return target


def protect_password(value: str) -> str:
    return _load_symbol("device_lock_tool", "protect_password")(value)


def unprotect_password(value: str) -> str:
    return _load_symbol("device_lock_tool", "unprotect_password")(value)


def _ensure_ai_module() -> bool:
    global AIChatBar, AI_PROVIDERS, AI_PROVIDER_ORDER, AI_NAME_TO_KEY
    global AI_PERSONAS, AI_PERSONA_ORDER, AI_PERMISSIONS, AI_PERMISSION_ORDER
    global office_edit_copy, office_attachment_preview, web_search_preview
    global scholar_search_preview, fetch_url_text
    global office_convert, office_create
    global ai_write_skill, ai_delete_skill, ai_list_skill_names
    global ai_find_skills, ai_read_skill
    global ai_write_plugin, ai_delete_plugin, ai_list_plugins, ai_run_plugin
    global ai_usage_summary
    if AIChatBar is not None:
        return True
    try:
        module = importlib.import_module("ai_chat")
        AIChatBar = module.AIChatBar
        AI_PROVIDERS = module.PROVIDERS
        AI_PROVIDER_ORDER = module.PROVIDER_ORDER
        AI_NAME_TO_KEY = module.NAME_TO_KEY
        AI_PERSONAS = module.AI_PERSONAS
        AI_PERSONA_ORDER = module.PERSONA_ORDER
        AI_PERMISSIONS = module.AI_PERMISSIONS
        AI_PERMISSION_ORDER = module.AI_PERMISSION_ORDER
        office_edit_copy = module.office_edit_copy
        office_attachment_preview = module.office_attachment_preview
        office_convert = module.office_convert
        office_create = module.office_create
        web_search_preview = module.web_search_preview
        scholar_search_preview = module.scholar_search_preview
        fetch_url_text = module.fetch_url_text
        ai_write_skill = module.write_skill
        ai_delete_skill = module.delete_skill
        ai_list_skill_names = module.list_skill_names
        ai_find_skills = module.find_skills
        ai_read_skill = module.read_skill
        ai_write_plugin = module.write_plugin
        ai_delete_plugin = module.delete_plugin
        ai_list_plugins = module.list_plugins
        ai_run_plugin = module.run_plugin
        ai_usage_summary = module.load_usage_summary
        return True
    except Exception as _ai_err:
        import traceback
        print(f"[Passer] Aira 模块加载失败：{_ai_err}\n{traceback.format_exc()}")
        return False


def _ensure_pycaw() -> bool:
    global AudioUtilities, PYCAW_AVAILABLE
    if PYCAW_AVAILABLE is not None:
        return bool(PYCAW_AVAILABLE)
    try:
        AudioUtilities = _load_symbol("pycaw.pycaw", "AudioUtilities")
        PYCAW_AVAILABLE = True
    except Exception:
        AudioUtilities = None
        PYCAW_AVAILABLE = False
    return bool(PYCAW_AVAILABLE)


def _ensure_pdfium() -> bool:
    global pdfium, PDFIUM_AVAILABLE, PDFIUM_IMPORT_ERROR
    if PDFIUM_AVAILABLE is not None:
        return bool(PDFIUM_AVAILABLE)
    try:
        pdfium = importlib.import_module("pypdfium2")
        PDFIUM_AVAILABLE = True
    except Exception as exc:
        pdfium = None
        PDFIUM_AVAILABLE = False
        PDFIUM_IMPORT_ERROR = exc
    return bool(PDFIUM_AVAILABLE)


def _ensure_openpyxl() -> bool:
    global openpyxl, get_column_letter, OPENPYXL_AVAILABLE, OPENPYXL_IMPORT_ERROR
    if OPENPYXL_AVAILABLE is not None:
        return bool(OPENPYXL_AVAILABLE)
    try:
        openpyxl = importlib.import_module("openpyxl")
        get_column_letter = _load_symbol("openpyxl.utils", "get_column_letter")
        OPENPYXL_AVAILABLE = True
    except Exception as exc:
        openpyxl = None
        get_column_letter = None
        OPENPYXL_AVAILABLE = False
        OPENPYXL_IMPORT_ERROR = exc
    return bool(OPENPYXL_AVAILABLE)


def _ensure_ocr():
    """惰性加载 image_ocr 模块（图片取字 OCR 后端）；失败返回 None。"""
    global ocr_module, OCR_MODULE_LOADED
    if OCR_MODULE_LOADED is not None:
        return ocr_module if OCR_MODULE_LOADED else None
    try:
        ocr_module = importlib.import_module("image_ocr")
        OCR_MODULE_LOADED = True
    except Exception:
        ocr_module = None
        OCR_MODULE_LOADED = False
    return ocr_module

LEGACY_DATA_DIR = SCRIPT_DIR / "中转坞数据"
LEGACY_STORE_DIR = LEGACY_DATA_DIR / "存入文件"


def _data_pointer_file() -> Path:
    """记录 PasserData 实际位置的小指针文件。

    放在用户配置区（%LOCALAPPDATA%\\Passer\\datadir.txt），既不在 exe 旁，也不在
    PasserData 内部——解决「数据目录可自选，但要有地方记住选了哪」的先有鸡先有蛋问题。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Passer" / "datadir.txt"


def read_data_pointer() -> "Path | None":
    try:
        text = _data_pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def write_data_pointer(data_dir: "str | os.PathLike") -> None:
    pointer = _data_pointer_file()
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(Path(data_dir)), encoding="utf-8")
    except OSError:
        pass


def _resolve_initial_data_dir() -> "tuple[Path, bool, bool]":
    """启动时确定 PasserData 位置，返回 (DATA_DIR, 需首次设置?, 沿用了 exe 旁旧数据?)。

    优先级：指针文件 > exe 旁已有数据（老安装，原地沿用并补写指针）> 需首次设置。
    """
    pointer = read_data_pointer()
    if pointer is not None:
        return pointer, False, False
    legacy_here = SCRIPT_DIR / "PasserData"
    if (legacy_here / "settings.json").exists() or (legacy_here / "items.json").exists():
        return legacy_here, False, True
    # 全新安装：先用一个临时默认值，main() 会弹窗让用户选择真正的位置。
    return legacy_here, True, False


DATA_DIR, DATA_DIR_NEEDS_SETUP, DATA_DIR_ADOPT_LEGACY = _resolve_initial_data_dir()
DEFAULT_STORE_DIR = DATA_DIR / "StoredFiles"
STORE_DIR = DEFAULT_STORE_DIR
DRAG_EXPORT_DIR = DATA_DIR / "DragExports"
OFFICE_PREVIEW_DIR = DATA_DIR / "OfficePreviews"
AI_OUTPUT_DIR = DATA_DIR / "AIOutputs"
BUILTIN_MODULE_DIR = DATA_DIR / "BuiltinModules"
MOD_DIR = DATA_DIR / "Mods"
ITEMS_FILE = DATA_DIR / "items.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PLANS_FILE = DATA_DIR / "plans.json"
AUTOMATIONS_FILE = DATA_DIR / "automations.json"
SETTINGS_SCHEMA_VERSION = 2
MAX_WINDOW_DIMENSION = 16384
APP_ICON_FILE = RESOURCE_DIR / "passer.ico"
APP_ICON_PNG = RESOURCE_DIR / "passer.png"
CRASH_LOG_MAX_BYTES = 2 * 1024 * 1024
CRASH_LOG_BACKUPS = 3
_CRASH_LOG_LOCK = threading.Lock()
_CRASH_HANDLERS_INSTALLED = False
_ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREAD_EXCEPTHOOK = getattr(threading, "excepthook", None)
_FAULT_LOG_FILE = None


def crash_log_path() -> Path:
    return DATA_DIR / "Logs" / "crash.log"


def _rotate_crash_log(path: Path) -> None:
    try:
        if path.stat().st_size < CRASH_LOG_MAX_BYTES:
            return
    except OSError:
        return
    try:
        oldest = path.with_suffix(path.suffix + f".{CRASH_LOG_BACKUPS}")
        oldest.unlink(missing_ok=True)
        for index in range(CRASH_LOG_BACKUPS - 1, 0, -1):
            source = path.with_suffix(path.suffix + f".{index}")
            if source.exists():
                os.replace(source, path.with_suffix(path.suffix + f".{index + 1}"))
        os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _diagnostic_field(value, fallback: str = "<none>", limit: int = 1600) -> str:
    """Keep one-line diagnostic metadata readable and bounded."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
    return (text[:limit] if text else fallback)


def _traceback_module_name(exc_tb) -> str:
    current = exc_tb
    while current is not None and current.tb_next is not None:
        current = current.tb_next
    if current is None:
        return "<unknown>"
    try:
        module_name = current.tb_frame.f_globals.get("__name__")
        if module_name:
            return str(module_name)
        return Path(current.tb_frame.f_code.co_filename).stem
    except Exception:
        return "<unknown>"


def write_crash_log(exc_type, exc_value, exc_tb, source: str = "main", *,
                    module: str | None = None, action: str | None = None,
                    target_path: str | os.PathLike | None = None) -> Path:
    """记录意外异常；不采集 API Key、密码或文件正文。"""
    path = crash_log_path()
    try:
        formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:
        formatted = f"{exc_type}: {exc_value}"
    header = (
        f"\n{'=' * 80}\n"
        f"time: {datetime.now().isoformat(timespec='seconds')}\n"
        f"source: {source}\n"
        f"module: {_diagnostic_field(module or _traceback_module_name(exc_tb))}\n"
        f"action: {_diagnostic_field(action or source)}\n"
        f"target_path: {_diagnostic_field(target_path)}\n"
        f"exception: {_diagnostic_field(getattr(exc_type, '__name__', exc_type))}\n"
        f"version: {APP_VERSION}\n"
        f"pid: {os.getpid()}\n"
        f"thread: {threading.current_thread().name}\n"
        f"python: {sys.version.replace(chr(10), ' ')}\n"
        f"platform: {platform.platform()}\n"
        f"executable: {sys.executable}\n"
        f"cwd: {Path.cwd()}\n"
        f"data_dir: {DATA_DIR}\n"
    )
    try:
        with _CRASH_LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_crash_log(path)
            with path.open("a", encoding="utf-8", errors="replace") as log:
                log.write(header)
                log.write(formatted.rstrip() + "\n")
    except OSError:
        pass
    return path


def log_unexpected_exception(exc: BaseException, *, module: str, action: str,
                             target_path: str | os.PathLike | None = None,
                             expected: tuple[type[BaseException], ...] = (),
                             source: str = "handled-operation") -> bool:
    """Log a handled exception only when it is outside the caller's expected set.

    Returns ``True`` when a diagnostic record was written. Expected input,
    environment, cancellation, and capability errors can keep their existing UI
    message without filling the crash log.
    """
    if expected and isinstance(exc, expected):
        return False
    write_crash_log(
        type(exc), exc, exc.__traceback__, source,
        module=module, action=action, target_path=target_path,
    )
    return True


def _enable_faulthandler() -> None:
    global _FAULT_LOG_FILE
    try:
        import faulthandler

        if _FAULT_LOG_FILE is not None:
            try:
                faulthandler.disable()
                _FAULT_LOG_FILE.close()
            except Exception:
                pass
        path = crash_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _FAULT_LOG_FILE = path.open("a", encoding="utf-8")
        faulthandler.enable(file=_FAULT_LOG_FILE, all_threads=True)
    except (OSError, RuntimeError):
        _FAULT_LOG_FILE = None


def install_crash_handlers() -> None:
    global _CRASH_HANDLERS_INSTALLED
    if _CRASH_HANDLERS_INSTALLED:
        _enable_faulthandler()
        return
    _CRASH_HANDLERS_INSTALLED = True

    def main_hook(exc_type, exc_value, exc_tb) -> None:
        write_crash_log(exc_type, exc_value, exc_tb, "main-thread")
        if callable(_ORIGINAL_SYS_EXCEPTHOOK):
            _ORIGINAL_SYS_EXCEPTHOOK(exc_type, exc_value, exc_tb)

    def thread_hook(args) -> None:
        name = getattr(getattr(args, "thread", None), "name", "background")
        write_crash_log(args.exc_type, args.exc_value, args.exc_traceback, f"thread:{name}")
        if callable(_ORIGINAL_THREAD_EXCEPTHOOK):
            _ORIGINAL_THREAD_EXCEPTHOOK(args)

    sys.excepthook = main_hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook
    _enable_faulthandler()


def install_tk_crash_handler(root: tk.Misc) -> None:
    reporting = {"active": False}

    def report_callback_exception(exc_type, exc_value, exc_tb) -> None:
        path = write_crash_log(exc_type, exc_value, exc_tb, "tk-callback")
        if reporting["active"]:
            return
        reporting["active"] = True
        try:
            messagebox.showerror(
                "Passer 发生错误",
                f"错误已记录，可在设置中一键导出诊断信息。\n\n日志：{path}",
                parent=root,
            )
        except Exception:
            pass
        finally:
            reporting["active"] = False

    root.report_callback_exception = report_callback_exception


def apply_data_dir(new_dir: "str | os.PathLike") -> None:
    """把所有数据路径全局变量切换到 new_dir，并同步通知 ai_chat 模块一起迁移。"""
    global DATA_DIR, DEFAULT_STORE_DIR, STORE_DIR, DRAG_EXPORT_DIR, OFFICE_PREVIEW_DIR
    global AI_OUTPUT_DIR, BUILTIN_MODULE_DIR, MOD_DIR, ITEMS_FILE, SETTINGS_FILE, PLANS_FILE
    global AUTOMATIONS_FILE
    DATA_DIR = Path(new_dir)
    DEFAULT_STORE_DIR = DATA_DIR / "StoredFiles"
    STORE_DIR = DEFAULT_STORE_DIR
    DRAG_EXPORT_DIR = DATA_DIR / "DragExports"
    OFFICE_PREVIEW_DIR = DATA_DIR / "OfficePreviews"
    AI_OUTPUT_DIR = DATA_DIR / "AIOutputs"
    BUILTIN_MODULE_DIR = DATA_DIR / "BuiltinModules"
    MOD_DIR = DATA_DIR / "Mods"
    ITEMS_FILE = DATA_DIR / "items.json"
    SETTINGS_FILE = DATA_DIR / "settings.json"
    PLANS_FILE = DATA_DIR / "plans.json"
    AUTOMATIONS_FILE = DATA_DIR / "automations.json"
    try:
        mod = sys.modules.get("ai_chat")
        if mod is None:
            mod = importlib.import_module("ai_chat")
        if hasattr(mod, "set_data_dir"):
            mod.set_data_dir(DATA_DIR)
    except Exception:
        pass
    try:
        reload_installed_builtin_modules()
    except Exception:
        pass
    if _CRASH_HANDLERS_INSTALLED:
        _enable_faulthandler()


FONT_SEARCH_DIR = Path(r"D:\Admin\Desktop\EXE\font")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".psd"}
PDF_EXTS = {".pdf"}
WORD_EXTS = {".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm"}
POWERPOINT_EXTS = {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".ppsm", ".pot", ".potx", ".potm"}
EXCEL_NATIVE_EXTS = {".xlsx", ".xlsm", ".xltx", ".xltm"}
EXCEL_EXTS = EXCEL_NATIVE_EXTS | {".xls", ".xlsb"}
OFFICE_OPEN_MODE_BUILTIN = "builtin"
OFFICE_OPEN_MODE_OFFICE = "office"
OFFICE_OPEN_MODE_WPS = "wps"
OFFICE_OPEN_MODE_LIBRE = "libreoffice"
OFFICE_OPEN_MODE_LABELS = {
    OFFICE_OPEN_MODE_BUILTIN: "内置预览器",
    OFFICE_OPEN_MODE_OFFICE: "Microsoft Office",
    OFFICE_OPEN_MODE_WPS: "WPS",
    OFFICE_OPEN_MODE_LIBRE: "LibreOffice",
}
OFFICE_OPEN_MODE_BY_LABEL = {label: key for key, label in OFFICE_OPEN_MODE_LABELS.items()}
OFFICE_OPEN_MODE_OPTIONS = [
    OFFICE_OPEN_MODE_LABELS[OFFICE_OPEN_MODE_BUILTIN],
    OFFICE_OPEN_MODE_LABELS[OFFICE_OPEN_MODE_OFFICE],
    OFFICE_OPEN_MODE_LABELS[OFFICE_OPEN_MODE_WPS],
    OFFICE_OPEN_MODE_LABELS[OFFICE_OPEN_MODE_LIBRE],
]


def normalize_office_open_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in OFFICE_OPEN_MODE_LABELS:
        return raw
    if raw in OFFICE_OPEN_MODE_BY_LABEL:
        return OFFICE_OPEN_MODE_BY_LABEL[raw]
    # 兼容旧版标签（"内置查看器" / "Office"）。
    if raw == "内置查看器":
        return OFFICE_OPEN_MODE_BUILTIN
    if raw == "Office":
        return OFFICE_OPEN_MODE_OFFICE
    return OFFICE_OPEN_MODE_BUILTIN


FOLDER_OPEN_MODE_BUILTIN = "builtin"
FOLDER_OPEN_MODE_EXPLORER = "explorer"
FOLDER_OPEN_MODE_LABELS = {
    FOLDER_OPEN_MODE_BUILTIN: "内置预览器",
    FOLDER_OPEN_MODE_EXPLORER: "资源管理器",
}
FOLDER_OPEN_MODE_BY_LABEL = {label: key for key, label in FOLDER_OPEN_MODE_LABELS.items()}
FOLDER_OPEN_MODE_OPTIONS = [
    FOLDER_OPEN_MODE_LABELS[FOLDER_OPEN_MODE_BUILTIN],
    FOLDER_OPEN_MODE_LABELS[FOLDER_OPEN_MODE_EXPLORER],
]


def normalize_folder_open_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in FOLDER_OPEN_MODE_LABELS:
        return raw
    if raw in FOLDER_OPEN_MODE_BY_LABEL:
        return FOLDER_OPEN_MODE_BY_LABEL[raw]
    return FOLDER_OPEN_MODE_BUILTIN


# PDF / 图片 / 视频 / 音频 等：可选「内置预览器」或交给「系统默认」程序打开。
SIMPLE_OPEN_MODE_BUILTIN = "builtin"
SIMPLE_OPEN_MODE_SYSTEM = "system"
SIMPLE_OPEN_MODE_LABELS = {
    SIMPLE_OPEN_MODE_BUILTIN: "内置预览器",
    SIMPLE_OPEN_MODE_SYSTEM: "系统默认",
}
SIMPLE_OPEN_MODE_BY_LABEL = {label: key for key, label in SIMPLE_OPEN_MODE_LABELS.items()}
SIMPLE_OPEN_MODE_OPTIONS = [
    SIMPLE_OPEN_MODE_LABELS[SIMPLE_OPEN_MODE_BUILTIN],
    SIMPLE_OPEN_MODE_LABELS[SIMPLE_OPEN_MODE_SYSTEM],
]


def normalize_simple_open_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in SIMPLE_OPEN_MODE_LABELS:
        return raw
    if raw in SIMPLE_OPEN_MODE_BY_LABEL:
        return SIMPLE_OPEN_MODE_BY_LABEL[raw]
    return SIMPLE_OPEN_MODE_BUILTIN


AIRA_NOTIFY_MODE_PASSER = "passer"
AIRA_NOTIFY_MODE_WINDOWS = "windows"
AIRA_NOTIFY_MODE_LABELS = {
    AIRA_NOTIFY_MODE_PASSER: "Passer 内通知",
    AIRA_NOTIFY_MODE_WINDOWS: "Windows 通知",
}
AIRA_NOTIFY_MODE_BY_LABEL = {label: key for key, label in AIRA_NOTIFY_MODE_LABELS.items()}


def normalize_aira_notify_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in AIRA_NOTIFY_MODE_LABELS:
        return raw
    if raw in AIRA_NOTIFY_MODE_BY_LABEL:
        return AIRA_NOTIFY_MODE_BY_LABEL[raw]
    if raw.casefold() in {"passer", "inside", "internal", "内置", "内部", "passer内通知"}:
        return AIRA_NOTIFY_MODE_PASSER
    return AIRA_NOTIFY_MODE_WINDOWS


CODE_OPEN_MODE_BUILTIN = "builtin"
CODE_OPEN_MODE_VSCODE = "vscode"
CODE_OPEN_MODE_LABELS = {
    CODE_OPEN_MODE_BUILTIN: "内置预览器",
    CODE_OPEN_MODE_VSCODE: "VS Code",
}
CODE_OPEN_MODE_BY_LABEL = {label: key for key, label in CODE_OPEN_MODE_LABELS.items()}
CODE_OPEN_MODE_OPTIONS = [
    CODE_OPEN_MODE_LABELS[CODE_OPEN_MODE_BUILTIN],
    CODE_OPEN_MODE_LABELS[CODE_OPEN_MODE_VSCODE],
]


def normalize_code_open_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in CODE_OPEN_MODE_LABELS:
        return raw
    if raw in CODE_OPEN_MODE_BY_LABEL:
        return CODE_OPEN_MODE_BY_LABEL[raw]
    if raw.lower() in ("code", "vscode", "vs code", "visual studio code"):
        return CODE_OPEN_MODE_VSCODE
    return CODE_OPEN_MODE_BUILTIN


CODE_EXTS = {
    ".py", ".pyw", ".ipynb", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".json", ".jsonc", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".env", ".bat", ".cmd", ".ps1", ".sh", ".bash", ".zsh", ".fish",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".cs", ".java",
    ".kt", ".kts", ".swift", ".m", ".mm", ".go", ".rs", ".rb", ".php", ".lua",
    ".sql", ".r", ".jl", ".dart", ".scala", ".pl", ".pm", ".erl", ".ex", ".exs",
    ".fs", ".fsx", ".clj", ".cljs", ".hs", ".nim", ".zig", ".gd", ".shader",
    ".glsl", ".hlsl", ".wgsl", ".dockerfile", ".makefile", ".mk",
}
CODE_FILENAMES = {
    "dockerfile", "containerfile", "makefile", "gnumakefile", "rakefile",
    "gemfile", "podfile", "procfile", "vagrantfile", "jenkinsfile",
    ".gitignore", ".gitattributes", ".editorconfig", ".env", ".env.local",
}
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".rtf", ".nfo",
    ".gitignore", ".gitattributes", ".editorconfig",
}
TEXT_EXTS |= CODE_EXTS


def is_code_path(path: Path) -> bool:
    name = path.name.casefold()
    suffix = path.suffix.lower()
    return suffix in CODE_EXTS or name in CODE_FILENAMES
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff", ".ape"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
FLASH_EXTS = {".swf"}
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz", ".rar", ".7z"}
ARCHIVE_NATIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"}  # 内置可读取
TEXT_VIEWER_MAX_BYTES = 4 * 1024 * 1024
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
FONT_FILE_EXTS = {".ttf", ".otf", ".ttc"}
FONT_KEYWORDS = ("等线", "Deng", "DengXian")
APP_FONT_FALLBACK = "Microsoft YaHei UI"
APP_FONT_FAMILY = APP_FONT_FALLBACK
APP_FONT_PREFERRED_FAMILY = "等线"
APP_FONT_PREFERRED_FILE = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "Deng.ttf"
APP_FONT_PREFERRED_BOLD_FILE = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "Dengb.ttf"
APP_FONT_FILE: Path | None = None

DEFAULT_WIDTH = 1800  # 默认主界面宽度
DEFAULT_HEIGHT = 1200
MIN_WIDTH = 690
MIN_HEIGHT = 480
CUSTOM_WINDOW_SIZE_LABEL = "自定义"
COMMON_WINDOW_SIZES = (
    ("900×600", 900, 600),
    ("1280×720", 1280, 720),
    ("1200×800", 1200, 800),
    ("1440×900", 1440, 900),
    ("1600×1000", 1600, 1000),
    ("1800×1200", DEFAULT_WIDTH, DEFAULT_HEIGHT),
    ("1920×1080", 1920, 1080),
    ("2560×1440", 2560, 1440),
    ("3840×2160", 3840, 2160),
)
COMMON_WINDOW_SIZE_LABELS = [label for label, _w, _h in COMMON_WINDOW_SIZES]
COMMON_WINDOW_SIZE_BY_LABEL = {label: (width, height) for label, width, height in COMMON_WINDOW_SIZES}
DEFAULT_FONT_SIZE_LABEL = "默认"
FONT_SIZE_OPTIONS = (
    ("小", -1),
    (DEFAULT_FONT_SIZE_LABEL, 0),
    ("大", 1),
    ("特大", 2),
)
FONT_SIZE_LABELS = [label for label, _delta in FONT_SIZE_OPTIONS]
FONT_SIZE_DELTA_BY_LABEL = {label: delta for label, delta in FONT_SIZE_OPTIONS}
DEFAULT_AIRA_LINE_SPACING_LABEL = "默认"
AIRA_LINE_SPACING_OPTIONS = (
    ("紧凑", 0),
    (DEFAULT_AIRA_LINE_SPACING_LABEL, 2),
    ("宽松", 5),
)
AIRA_LINE_SPACING_LABELS = [label for label, _pixels in AIRA_LINE_SPACING_OPTIONS]
AIRA_LINE_SPACING_BY_LABEL = dict(AIRA_LINE_SPACING_OPTIONS)
APP_FONT_SIZE_DELTA = 0
SHELL_ICON_SIZE = 72

APP_BG = "#f3f6fb"
SURFACE_BG = "#ffffff"
TITLE_BG = "#101827"
TITLE_FG = "#f8fafc"
HEADER_TRANSPARENT_KEY = "#010204"
MUTED_FG = "#64748b"
BORDER = "#d7dde8"
DANGER = "#ef4444"
ACCENT = "#2563eb"
ACCENT_SOFT = "#e8f1ff"
ACCENT_SOFT_HOVER = "#dbeafe"
ACCENT_FAINT = "#eff6ff"
ACCENT_HOVER = "#1d4ed8"
ACCENT_TRACK_START = "#6ea0f5"
ACCENT_TRACK_END = "#1b48c4"
ACCENT_SHINE = "#e3eeff"
TITLE_BUTTON_BG = "#172235"
TITLE_BUTTON_HOVER = "#223047"
DEFAULT_APP_BG = APP_BG
CARD_SELECTED = "#edf5ff"
DEFAULT_THEME_COLOR = "blue"
PASSER_THEME_COLOR_OPTIONS = (
    ("blue", "蓝色"),
    ("pink", "粉色"),
    ("green", "绿色"),
    ("purple", "紫色"),
    ("yellow", "黄色"),
    ("orange", "橙色"),
    ("red", "红色"),
    ("cyan", "青色"),
)
PASSER_THEME_LABELS = [label for _key, label in PASSER_THEME_COLOR_OPTIONS]
PASSER_THEME_BY_LABEL = {label: key for key, label in PASSER_THEME_COLOR_OPTIONS}
PASSER_THEME_PALETTES = {
    "blue": {
        "title_bg": "#101827",
        "border": "#d7dde8",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "accent_soft": "#e8f1ff",
        "accent_soft_hover": "#dbeafe",
        "accent_faint": "#eff6ff",
        "card_selected": "#edf5ff",
        "track_start": "#6ea0f5",
        "track_end": "#1b48c4",
        "shine": "#e3eeff",
        "title_button_bg": "#172235",
        "title_button_hover": "#223047",
    },
    "pink": {
        "title_bg": "#3b1028",
        "border": "#f9a8d4",
        "accent": "#db2777",
        "accent_hover": "#be185d",
        "accent_soft": "#fce7f3",
        "accent_soft_hover": "#fbcfe8",
        "accent_faint": "#fdf2f8",
        "card_selected": "#fce7f3",
        "track_start": "#f472b6",
        "track_end": "#9d174d",
        "shine": "#fdf2f8",
        "title_button_bg": "#541635",
        "title_button_hover": "#7a1d4b",
    },
    "green": {
        "title_bg": "#052e16",
        "border": "#86efac",
        "accent": "#16a34a",
        "accent_hover": "#15803d",
        "accent_soft": "#dcfce7",
        "accent_soft_hover": "#bbf7d0",
        "accent_faint": "#f0fdf4",
        "card_selected": "#dcfce7",
        "track_start": "#4ade80",
        "track_end": "#166534",
        "shine": "#f0fdf4",
        "title_button_bg": "#093d1f",
        "title_button_hover": "#14532d",
    },
    "purple": {
        "title_bg": "#251047",
        "border": "#c4b5fd",
        "accent": "#7c3aed",
        "accent_hover": "#6d28d9",
        "accent_soft": "#ede9fe",
        "accent_soft_hover": "#ddd6fe",
        "accent_faint": "#f5f3ff",
        "card_selected": "#ede9fe",
        "track_start": "#a78bfa",
        "track_end": "#5b21b6",
        "shine": "#f5f3ff",
        "title_button_bg": "#32165f",
        "title_button_hover": "#4c1d95",
    },
    "yellow": {
        "title_bg": "#422006",
        "border": "#facc15",
        "accent": "#ca8a04",
        "accent_hover": "#a16207",
        "accent_soft": "#fef3c7",
        "accent_soft_hover": "#fde68a",
        "accent_faint": "#fefce8",
        "card_selected": "#fef3c7",
        "track_start": "#facc15",
        "track_end": "#854d0e",
        "shine": "#fffbeb",
        "title_button_bg": "#57310a",
        "title_button_hover": "#713f12",
    },
    "orange": {
        "title_bg": "#431407",
        "border": "#fdba74",
        "accent": "#ea580c",
        "accent_hover": "#c2410c",
        "accent_soft": "#ffedd5",
        "accent_soft_hover": "#fed7aa",
        "accent_faint": "#fff7ed",
        "card_selected": "#ffedd5",
        "track_start": "#fb923c",
        "track_end": "#9a3412",
        "shine": "#fff7ed",
        "title_button_bg": "#5b1b09",
        "title_button_hover": "#7c2d12",
    },
    "red": {
        "title_bg": "#450a0a",
        "border": "#fca5a5",
        "accent": "#dc2626",
        "accent_hover": "#b91c1c",
        "accent_soft": "#fee2e2",
        "accent_soft_hover": "#fecaca",
        "accent_faint": "#fef2f2",
        "card_selected": "#fee2e2",
        "track_start": "#f87171",
        "track_end": "#991b1b",
        "shine": "#fef2f2",
        "title_button_bg": "#5f1111",
        "title_button_hover": "#7f1d1d",
    },
    "cyan": {
        "title_bg": "#083344",
        "border": "#67e8f9",
        "accent": "#0891b2",
        "accent_hover": "#0e7490",
        "accent_soft": "#cffafe",
        "accent_soft_hover": "#a5f3fc",
        "accent_faint": "#ecfeff",
        "card_selected": "#cffafe",
        "track_start": "#22d3ee",
        "track_end": "#155e75",
        "shine": "#ecfeff",
        "title_button_bg": "#0e4358",
        "title_button_hover": "#155e75",
    },
}
ITEM_MARK_PALETTE = {
    "red": ("#fff1f2", "#fca5a5"),
    "yellow": ("#fffbeb", "#fcd34d"),
    "blue": ("#eff6ff", "#93c5fd"),
    "green": ("#f0fdf4", "#86efac"),
}


def normalize_hex_color(value, default: str = DEFAULT_APP_BG) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.upper()
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return f"#{text.upper()}"
    return str(default or DEFAULT_APP_BG)


def normalize_passer_theme_color(value) -> str:
    text = str(value or "").strip()
    key = text.lower()
    if key in PASSER_THEME_PALETTES:
        return key
    return PASSER_THEME_BY_LABEL.get(text, DEFAULT_THEME_COLOR)


def passer_theme_label(value) -> str:
    key = normalize_passer_theme_color(value)
    for option_key, label in PASSER_THEME_COLOR_OPTIONS:
        if option_key == key:
            return label
    return "蓝色"


def passer_theme_palette(value) -> dict:
    key = normalize_passer_theme_color(value)
    return dict(PASSER_THEME_PALETTES.get(key) or PASSER_THEME_PALETTES[DEFAULT_THEME_COLOR])


def apply_passer_theme_color(value) -> str:
    global TITLE_BG, ACCENT, ACCENT_SOFT, ACCENT_SOFT_HOVER, ACCENT_FAINT
    global ACCENT_HOVER, ACCENT_TRACK_START, ACCENT_TRACK_END, ACCENT_SHINE
    global TITLE_BUTTON_BG, TITLE_BUTTON_HOVER, CARD_SELECTED
    key = normalize_passer_theme_color(value)
    palette = passer_theme_palette(key)
    TITLE_BG = palette["title_bg"]
    ACCENT = palette["accent"]
    ACCENT_HOVER = palette["accent_hover"]
    ACCENT_SOFT = palette["accent_soft"]
    ACCENT_SOFT_HOVER = palette["accent_soft_hover"]
    ACCENT_FAINT = palette["accent_faint"]
    ACCENT_TRACK_START = palette["track_start"]
    ACCENT_TRACK_END = palette["track_end"]
    ACCENT_SHINE = palette["shine"]
    TITLE_BUTTON_BG = palette["title_button_bg"]
    TITLE_BUTTON_HOVER = palette["title_button_hover"]
    CARD_SELECTED = palette["card_selected"]
    return key


def normalize_background_image(value) -> str:
    text = str(value or "").strip()
    return text if text else ""
TILE_WIDTH = 138
TILE_HEIGHT = 138
TILE_COLUMN_WIDTH = 162
TILE_ROW_HEIGHT = 162
TILE_PAD = 8
GROUP_MAX_MEMBERS = 16
SEARCH_RECENT_LIMIT = 8
DRAG_THRESHOLD = 4
SHIFT_MASK = 0x0001
CTRL_MASK = 0x0004
TRANSPARENT_ALPHA = 0.72
TRANSPARENT_ALPHA_MIN = 0.20

# WeChatAppEx.exe can linger after the main WeChat UI exits; treating it as
# WeChat itself prevents Passer from reclaiming Alt+A when the user has closed
# WeChat. Keep the hotkey decision tied to the main WeChat executables.
WECHAT_PROCESS_NAMES = ("wechat.exe", "weixin.exe")
ANNOTATION_DEFAULT_COLOR = "#fa3e3e"
ANNOTATION_COLORS = ("#fa3e3e", "#ff9500", "#ffcc00", "#34c759", "#0a84ff", "#111827", "#ffffff")
ANNOTATION_PEN_WIDTH = 3
ANNOTATION_TEXT_SIZE = 22

MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_MENU = 0x12
VK_A = 0x41
VK_P = 0x50
HOTKEY_SCREENSHOT_ID = 0xA001
HOTKEY_POLL_MS = 35
HOTKEY_WECHAT_CHECK_MS = 2000
ENABLE_NATIVE_WM_DROPFILES = False
WINDOWSAPPS_AUMID_CACHE: dict[str, str | None] = {}
_RIGHT_CLICK_SWALLOW_HOOK = None
_RIGHT_CLICK_SWALLOW_PROC = None
PINYIN_INITIALS = {
    "安": "a", "全": "q", "须": "x", "知": "z", "专": "z", "业": "y", "学": "x", "位": "w",
    "硕": "s", "士": "s", "研": "y", "究": "j", "生": "s", "实": "s", "践": "j", "环": "h",
    "节": "j", "记": "j", "录": "l", "本": "b", "杨": "y", "赵": "z", "华": "h", "审": "s",
    "批": "p", "表": "b", "连": "l", "点": "d", "器": "q", "随": "s", "机": "j", "数": "s",
    "闹": "n", "钟": "z", "计": "j", "算": "s", "网": "w", "络": "l", "检": "j", "测": "c",
    "设": "s", "备": "b", "锁": "s", "截": "j", "图": "t", "注": "z", "释": "s", "命": "m",
    "令": "l", "提": "t", "示": "s", "符": "f", "注": "z", "册": "c", "任": "r", "务": "w",
    "管": "g", "理": "l", "目": "m", "录": "l", "置": "z",
}


def fuzzy_subsequence_score(needle: str, haystack: str) -> int | None:
    """Return a compactness score if all query characters appear in order."""
    needle = str(needle or "").casefold()
    haystack = str(haystack or "").casefold()
    if not needle:
        return 0
    pos = -1
    first = None
    gaps = 0
    for char in needle:
        found = haystack.find(char, pos + 1)
        if found < 0:
            return None
        if first is None:
            first = found
        if pos >= 0:
            gaps += max(0, found - pos - 1)
        pos = found
    return (first or 0) + gaps


def pinyin_search_forms(text: str) -> tuple[str, str]:
    """Best-effort pinyin forms: full pinyin if pypinyin exists, initials otherwise."""
    text = str(text or "")
    try:
        from pypinyin import lazy_pinyin, Style  # type: ignore
        full = "".join(lazy_pinyin(text, errors="ignore")).casefold()
        initials = "".join(lazy_pinyin(text, style=Style.FIRST_LETTER, errors="ignore")).casefold()
        if full or initials:
            return full, initials
    except Exception:
        pass
    initials = "".join(PINYIN_INITIALS.get(ch, ch if ch.isascii() and ch.isalnum() else "") for ch in text).casefold()
    return initials, initials


@dataclass
class DockItem:
    id: str
    kind: str
    target: str
    title: str
    added_at: str
    grid_x: int | None = None
    grid_y: int | None = None
    passer_name: str | None = None
    group_id: str | None = None
    reminder_at: str | None = None
    mark_color: str | None = None
    group_order: int | None = None

    @property
    def display_title(self) -> str:
        name = (self.passer_name or "").strip()
        return name or self.title


BUILTIN_TOOL_KIND = "builtin_tool"
REMOVED_BUILTIN_TARGETS = frozenset({"passer://tunnel"})
BUILTIN_CLICKER_TARGET = "passer://clicker"
BUILTIN_RANDOM_TARGET = "passer://random"
BUILTIN_PLAN_TARGET = "passer://plan"
BUILTIN_AUTOMATION_TARGET = "passer://automation"
BUILTIN_AIRA_TARGET = "passer://aira"
BUILTIN_CALCULATOR_TARGET = "passer://calculator"
BUILTIN_SHUTDOWN_TARGET = "passer://shutdown"
BUILTIN_NETWORK_TARGET = "passer://network"
BUILTIN_SERVER_TARGET = "passer://server"
BUILTIN_CLIPBOARD_TARGET = "passer://clipboard"
BUILTIN_MAIL_TARGET = "passer://mail"
BUILTIN_QR_TARGET = "passer://qr"
BUILTIN_MARKDOWN_TARGET = "passer://markdown"
BUILTIN_FILE_SEARCH_TARGET = "passer://file-search"
BUILTIN_SCREEN_RECORD_TARGET = "passer://screen-record"
BUILTIN_MAGNET_TARGET = "passer://magnet-download"
BUILTIN_MAP_TARGET = "passer://map"
BUILTIN_DEVICE_LOCK_TARGET = "passer://device-lock"
BUILTIN_FILE_SHARE_TARGET = "passer://file-share"
BUILTIN_DEVICE_INFO_TARGET = "passer://device-info"
BUILTIN_PHONE_MIRROR_TARGET = "passer://phone-mirror"
BUILTIN_SCREENSHOT_TARGET = "passer://screenshot"
BUILTIN_ANNOTATE_TARGET = "passer://annotate"
BUILTIN_STORE_TARGET = "passer://store"
BUILTIN_SETTINGS_TARGET = "passer://settings"
BUILTIN_CMD_TARGET = "passer://system/cmd"
BUILTIN_REGEDIT_TARGET = "passer://system/regedit"
BUILTIN_TASKMGR_TARGET = "passer://system/taskmgr"
BUILTIN_CLICKER_TITLE = "连点器 Clicker"
BUILTIN_RANDOM_TITLE = "随机数 Random"
BUILTIN_PLAN_TITLE = "计划 Plan"
BUILTIN_AUTOMATION_TITLE = "自动化 Automation"
BUILTIN_AIRA_TITLE = "Aira"
BUILTIN_CALCULATOR_TITLE = "计算器 Calculator"
BUILTIN_SHUTDOWN_TITLE = "定时关机 Shutdown"
BUILTIN_NETWORK_TITLE = "网络检测 Network"
BUILTIN_SERVER_TITLE = "服务器 Server"
BUILTIN_CLIPBOARD_TITLE = "剪贴板 Clipboard"
BUILTIN_MAIL_TITLE = "邮件 Mail"
BUILTIN_QR_TITLE = "二维码 QR"
BUILTIN_MARKDOWN_TITLE = "Markdown 预览器"
BUILTIN_FILE_SEARCH_TITLE = "文件搜索 File Search"
BUILTIN_SCREEN_RECORD_TITLE = "屏幕录制 Recorder"
BUILTIN_MAGNET_TITLE = "磁力下载 Magnet"
BUILTIN_MAP_TITLE = "地图 Map"
BUILTIN_DEVICE_LOCK_TITLE = "设备锁 Device Lock"
BUILTIN_FILE_SHARE_TITLE = "文件共享 File Share"
BUILTIN_DEVICE_INFO_TITLE = "设备检测 Device Info"
BUILTIN_PHONE_MIRROR_TITLE = "\u624b\u673a\u6295\u5c4f Phone Mirror"
BUILTIN_TOOLS = (
    {
        "id": "__passer_builtin_clicker__",
        "target": BUILTIN_CLICKER_TARGET,
        "title": BUILTIN_CLICKER_TITLE,
        "detail": "内置工具",
        "aliases": ("clicker", "连点器", "鼠标连点器", "自动连点", "自动点击", "auto clicker", "autoclicker"),
    },
    {
        "id": "__passer_builtin_random__",
        "target": BUILTIN_RANDOM_TARGET,
        "title": BUILTIN_RANDOM_TITLE,
        "detail": "内置工具",
        "aliases": ("随机数", "随机", "抽签", "抽数", "random", "rng", "random number"),
    },
    {
        "id": "__passer_builtin_plan__",
        "target": BUILTIN_PLAN_TARGET,
        "title": BUILTIN_PLAN_TITLE,
        "detail": "内置工具",
        "aliases": ("计划", "日程", "安排", "提醒", "计划提醒", "plan", "schedule", "agenda", "todo"),
    },
    {
        "id": "__passer_builtin_automation__",
        "target": BUILTIN_AUTOMATION_TARGET,
        "title": BUILTIN_AUTOMATION_TITLE,
        "detail": "内置工具",
        "aliases": ("自动化", "定时任务", "自动任务", "ai任务", "定时", "例程", "automation",
                    "scheduled task", "scheduled", "routine", "cron"),
    },
    {
        "id": "__passer_builtin_aira__",
        "target": BUILTIN_AIRA_TARGET,
        "title": BUILTIN_AIRA_TITLE,
        "detail": "内置工具",
        "aliases": ("aira", "微信总结", "微信消息总结", "消息总结", "微信监听", "aira总结", "wechat summary"),
    },
    {
        "id": "__passer_builtin_calculator__",
        "target": BUILTIN_CALCULATOR_TARGET,
        "title": BUILTIN_CALCULATOR_TITLE,
        "detail": "内置工具",
        "aliases": ("计算器", "计算", "表达式", "单位换算", "汇率", "calculator", "calc", "unit", "currency"),
    },
    {
        "id": "__passer_builtin_shutdown__",
        "target": BUILTIN_SHUTDOWN_TARGET,
        "title": BUILTIN_SHUTDOWN_TITLE,
        "detail": "内置工具",
        "aliases": ("定时关机", "关机", "倒计时关机", "计划关机", "取消关机", "shutdown", "power off", "timer shutdown"),
    },
    {
        "id": "__passer_builtin_network__",
        "target": BUILTIN_NETWORK_TARGET,
        "title": BUILTIN_NETWORK_TITLE,
        "detail": "内置工具",
        "aliases": ("网络检测", "网络", "测速", "下载测速", "ping", "延迟", "network", "speed test", "speedtest"),
    },
    {
        "id": "__passer_builtin_server__",
        "target": BUILTIN_SERVER_TARGET,
        "title": BUILTIN_SERVER_TITLE,
        "detail": "内置工具",
        "aliases": (
            "服务器", "本地服务器", "静态服务器", "HTTP服务器", "网关",
            "server", "web server", "http server", "localhost", "gateway",
        ),
    },
    {
        "id": "__passer_builtin_clipboard__",
        "target": BUILTIN_CLIPBOARD_TARGET,
        "title": BUILTIN_CLIPBOARD_TITLE,
        "detail": "内置工具",
        "aliases": (
            "剪贴板", "剪切板", "复制历史", "粘贴板", "clipboard",
            "clipboard history", "copy history", "pasteboard",
        ),
    },
    {
        "id": "__passer_builtin_mail__",
        "target": BUILTIN_MAIL_TARGET,
        "title": BUILTIN_MAIL_TITLE,
        "detail": "内置工具",
        "aliases": ("邮件", "邮箱", "电子邮件", "收件箱", "写邮件", "发邮件", "mail", "email", "inbox", "smtp", "imap"),
    },
    {
        "id": "__passer_builtin_qr__",
        "target": BUILTIN_QR_TARGET,
        "title": BUILTIN_QR_TITLE,
        "detail": "内置工具",
        "aliases": ("二维码", "QR", "qrcode", "二维码生成", "二维码识别", "扫码"),
    },
    {
        "id": "__passer_builtin_markdown__",
        "target": BUILTIN_MARKDOWN_TARGET,
        "title": BUILTIN_MARKDOWN_TITLE,
        "detail": "内置工具",
        "aliases": ("markdown", "md", "预览器", "markdown预览", "反渲染", "html转markdown", "html", "渲染"),
    },
    {
        "id": "__passer_builtin_file_search__",
        "target": BUILTIN_FILE_SEARCH_TARGET,
        "title": BUILTIN_FILE_SEARCH_TITLE,
        "detail": "内置工具",
        "aliases": ("文件搜索", "搜索文件", "查找文件", "本地搜索", "快速搜索", "file search", "find file", "search"),
    },
    {
        "id": "__passer_builtin_screen_record__",
        "target": BUILTIN_SCREEN_RECORD_TARGET,
        "title": BUILTIN_SCREEN_RECORD_TITLE,
        "detail": "内置工具",
        "aliases": ("屏幕录制", "录屏", "录制", "GIF录制", "MP4录制", "screen record", "recorder", "gif"),
    },
    {
        "id": "__passer_builtin_magnet__",
        "target": BUILTIN_MAGNET_TARGET,
        "title": BUILTIN_MAGNET_TITLE,
        "detail": "内置工具",
        "aliases": ("磁力下载", "磁力链", "magnet", "bt下载", "torrent", "种子下载"),
    },
    {
        "id": "__passer_builtin_map__",
        "target": BUILTIN_MAP_TARGET,
        "title": BUILTIN_MAP_TITLE,
        "detail": "内置工具",
        "aliases": ("地图", "地图工具", "位置", "经纬度", "导航", "map", "location", "coordinates"),
    },
    {
        "id": "__passer_builtin_device_lock__",
        "target": BUILTIN_DEVICE_LOCK_TARGET,
        "title": BUILTIN_DEVICE_LOCK_TITLE,
        "detail": "内置工具",
        "aliases": ("设备锁", "锁定键盘", "锁定鼠标", "禁用键盘", "禁用鼠标", "device lock", "keyboard lock", "mouse lock"),
    },
    {
        "id": "__passer_builtin_file_share__",
        "target": BUILTIN_FILE_SHARE_TARGET,
        "title": BUILTIN_FILE_SHARE_TITLE,
        "detail": "内置工具",
        "aliases": ("文件共享", "局域网", "局域网传输", "传文件", "共享", "发送文件", "file share", "lan", "share", "send file"),
    },
    {
        "id": "__passer_builtin_device_info__",
        "target": BUILTIN_DEVICE_INFO_TARGET,
        "title": BUILTIN_DEVICE_INFO_TITLE,
        "detail": "内置工具",
        "aliases": ("设备检测", "设备信息", "硬件信息", "硬件检测", "cpu", "主板", "内存", "硬盘", "gpu", "显卡", "device info", "hardware", "system info", "specs"),
    },
    {
        "id": "__passer_builtin_phone_mirror__",
        "target": BUILTIN_PHONE_MIRROR_TARGET,
        "title": BUILTIN_PHONE_MIRROR_TITLE,
        "detail": "\u5185\u7f6e\u5de5\u5177",
        "aliases": ("\u624b\u673a\u6295\u5c4f", "\u6295\u5c4f", "\u624b\u673a\u4e92\u8054", "\u624b\u673a\u4ea4\u4e92", "\u5b89\u5353\u6295\u5c4f", "scrcpy", "phone mirror", "screen mirror", "android mirror", "mirror phone"),
    },
    {
        "id": "__passer_builtin_screenshot__",
        "target": BUILTIN_SCREENSHOT_TARGET,
        "title": "截图 Screenshot",
        "detail": "内置工具",
        "aliases": ("截图", "截屏", "screenshot", "screen shot", "capture", "snip"),
    },
    {
        "id": "__passer_builtin_annotate__",
        "target": BUILTIN_ANNOTATE_TARGET,
        "title": "注释 Annotate",
        "detail": "内置工具",
        "aliases": ("注释", "标注", "批注", "annotate", "annotation", "draw"),
    },
    {
        "id": "__passer_builtin_cmd__",
        "target": BUILTIN_CMD_TARGET,
        "title": "命令提示符 CMD",
        "detail": "系统工具",
        "aliases": ("cmd", "命令提示符", "命令行", "终端", "command", "command prompt", "terminal", "console"),
    },
    {
        "id": "__passer_builtin_regedit__",
        "target": BUILTIN_REGEDIT_TARGET,
        "title": "注册表 Registry",
        "detail": "系统工具",
        "aliases": ("注册表", "注册表编辑器", "regedit", "registry", "registry editor"),
    },
    {
        "id": "__passer_builtin_taskmgr__",
        "target": BUILTIN_TASKMGR_TARGET,
        "title": "任务管理器 Task Manager",
        "detail": "系统工具",
        "aliases": ("任务管理器", "任务", "taskmgr", "task manager", "process", "进程"),
    },
    {
        "id": "__passer_builtin_store__",
        "target": BUILTIN_STORE_TARGET,
        "title": "目录 Directory",
        "detail": "设置",
        "aliases": ("目录", "文件目录", "存储目录", "打开目录", "directory", "folder", "store"),
    },
    {
        "id": "__passer_builtin_settings__",
        "target": BUILTIN_SETTINGS_TARGET,
        "title": "设置 Settings",
        "detail": "设置",
        "aliases": ("设置", "选项", "配置", "settings", "preferences", "config"),
    },
)
BUILTIN_TOOL_BY_TARGET = {str(tool["target"]): tool for tool in BUILTIN_TOOLS}

# Builtin and system tool icons: rounded square + simple line symbol.
BUILTIN_TOOL_ICON_STYLE = {
    BUILTIN_CLICKER_TARGET: ("clicker", "#2563eb"),
    BUILTIN_RANDOM_TARGET: ("dice", "#7c3aed"),
    BUILTIN_PLAN_TARGET: ("calendar", "#9333ea"),
    BUILTIN_AUTOMATION_TARGET: ("clock", "#0891b2"),
    BUILTIN_AIRA_TARGET: ("aira", "#2563eb"),
    BUILTIN_CALCULATOR_TARGET: ("calculator", "#0f766e"),
    BUILTIN_SHUTDOWN_TARGET: ("power", "#dc2626"),
    BUILTIN_NETWORK_TARGET: ("network", "#0284c7"),
    BUILTIN_SERVER_TARGET: ("network", "#0f766e"),
    BUILTIN_CLIPBOARD_TARGET: ("clipboard", "#2563eb"),
    BUILTIN_MAIL_TARGET: ("mail", "#2563eb"),
    BUILTIN_QR_TARGET: ("qr", "#7c2d12"),
    BUILTIN_MARKDOWN_TARGET: ("document", "#334155"),
    BUILTIN_FILE_SEARCH_TARGET: ("search", "#0d9488"),
    BUILTIN_SCREEN_RECORD_TARGET: ("record", "#be123c"),
    BUILTIN_MAGNET_TARGET: ("magnet", "#0369a1"),
    BUILTIN_MAP_TARGET: ("pin", "#16a34a"),
    BUILTIN_DEVICE_LOCK_TARGET: ("lock", "#dc2626"),
    BUILTIN_FILE_SHARE_TARGET: ("share", "#0891b2"),
    BUILTIN_DEVICE_INFO_TARGET: ("chip", "#1e3a8a"),
    BUILTIN_PHONE_MIRROR_TARGET: ("phone", "#4f46e5"),
    BUILTIN_SCREENSHOT_TARGET: ("crop", "#db2777"),
    BUILTIN_ANNOTATE_TARGET: ("pen", "#d97706"),
    BUILTIN_CMD_TARGET: ("terminal", "#0f172a"),
    BUILTIN_REGEDIT_TARGET: ("registry", "#0891b2"),
    BUILTIN_TASKMGR_TARGET: ("bars", "#16a34a"),
    BUILTIN_STORE_TARGET: ("folder", "#475569"),
    BUILTIN_SETTINGS_TARGET: ("gear", "#475569"),
}


CORE_BUILTIN_TOOLS = BUILTIN_TOOLS
INSTALLED_BUILTIN_MODULES: dict[str, dict] = {}
INSTALLED_MODS: dict[str, dict] = {}
MOD_LOAD_ERRORS: dict[str, str] = {}
BUILTIN_MODULE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,63}$")
MOD_SOURCE_MAX_BYTES = 192 * 1024


def _normalize_mod_permissions(raw_permissions, *, legacy_if_missing: bool = False) -> tuple[str, ...]:
    if raw_permissions is None:
        values = MOD_PERMISSIONS if legacy_if_missing else MOD_DEFAULT_PERMISSIONS
    elif isinstance(raw_permissions, str):
        values = re.split(r"[,，\s]+", raw_permissions)
    elif isinstance(raw_permissions, (list, tuple, set, frozenset)):
        values = raw_permissions
    else:
        raise ValueError("MOD permissions 必须是字符串列表。")
    normalized = {str(value or "").strip().casefold() for value in values}
    normalized.discard("")
    unknown = normalized - MOD_PERMISSIONS
    if unknown:
        raise ValueError("MOD 声明了未知权限：" + "、".join(sorted(unknown)))
    return tuple(sorted(normalized))


def _mod_source_sensitive_permissions(source: str) -> set[str]:
    """Conservatively detect direct sensitive Python capabilities before execution."""
    ast_module = importlib.import_module("ast")
    tree = ast_module.parse(source)
    required: set[str] = set()
    module_permissions = {
        "socket": "network", "urllib": "network", "http": "network",
        "requests": "network", "ftplib": "network", "websockets": "network",
        "subprocess": "process", "ctypes": "process",
        "webbrowser": "browser", "selenium": "browser", "browser_bridge": "browser",
        "pathlib": "filesystem", "shutil": "filesystem", "glob": "filesystem",
        "tempfile": "filesystem",
    }
    os_file_calls = {
        "access", "chmod", "chown", "exists", "getsize", "isdir", "isfile", "link",
        "listdir", "lstat", "makedirs", "mkdir", "readlink", "remove", "removedirs",
        "rename", "renames", "replace", "rmdir", "scandir", "stat", "symlink",
        "unlink", "walk",
    }
    os_process_calls = {"popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
                        "spawnve", "spawnvp", "spawnvpe", "startfile", "system"}
    os_aliases = {"os"}

    def attribute_root(node) -> str:
        while isinstance(node, ast_module.Attribute):
            node = node.value
        return node.id if isinstance(node, ast_module.Name) else ""

    for node in ast_module.walk(tree):
        if isinstance(node, ast_module.Import):
            names = []
            for alias in node.names:
                top_name = alias.name.split(".", 1)[0]
                if top_name == "os":
                    os_aliases.add(str(alias.asname or top_name))
                else:
                    names.append(top_name)
        elif isinstance(node, ast_module.ImportFrom):
            imported_module = str(node.module or "")
            top_name = imported_module.split(".", 1)[0]
            names = [] if top_name == "os" else [top_name]
            if top_name == "os":
                if imported_module.startswith("os.path"):
                    required.add("filesystem")
                for alias in node.names:
                    imported_name = str(alias.name or "").casefold()
                    if imported_name in os_file_calls:
                        required.add("filesystem")
                    if imported_name in os_process_calls:
                        required.add("process")
        else:
            names = []
        for name in names:
            permission = module_permissions.get(name)
            if permission:
                required.add(permission)
        if not isinstance(node, ast_module.Call):
            continue
        func = node.func
        if isinstance(func, ast_module.Name) and func.id == "open":
            required.add("filesystem")
        elif isinstance(func, ast_module.Attribute):
            name = str(func.attr or "").casefold()
            if attribute_root(func) in os_aliases:
                if name in os_file_calls:
                    required.add("filesystem")
                if name in os_process_calls:
                    required.add("process")
    return required


def _normalize_module_aliases(raw_aliases) -> tuple[str, ...]:
    if isinstance(raw_aliases, str):
        raw_aliases = re.split(r"[,，\n]", raw_aliases)
    if not isinstance(raw_aliases, (list, tuple, set)):
        return ()
    aliases = []
    for value in raw_aliases:
        alias = str(value).strip()
        if alias and alias not in aliases:
            aliases.append(alias[:80])
    return tuple(aliases[:24])


def _normalize_builtin_module_manifest(raw: dict, module_dir: Path) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("manifest.json 必须是 JSON 对象。")
    module_id = str(raw.get("id") or "").strip()
    if not BUILTIN_MODULE_ID_RE.fullmatch(module_id):
        raise ValueError("模块 id 必须以英文字母开头，且只能包含字母、数字、点、下划线和连字符。")
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > 80:
        raise ValueError("模块 title 不能为空且不能超过 80 个字符。")
    try:
        api = int(raw.get("api", 1))
    except (TypeError, ValueError):
        api = 0
    if api != BUILTIN_MODULE_API_VERSION:
        raise ValueError(f"不支持的模块 API 版本：{api}；当前需要 {BUILTIN_MODULE_API_VERSION}。")
    entry = str(raw.get("entry") or "tool.py").replace("\\", "/").strip("/")
    entry_parts = PurePosixPath(entry).parts
    if not entry.endswith(".py") or not entry_parts or any(part in ("", ".", "..") for part in entry_parts):
        raise ValueError("模块 entry 必须是模块目录内的 Python 文件。")
    callable_name = str(raw.get("callable") or "open_tool").strip()
    if not callable_name.isidentifier():
        raise ValueError("模块 callable 必须是有效的 Python 标识符。")
    aliases = _normalize_module_aliases(raw.get("aliases", []))
    icon = str(raw.get("icon") or "").replace("\\", "/").strip("/")
    if icon and any(part in ("", ".", "..") for part in PurePosixPath(icon).parts):
        raise ValueError("模块 icon 必须位于模块目录内。")
    color = str(raw.get("color") or "#475569").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        color = "#475569"
    entry_path = module_dir.joinpath(*entry_parts)
    if module_dir.exists() and not entry_path.is_file():
        raise ValueError(f"找不到模块入口文件：{entry}")
    icon_path = module_dir.joinpath(*PurePosixPath(icon).parts) if icon else None
    return {
        "module_id": module_id,
        "api": api,
        "title": title,
        "version": str(raw.get("version") or "1.0").strip(),
        "entry": entry,
        "callable": callable_name,
        "aliases": aliases,
        "icon": icon,
        "icon_path": str(icon_path) if icon_path and icon_path.is_file() else "",
        "color": color,
        "module_dir": str(module_dir),
        "target": f"passer-module://{module_id}",
        "description": str(raw.get("description") or "").strip()[:500],
        "source_type": "builtin",
        "enabled": True,
        "manifest": dict(raw),
    }


def _normalize_mod_manifest(raw: dict, module_dir: Path) -> dict:
    """Validate an external AI/user MOD manifest without executing its code."""
    if not isinstance(raw, dict):
        raise ValueError("manifest.json 必须是 JSON 对象。")
    adapted = dict(raw)
    adapted["title"] = str(raw.get("title") or raw.get("name") or "").strip()
    adapted["entry"] = str(raw.get("entry") or "mod.py")
    adapted["callable"] = str(raw.get("callable") or "open_mod")
    info = _normalize_builtin_module_manifest(adapted, module_dir)
    if module_dir.exists() and not module_dir.name.startswith(".") and module_dir.name != info["module_id"]:
        raise ValueError("MOD 文件夹名称必须与 manifest 的 id 一致。")
    def manifest_bool(name: str, default: bool) -> bool:
        value = raw.get(name, default)
        if isinstance(value, str):
            return value.strip().casefold() not in ("0", "false", "no", "off", "disabled")
        return bool(value)

    setup_callable = str(raw.get("setup") or "setup_mod").strip()
    if not setup_callable.isidentifier():
        raise ValueError("MOD setup 必须是有效的 Python 标识符。")
    permissions_declared = "permissions" in raw
    permissions = _normalize_mod_permissions(
        raw.get("permissions"), legacy_if_missing=not permissions_declared
    )
    entry_path = Path(info["module_dir"]).joinpath(*PurePosixPath(info["entry"]).parts)
    if entry_path.is_file():
        source = entry_path.read_text(encoding="utf-8-sig")
        required = _mod_source_sensitive_permissions(source)
        missing = required - set(permissions)
        if missing:
            raise ValueError("MOD 源码使用了未声明权限：" + "、".join(sorted(missing)))
    normalized_manifest = dict(raw)
    normalized_manifest["permissions"] = list(permissions)
    info.update({
        "target": f"passer-mod://{info['module_id']}",
        "source_type": "mod",
        "enabled": manifest_bool("enabled", True),
        "startup": manifest_bool("startup", True),
        "expose_tool": manifest_bool("expose_tool", True),
        "setup": setup_callable,
        "description": str(raw.get("description") or "").strip()[:500],
        "permissions": permissions,
        "permissions_declared": permissions_declared,
        "manifest": normalized_manifest,
    })
    return info


def _module_tool_spec(info: dict, detail: str) -> dict:
    prefix = "mod" if info.get("source_type") == "mod" else "passer_module"
    return {
        "id": f"__{prefix}_{info['module_id']}__",
        "target": info["target"],
        "title": info["title"],
        "detail": detail,
        "aliases": info["aliases"],
        "icon_path": info["icon_path"],
        "module_id": info["module_id"],
        "description": info.get("description", ""),
        "source_type": info.get("source_type", "builtin"),
    }


def reload_installed_builtin_modules() -> None:
    global BUILTIN_TOOLS
    INSTALLED_BUILTIN_MODULES.clear()
    INSTALLED_MODS.clear()
    MOD_LOAD_ERRORS.clear()
    specs = []
    if BUILTIN_MODULE_DIR.exists():
        for module_dir in sorted(BUILTIN_MODULE_DIR.iterdir(), key=lambda item: item.name.casefold()):
            manifest_path = module_dir / "manifest.json"
            if not module_dir.is_dir() or not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                info = _normalize_builtin_module_manifest(manifest, module_dir)
            except Exception:
                continue
            tool = _module_tool_spec(info, "内置工具")
            specs.append(tool)
            INSTALLED_BUILTIN_MODULES[info["target"]] = info
            BUILTIN_TOOL_ICON_STYLE[info["target"]] = ("tool", info["color"])
    if MOD_DIR.exists():
        for module_dir in sorted(MOD_DIR.iterdir(), key=lambda item: item.name.casefold()):
            manifest_path = module_dir / "manifest.json"
            if not module_dir.is_dir() or not manifest_path.is_file() or module_dir.name.startswith("."):
                continue
            if module_dir.is_symlink():
                MOD_LOAD_ERRORS[module_dir.name] = "MOD 目录不能是符号链接。"
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                info = _normalize_mod_manifest(manifest, module_dir)
            except Exception as exc:
                MOD_LOAD_ERRORS[module_dir.name] = str(exc)
                continue
            INSTALLED_MODS[info["module_id"]] = info
            if not info["enabled"] or not info.get("expose_tool", True):
                continue
            tool = _module_tool_spec(info, "AI MOD")
            specs.append(tool)
            INSTALLED_BUILTIN_MODULES[info["target"]] = info
            BUILTIN_TOOL_ICON_STYLE[info["target"]] = ("tool", info["color"])
    BUILTIN_TOOLS = CORE_BUILTIN_TOOLS + tuple(specs)
    BUILTIN_TOOL_BY_TARGET.clear()
    BUILTIN_TOOL_BY_TARGET.update({str(tool["target"]): tool for tool in BUILTIN_TOOLS})


def _atomic_write_mod_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_mod_source(mod_id: str, source: str) -> dict[str, object]:
    encoded = source.encode("utf-8")
    if not source.strip():
        raise ValueError("MOD 代码不能为空。")
    if len(encoded) > MOD_SOURCE_MAX_BYTES:
        raise ValueError(f"MOD 代码过大（上限 {MOD_SOURCE_MAX_BYTES} 字节）。")
    try:
        compile(source, f"<mod:{mod_id}>", "exec")
    except SyntaxError as exc:
        raise ValueError(f"MOD 代码语法错误：{exc}") from exc
    entries = {
        "open_mod": bool(re.search(r"^\s*def\s+open_mod\s*\(", source, re.MULTILINE)),
        "setup_mod": bool(re.search(r"^\s*def\s+setup_mod\s*\(", source, re.MULTILINE)),
    }
    if not any(entries.values()):
        raise ValueError("MOD 必须至少定义 `open_mod(context)` 或 `setup_mod(context)`。")
    entries["sensitive_permissions"] = _mod_source_sensitive_permissions(source)
    return entries


def write_mod(mod_id: str, title: str, code: str, *, description: str = "",
              aliases=None, version: str = "1.0.0", color: str = "#7c3aed",
              enabled: bool = True, overwrite: bool = False, permissions=None) -> dict:
    """Create/update an external MOD while preserving its persistent data folder."""
    mod_id = str(mod_id or "").strip()
    if not BUILTIN_MODULE_ID_RE.fullmatch(mod_id):
        raise ValueError("MOD id 必须以英文字母开头，只能包含字母、数字、点、下划线和连字符。")
    title = str(title or "").strip()
    if not title or len(title) > 80:
        raise ValueError("MOD title 不能为空且不能超过 80 个字符。")
    source = str(code or "")
    entries = _validate_mod_source(mod_id, source)
    permission_input = permissions
    if permission_input is None and overwrite:
        existing_manifest_path = MOD_DIR / mod_id / "manifest.json"
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            existing_manifest = {}
        if isinstance(existing_manifest, dict) and existing_manifest:
            permission_input = (
                existing_manifest.get("permissions")
                if "permissions" in existing_manifest
                else MOD_PERMISSIONS
            )
    normalized_permissions = _normalize_mod_permissions(permission_input)
    missing_permissions = set(entries["sensitive_permissions"]) - set(normalized_permissions)
    if missing_permissions:
        raise PermissionError(
            "MOD 源码使用了未声明权限：" + "、".join(sorted(missing_permissions))
        )
    normalized_aliases = _normalize_module_aliases(aliases or [])
    manifest = {
        "id": mod_id,
        "title": title,
        "version": str(version or "1.0.0").strip()[:40],
        "api": BUILTIN_MODULE_API_VERSION,
        "entry": "mod.py",
        "callable": "open_mod",
        "setup": "setup_mod",
        "startup": True,
        "expose_tool": entries["open_mod"],
        "description": str(description or "").strip()[:500],
        "aliases": list(normalized_aliases),
        "color": str(color or "#7c3aed").strip(),
        "enabled": bool(enabled),
        "permissions": list(normalized_permissions),
    }
    _normalize_mod_manifest(manifest, MOD_DIR / ".preview")
    MOD_DIR.mkdir(parents=True, exist_ok=True)
    target_dir = MOD_DIR / mod_id
    if target_dir.exists() and not overwrite:
        raise FileExistsError(mod_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_mod_file(target_dir / "mod.py", source.rstrip() + "\n")
    _atomic_write_mod_file(
        target_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    (target_dir / "data").mkdir(parents=True, exist_ok=True)
    reload_installed_builtin_modules()
    return dict(INSTALLED_MODS[mod_id])


def list_installed_mods(refresh: bool = True) -> list[dict]:
    """Return all valid and invalid MODs without importing executable code."""
    if refresh:
        reload_installed_builtin_modules()
    records = []
    for mod_id, info in sorted(INSTALLED_MODS.items(), key=lambda item: item[0].casefold()):
        records.append({
            "id": mod_id,
            "title": info["title"],
            "version": info["version"],
            "description": info.get("description", ""),
            "enabled": bool(info["enabled"]),
            "path": info["module_dir"],
            "target": info["target"],
            "startup": bool(info.get("startup", True)),
            "expose_tool": bool(info.get("expose_tool", True)),
            "permissions": list(info.get("permissions") or ()),
            "permissions_declared": bool(info.get("permissions_declared", False)),
            "error": "",
        })
    for folder, error in sorted(MOD_LOAD_ERRORS.items(), key=lambda item: item[0].casefold()):
        records.append({
            "id": folder, "title": folder, "version": "", "description": "",
            "enabled": False, "path": str(MOD_DIR / folder), "target": "", "error": error,
        })
    return records


def set_mod_enabled(mod_id: str, enabled: bool) -> dict:
    mod_id = str(mod_id or "").strip()
    if not BUILTIN_MODULE_ID_RE.fullmatch(mod_id):
        raise ValueError("无效的 MOD id。")
    manifest_path = MOD_DIR / mod_id / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"MOD 不存在：{mod_id}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    raw["enabled"] = bool(enabled)
    _normalize_mod_manifest(raw, manifest_path.parent)
    _atomic_write_mod_file(manifest_path, json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
    reload_installed_builtin_modules()
    return dict(INSTALLED_MODS[mod_id])


def delete_mod(mod_id: str) -> str:
    mod_id = str(mod_id or "").strip()
    if not BUILTIN_MODULE_ID_RE.fullmatch(mod_id):
        raise ValueError("无效的 MOD id。")
    target_dir = MOD_DIR / mod_id
    if not target_dir.is_dir():
        raise ValueError(f"MOD 不存在：{mod_id}")
    shutil.rmtree(target_dir)
    reload_installed_builtin_modules()
    return mod_id


def install_builtin_module_archive(archive_path: str | os.PathLike, overwrite: bool = False) -> dict:
    archive = Path(archive_path)
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise ValueError("请选择有效的内置工具 ZIP 文件。")
    BUILTIN_MODULE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as bundle:
        infos = [info for info in bundle.infolist() if not info.is_dir()]
        if not infos or sum(max(0, info.file_size) for info in infos) > 100 * 1024 * 1024:
            raise ValueError("模块压缩包为空或解压后超过 100 MB。")
        manifest_infos = [
            info for info in infos
            if PurePosixPath(info.filename.replace("\\", "/")).name.casefold() == "manifest.json"
        ]
        if not manifest_infos:
            raise ValueError("压缩包中缺少 manifest.json。")
        manifest_info = min(manifest_infos, key=lambda info: len(PurePosixPath(info.filename).parts))
        manifest_path = PurePosixPath(manifest_info.filename.replace("\\", "/"))
        prefix = manifest_path.parent
        try:
            raw = json.loads(bundle.read(manifest_info).decode("utf-8-sig"))
        except Exception as exc:
            raise ValueError(f"manifest.json 无法读取：{exc}") from exc
        preview_dir = BUILTIN_MODULE_DIR / "_preview_"
        normalized = _normalize_builtin_module_manifest(raw, preview_dir)
        module_id = normalized["module_id"]
        target_dir = BUILTIN_MODULE_DIR / module_id
        if target_dir.exists() and not overwrite:
            raise FileExistsError(module_id)
        stage = BUILTIN_MODULE_DIR / f".install-{module_id}-{uuid.uuid4().hex}"
        stage.mkdir(parents=True, exist_ok=False)
        try:
            for info in infos:
                source = PurePosixPath(info.filename.replace("\\", "/"))
                try:
                    relative = source.relative_to(prefix)
                except ValueError:
                    continue
                if not relative.parts or any(part in ("", ".", "..") or ":" in part for part in relative.parts):
                    raise ValueError("压缩包包含不安全的文件路径。")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("模块压缩包不能包含符号链接。")
                destination = stage.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source_file, destination.open("wb") as output:
                    shutil.copyfileobj(source_file, output)
            final_manifest = stage / "manifest.json"
            final_manifest.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            _normalize_builtin_module_manifest(raw, stage)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            stage.replace(target_dir)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    reload_installed_builtin_modules()
    return INSTALLED_BUILTIN_MODULES[f"passer-module://{module_id}"]


def builtin_tool_style(target: str) -> tuple[str, str]:
    return BUILTIN_TOOL_ICON_STYLE.get(target, ("tool", "#475569"))


def builtin_tool_icon_image(target: str, size: int = 44) -> "Image.Image | None":
    if not PIL_AVAILABLE:
        return None
    tool = BUILTIN_TOOL_BY_TARGET.get(target) or {}
    icon_path = str(tool.get("icon_path") or "")
    if icon_path:
        try:
            with Image.open(icon_path) as source:
                icon = source.convert("RGBA")
                resampling = getattr(Image, "Resampling", None)
                resample = resampling.LANCZOS if resampling is not None else Image.LANCZOS
                icon.thumbnail((size, size), resample)
                canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                canvas.alpha_composite(icon, ((size - icon.width) // 2, (size - icon.height) // 2))
                return canvas
        except Exception:
            pass
    kind, color = builtin_tool_style(target)
    scale = 3
    s = max(24, int(size))
    img = Image.new("RGBA", (s * scale, s * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def xy(*vals):
        return tuple(int(round(v * s / 44 * scale)) for v in vals)

    def line(points, width=2, joint="curve"):
        d.line([xy(x, y) for x, y in points], fill="white", width=max(1, int(width * scale)), joint=joint)

    def rect(box, width=2, radius=0, fill=None):
        b = xy(*box)
        if radius:
            d.rounded_rectangle(b, radius=int(radius * s / 44 * scale), outline="white", width=max(1, int(width * scale)), fill=fill)
        else:
            d.rectangle(b, outline="white", width=max(1, int(width * scale)), fill=fill)

    def ellipse(box, width=2, fill=None):
        d.ellipse(xy(*box), outline="white", width=max(1, int(width * scale)), fill=fill)

    def arc(box, start, end, width=2):
        d.arc(xy(*box), start=start, end=end, fill="white", width=max(1, int(width * scale)))

    d.rounded_rectangle(xy(2, 2, 42, 42), radius=int(10 * s / 44 * scale), fill=color)

    if kind == "clicker":
        ellipse((15, 10, 29, 34), 2)
        line([(22, 10), (22, 19)], 2)
        line([(29, 13), (34, 9)], 2)
        line([(31, 19), (37, 19)], 2)
    elif kind == "dice":
        rect((12, 12, 32, 32), 2, 5)
        for box in [(16, 16, 18, 18), (26, 16, 28, 18), (21, 21, 23, 23), (16, 26, 18, 28), (26, 26, 28, 28)]:
            ellipse(box, 1, fill="white")
    elif kind == "clock":
        ellipse((11, 11, 33, 33), 2)
        line([(22, 22), (22, 15), (27, 24)], 2)
        arc((13, 5, 21, 13), 200, 340, 2)
        arc((23, 5, 31, 13), 200, 340, 2)
    elif kind == "aira":
        line([(14, 32), (22, 11), (30, 32)], 3)
        line([(18, 24), (27, 24)], 2)
        line([(33, 9), (33, 17)], 2)
        line([(29, 13), (37, 13)], 2)
    elif kind == "calendar":
        rect((10, 12, 34, 34), 2, 4)
        line([(10, 18), (34, 18)], 2)
        line([(16, 9), (16, 14)], 2)
        line([(28, 9), (28, 14)], 2)
        for x in (16, 22, 28):
            line([(x, 24), (x + 1, 24)], 2)
            line([(x, 29), (x + 1, 29)], 2)
    elif kind == "calculator":
        rect((12, 9, 32, 35), 2, 4)
        rect((16, 13, 28, 17), 1, 1)
        for x in (17, 22, 27):
            for y in (23, 29):
                ellipse((x - 1, y - 1, x + 1, y + 1), 1, fill="white")
    elif kind == "power":
        line([(22, 10), (22, 23)], 3)
        arc((11, 13, 33, 35), 120, 420, 3)
        line([(15, 16), (18, 20)], 2)
        line([(29, 16), (26, 20)], 2)
    elif kind == "network":
        ellipse((9, 19, 15, 25), 2, fill="white")
        ellipse((29, 10, 35, 16), 2, fill="white")
        ellipse((29, 28, 35, 34), 2, fill="white")
        line([(15, 22), (29, 13)], 2)
        line([(15, 22), (29, 31)], 2)
    elif kind == "clipboard":
        rect((11, 12, 33, 35), 2, 4)
        rect((16, 9, 28, 15), 2, 3, fill=color)
        line([(16, 21), (28, 21)], 2)
        line([(16, 26), (28, 26)], 2)
        line([(16, 31), (24, 31)], 2)
    elif kind == "mail":
        rect((9, 13, 35, 31), 2, 4)
        line([(10, 15), (22, 24), (34, 15)], 2)
        line([(10, 30), (18, 22)], 2)
        line([(34, 30), (26, 22)], 2)
    elif kind == "qr":
        for box in [(10, 10, 18, 18), (26, 10, 34, 18), (10, 26, 18, 34)]:
            rect(box, 2, 1)
        rect((26, 26, 30, 30), 2)
        line([(33, 26), (33, 34), (25, 34)], 2)
    elif kind == "document":
        rect((13, 9, 31, 35), 2, 3)
        line([(19, 17), (27, 17)], 2)
        line([(17, 23), (27, 23)], 2)
        line([(17, 28), (24, 28)], 2)
    elif kind == "search":
        rect((12, 9, 28, 32), 2, 3)
        ellipse((20, 19, 32, 31), 2)
        line([(29, 28), (35, 34)], 3)
    elif kind == "record":
        rect((10, 15, 27, 30), 2, 4)
        line([(27, 19), (35, 15), (35, 30), (27, 26), (27, 19)], 2)
        ellipse((16, 19, 24, 27), 2, fill="white")
    elif kind == "magnet":
        arc((10, 10, 34, 36), 180, 360, 5)
        line([(12, 23), (12, 32)], 5)
        line([(32, 23), (32, 32)], 5)
    elif kind == "pin":
        ellipse((14, 8, 30, 24), 2)
        line([(22, 24), (22, 36), (16, 28)], 2)
        line([(22, 36), (28, 28)], 2)
        ellipse((20, 14, 24, 18), 1, fill="white")
    elif kind == "lock":
        rect((12, 19, 32, 34), 2, 3)
        arc((15, 8, 29, 26), 180, 360, 3)
    elif kind == "share":
        for box in [(10, 18, 16, 24), (28, 10, 34, 16), (28, 28, 34, 34)]:
            ellipse(box, 2, fill="white")
        line([(16, 21), (28, 13)], 2)
        line([(16, 21), (28, 31)], 2)
    elif kind == "chip":
        rect((13, 13, 31, 31), 2, 3)
        rect((18, 18, 26, 26), 2, 1)
        for a, b in [
            ((16, 9), (16, 13)), ((22, 9), (22, 13)), ((28, 9), (28, 13)),
            ((16, 31), (16, 35)), ((22, 31), (22, 35)), ((28, 31), (28, 35)),
            ((9, 16), (13, 16)), ((9, 22), (13, 22)), ((9, 28), (13, 28)),
            ((31, 16), (35, 16)), ((31, 22), (35, 22)), ((31, 28), (35, 28)),
        ]:
            line([a, b], 2)
    elif kind == "phone":
        rect((15, 8, 29, 36), 2, 4)
        line([(19, 12), (25, 12)], 1)
        ellipse((21, 30, 23, 32), 1, fill="white")
        arc((9, 15, 35, 31), 210, 330, 2)
        arc((6, 11, 38, 35), 210, 330, 2)
    elif kind == "crop":
        line([(12, 20), (12, 12), (20, 12)], 3)
        line([(24, 12), (32, 12), (32, 20)], 3)
        line([(32, 24), (32, 32), (24, 32)], 3)
        line([(20, 32), (12, 32), (12, 24)], 3)
    elif kind == "pen":
        line([(14, 31), (29, 16)], 4)
        d.polygon([xy(29, 16), xy(33, 11), xy(35, 13), xy(31, 18)], fill="white")
        line([(12, 34), (20, 32)], 2)
    elif kind == "terminal":
        rect((9, 12, 35, 32), 2, 4)
        line([(14, 19), (18, 22), (14, 25)], 2)
        line([(21, 25), (28, 25)], 2)
    elif kind == "registry":
        rect((11, 12, 21, 22), 2, 2)
        rect((23, 12, 33, 22), 2, 2)
        rect((17, 24, 27, 34), 2, 2)
    elif kind == "bars":
        line([(12, 32), (32, 32)], 2)
        for box in [(14, 23, 18, 32), (21, 15, 25, 32), (28, 19, 32, 32)]:
            d.rounded_rectangle(xy(*box), radius=int(1.5 * scale), fill="white")
    elif kind == "folder":
        d.rounded_rectangle(xy(9, 16, 35, 33), radius=int(4 * scale), outline="white", width=int(2 * scale))
        line([(11, 16), (17, 11), (25, 11), (28, 16)], 2)
    elif kind == "gear":
        ellipse((16, 16, 28, 28), 2)
        ellipse((20, 20, 24, 24), 2, fill="white")
        for a, b in [((22, 9), (22, 14)), ((22, 30), (22, 35)), ((9, 22), (14, 22)), ((30, 22), (35, 22)), ((13, 13), (16, 16)), ((31, 13), (28, 16)), ((13, 31), (16, 28)), ((31, 31), (28, 28))]:
            line([a, b], 2)
    else:
        rect((13, 13, 31, 31), 2, 4)
        line([(22, 15), (22, 29)], 2)
        line([(15, 22), (29, 22)], 2)

    if scale != 1:
        resampling = getattr(Image, "Resampling", None)
        resample = resampling.LANCZOS if resampling is not None else Image.LANCZOS
        img = img.resize((s, s), resample)
    return img


reload_installed_builtin_modules()


def is_pinnable_builtin_item(item: DockItem) -> bool:
    """内置工具 / 系统工具 / MOD 才显示「添加到 Passer」的固定图标。"""
    if item.kind != BUILTIN_TOOL_KIND:
        return False
    tool = BUILTIN_TOOL_BY_TARGET.get(item.target)
    return bool(tool) and tool.get("detail") in ("内置工具", "系统工具", "AI MOD")


def builtin_tool_item(tool: dict) -> DockItem:
    return DockItem(
        str(tool["id"]),
        BUILTIN_TOOL_KIND,
        str(tool["target"]),
        str(tool["title"]),
        "",
    )


def builtin_tool_detail(item: DockItem) -> str:
    tool = BUILTIN_TOOL_BY_TARGET.get(item.target)
    return str(tool.get("detail", "内置工具")) if tool else "内置工具"


def ensure_dirs() -> None:
    migrate_legacy_storage()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    DRAG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    OFFICE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    AI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILTIN_MODULE_DIR.mkdir(parents=True, exist_ok=True)
    MOD_DIR.mkdir(parents=True, exist_ok=True)


def set_store_directory(path: str | os.PathLike) -> Path:
    """Switch the runtime storage folder used for newly created Passer files."""
    global STORE_DIR
    expanded = os.path.expandvars(os.path.expanduser(str(path).strip()))
    STORE_DIR = Path(expanded) if expanded else DEFAULT_STORE_DIR
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    return STORE_DIR


def office_preview_pdf_path(source: Path, office_kind: str) -> Path:
    stat = source.stat()
    fingerprint = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{office_kind}"
    digest = hashlib.sha1(fingerprint.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    safe_stem = sanitize_filename_piece(source.stem or office_kind, 48)
    return OFFICE_PREVIEW_DIR / f"{safe_stem}_{digest}.pdf"


# 转换出的 PDF/XLSX 预览都是可再生缓存，给整个目录设一个体积上限，避免长期使用后
# 无限膨胀。超出上限时按修改时间淘汰最旧的文件（被查看器占用的文件删除会失败，自动跳过）。
OFFICE_PREVIEW_CACHE_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MB


def prune_office_preview_cache(*, remove_orphan_temp: bool = False,
                               limit_bytes: int = OFFICE_PREVIEW_CACHE_LIMIT_BYTES) -> None:
    """约束可再生的 Office 预览缓存体积。

    ``remove_orphan_temp`` 仅应在启动时为真：此时没有正在进行的转换，残留的
    ``*.tmp.pdf`` / ``*.tmp.xlsx`` 必然是上次崩溃遗留的孤儿文件，可安全清除。
    随后在总体积超过 ``limit_bytes`` 时按最久未修改优先淘汰缓存文件。
    """
    try:
        children = list(OFFICE_PREVIEW_DIR.iterdir())
    except OSError:
        return
    cached: list[tuple[float, int, Path]] = []
    for child in children:
        name = child.name.lower()
        is_temp = name.endswith(".tmp.pdf") or name.endswith(".tmp.xlsx")
        if is_temp:
            if remove_orphan_temp:
                try:
                    child.unlink()
                except OSError:
                    pass
            continue
        try:
            if not child.is_file():
                continue
            stat = child.stat()
        except OSError:
            continue
        cached.append((stat.st_mtime, stat.st_size, child))

    total = sum(size for _mtime, size, _path in cached)
    if total <= limit_bytes:
        return
    for _mtime, size, path in sorted(cached, key=lambda entry: entry[0]):
        if total <= limit_bytes:
            break
        try:
            path.unlink()
        except OSError:
            # 被打开的查看器锁定或已被删除：保留计数，继续看下一个。
            continue
        total -= size


def _run_office_com_conversion(script: str, destination: Path,
                               env_extra: dict[str, str], fail_message: str) -> Path:
    """运行一段把 Office 文档转换为 ``destination`` 的 COM 自动化 PowerShell 脚本。

    脚本先写入一个唯一的临时同级文件（通过 ``PASSER_OFFICE_DESTINATION`` 传入），
    成功后再原子替换到 ``destination``，因此中途崩溃不会留下半成品缓存。转换成功
    后顺带修剪缓存目录，把体积控制在上限内。
    """
    temporary = destination.with_name(f"{destination.stem}_{uuid.uuid4().hex}.tmp{destination.suffix}")
    environment = os.environ.copy()
    environment.update(env_extra)
    environment["PASSER_OFFICE_DESTINATION"] = str(temporary)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
        if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size <= 0:
            detail = (result.stderr or result.stdout or fail_message).strip()
            raise RuntimeError(detail)
        temporary.replace(destination)
        prune_office_preview_cache()
        return destination
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def convert_office_to_pdf(source: Path, office_kind: str) -> Path:
    """Use an installed Microsoft Office application to create a cached preview PDF."""
    source = source.resolve()
    destination = office_preview_pdf_path(source, office_kind)
    if destination.exists() and destination.stat().st_size > 0:
        return destination

    script = r"""
$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($env:PASSER_OFFICE_SOURCE)
$destination = [IO.Path]::GetFullPath($env:PASSER_OFFICE_DESTINATION)
$kind = $env:PASSER_OFFICE_KIND
$application = $null
$document = $null
try {
    if ($kind -eq 'word') {
        $application = New-Object -ComObject Word.Application
        $application.Visible = $false
        $application.DisplayAlerts = 0
        $document = $application.Documents.Open($source, $false, $true)
        $document.ExportAsFixedFormat($destination, 17)
    } elseif ($kind -eq 'powerpoint') {
        $application = New-Object -ComObject PowerPoint.Application
        $document = $application.Presentations.Open($source, $true, $false, $false)
        $document.SaveAs($destination, 32)
    } else {
        throw "Unsupported Office preview kind: $kind"
    }
} finally {
    if ($null -ne $document) {
        try { $document.Close() } catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) } catch {}
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    return _run_office_com_conversion(
        script,
        destination,
        {
            "PASSER_OFFICE_SOURCE": str(source),
            "PASSER_OFFICE_KIND": office_kind,
        },
        "Office 未生成预览文件。",
    )


def convert_excel_to_xlsx(source: Path) -> Path:
    source = source.resolve()
    stat = source.stat()
    fingerprint = f"{source}|{stat.st_mtime_ns}|{stat.st_size}|excel"
    digest = hashlib.sha1(fingerprint.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    safe_stem = sanitize_filename_piece(source.stem or "excel", 48)
    destination = OFFICE_PREVIEW_DIR / f"{safe_stem}_{digest}.xlsx"
    if destination.exists() and destination.stat().st_size > 0:
        return destination

    script = r"""
$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($env:PASSER_OFFICE_SOURCE)
$destination = [IO.Path]::GetFullPath($env:PASSER_OFFICE_DESTINATION)
$application = $null
$workbook = $null
try {
    $application = New-Object -ComObject Excel.Application
    $application.Visible = $false
    $application.DisplayAlerts = $false
    $workbook = $application.Workbooks.Open($source, 0, $true)
    $workbook.SaveAs($destination, 51)
} finally {
    if ($null -ne $workbook) {
        try { $workbook.Close($false) } catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook) } catch {}
    }
    if ($null -ne $application) {
        try { $application.Quit() } catch {}
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application) } catch {}
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    return _run_office_com_conversion(
        script,
        destination,
        {"PASSER_OFFICE_SOURCE": str(source)},
        "Excel 未生成预览文件。",
    )


def merge_directory(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if destination.exists():
            if child.is_dir() and destination.is_dir():
                merge_directory(child, destination)
                try:
                    child.rmdir()
                except OSError:
                    pass
            continue
        child.rename(destination)
    try:
        source.rmdir()
    except OSError:
        pass


def migrate_legacy_storage() -> None:
    if LEGACY_DATA_DIR.exists() and not DATA_DIR.exists():
        LEGACY_DATA_DIR.rename(DATA_DIR)
    elif LEGACY_DATA_DIR.exists() and DATA_DIR.exists():
        merge_directory(LEGACY_DATA_DIR, DATA_DIR)

    renamed_legacy_store = DATA_DIR / "存入文件"
    if renamed_legacy_store.exists() and not STORE_DIR.exists():
        renamed_legacy_store.rename(STORE_DIR)
    elif renamed_legacy_store.exists() and STORE_DIR.exists():
        merge_directory(renamed_legacy_store, STORE_DIR)


def migrate_target_path(target: str) -> str:
    replacements = (
        (LEGACY_STORE_DIR, STORE_DIR),
        (LEGACY_DATA_DIR, DATA_DIR),
        (DATA_DIR / "存入文件", STORE_DIR),
    )
    result = str(target)
    for old, new in replacements:
        old_text = str(old)
        if result.lower().startswith(old_text.lower()):
            return str(new) + result[len(old_text) :]
    return result


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """解析计划/提醒时间字符串，返回一个未来时间点；无法解析时返回 None。

    支持的格式（也接受用 / 作日期分隔符）：
      * ``2026-06-23 09:00[:00]`` —— 完整日期时间，原样返回。
      * ``06-23 09:00``           —— 省略年份，补当年；若已过则顺延到下一年。
      * ``14:30[:00]``            —— 仅时间，补今天；若已过则顺延到明天。
    （此前由 alarm_tool.AlarmWindow.parse_when 提供，模块移除后内置于此。）
    """
    if now is None:
        now = datetime.now()
    raw = (text or "").strip().replace("/", "-")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    for fmt in ("%m-%d %H:%M",):
        try:
            parsed = datetime.strptime(raw, fmt).replace(year=now.year)
        except ValueError:
            continue
        if parsed <= now:
            parsed = parsed.replace(year=now.year + 1)
        return parsed
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        parsed = now.replace(
            hour=clock.hour, minute=clock.minute, second=clock.second, microsecond=0
        )
        if parsed <= now:
            parsed = parsed + timedelta(days=1)
        return parsed
    return None


def read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def _quarantine_invalid_settings(reason: str) -> Path | None:
    """Move an unreadable settings file aside so the next startup can recover."""
    if not SETTINGS_FILE.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = SETTINGS_FILE.with_name(f"settings.broken_{timestamp}.json")
    serial = 2
    while candidate.exists():
        candidate = SETTINGS_FILE.with_name(f"settings.broken_{timestamp}_{serial}.json")
        serial += 1
    try:
        os.replace(SETTINGS_FILE, candidate)
    except OSError:
        return None
    try:
        write_crash_log(
            ValueError, ValueError(reason), None, "settings-recovery",
            module="settings", action="recover_corrupt_settings", target_path=SETTINGS_FILE,
        )
    except Exception:
        pass
    return candidate


def _read_settings_payload() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _quarantine_invalid_settings(f"settings.json 无法解析：{exc}")
        return {}
    if not isinstance(raw, dict):
        _quarantine_invalid_settings("settings.json 顶层必须是 JSON 对象")
        return {}
    try:
        version = int(raw.get("schema_version") or 0)
    except (TypeError, ValueError):
        version = 0
    # 目前旧版本字段均可直接兼容；保留显式版本入口，便于后续逐版迁移。
    raw["schema_version"] = max(0, min(version, SETTINGS_SCHEMA_VERSION))
    return raw


def _coerce_int(value, default: int, minimum: int | None = None,
                maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_optional_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_float(value, default: float, minimum: float | None = None,
                  maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        result = float(default)
    if not math.isfinite(result):
        result = float(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def load_plans() -> list[dict]:
    """读取持久化的计划提醒（即使 Passer 重启，到点/逾期仍会在下次打开时提醒）。"""
    raw = read_json(PLANS_FILE, [])
    plans: list[dict] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                when = datetime.fromisoformat(str(entry.get("when")))
            except (TypeError, ValueError):
                continue
            notify = "windows" if str(entry.get("notify")) == "windows" else "passer"
            plan = {"when": when, "event": str(entry.get("event") or "（无事件说明）"),
                    "notify": notify}
            attachments = []
            raw_atts = entry.get("attachments")
            if isinstance(raw_atts, list):
                for att in raw_atts:
                    if not isinstance(att, dict):
                        continue
                    kind = str(att.get("kind") or "")
                    value = str(att.get("value") or "")
                    if kind in ("place", "file", "folder", "task") and value:
                        name = str(att.get("name") or value)
                        attachments.append({"kind": kind, "value": value, "name": name})
            else:  # 兼容旧版单条 place / path 字段
                if entry.get("place"):
                    attachments.append({"kind": "place", "value": str(entry["place"]),
                                        "name": str(entry["place"])})
                if entry.get("path"):
                    p = str(entry["path"])
                    attachments.append({"kind": "file", "value": p,
                                        "name": os.path.basename(p) or p})
            if attachments:
                plan["attachments"] = attachments
            plans.append(plan)
    plans.sort(key=lambda plan: plan["when"])
    return plans


def save_plans(plans: list[dict]) -> None:
    data = []
    for plan in plans:
        entry = {"when": plan["when"].isoformat(), "event": plan["event"],
                 "notify": ("windows" if plan.get("notify") == "windows" else "passer")}
        atts = plan.get("attachments")
        if isinstance(atts, list) and atts:
            entry["attachments"] = [
                {"kind": str(a.get("kind")), "value": str(a.get("value")),
                 "name": str(a.get("name") or a.get("value"))}
                for a in atts if isinstance(a, dict)
            ]
        data.append(entry)
    write_json(PLANS_FILE, data)


def load_automations() -> list[dict]:
    """读取持久化的 Aira 自动化任务（到点由 Passer 后台自动调用 Aira 执行）。"""
    raw = read_json(AUTOMATIONS_FILE, [])
    tasks: list[dict] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("id") and entry.get("prompt"):
                tasks.append({
                    key: value
                    for key, value in entry.items()
                    if not str(key).startswith("_")
                })
    return tasks


def save_automations(tasks: list[dict]) -> None:
    persistent = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        persistent.append({
            key: value
            for key, value in task.items()
            if not str(key).startswith("_")
        })
    write_json(AUTOMATIONS_FILE, persistent)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_json_transaction(values: dict[Path, object]) -> None:
    """Atomically replace a group of JSON files, rolling every file back on failure."""
    if not values:
        return
    transaction_id = uuid.uuid4().hex
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    installed: set[Path] = set()
    try:
        # Serialize and fsync every candidate before touching any live file.
        for raw_path, value in values.items():
            path = Path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            stage = path.with_name(f".{path.name}.{transaction_id}.stage")
            payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            with stage.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged[path] = stage

        # Move existing files aside. They remain available until all installs succeed.
        for path in staged:
            if path.exists():
                backup = path.with_name(f".{path.name}.{transaction_id}.backup")
                os.replace(path, backup)
                backups[path] = backup

        for path, stage in staged.items():
            os.replace(stage, path)
            installed.add(path)

        for backup in backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                # The transaction is already committed; a stale hidden backup
                # is safer than rolling a successful multi-file install back.
                pass
    except Exception:
        # Remove any newly installed subset, then restore every original file.
        for path in installed:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for path, backup in backups.items():
            try:
                if backup.exists():
                    os.replace(backup, path)
            except OSError:
                pass
        raise
    finally:
        for stage in staged.values():
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
        for path, backup in backups.items():
            if backup.exists() and path.exists():
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass


def load_ai_keys(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    keys: dict[str, str] = {}
    for provider, stored in value.items():
        if not isinstance(provider, str) or not isinstance(stored, (str, int)):
            continue
        secret = str(stored).strip()
        if not secret:
            continue
        if secret.startswith("dpapi:"):
            secret = unprotect_password(secret)
        if secret:
            keys[provider] = secret
    return keys


def load_ai_models(value) -> dict[str, str]:
    """每个服务商当前选用的模型（provider -> model id）；非字符串项忽略。"""
    if not isinstance(value, dict):
        return {}
    models: dict[str, str] = {}
    for provider, model in value.items():
        if isinstance(provider, str) and isinstance(model, str) and model.strip():
            models[provider] = model.strip()
    return models


def normalize_ai_thinking_mode(value) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"auto", "enabled", "disabled"} else "auto"


def normalize_ai_reasoning(value) -> str:
    level = str(value or "auto").strip().lower()
    if level == "off":
        level = "auto"
    return level if level in {"auto", "low", "medium", "high", "max"} else "auto"


def protect_ai_keys(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    protected_keys: dict[str, str] = {}
    for provider, clear_value in value.items():
        if not isinstance(provider, str) or not isinstance(clear_value, (str, int)):
            continue
        secret = str(clear_value).strip()
        if not secret:
            continue
        if secret.startswith("dpapi:") and unprotect_password(secret):
            protected_keys[provider] = secret
            continue
        protected = protect_password(secret)
        if protected:
            protected_keys[provider] = protected
    return protected_keys


def normalize_kind(kind: str | None) -> str:
    value = (kind or "file").strip().lower()
    mapping = {
        "url": "url",
        "file": "file",
        "folder": "folder",
        "image": "image",
        "text": "text",
        "group": "group",
        BUILTIN_TOOL_KIND: BUILTIN_TOOL_KIND,
    }
    return mapping.get(value, "file")


def normalize_grid_value(value) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def normalize_item(raw) -> DockItem | None:
    if not isinstance(raw, dict):
        return None
    kind = normalize_kind(raw.get("kind") or raw.get("Kind"))
    target = raw.get("target") or raw.get("Target")
    if not target:
        # Group items are containers and carry no real target; everything else
        # needs one to be useful.
        if kind != "group":
            return None
        target = ""
    else:
        target = migrate_target_path(str(target))
    title = raw.get("title") or raw.get("Title") or title_for(kind, target)
    item_id = raw.get("id") or raw.get("Id") or uuid.uuid4().hex
    added_at = raw.get("added_at") or raw.get("AddedAt") or datetime.now().isoformat()
    grid_x_raw = raw["grid_x"] if "grid_x" in raw else raw.get("GridX")
    grid_y_raw = raw["grid_y"] if "grid_y" in raw else raw.get("GridY")
    grid_x = normalize_grid_value(grid_x_raw)
    grid_y = normalize_grid_value(grid_y_raw)
    passer_name = raw.get("passer_name") if "passer_name" in raw else raw.get("PasserName")
    if passer_name is not None:
        passer_name = str(passer_name).strip() or None
    group_id = raw.get("group_id") if "group_id" in raw else raw.get("GroupId")
    if group_id is not None:
        group_id = str(group_id).strip() or None
    reminder_at = raw.get("reminder_at") if "reminder_at" in raw else raw.get("ReminderAt")
    if reminder_at is not None:
        reminder_at = str(reminder_at).strip() or None
    mark_color = raw.get("mark_color") if "mark_color" in raw else raw.get("MarkColor")
    mark_color = str(mark_color).strip().lower() if mark_color is not None else None
    if mark_color not in ITEM_MARK_PALETTE:
        mark_color = None
    group_order_raw = raw["group_order"] if "group_order" in raw else raw.get("GroupOrder")
    group_order = normalize_grid_value(group_order_raw)
    return DockItem(
        str(item_id), kind, target, str(title), str(added_at),
        grid_x, grid_y, passer_name, group_id, reminder_at, mark_color, group_order,
    )


def load_items() -> list[DockItem]:
    raw_items = read_json(ITEMS_FILE, None)
    legacy = Path(os.environ.get("APPDATA", "")) / "RelayDock" / "items.json"
    if raw_items is None and legacy.exists():
        raw_items = read_json(legacy, [])
    if raw_items is None:
        raw_items = []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    result: list[DockItem] = []
    removed_stale_tool = False
    for raw in raw_items:
        item = normalize_item(raw)
        if not item:
            continue
        if item.kind == BUILTIN_TOOL_KIND and item.target in REMOVED_BUILTIN_TARGETS:
            removed_stale_tool = True
            continue
        result.append(item)
    if removed_stale_tool:
        write_json(ITEMS_FILE, [asdict(item) for item in result])
    return result


def save_items(items: list[DockItem]) -> None:
    write_json(ITEMS_FILE, [asdict(item) for item in items])


HISTORY_NAMING_TIME = "time"
HISTORY_NAMING_VERSION = "version"
HISTORY_NAMING_CUSTOM = "custom"
HISTORY_NAMING_LABELS = {
    HISTORY_NAMING_TIME: "时间",
    HISTORY_NAMING_VERSION: "版本号",
    HISTORY_NAMING_CUSTOM: "自定义",
}
HISTORY_NAMING_BY_LABEL = {label: key for key, label in HISTORY_NAMING_LABELS.items()}
HISTORY_NAMING_OPTIONS = tuple(HISTORY_NAMING_BY_LABEL)
DEFAULT_HISTORY_NAMING_PATTERN = "{stem}_{datetime}_v{version}{ext}"
HISTORY_PATTERN_FIELDS = frozenset({"name", "stem", "ext", "date", "time", "datetime", "version"})
HISTORY_SCHEMA_VERSION = 1


def normalize_history_naming_mode(value) -> str:
    raw = str(value or "").strip()
    if raw in HISTORY_NAMING_LABELS:
        return raw
    if raw in HISTORY_NAMING_BY_LABEL:
        return HISTORY_NAMING_BY_LABEL[raw]
    aliases = {
        "date": HISTORY_NAMING_TIME,
        "datetime": HISTORY_NAMING_TIME,
        "timestamp": HISTORY_NAMING_TIME,
        "时间戳": HISTORY_NAMING_TIME,
        "number": HISTORY_NAMING_VERSION,
        "version_number": HISTORY_NAMING_VERSION,
        "版本": HISTORY_NAMING_VERSION,
        "自定义规则": HISTORY_NAMING_CUSTOM,
    }
    return aliases.get(raw.casefold(), HISTORY_NAMING_TIME)


def normalize_history_naming_pattern(value) -> str:
    pattern = str(value or "").strip()
    return pattern[:160] or DEFAULT_HISTORY_NAMING_PATTERN


def _history_pattern_fields(pattern: str) -> set[str]:
    fields = set(re.findall(r"\{([^{}]+)\}", pattern))
    stripped = re.sub(r"\{[^{}]+\}", "", pattern)
    if "{" in stripped or "}" in stripped:
        raise ValueError("自定义命名规则中的大括号不完整。")
    unsupported = sorted(fields - HISTORY_PATTERN_FIELDS)
    if unsupported:
        raise ValueError(
            "不支持的历史版本命名占位符：" + "、".join(unsupported)
            + "。可用：" + "、".join(f"{{{name}}}" for name in sorted(HISTORY_PATTERN_FIELDS))
        )
    return fields


def validate_history_naming_pattern(value) -> str:
    pattern = normalize_history_naming_pattern(value)
    _history_pattern_fields(pattern)
    sample = {
        "name": "示例.txt", "stem": "示例", "ext": ".txt",
        "date": "20260813", "time": "093000", "datetime": "20260813_093000",
        "version": "1",
    }
    try:
        rendered = pattern.format_map(sample)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"历史版本命名规则无效：{exc}") from exc
    if not str(rendered).strip(" ."):
        raise ValueError("历史版本命名规则不能生成空文件名。")
    return pattern


def history_versions_root() -> Path:
    return DATA_DIR / "Versions"


def history_index_file() -> Path:
    return history_versions_root() / "history.json"


def _safe_history_item_key(item_id: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item_id or "").strip()).strip("._")
    return raw[:80] or hashlib.sha256(str(item_id).encode("utf-8", "replace")).hexdigest()[:24]


def history_item_directory(item_id: str) -> Path:
    return history_versions_root() / _safe_history_item_key(item_id)


def history_record_path(record: dict) -> Path | None:
    if not isinstance(record, dict):
        return None
    raw = str(record.get("path") or "").strip()
    if not raw:
        return None
    relative = Path(raw.replace("/", os.sep))
    if relative.is_absolute():
        return None
    try:
        candidate = (DATA_DIR / relative).resolve(strict=False)
        root = history_versions_root().resolve(strict=False)
        if os.path.commonpath((str(candidate), str(root))) != str(root):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def history_relative_path(path: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    root = history_versions_root().resolve(strict=False)
    try:
        if os.path.commonpath((str(resolved), str(root))) != str(root):
            raise ValueError("历史版本文件必须保存在 PasserData\\Versions 中。")
        return resolved.relative_to(DATA_DIR.resolve(strict=False)).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError("历史版本路径不在 Passer 数据目录内。") from exc


def load_version_history() -> dict[str, list[dict]]:
    payload = read_json(history_index_file(), {})
    if not isinstance(payload, dict):
        return {}
    raw_items = payload.get("items", payload)
    if not isinstance(raw_items, dict):
        return {}
    result: dict[str, list[dict]] = {}
    for raw_item_id, raw_records in raw_items.items():
        item_id = str(raw_item_id or "").strip()
        if not item_id or not isinstance(raw_records, list):
            continue
        records: list[dict] = []
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                continue
            path = history_record_path(raw_record)
            if path is None or not path.is_file():
                continue
            records.append({
                "id": str(raw_record.get("id") or uuid.uuid4().hex),
                "label": str(raw_record.get("label") or path.name),
                "created_at": str(raw_record.get("created_at") or ""),
                "path": history_relative_path(path),
                "source_target": str(raw_record.get("source_target") or ""),
                "version": max(1, _coerce_int(raw_record.get("version"), len(records) + 1, 1)),
            })
        if records:
            records.sort(key=lambda entry: (entry.get("created_at", ""), int(entry.get("version", 0))))
            result[item_id] = records
    return result


def save_version_history(history: dict[str, list[dict]]) -> None:
    clean: dict[str, list[dict]] = {}
    for raw_item_id, raw_records in dict(history or {}).items():
        item_id = str(raw_item_id or "").strip()
        if not item_id or not isinstance(raw_records, list):
            continue
        records = []
        for raw_record in raw_records:
            path = history_record_path(raw_record)
            if path is None or not path.is_file():
                continue
            records.append({
                "id": str(raw_record.get("id") or uuid.uuid4().hex),
                "label": str(raw_record.get("label") or path.name),
                "created_at": str(raw_record.get("created_at") or ""),
                "path": history_relative_path(path),
                "source_target": str(raw_record.get("source_target") or ""),
                "version": max(1, _coerce_int(raw_record.get("version"), len(records) + 1, 1)),
            })
        if records:
            clean[item_id] = records
    write_json(history_index_file(), {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "items": clean,
    })


def _sanitize_history_filename(value: str, suffix: str = "") -> str:
    cleaned = "".join(
        "_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch
        for ch in str(value or "")
    ).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "版本"
    suffix = str(suffix or "")
    if suffix and len(cleaned) > max(1, 180 - len(suffix)):
        cleaned = cleaned[:max(1, 180 - len(suffix))].rstrip(" .")
    else:
        cleaned = cleaned[:180].rstrip(" .")
    stem = Path(cleaned).stem.casefold()
    if stem in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        cleaned = "_" + cleaned
    return cleaned


def build_history_version_name(
    source: Path,
    mode: str,
    pattern: str,
    version: int,
    *,
    now: datetime | None = None,
    suffix: str | None = None,
) -> tuple[str, str]:
    source = Path(source)
    stamp = now or datetime.now()
    version = max(1, int(version))
    output_suffix = source.suffix if suffix is None else str(suffix)
    normalized_mode = normalize_history_naming_mode(mode)
    if normalized_mode == HISTORY_NAMING_VERSION:
        label = f"v{version}"
        filename = f"{source.stem}_{label}{output_suffix}"
    elif normalized_mode == HISTORY_NAMING_CUSTOM:
        validated = validate_history_naming_pattern(pattern)
        values = {
            "name": source.name,
            "stem": source.stem,
            "ext": output_suffix,
            "date": stamp.strftime("%Y%m%d"),
            "time": stamp.strftime("%H%M%S"),
            "datetime": stamp.strftime("%Y%m%d_%H%M%S"),
            "version": str(version),
        }
        rendered = validated.format_map(values).strip()
        if output_suffix and not rendered.casefold().endswith(output_suffix.casefold()):
            rendered += output_suffix
        filename = rendered
        label = Path(rendered).stem or f"v{version}"
    else:
        label = stamp.strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{source.stem}_{stamp.strftime('%Y%m%d_%H%M%S')}{output_suffix}"
    safe_name = _sanitize_history_filename(filename, output_suffix)
    if output_suffix and not safe_name.casefold().endswith(output_suffix.casefold()):
        safe_name = _sanitize_history_filename(Path(safe_name).stem, output_suffix) + output_suffix
    return safe_name, str(label)[:120]


def load_settings() -> dict:
    # 新电脑首次启动：settings.json 尚未生成，据此判断是否为第一次打开。
    first_run = not SETTINGS_FILE.exists()
    settings = _read_settings_payload()
    screenshot_seq = _coerce_int(settings.get("screenshot_seq"), 0, 0)
    transparent_alpha = _coerce_float(
        settings.get("transparent_alpha"), TRANSPARENT_ALPHA,
        TRANSPARENT_ALPHA_MIN, 1.0,
    )
    window_x = _coerce_optional_int(settings.get("window_x"))
    window_y = _coerce_optional_int(settings.get("window_y"))
    settings_window_x = _coerce_optional_int(settings.get("settings_window_x"))
    settings_window_y = _coerce_optional_int(settings.get("settings_window_y"))
    phone_mirror_mouse_sensitivity = _coerce_int(
        settings.get("phone_mirror_mouse_sensitivity"), 8, 1, 15,
    )
    phone_mirror_wireless_port = _coerce_int(
        settings.get("phone_mirror_wireless_port"), 5555, 1, 65535,
    )
    aira_poll_interval = _coerce_int(settings.get("aira_poll_interval"), 3, 2, 30)
    recent_search_items = settings.get("recent_search_items")
    if not isinstance(recent_search_items, list):
        recent_search_items = []
    disabled_builtin_tools = settings.get("disabled_builtin_tools")
    if not isinstance(disabled_builtin_tools, list):
        disabled_builtin_tools = []
    stored_share_code = str(settings.get("file_share_code") or "").strip()
    if stored_share_code.startswith("dpapi:"):
        stored_share_code = unprotect_password(stored_share_code)
    if not (4 <= len(stored_share_code) <= 8 and stored_share_code.isdigit()):
        stored_share_code = ""
    ai_enabled = bool(settings.get("ai_enabled", False))
    history_naming_mode = normalize_history_naming_mode(settings.get("history_naming_mode"))
    history_naming_pattern = normalize_history_naming_pattern(settings.get("history_naming_pattern"))
    if history_naming_mode == HISTORY_NAMING_CUSTOM:
        try:
            history_naming_pattern = validate_history_naming_pattern(history_naming_pattern)
        except ValueError:
            history_naming_pattern = DEFAULT_HISTORY_NAMING_PATTERN
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "topmost": bool(settings.get("topmost", True)),
        "locked": bool(settings.get("locked", False)),
        "width": _coerce_int(settings.get("width"), DEFAULT_WIDTH, MIN_WIDTH, MAX_WINDOW_DIMENSION),
        "height": _coerce_int(settings.get("height"), DEFAULT_HEIGHT, MIN_HEIGHT, MAX_WINDOW_DIMENSION),
        "window_x": window_x,
        "window_y": window_y,
        "settings_window_x": settings_window_x,
        "settings_window_y": settings_window_y,
        "screenshot_seq": max(0, screenshot_seq),
        "transparent": bool(settings.get("transparent", False)),
        "transparent_alpha": transparent_alpha,
        "font_size": normalize_font_size_label(settings.get("font_size")),
        "aira_font_size": normalize_font_size_label(settings.get("aira_font_size")),
        "aira_line_spacing": (
            str(settings.get("aira_line_spacing") or DEFAULT_AIRA_LINE_SPACING_LABEL)
            if str(settings.get("aira_line_spacing") or DEFAULT_AIRA_LINE_SPACING_LABEL)
            in AIRA_LINE_SPACING_BY_LABEL
            else DEFAULT_AIRA_LINE_SPACING_LABEL
        ),
        "theme_color": normalize_passer_theme_color(settings.get("theme_color")),
        "background_color": normalize_hex_color(settings.get("background_color")),
        "background_image": normalize_background_image(settings.get("background_image")),
        "store_dir": str(settings.get("store_dir") or DEFAULT_STORE_DIR),
        "history_naming_mode": history_naming_mode,
        "history_naming_pattern": history_naming_pattern,
        "recent_search_items": [
            {
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", "")),
                "target": str(item.get("target", "")),
                "title": str(item.get("title", "")),
            }
            for item in recent_search_items
            if (
                isinstance(item, dict)
                and (item.get("id") or item.get("target"))
                and str(item.get("target", "")) not in REMOVED_BUILTIN_TARGETS
            )
        ][:SEARCH_RECENT_LIMIT],
        "ai_enabled": ai_enabled,
        "ai_external_interface_enabled": bool(
            ai_enabled and settings.get("ai_external_interface_enabled", True)
        ),
        "ai_provider": str(settings.get("ai_provider") or "deepseek"),
        "ai_keys": load_ai_keys(settings.get("ai_keys")),
        "ai_models": load_ai_models(settings.get("ai_models")),
        "ai_thinking_mode": normalize_ai_thinking_mode(settings.get("ai_thinking_mode")),
        "ai_reasoning": normalize_ai_reasoning(settings.get("ai_reasoning")),
        "ai_persona": str(settings.get("ai_persona") or "default"),
        "ai_permission": str(settings.get("ai_permission") or "auto_approve"),
        "ai_prompt_cache": bool(settings.get("ai_prompt_cache", True)),
        "openclaw_enabled": bool(settings.get("openclaw_enabled", False)),
        "search_hotkey": str(settings.get("search_hotkey") or "Alt+Space"),
        "ai_hotkey": str(settings.get("ai_hotkey") or "Alt+Shift+Space"),
        "office_open_mode": normalize_office_open_mode(settings.get("office_open_mode")),
        "folder_open_mode": normalize_folder_open_mode(settings.get("folder_open_mode")),
        "code_open_mode": normalize_code_open_mode(settings.get("code_open_mode")),
        "pdf_open_mode": normalize_simple_open_mode(settings.get("pdf_open_mode")),
        "image_open_mode": normalize_simple_open_mode(settings.get("image_open_mode")),
        "video_open_mode": normalize_simple_open_mode(settings.get("video_open_mode")),
        "audio_open_mode": normalize_simple_open_mode(settings.get("audio_open_mode")),
        "disabled_builtin_tools": [
            str(t) for t in disabled_builtin_tools
            if isinstance(t, str) and t not in REMOVED_BUILTIN_TARGETS
        ],
        "first_run": first_run,
        "festival_reminder_date": str(settings.get("festival_reminder_date") or ""),
        "device_lock_password": str(settings.get("device_lock_password") or ""),
        "device_lock_keyboard": bool(settings.get("device_lock_keyboard", True)),
        "device_lock_mouse": bool(settings.get("device_lock_mouse", False)),
        "device_lock_active": bool(settings.get("device_lock_active", False)),
        "file_share_code": stored_share_code,
        "phone_mirror_mouse_mode": str(settings.get("phone_mirror_mouse_mode") or "seamless"),
        "phone_mirror_mouse_sensitivity": phone_mirror_mouse_sensitivity,
        "phone_mirror_audio_mode": str(settings.get("phone_mirror_audio_mode") or "sync"),
        "phone_mirror_transfer_path": str(settings.get("phone_mirror_transfer_path") or "/sdcard/Download/Passer"),
        "phone_mirror_wireless_ip": str(settings.get("phone_mirror_wireless_ip") or ""),
        "phone_mirror_wireless_port": phone_mirror_wireless_port,
        "phone_mirror_device_serial": str(settings.get("phone_mirror_device_serial") or ""),
        "aira_monitor_enabled": bool(settings.get("aira_monitor_enabled", False)),
        "aira_contacts": str(settings.get("aira_contacts") or ""),
        "aira_poll_interval": aira_poll_interval,
        "aira_notify": bool(settings.get("aira_notify", True)),
        "aira_notify_mode": normalize_aira_notify_mode(settings.get("aira_notify_mode")),
        "aira_save_raw": bool(settings.get("aira_save_raw", False)),
        "aira_usage_reminder_enabled": bool(
            settings.get("aira_usage_reminder_enabled", False)
        ),
        "aira_usage_notify_mode": normalize_aira_notify_mode(
            settings.get("aira_usage_notify_mode")
        ),
        "aira_mobile_enabled": bool(settings.get("aira_mobile_enabled", False)),
        "aira_relay_url": str(settings.get("aira_relay_url") or "").strip()[:2048],
    }


def save_settings(
    root: tk.Tk,
    topmost: bool,
    locked: bool = False,
    screenshot_seq: int = 0,
    transparent: bool = False,
    transparent_alpha: float = TRANSPARENT_ALPHA,
    font_size: str = DEFAULT_FONT_SIZE_LABEL,
    theme_color: str = DEFAULT_THEME_COLOR,
    background_color: str = DEFAULT_APP_BG,
    background_image: str = "",
    store_dir: str | os.PathLike | None = None,
    recent_search_items: list[dict] | None = None,
    ai_enabled: bool = False,
    ai_provider: str = "deepseek",
    ai_keys: dict | None = None,
    device_lock_password: str = "",
    device_lock_keyboard: bool = True,
    device_lock_mouse: bool = False,
    device_lock_active: bool = False,
    file_share_code: str = "",
    ai_models: dict | None = None,
    ai_thinking_mode: str = "auto",
    ai_reasoning: str = "auto",
    ai_persona: str = "default",
    ai_permission: str = "auto_approve",
    ai_prompt_cache: bool = True,
    openclaw_enabled: bool = False,
    search_hotkey: str = "Alt+Space",
    ai_hotkey: str = "Alt+Shift+Space",
    office_open_mode: str = OFFICE_OPEN_MODE_BUILTIN,
    folder_open_mode: str = FOLDER_OPEN_MODE_BUILTIN,
    code_open_mode: str = CODE_OPEN_MODE_BUILTIN,
    pdf_open_mode: str = SIMPLE_OPEN_MODE_BUILTIN,
    image_open_mode: str = SIMPLE_OPEN_MODE_BUILTIN,
    video_open_mode: str = SIMPLE_OPEN_MODE_BUILTIN,
    audio_open_mode: str = SIMPLE_OPEN_MODE_BUILTIN,
    disabled_builtin_tools: list[str] | None = None,
    festival_reminder_date: str = "",
    settings_window_x: int | None = None,
    settings_window_y: int | None = None,
    phone_mirror_mouse_mode: str = "seamless",
    phone_mirror_mouse_sensitivity: int = 8,
    phone_mirror_audio_mode: str = "sync",
    phone_mirror_transfer_path: str = "/sdcard/Download/Passer",
    phone_mirror_wireless_ip: str = "",
    phone_mirror_wireless_port: int = 5555,
    phone_mirror_device_serial: str = "",
    aira_monitor_enabled: bool = False,
    aira_contacts: str = "",
    aira_poll_interval: int = 3,
    aira_notify: bool = True,
    aira_notify_mode: str = AIRA_NOTIFY_MODE_WINDOWS,
    aira_save_raw: bool = False,
    aira_usage_reminder_enabled: bool = False,
    aira_usage_notify_mode: str = AIRA_NOTIFY_MODE_WINDOWS,
    aira_font_size: str = DEFAULT_FONT_SIZE_LABEL,
    aira_line_spacing: str = DEFAULT_AIRA_LINE_SPACING_LABEL,
    aira_mobile_enabled: bool = False,
    aira_relay_url: str = "",
    ai_external_interface_enabled: bool = True,
    history_naming_mode: str = HISTORY_NAMING_TIME,
    history_naming_pattern: str = DEFAULT_HISTORY_NAMING_PATTERN,
) -> None:
    clear_share_code = str(file_share_code or "").strip()
    protected_share_code = protect_password(clear_share_code) if clear_share_code else ""
    write_json(
        SETTINGS_FILE,
        {
            "schema_version": SETTINGS_SCHEMA_VERSION,
            "topmost": bool(topmost),
            "locked": bool(locked),
            "width": min(MAX_WINDOW_DIMENSION, max(root.winfo_width(), MIN_WIDTH)),
            "height": min(MAX_WINDOW_DIMENSION, max(root.winfo_height(), MIN_HEIGHT)),
            "window_x": int(root.winfo_x()),
            "window_y": int(root.winfo_y()),
            "settings_window_x": None if settings_window_x is None else int(settings_window_x),
            "settings_window_y": None if settings_window_y is None else int(settings_window_y),
            "screenshot_seq": max(0, int(screenshot_seq)),
            "transparent": bool(transparent),
            "transparent_alpha": min(1.0, max(TRANSPARENT_ALPHA_MIN, float(transparent_alpha))),
            "font_size": normalize_font_size_label(font_size),
            "aira_font_size": normalize_font_size_label(aira_font_size),
            "aira_line_spacing": (
                str(aira_line_spacing)
                if str(aira_line_spacing) in AIRA_LINE_SPACING_BY_LABEL
                else DEFAULT_AIRA_LINE_SPACING_LABEL
            ),
            "theme_color": normalize_passer_theme_color(theme_color),
            "background_color": normalize_hex_color(background_color),
            "background_image": normalize_background_image(background_image),
            "store_dir": str(store_dir or STORE_DIR),
            "history_naming_mode": normalize_history_naming_mode(history_naming_mode),
            "history_naming_pattern": normalize_history_naming_pattern(history_naming_pattern),
            "recent_search_items": list(recent_search_items or [])[:SEARCH_RECENT_LIMIT],
            "ai_enabled": bool(ai_enabled),
            "ai_external_interface_enabled": bool(
                ai_enabled and ai_external_interface_enabled
            ),
            "ai_provider": str(ai_provider or "deepseek"),
            "ai_keys": protect_ai_keys(ai_keys),
            "ai_models": load_ai_models(ai_models),
            "ai_thinking_mode": normalize_ai_thinking_mode(ai_thinking_mode),
            "ai_reasoning": normalize_ai_reasoning(ai_reasoning),
            "ai_persona": str(ai_persona or "default"),
            "ai_permission": str(ai_permission or "auto_approve"),
            "ai_prompt_cache": bool(ai_prompt_cache),
            "openclaw_enabled": bool(openclaw_enabled),
            "search_hotkey": str(search_hotkey or "Alt+Space"),
            "ai_hotkey": str(ai_hotkey or "Alt+Shift+Space"),
            "office_open_mode": normalize_office_open_mode(office_open_mode),
            "folder_open_mode": normalize_folder_open_mode(folder_open_mode),
            "code_open_mode": normalize_code_open_mode(code_open_mode),
            "pdf_open_mode": normalize_simple_open_mode(pdf_open_mode),
            "image_open_mode": normalize_simple_open_mode(image_open_mode),
            "video_open_mode": normalize_simple_open_mode(video_open_mode),
            "audio_open_mode": normalize_simple_open_mode(audio_open_mode),
            "disabled_builtin_tools": [
                str(t) for t in (disabled_builtin_tools or []) if str(t) not in REMOVED_BUILTIN_TARGETS
            ],
            "festival_reminder_date": str(festival_reminder_date or ""),
            "device_lock_password": str(device_lock_password or ""),
            "device_lock_keyboard": bool(device_lock_keyboard),
            "device_lock_mouse": bool(device_lock_mouse),
            "device_lock_active": bool(device_lock_active),
            # Never persist the LAN transfer code in plaintext. If DPAPI is
            # unavailable, keep it for this session only instead of weakening storage.
            "file_share_code": protected_share_code,
            "phone_mirror_mouse_mode": str(phone_mirror_mouse_mode or "seamless"),
            "phone_mirror_mouse_sensitivity": max(1, min(15, int(phone_mirror_mouse_sensitivity or 8))),
            "phone_mirror_audio_mode": str(phone_mirror_audio_mode or "sync"),
            "phone_mirror_transfer_path": str(phone_mirror_transfer_path or "/sdcard/Download/Passer"),
            "phone_mirror_wireless_ip": str(phone_mirror_wireless_ip or ""),
            "phone_mirror_wireless_port": max(1, min(65535, int(phone_mirror_wireless_port or 5555))),
            "phone_mirror_device_serial": str(phone_mirror_device_serial or ""),
            "aira_monitor_enabled": bool(aira_monitor_enabled),
            "aira_contacts": str(aira_contacts or ""),
            "aira_poll_interval": max(2, min(30, int(aira_poll_interval or 3))),
            "aira_notify": bool(aira_notify),
            "aira_notify_mode": normalize_aira_notify_mode(aira_notify_mode),
            "aira_save_raw": bool(aira_save_raw),
            "aira_usage_reminder_enabled": bool(aira_usage_reminder_enabled),
            "aira_usage_notify_mode": normalize_aira_notify_mode(aira_usage_notify_mode),
            "aira_mobile_enabled": bool(aira_mobile_enabled),
            "aira_relay_url": str(aira_relay_url or "").strip()[:2048],
        },
    )

