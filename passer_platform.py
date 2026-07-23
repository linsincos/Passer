from __future__ import annotations

# Loaded into the parent module's global namespace by the parent file.
# Keep this file focused on the extracted feature area.

from popup_manager import PopupManager, tk_geometry as _popup_tk_geometry

_POPUP_MANAGER = PopupManager()

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


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _Point),
    ]


def get_windows_work_areas() -> list[tuple[bool, int, int, int, int]]:
    return _POPUP_MANAGER.get_work_areas()

def root_monitor_work_area(root: tk.Tk) -> tuple[int, int, int, int] | None:
    return _POPUP_MANAGER.root_work_area(root)

def point_monitor_work_area(x: int, y: int) -> tuple[int, int, int, int] | None:
    """Work area of the monitor containing (x, y), falling back to the nearest."""
    return _POPUP_MANAGER.point_work_area(x, y)

def clamp_to_work_area(x: int, y: int, width: int, height: int, area: tuple[int, int, int, int] | None) -> tuple[int, int]:
    return _POPUP_MANAGER.clamp_to_work_area(x, y, width, height, area)

def rect_intersects_any_work_area(x: int, y: int, width: int, height: int) -> bool:
    return _POPUP_MANAGER.rect_intersects_any_work_area(x, y, width, height)

def center_over_root(root: tk.Tk, width: int, height: int) -> tuple[int, int]:
    return _POPUP_MANAGER.center_over_root(
        root, width, height, min_width=MIN_WIDTH, min_height=MIN_HEIGHT
    )

def tk_geometry(width: int, height: int, x: int, y: int) -> str:
    return _popup_tk_geometry(width, height, x, y)

def window_size_label(width: int, height: int) -> str:
    for label, preset_w, preset_h in COMMON_WINDOW_SIZES:
        if int(width) == preset_w and int(height) == preset_h:
            return label
    return CUSTOM_WINDOW_SIZE_LABEL


def normalize_font_size_label(value) -> str:
    raw = str(value or "").strip()
    if raw in FONT_SIZE_DELTA_BY_LABEL:
        return raw
    try:
        delta = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE_LABEL
    for label, option_delta in FONT_SIZE_OPTIONS:
        if delta == option_delta:
            return label
    return DEFAULT_FONT_SIZE_LABEL


def set_app_font_size(value) -> str:
    global APP_FONT_SIZE_DELTA
    label = normalize_font_size_label(value)
    APP_FONT_SIZE_DELTA = FONT_SIZE_DELTA_BY_LABEL.get(label, 0)
    return label


def native_window_handle(window: tk.Tk | tk.Toplevel) -> int:
    return _POPUP_MANAGER.native_window_handle(window)

def place_toplevel_absolute(window: tk.Toplevel, width: int, height: int, x: int, y: int) -> None:
    _POPUP_MANAGER.place_absolute(window, width, height, x, y)

def startup_window_position(width: int, height: int, root: tk.Tk) -> tuple[int, int]:
    return _POPUP_MANAGER.startup_window_position(width, height, root)

def first_run_geometry(root: tk.Tk) -> tuple[int, int, int, int]:
    """First-run geometry: half of the target monitor, centered.

    Prefer a non-primary monitor when one is present, matching Passer's previous
    startup behavior.
    """
    return _POPUP_MANAGER.first_run_geometry(
        root, min_width=MIN_WIDTH, min_height=MIN_HEIGHT
    )

def restored_window_position(
    width: int, height: int, root: tk.Tk, saved_x, saved_y
) -> tuple[int, int]:
    """Restore a saved position, clamping it into a currently available monitor."""
    return _POPUP_MANAGER.restored_window_position(width, height, root, saved_x, saved_y)

def centered_geometry(width: int, height: int, root: tk.Tk) -> str:
    x, y = startup_window_position(width, height, root)
    return tk_geometry(width, height, x, y)

def find_app_font_files() -> list[Path]:
    candidates: list[Path] = []
    if APP_FONT_PREFERRED_FILE.exists():
        # 首选字体存在时初始化一定会采用它，无需再递归扫描外部字体目录。
        return [APP_FONT_PREFERRED_FILE]
    if FONT_SEARCH_DIR.exists():
        keywords = FONT_KEYWORDS
        for path in FONT_SEARCH_DIR.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in FONT_FILE_EXTS:
                continue
            if path in candidates:
                continue
            name = path.name.lower()
            if any(keyword.lower() in name for keyword in keywords):
                candidates.append(path)
    return candidates


def add_private_font(path: Path) -> bool:
    if sys.platform != "win32":
        return path.exists()
    try:
        gdi32 = ctypes.windll.gdi32
        gdi32.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        gdi32.AddFontResourceExW.restype = ctypes.c_int
        fr_private = 0x10
        return gdi32.AddFontResourceExW(str(path), fr_private, None) > 0
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        awareness_context_per_monitor_v2 = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(awareness_context_per_monitor_v2):
            return
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_APP_ICON_PHOTO = None  # module-level ref so Tk doesn't garbage-collect the icon
_APP_ICON_PHOTOS = []   # multi-size taskbar icons


def apply_app_icon(root: tk.Tk) -> None:
    """Apply Passer's custom icon to the window, dialogs and taskbar (best-effort)."""
    global _APP_ICON_PHOTO, _APP_ICON_PHOTOS
    try:
        if APP_ICON_FILE.exists():
            # default=... makes every future Toplevel/dialog inherit the icon too.
            root.iconbitmap(default=str(APP_ICON_FILE))
    except Exception:
        pass
    try:
        if PIL_AVAILABLE and APP_ICON_PNG.exists():
            source = Image.open(APP_ICON_PNG).convert("RGBA")
            photos = []
            for size in (256, 128, 64, 48, 32, 24, 16):
                icon = source.resize((size, size), resize_filter())
                photos.append(ImageTk.PhotoImage(icon))
            _APP_ICON_PHOTOS = photos
            _APP_ICON_PHOTO = photos[0] if photos else None
            if photos:
                root.iconphoto(True, *photos)
        elif APP_ICON_PNG.exists():
            _APP_ICON_PHOTO = tk.PhotoImage(file=str(APP_ICON_PNG))
            root.iconphoto(True, _APP_ICON_PHOTO)
    except Exception:
        pass
    _apply_win32_window_icons(root)


def _apply_win32_window_icons(root: tk.Tk) -> None:
    """Set crisp Win32 small/large icons; Tk's PNG downscale can blur taskbar icons."""
    if sys.platform != "win32" or not APP_ICON_FILE.exists():
        return
    try:
        root.update_idletasks()
        hwnd = int(root.winfo_id())
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        large = user32.LoadImageW(None, str(APP_ICON_FILE), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, str(APP_ICON_FILE), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if large:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, large)
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
    except Exception:
        pass


def initialize_app_font(root: tk.Tk) -> str:
    global APP_FONT_FAMILY, APP_FONT_FILE
    for path in find_app_font_files():
        if add_private_font(path):
            APP_FONT_FILE = path
            break

    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    if APP_FONT_PREFERRED_FAMILY in families or APP_FONT_FILE:
        APP_FONT_FAMILY = APP_FONT_PREFERRED_FAMILY
    else:
        APP_FONT_FAMILY = APP_FONT_FALLBACK
    return APP_FONT_FAMILY


def app_font(size: int = 9, weight: str = "normal"):
    size = max(6, int(size) + APP_FONT_SIZE_DELTA)
    if weight and weight != "normal":
        return (APP_FONT_FAMILY, size, weight)
    return (APP_FONT_FAMILY, size)


def truncate_to_pixels(text: str, font: tkfont.Font, max_px: int) -> str:
    """Trim ``text`` with an ellipsis so it renders within ``max_px`` pixels."""
    text = str(text or "")
    if max_px <= 0 or not text or font.measure(text) <= max_px:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + ellipsis) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ellipsis


def configure_app_fonts(root: tk.Tk) -> None:
    family = initialize_app_font(root)
    default_size = max(6, 10 + APP_FONT_SIZE_DELTA)
    root.option_add("*Font", f"{{{family}}} {default_size}")
    for font_name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkFixedFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            tkfont.nametofont(font_name).configure(family=family, size=default_size)
        except Exception:
            pass


def strip_wrappers(text: str) -> str:
    result = text.strip()
    changed = True
    while changed and len(result) >= 2:
        changed = False
        pairs = {('"', '"'), ("'", "'"), ("<", ">")}
        if (result[0], result[-1]) in pairs:
            result = result[1:-1].strip()
            changed = True
    return result


def is_url(text: str) -> bool:
    return bool(re.match(r"^(https?|ftp)://", text.strip(), re.IGNORECASE))


def file_uri_to_path(text: str) -> str | None:
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    if parsed.scheme.lower() != "file":
        return None

    path = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    path = unquote(path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path.replace("/", "\\")


def title_for(kind: str, target: str) -> str:
    if kind == "group":
        return "组"
    if kind == "url":
        try:
            parsed = urlparse(target)
            host = parsed.netloc or target
            leaf = Path(unquote(parsed.path)).name
            return f"{host} / {leaf}" if leaf else host
        except Exception:
            return target
    name = Path(target.rstrip("\\/")).name
    return name or target


def new_item(kind: str, target: str, title: str | None = None) -> DockItem:
    return DockItem(
        id=uuid.uuid4().hex,
        kind=kind,
        target=target,
        title=title or title_for(kind, target),
        added_at=datetime.now().isoformat(timespec="seconds"),
    )


def read_url_shortcut(path: Path) -> str | None:
    try:
        for encoding in ("utf-8-sig", "gbk", "utf-16"):
            try:
                lines = path.read_text(encoding=encoding).splitlines()
                break
            except UnicodeError:
                continue
        else:
            return None

        for line in lines:
            if line.strip().lower().startswith("url="):
                url = line.split("=", 1)[1].strip()
                return url if is_url(url) else None
    except Exception:
        return None
    return None


def resolve_windows_lnk(path: Path) -> str | None:
    if sys.platform != "win32" or path.suffix.lower() != ".lnk":
        return None

    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::UTF8; "
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]); "
        "[Console]::Write($s.TargetPath)"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        return None

    target = result.stdout.strip()
    return target or None


def item_from_link_or_path(text: str) -> DockItem | None:
    value = strip_wrappers(os.path.expandvars(text))
    if not value:
        return None
    if value.lower().startswith("file://"):
        local = file_uri_to_path(value)
        if local:
            value = local
    if value.lower().startswith("www."):
        value = "https://" + value
    if is_url(value):
        return new_item("url", value)

    path = Path(value)
    if path.exists():
        resolved = str(path.resolve())
        suffix = path.suffix.lower()
        if suffix == ".url":
            url = read_url_shortcut(path)
            if url:
                return new_item("url", url)
        if suffix == ".lnk":
            shortcut_target = resolve_windows_lnk(path)
            if shortcut_target:
                target_item = item_from_link_or_path(shortcut_target)
                if target_item:
                    return target_item
                return new_item("file", shortcut_target, title_for("file", shortcut_target))
        if path.is_dir():
            return new_item("folder", resolved)
        if suffix in IMAGE_EXTS:
            return new_item("image", resolved)
        if suffix == ".txt":
            return new_item("text", resolved)
        return new_item("file", resolved)
    return None


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def parse_page_ranges(spec: str, page_count: int) -> list[int]:
    """把 "1-3,5,8" 解析为有序去重的 1-based 页码列表（裁剪到 [1, page_count]）。"""
    pages: list[int] = []
    seen: set[int] = set()
    for chunk in str(spec or "").replace("，", ",").replace("－", "-").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                lo, hi = int(a), int(b)
                if lo > hi:
                    lo, hi = hi, lo
            else:
                lo = hi = int(chunk)
        except ValueError:
            continue
        for n in range(lo, hi + 1):
            if 1 <= n <= page_count and n not in seen:
                seen.add(n)
                pages.append(n)
    return pages


def sanitize_filename_piece(text: str, limit: int = 24) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    cleaned = "".join("_" if ch in INVALID_FILENAME_CHARS or ord(ch) < 32 else ch for ch in text)
    cleaned = cleaned.strip(" ._")
    return cleaned[:limit]


def sanitize_local_name(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    cleaned = "".join("_" if ch in INVALID_FILENAME_CHARS or ord(ch) < 32 else ch for ch in text)
    return cleaned.strip(" .")


def first_text_hint(text: str, limit: int = 24) -> str:
    for line in text.splitlines():
        hint = sanitize_filename_piece(line, limit)
        if hint:
            return hint
    return ""


def save_text_as_item(text: str) -> DockItem:
    # Name the file after the leading portion of the text itself.
    stem = first_text_hint(text, limit=30) or f"文本_{now_stamp()}"
    path = unique_path(STORE_DIR, stem, ".txt")
    path.write_text(text, encoding="utf-8-sig")
    return new_item("text", str(path), path.name)


def save_image_as_item(image: "Image.Image") -> DockItem:
    path = unique_path(STORE_DIR, f"图片_{now_stamp()}", ".png")
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    image.save(path, "PNG")
    return new_item("image", str(path), path.name)


def write_blank_docx(path: Path) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>""",
        )


def write_blank_xlsx(path: Path) -> None:
    if _ensure_openpyxl():
        workbook = openpyxl.Workbook()
        workbook.save(path)
        return

    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>""",
        )


def write_blank_pptx(path: Path) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "ppt/presentation.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldSz cx="9144000" cy="5143500" type="screen16x9"/><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>""",
        )
        zf.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMapOvr><p:masterClrMapping/></p:clrMapOvr></p:sld>""",
        )


def entries_from_text(text: str) -> list[DockItem]:
    if not text:
        return []
    lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text) if line.strip()]
    recognized = [item_from_link_or_path(line) for line in lines]
    if lines and all(item is not None for item in recognized):
        return [item for item in recognized if item is not None]
    return [save_text_as_item(text)]


def entries_from_paths(paths: list[str] | tuple[str, ...]) -> list[DockItem]:
    entries: list[DockItem] = []
    for path in paths:
        item = item_from_link_or_path(str(path))
        if item:
            entries.append(item)
    return entries


def is_image_file_path(path: Path) -> bool:
    # Check the cheap suffix first.  Calling is_file() for every executable,
    # shortcut and URL made a double-click noticeably pause on slow/network disks.
    return path.suffix.lower() in IMAGE_EXTS and path.is_file()


def resize_filter():
    resampling = getattr(Image, "Resampling", Image) if PIL_AVAILABLE else None
    return getattr(resampling, "LANCZOS", 1)


def _no_window_flag() -> int:
    return subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def notify_windows(title: str, message: str) -> None:
    """Show a Windows toast in the notification centre (通知栏).

    Uses the built-in WinRT toast API via PowerShell under PowerShell's own
    AppUserModelID, so no extra dependency or registered app id is required.
    Title/message are passed through the environment and added as XML text
    nodes, so they need no manual escaping.
    """
    if sys.platform != "win32":
        return
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null; "
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$x=$t.GetElementsByTagName('text'); "
        "$x.Item(0).AppendChild($t.CreateTextNode($env:PASSER_NOTIFY_TITLE))|Out-Null; "
        "$x.Item(1).AppendChild($t.CreateTextNode($env:PASSER_NOTIFY_MSG))|Out-Null; "
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($t); "
        "$aumid='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($toast)"
    )
    env = os.environ.copy()
    env["PASSER_NOTIFY_TITLE"] = title
    env["PASSER_NOTIFY_MSG"] = message
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            env=env,
            creationflags=_no_window_flag(),
        )
    except Exception:
        pass


def launch_executable(path: Path) -> None:
    """Start an .exe from its own folder, in the foreground.

    Many portable / Electron apps (e.g. Upscayl) resolve resources relative to the
    working directory and never show a window when launched with the wrong cwd
    (``os.startfile`` uses Passer's folder). ``ShellExecuteW`` with ``lpDirectory``
    set to the exe's folder fixes the cwd *and* grants foreground-activation rights
    so the new window isn't stuck behind Passer's always-on-top dock.
    """
    parent = str(path.parent)
    if sys.platform == "win32":
        allow_any_foreground()
        try:
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
            ]
            shell32.ShellExecuteW.restype = ctypes.c_ssize_t
            SW_SHOWNORMAL = 1
            result = shell32.ShellExecuteW(None, "open", str(path), None, parent, SW_SHOWNORMAL)
            if result > 32:
                return
        except Exception:
            pass
        try:
            subprocess.Popen([str(path)], cwd=parent, close_fds=True, creationflags=_no_window_flag())
            return
        except Exception:
            pass
    os.startfile(str(path))  # type: ignore[attr-defined]


def allow_any_foreground() -> None:
    """Let the app we launch pull its window to the foreground.

    A tray-minimised single-instance app (Clash, etc.) shows its existing window
    via a SetForegroundWindow call when relaunched. That call is normally blocked
    because Passer — a borderless ``overrideredirect`` window — isn't the
    foreground process, so clicking the tile looks like "nothing happened".
    Zeroing the foreground-lock timeout (plus ASFW_ANY) removes that block, which
    is exactly what makes the desktop shortcut work.
    """
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
        SPIF_SENDCHANGE = 2
        user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE
        )
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass


def set_window_owner(child: tk.Misc, owner: tk.Misc) -> None:
    """让 ``child`` 成为 ``owner`` 的「被拥有窗口」(owned window)。

    Windows 保证被拥有窗口在 Z 序上**始终位于其拥有者之上**，即使拥有者被
    SetForegroundWindow 重新激活也不会盖住它——这正是悬浮聊天层、图片/PDF
    查看器需要的「永远在 Passer 之上」行为，比反复 lift() 抢 Z 序更可靠。
    ``overrideredirect`` 会重建窗口、丢失初始 owner，故需在窗口已实体化后再设。
    """
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        GA_ROOT = 2
        GWLP_HWNDPARENT = -8
        child_hwnd = user32.GetAncestor(child.winfo_id(), GA_ROOT) or child.winfo_id()
        owner_hwnd = user32.GetAncestor(owner.winfo_id(), GA_ROOT) or owner.winfo_id()
        if not child_hwnd or not owner_hwnd:
            return
        if hasattr(user32, "SetWindowLongPtrW"):
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.SetWindowLongPtrW(child_hwnd, GWLP_HWNDPARENT, owner_hwnd)
        else:  # 32-bit Python
            user32.SetWindowLongW(child_hwnd, GWLP_HWNDPARENT, owner_hwnd)
    except Exception:
        pass


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
    ]


def shell_thumbnail(path: Path, size: int = 256) -> "Image.Image | None":
    """用 Windows 资源管理器同款 IShellItemImageFactory 取文件缩略图。

    视频会得到首帧/海报，音频会得到专辑封面（若有），都无需第三方依赖。失败返回
    None，调用方应有回退（如显示通用图标）。
    """
    if sys.platform != "win32" or Image is None:
        return None
    try:
        ole32 = ctypes.oledll.ole32
        shell32 = ctypes.windll.shell32
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        ole32.CoInitialize(None)
        iid = _GUID()
        ole32.CLSIDFromString("{bcc18b79-ba16-442f-80c4-8a59c30c463b}", ctypes.byref(iid))

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_int), ("cy", ctypes.c_int)]

        shell32.SHCreateItemFromParsingName.argtypes = [
            wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)
        ]
        pitem = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(iid), ctypes.byref(pitem))
        if hr != 0 or not pitem:
            return None
        vtbl = ctypes.cast(pitem, ctypes.POINTER(ctypes.c_void_p))[0]
        funcs = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
        GetImage = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_int, ctypes.POINTER(wintypes.HBITMAP)
        )(funcs[3])
        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(funcs[2])
        hbmp = wintypes.HBITMAP()
        SIIGBF_BIGGERSIZEOK = 0x1
        hr = GetImage(pitem, SIZE(size, size), SIIGBF_BIGGERSIZEOK, ctypes.byref(hbmp))
        Release(pitem)
        if hr != 0 or not hbmp:
            return None

        class BITMAP(ctypes.Structure):
            _fields_ = [
                ("bmType", ctypes.c_int), ("bmWidth", ctypes.c_int), ("bmHeight", ctypes.c_int),
                ("bmWidthBytes", ctypes.c_int), ("bmPlanes", ctypes.c_ushort),
                ("bmBitsPixel", ctypes.c_ushort), ("bmBits", ctypes.c_void_p),
            ]

        bm = BITMAP()
        gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0:
            gdi32.DeleteObject(hbmp)
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_int), ("biHeight", ctypes.c_int),
                ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort),
                ("biCompression", ctypes.c_ulong), ("biSizeImage", ctypes.c_ulong),
                ("biXPels", ctypes.c_int), ("biYPels", ctypes.c_int),
                ("biClrUsed", ctypes.c_ulong), ("biClrImp", ctypes.c_ulong),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("h", BITMAPINFOHEADER), ("c", ctypes.c_ulong * 3)]

        bi = BITMAPINFO()
        bi.h.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.h.biWidth = w
        bi.h.biHeight = -h  # top-down
        bi.h.biPlanes = 1
        bi.h.biBitCount = 32
        bi.h.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(0)
        gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        user32.ReleaseDC(0, hdc)
        gdi32.DeleteObject(hbmp)
        return Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
    except Exception:
        return None


# Shell 详情列里我们关心的媒体字段（中英文系统都尝试匹配）。
_MEDIA_DETAIL_KEYS = (
    "时长", "Length", "长度", "Duration",
    "帧高度", "Frame height", "帧宽度", "Frame width", "尺寸", "Dimensions",
    "数据速率", "Data rate", "总比特率", "Total bitrate", "比特率", "Bit rate",
    "帧速率", "Frame rate",
    "audio采样率", "音频采样率", "Audio sample rate",
    "参与创作的艺术家", "艺术家", "Artists", "Contributing artists",
    "唱片集", "专辑", "Album",
    "标题", "Title", "流派", "Genre", "年", "Year",
)


def shell_media_metadata(path: Path) -> list[tuple[str, str]]:
    """用 Shell.Application 读取媒体文件的元数据（时长/分辨率/比特率/标签等）。"""
    if sys.platform != "win32":
        return []
    try:
        import comtypes.client as cc
        sh = cc.CreateObject("Shell.Application")
        folder = sh.NameSpace(str(path.parent))
        if folder is None:
            return []
        item = folder.ParseName(path.name)
        if item is None:
            return []
        wanted = {k.casefold() for k in _MEDIA_DETAIL_KEYS}
        rows: list[tuple[str, str]] = []
        seen = set()
        for i in range(0, 320):
            name = folder.GetDetailsOf(None, i)
            if not name or name.casefold() not in wanted:
                continue
            value = folder.GetDetailsOf(item, i)
            if value and name not in seen:
                rows.append((name, value))
                seen.add(name)
        return rows
    except Exception:
        return []


# ---- WAV 读写 / 处理（内置音频编辑用，无第三方依赖）------------------------
def read_wav_float(path):
    """读取 PCM WAV -> (np.float32 [-1,1] 形状 (n, ch), samplerate)。不支持则抛异常。"""
    import wave
    import numpy as np
    with wave.open(str(path), "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        arr = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"暂不支持 {sw * 8} 位 WAV。")
    if nch < 1:
        nch = 1
    arr = arr.reshape(-1, nch)
    return arr, sr


def write_wav_int16(dest, data, sr: int) -> None:
    """把 float 数组写成 16-bit PCM WAV。dest 可为路径或可写缓冲。"""
    import wave
    import numpy as np
    d = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    if d.ndim == 1:
        d = d.reshape(-1, 1)
    pcm = (d * 32767.0).astype("<i2")
    with wave.open(dest, "wb") as w:
        w.setnchannels(d.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


def wav_bytes(data, sr: int) -> bytes:
    """生成内存 WAV 字节，供 winsound 即时播放。"""
    import io
    buf = io.BytesIO()
    write_wav_int16(buf, data, sr)
    return buf.getvalue()


def denoise_audio(data, sr: int, noise_range=None, strength: float = 1.3, floor: float = 0.1):
    """谱减法降噪：从指定噪声段（或自动选最安静窗口）估计噪声谱，逐通道衰减。

    noise_range: (start, end) 样本下标，作为纯噪声参考；None 时自动取 RMS 最低的 0.5s。
    """
    import numpy as np
    from scipy.signal import stft, istft
    x = np.asarray(data, dtype=np.float32)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    n = x.shape[0]
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        ch = x[:, c]
        if noise_range and noise_range[1] - noise_range[0] > sr * 0.1:
            seg = ch[noise_range[0]:noise_range[1]]
        else:
            win = max(1, int(sr * 0.5))
            if n > win * 2:
                step = max(1, win // 2)
                # 累积和一次算出所有候选窗口能量，避免在 Python 中反复切片。
                power_sum = np.concatenate(([0.0], np.cumsum(ch * ch, dtype=np.float64)))
                starts = np.arange(0, n - win, step, dtype=np.int64)
                energy = (power_sum[starts + win] - power_sum[starts]) / win
                j = int(starts[int(np.argmin(energy))])
                seg = ch[j:j + win]
            else:
                seg = ch[:max(1, n // 5)]
        _f, _t, noise_z = stft(seg, fs=sr, nperseg=1024)
        noise_mag = np.mean(np.abs(noise_z), axis=1, keepdims=True)
        _f, _t, z = stft(ch, fs=sr, nperseg=1024)
        mag, phase = np.abs(z), np.angle(z)
        gain = np.clip((mag - strength * noise_mag) / (mag + 1e-9), 0.0, 1.0)
        gain = floor + (1.0 - floor) * gain
        _, y = istft(gain * mag * np.exp(1j * phase), fs=sr, nperseg=1024)
        y = y[:n]
        if len(y) < n:
            y = np.pad(y, (0, n - len(y)))
        out[:, c] = y
    return out


def find_ffmpeg() -> str | None:
    """定位 ffmpeg：打包内置 tools/ffmpeg → PATH → 用户上次指定的路径。"""
    bundled = SCRIPT_DIR / "tools" / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        saved = DATA_DIR / "ffmpeg_path.txt"
        if saved.is_file():
            p = saved.read_text(encoding="utf-8").strip()
            if p and Path(p).is_file():
                return p
    except OSError:
        pass
    return None


def remember_ffmpeg_path(path: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "ffmpeg_path.txt").write_text(str(path), encoding="utf-8")
    except OSError:
        pass


def ffmpeg_usable(path: str) -> bool:
    try:
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run([path, "-version"], capture_output=True, timeout=8, startupinfo=si)
        return result.returncode == 0 and b"ffmpeg" in (result.stdout + result.stderr).lower()
    except Exception:
        return False


def parse_time_to_seconds(text: str):
    """把 "90" / "1:30" / "01:02:03.5" 解析为秒（float）。空/非法返回 None。"""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds
        return float(text)
    except ValueError:
        return None


def _lerp_hex(a: str, b: str, t: float) -> str:
    """在两个 #rrggbb 颜色之间线性插值。"""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t)
    )


def draw_pill(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
              *, tag: str, solid: str | None = None,
              grad: tuple[str, str, str] | None = None) -> None:
    """在 canvas 上画一个胶囊（两端半圆）形状，可纯色或竖向三段渐变。

    渐变用逐行水平线实现，并按行到垂直中线的距离收边，得到平滑的圆角药丸——
    与示例进度条一致的「光泽填充」观感，配色走 Passer 主题。
    """
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    r = (y1 - y0) / 2.0
    yc = (y0 + y1) / 2.0
    y = int(round(y0))
    end = int(round(y1))
    while y < end:
        dy = (y + 0.5) - yc
        half = math.sqrt(max(0.0, r * r - dy * dy))
        x_left = x0 + (r - half)
        x_right = x1 - (r - half)
        if grad is not None:
            c_top, c_mid, c_bot = grad
            t = (y - y0) / max(1.0, (y1 - y0))
            color = _lerp_hex(c_top, c_mid, t / 0.45) if t < 0.45 else _lerp_hex(c_mid, c_bot, (t - 0.45) / 0.55)
        else:
            color = solid or "#000000"
        canvas.create_line(x_left, y + 0.5, x_right, y + 0.5, fill=color, tags=tag)
        y += 1


def shell_open(path: Path) -> None:
    """Open a file/shortcut the way Explorer does (ShellExecute 'open' verb).

    Unlike os.startfile this grants the launched app foreground-activation
    rights, so a running single-instance app focuses its window instead of
    appearing to do nothing.
    """
    if sys.platform == "win32":
        try:
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.restype = ctypes.c_ssize_t
            SW_SHOWNORMAL = 1
            result = shell32.ShellExecuteW(None, "open", str(path), None, None, SW_SHOWNORMAL)
            if result > 32:
                return
        except Exception:
            pass
    os.startfile(str(path))  # type: ignore[attr-defined]


def is_windowsapps_path(path: Path) -> bool:
    return any(part.lower() == "windowsapps" for part in path.parts)


def windowsapps_family_name(path: Path) -> str | None:
    """Derive the PackageFamilyName from a ...\\WindowsApps\\<PackageFullName>\\... path."""
    parts = path.parts
    folder = None
    for index, part in enumerate(parts):
        if part.lower() == "windowsapps" and index + 1 < len(parts):
            folder = parts[index + 1]
            break
    if not folder or "__" not in folder:
        return None
    name = folder.split("_", 1)[0]
    publisher = folder.split("__", 1)[-1]
    if not name or not publisher:
        return None
    return f"{name}_{publisher}"


def resolve_app_user_model_id(family_name: str) -> str | None:
    """Look up the launchable AUMID (e.g. ``Claude_xxx!Claude``) for a package family."""
    if sys.platform != "win32":
        return None
    if family_name in WINDOWSAPPS_AUMID_CACHE:
        return WINDOWSAPPS_AUMID_CACHE[family_name]
    pattern = (family_name + "!*").replace("'", "''")
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::UTF8; "
        f"Get-StartApps | Where-Object {{ $_.AppID -like '{pattern}' }} | "
        "Select-Object -First 1 -ExpandProperty AppID"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=10, creationflags=_no_window_flag(),
        )
    except Exception:
        return None
    aumid = result.stdout.strip()
    WINDOWSAPPS_AUMID_CACHE[family_name] = aumid or None
    return WINDOWSAPPS_AUMID_CACHE[family_name]


def windowsapps_aumid_for_path(path: Path) -> str | None:
    if not is_windowsapps_path(path):
        return None
    family = windowsapps_family_name(path)
    return resolve_app_user_model_id(family) if family else None


def is_launchable_windowsapps_path(path: Path) -> bool:
    return windowsapps_aumid_for_path(path) is not None


def is_deletable_original_path(path: Path) -> bool:
    try:
        return path.exists() and not is_windowsapps_path(path)
    except Exception:
        return False


def launch_packaged_app(path: Path) -> bool:
    """Launch a packaged (WindowsApps) app via its AppsFolder AUMID."""
    aumid = windowsapps_aumid_for_path(path)
    if not aumid:
        return False
    try:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"], creationflags=_no_window_flag())
        return True
    except Exception:
        return False


def office_family_for_suffix(suffix: str) -> str | None:
    ext = suffix.lower()
    if ext in WORD_EXTS:
        return "word"
    if ext in POWERPOINT_EXTS:
        return "powerpoint"
    if ext in EXCEL_EXTS:
        return "excel"
    return None


def is_office_document_path(path: Path) -> bool:
    return bool(office_family_for_suffix(path.suffix) and path.is_file())


PREVIEW_HANDLER_SHELLEX = "{8895b1c6-b41f-4c1c-a562-0d564250836f}"
MICROSOFT_OFFICE_PREVIEW_HANDLER_CLSIDS = {
    "word": "{84F66100-FF7C-4fb4-B0C0-02CD7FB668FE}",
    "excel": "{00020827-0000-0000-C000-000000000046}",
    "powerpoint": "{65235197-874B-4A07-BDC5-E65EA825B718}",
}
_COM_PREVIEW_INTERFACES = None
_PREVIEW_HANDLER_CLSID_CACHE: dict[str, str | None] = {}


def normalize_clsid(value: str | None) -> str:
    return str(value or "").strip().casefold()


def preview_handler_clsid_for_path(path: Path) -> str | None:
    """Return the registered Windows Preview Handler CLSID for a file, if any."""
    if sys.platform != "win32":
        return None
    suffix = path.suffix.lower()
    if not suffix:
        return None
    if suffix in _PREVIEW_HANDLER_CLSID_CACHE:
        return _PREVIEW_HANDLER_CLSID_CACHE[suffix]
    try:
        import winreg
    except Exception:
        _PREVIEW_HANDLER_CLSID_CACHE[suffix] = None
        return None

    def read_default(subkey: str) -> str | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            return None
        text = str(value or "").strip()
        return text or None

    candidates = [
        f"{suffix}\\shellex\\{PREVIEW_HANDLER_SHELLEX}",
        f"SystemFileAssociations\\{suffix}\\shellex\\{PREVIEW_HANDLER_SHELLEX}",
    ]
    prog_id = read_default(suffix)
    if prog_id:
        candidates.append(f"{prog_id}\\shellex\\{PREVIEW_HANDLER_SHELLEX}")
    for subkey in candidates:
        clsid = read_default(subkey)
        if clsid:
            _PREVIEW_HANDLER_CLSID_CACHE[suffix] = clsid
            return clsid
    _PREVIEW_HANDLER_CLSID_CACHE[suffix] = None
    return None


def _preview_com_interfaces():
    global _COM_PREVIEW_INTERFACES
    if _COM_PREVIEW_INTERFACES is not None:
        return _COM_PREVIEW_INTERFACES
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IInitializeWithFile(IUnknown):
        _iid_ = GUID("{b7d14566-0509-4cce-a71f-0a554233bd9b}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize",
                      (["in"], wintypes.LPCWSTR, "pszFilePath"),
                      (["in"], wintypes.DWORD, "grfMode")),
        ]

    class IPreviewHandler(IUnknown):
        _iid_ = GUID(PREVIEW_HANDLER_SHELLEX)
        _methods_ = [
            COMMETHOD([], HRESULT, "SetWindow",
                      (["in"], wintypes.HWND, "hwnd"),
                      (["in"], ctypes.POINTER(_Rect), "prc")),
            COMMETHOD([], HRESULT, "SetRect",
                      (["in"], ctypes.POINTER(_Rect), "prc")),
            COMMETHOD([], HRESULT, "DoPreview"),
            COMMETHOD([], HRESULT, "Unload"),
            COMMETHOD([], HRESULT, "SetFocus"),
            COMMETHOD([], HRESULT, "QueryFocus",
                      (["out"], ctypes.POINTER(wintypes.HWND), "phwnd")),
            COMMETHOD([], HRESULT, "TranslateAccelerator",
                      (["in"], ctypes.POINTER(_Msg), "pmsg")),
        ]

    _COM_PREVIEW_INTERFACES = (comtypes, IInitializeWithFile, IPreviewHandler)
    return _COM_PREVIEW_INTERFACES


def windows_preview_handler_available(path: Path) -> bool:
    return preview_handler_clsid_for_path(path) is not None


def office_fast_preview_clsid_for_path(path: Path) -> str | None:
    """Use only Microsoft's Office Preview Handlers; skip WPS-registered handlers."""
    family = office_family_for_suffix(path.suffix)
    if not family:
        return None
    clsid = preview_handler_clsid_for_path(path)
    expected = MICROSOFT_OFFICE_PREVIEW_HANDLER_CLSIDS.get(family)
    if clsid and expected and normalize_clsid(clsid) == normalize_clsid(expected):
        return clsid
    return None


def _office_executable_names(suite: str, family: str) -> list[str]:
    if suite == OFFICE_OPEN_MODE_WPS:
        return {"word": ["wps.exe"], "excel": ["et.exe"], "powerpoint": ["wpp.exe"]}.get(family, [])
    if suite == OFFICE_OPEN_MODE_LIBRE:
        # LibreOffice 用同一个 soffice 启动器打开所有文档类型。
        return ["soffice.exe", "soffice.com"]
    return {"word": ["WINWORD.EXE"], "excel": ["EXCEL.EXE"], "powerpoint": ["POWERPNT.EXE"]}.get(family, [])


def find_office_suite_executable(suite: str, family: str) -> Path | None:
    names = _office_executable_names(suite, family)
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = []
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    for root in roots:
        if not root.exists():
            continue
        if suite == OFFICE_OPEN_MODE_WPS:
            patterns = [f"Kingsoft/WPS Office/**/office6/{name}" for name in names]
            patterns += [f"WPS Office/**/office6/{name}" for name in names]
        elif suite == OFFICE_OPEN_MODE_LIBRE:
            patterns = [f"LibreOffice/program/{name}" for name in names]
            patterns += [f"LibreOffice */program/{name}" for name in names]
        else:
            patterns = []
            for name in names:
                patterns.extend([
                    f"Microsoft Office/root/Office*/{name}",
                    f"Microsoft Office/Office*/{name}",
                ])
        for pattern in patterns:
            try:
                matches = sorted(root.glob(pattern), key=lambda candidate: len(str(candidate)))
            except OSError:
                matches = []
            for candidate in matches:
                if candidate.exists() and candidate.is_file():
                    return candidate
    return None


def launch_office_document_with_suite(path: Path, suite: str) -> bool:
    family = office_family_for_suffix(path.suffix)
    if not family:
        return False
    exe = find_office_suite_executable(suite, family)
    if exe is None:
        return False
    subprocess.Popen([str(exe), str(path)], cwd=str(path.parent), creationflags=_no_window_flag())
    return True


def find_vscode_executable() -> Path | None:
    for name in ("code.exe", "Code.exe", "code.cmd", "code"):
        found = shutil.which(name)
        if found:
            return Path(found)
    roots = [
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    candidates = []
    for root in roots:
        if not root:
            continue
        base = Path(root)
        candidates.extend([
            base / "Programs" / "Microsoft VS Code" / "Code.exe",
            base / "Programs" / "Microsoft VS Code Insiders" / "Code - Insiders.exe",
            base / "Microsoft VS Code" / "Code.exe",
            base / "Microsoft VS Code Insiders" / "Code - Insiders.exe",
        ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def launch_vscode_file(path: Path) -> bool:
    exe = find_vscode_executable()
    if exe is None:
        return False
    subprocess.Popen([str(exe), str(path)], cwd=str(path.parent), creationflags=_no_window_flag())
    return True


def office_open_mode_available(mode: str) -> bool:
    mode = normalize_office_open_mode(mode)
    if mode == OFFICE_OPEN_MODE_BUILTIN:
        return True
    if mode == OFFICE_OPEN_MODE_LIBRE:
        return find_office_suite_executable(mode, "word") is not None
    families = ("word", "excel", "powerpoint")
    return any(find_office_suite_executable(mode, family) is not None for family in families)


def code_open_mode_available(mode: str) -> bool:
    mode = normalize_code_open_mode(mode)
    if mode == CODE_OPEN_MODE_BUILTIN:
        return True
    if mode == CODE_OPEN_MODE_VSCODE:
        return find_vscode_executable() is not None
    return False


def open_target(item: DockItem) -> None:
    if item.kind == "url":
        webbrowser.open(item.target)
        return
    path = Path(item.target)
    if sys.platform == "win32":
        # Packaged WindowsApps apps (e.g. Claude, Codex) can't be stat'd or launched by
        # full path; route them through their AppsFolder AUMID instead.
        if is_windowsapps_path(path) and launch_packaged_app(path):
            return
        if not path.exists():
            raise FileNotFoundError(item.target)
        # Grant foreground rights so an already-running single-instance app (e.g.
        # Clash) can bring its existing window forward — exactly what happens when
        # you double-click the desktop shortcut. Without this, os.startfile leaves
        # the app's focus call blocked and it looks like "nothing happened".
        allow_any_foreground()
        if path.suffix.lower() == ".exe":
            launch_executable(path)
        else:
            shell_open(path)
        return
    if not path.exists():
        raise FileNotFoundError(item.target)
    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def reveal_target(item: DockItem) -> None:
    if item.kind == "url":
        return
    path = Path(item.target)
    if not path.exists():
        raise FileNotFoundError(item.target)
    if sys.platform == "win32":
        if path.is_dir():
            subprocess.Popen(["explorer.exe", str(path)])
        else:
            subprocess.Popen(["explorer.exe", f"/select,{path}"])
    else:
        parent = path if path.is_dir() else path.parent
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(parent)])


class _ShFileOpStruct(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400


def move_path_to_recycle_bin(path: Path, hwnd=None) -> None:
    if sys.platform != "win32":
        raise RuntimeError("删除原文件目前只支持 Windows。")

    shell32 = ctypes.windll.shell32
    operation = _ShFileOpStruct()
    operation.hwnd = hwnd or None
    operation.wFunc = FO_DELETE
    operation.pFrom = str(path) + "\0\0"
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None

    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise OSError(result, "移到回收站失败")
    if operation.fAnyOperationsAborted:
        raise RuntimeError("操作已取消。")


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


TH32CS_SNAPPROCESS = 0x00000002


_TOOLHELP_READY = False


def _ensure_toolhelp_signatures(kernel32) -> None:
    """Set kernel32 toolhelp argtypes/restypes once instead of on every poll."""
    global _TOOLHELP_READY
    if _TOOLHELP_READY:
        return
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    _TOOLHELP_READY = True


def _scan_processes(match=None) -> set[str]:
    """Walk the process snapshot once.

    If ``match`` is given, stop and return ``{name}`` as soon as a process whose
    lower-cased name is in ``match`` is found (cheap membership probe). Otherwise
    return the full lower-cased name set.
    """
    if sys.platform != "win32":
        return set()
    names: set[str] = set()
    try:
        kernel32 = ctypes.windll.kernel32
        _ensure_toolhelp_signatures(kernel32)
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == wintypes.HANDLE(-1).value or not snapshot:
            return names
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile.lower()
                if match is not None:
                    if name in match:
                        return {name}
                else:
                    names.add(name)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return names
    return names


def running_process_names() -> set[str]:
    """Lower-cased set of running process executable names (Windows)."""
    return _scan_processes()


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_QUERY_IMAGE_READY = False


def _ensure_image_path_signatures(kernel32) -> None:
    """Set OpenProcess / QueryFullProcessImageNameW argtypes once."""
    global _QUERY_IMAGE_READY
    if _QUERY_IMAGE_READY:
        return
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    _QUERY_IMAGE_READY = True


def _process_image_path(kernel32, pid: int) -> str:
    """Full executable path for a PID, or "" if it can't be queried."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(handle)
    return ""


def running_processes_detailed() -> list[dict]:
    """List of {'pid','name','path'} for running processes (Windows)."""
    if sys.platform != "win32":
        return []
    procs: list[dict] = []
    try:
        kernel32 = ctypes.windll.kernel32
        _ensure_toolhelp_signatures(kernel32)
        _ensure_image_path_signatures(kernel32)
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == wintypes.HANDLE(-1).value or not snapshot:
            return procs
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                if pid:
                    procs.append({
                        "pid": pid,
                        "name": entry.szExeFile,
                        "path": _process_image_path(kernel32, pid),
                    })
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return procs
    return procs


def _process_matches_target(proc_path: str, proc_name: str, target: Path) -> bool:
    """True if a running process belongs to a Passer item's target.

    Prefers a full-path match (exact exe, or an exe living inside a target
    folder). When the image path can't be read (protected/system process), it
    falls back to an exe-name match for ``.exe`` targets only.
    """
    try:
        target_norm = os.path.normcase(os.path.normpath(str(target)))
    except Exception:
        return False
    if proc_path:
        proc_norm = os.path.normcase(os.path.normpath(proc_path))
        if proc_norm == target_norm:
            return True
        try:
            if target.is_dir() and proc_norm.startswith(target_norm + os.sep):
                return True
        except Exception:
            pass
        return False
    if target.suffix.lower() == ".exe":
        return proc_name.lower() == target.name.lower()
    return False


def _audio_volume_controls_for_targets(targets: list[Path]) -> list:
    """ISimpleAudioVolume controls whose owning process matches a Passer target."""
    controls: list = []
    if not targets or not _ensure_pycaw():
        return controls
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return controls
    for session in sessions:
        proc = getattr(session, "Process", None)
        if proc is None:
            continue
        try:
            name = proc.name()
        except Exception:
            continue
        try:
            path = proc.exe()
        except Exception:
            path = ""
        if any(_process_matches_target(path, name, tgt) for tgt in targets):
            volume = getattr(session, "SimpleAudioVolume", None)
            if volume is not None:
                controls.append(volume)
    return controls


def targets_audio_muted(targets: list[Path]) -> bool:
    """True if any audio session of the given targets is currently muted."""
    for volume in _audio_volume_controls_for_targets(targets):
        try:
            if volume.GetMute():
                return True
        except Exception:
            continue
    return False


def set_targets_audio_muted(targets: list[Path], mute: bool) -> int:
    """Mute/unmute every audio session of the given targets; return count changed."""
    controls = _audio_volume_controls_for_targets(targets)
    changed = 0
    for volume in controls:
        try:
            volume.SetMute(1 if mute else 0, None)
            changed += 1
        except Exception:
            continue
    return changed


def swallow_next_right_click_release(timeout_ms: int = 400) -> None:
    """Temporarily swallow the right-button release that follows screenshot cancel."""
    return


def is_wechat_running() -> bool:
    return bool(_scan_processes(match=frozenset(WECHAT_PROCESS_NAMES)))


def _resolve_get_async_key_state():
    """Resolve user32.GetAsyncKeyState once; reused by the 35 ms hotkey poll."""
    if sys.platform != "win32":
        return None
    try:
        fn = ctypes.windll.user32.GetAsyncKeyState
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_short
        return fn
    except Exception:
        return None


_GET_ASYNC_KEY_STATE = _resolve_get_async_key_state()


def is_key_down(vk: int) -> bool:
    """True while the given virtual-key is physically down (cached fn pointer)."""
    fn = _GET_ASYNC_KEY_STATE
    if fn is None:
        return False
    try:
        return bool(fn(vk) & 0x8000)
    except Exception:
        return False


def is_global_alt_a_down() -> bool:
    fn = _GET_ASYNC_KEY_STATE
    if fn is None:
        return False
    try:
        return bool(fn(VK_MENU) & 0x8000) and bool(fn(VK_A) & 0x8000)
    except Exception:
        return False


def is_global_alt_p_down() -> bool:
    fn = _GET_ASYNC_KEY_STATE
    if fn is None:
        return False
    try:
        return bool(fn(VK_MENU) & 0x8000) and bool(fn(VK_P) & 0x8000)
    except Exception:
        return False


def copy_image_to_clipboard(image: "Image.Image") -> bool:
    """Place a PIL image onto the Windows clipboard as CF_DIB."""
    if sys.platform != "win32":
        return False
    try:
        output = io.BytesIO()
        image.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]  # strip 14-byte BMP file header -> DIB
        output.close()

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_DIB, handle)
        finally:
            user32.CloseClipboard()
        return True
    except Exception:
        return False


def copy_paths_to_clipboard(paths: list[str]) -> bool:
    """Place file/folder paths onto the Windows clipboard as a copy operation."""
    if sys.platform != "win32" or not paths:
        return False
    try:
        normalized = [str(Path(path)) for path in paths if path]
        if not normalized:
            return False

        encoded_paths = ("\0".join(normalized) + "\0\0").encode("utf-16le")
        dropfiles_size = 20
        data_size = dropfiles_size + len(encoded_paths)

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

        GMEM_MOVEABLE = 0x0002
        CF_HDROP = 15
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, data_size)
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False

        header = (
            int(dropfiles_size).to_bytes(4, "little")
            + int(0).to_bytes(4, "little", signed=True)
            + int(0).to_bytes(4, "little", signed=True)
            + int(0).to_bytes(4, "little")
            + int(1).to_bytes(4, "little")
        )
        ctypes.memmove(pointer, header + encoded_paths, data_size)
        kernel32.GlobalUnlock(handle)

        effect_handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(wintypes.DWORD))
        if not effect_handle:
            kernel32.GlobalFree(handle)
            return False
        effect_pointer = kernel32.GlobalLock(effect_handle)
        if not effect_pointer:
            kernel32.GlobalFree(handle)
            kernel32.GlobalFree(effect_handle)
            return False
        ctypes.memmove(effect_pointer, int(1).to_bytes(4, "little"), 4)
        kernel32.GlobalUnlock(effect_handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            kernel32.GlobalFree(effect_handle)
            return False
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_HDROP, handle)
            preferred = user32.RegisterClipboardFormatW("Preferred DropEffect")
            if preferred:
                user32.SetClipboardData(preferred, effect_handle)
        finally:
            user32.CloseClipboard()
        return True
    except Exception:
        return False


def capture_virtual_screen() -> "Image.Image | None":
    """Grab the whole virtual desktop (all monitors) as an RGB image."""
    if not PIL_AVAILABLE:
        return None
    try:
        img = ImageGrab.grab(all_screens=True)
        return img if img.mode == "RGB" else img.convert("RGB")
    except Exception:
        try:
            img = ImageGrab.grab()
            return img if img.mode == "RGB" else img.convert("RGB")
        except Exception:
            return None


AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "Passer"


def autostart_command() -> str:
    """Command line used for the Windows startup (Run) registry entry."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = str(Path(__file__).resolve())
    exe = sys.executable
    pythonw = Path(exe).with_name("pythonw.exe")
    if pythonw.exists():
        exe = str(pythonw)
    return f'"{exe}" "{script}"'


def get_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart_enabled(enable: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            if enable:
                winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def find_zotero_executable() -> str | None:
    """Locate zotero.exe via the registered ``zotero:`` protocol handler or common paths."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        candidates = (
            (winreg.HKEY_CLASSES_ROOT, r"zotero\shell\open\command"),
            (winreg.HKEY_CURRENT_USER, r"Software\Classes\zotero\shell\open\command"),
        )
        for hive, sub in candidates:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                match = re.search(r'"([^"]+zotero\.exe)"', value, re.IGNORECASE) or re.search(
                    r"(\S+zotero\.exe)", value, re.IGNORECASE
                )
                if match and Path(match.group(1)).exists():
                    return match.group(1)
            except OSError:
                continue
    except Exception:
        pass
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Zotero" / "zotero.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Zotero" / "zotero.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Zotero" / "zotero.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def find_photoshop_executable() -> str | None:
    if sys.platform != "win32":
        return None
    direct = shutil.which("Photoshop.exe")
    if direct and Path(direct).exists():
        return direct
    try:
        import winreg

        keys = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe"),
            (winreg.HKEY_CLASSES_ROOT, r"Applications\Photoshop.exe\shell\open\command"),
        )
        for hive, subkey in keys:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                match = re.search(r'"([^"]+Photoshop\.exe)"', str(value), re.IGNORECASE)
                candidate = match.group(1) if match else str(value).strip(' "')
                if Path(candidate).exists():
                    return candidate
            except OSError:
                continue
    except Exception:
        pass

    roots = []
    for drive in ("C", "D", "E"):
        roots.extend(
            [
                Path(f"{drive}:\\Program Files\\Adobe"),
                Path(f"{drive}:\\Program Files"),
                Path(f"{drive}:\\Adobe"),
            ]
        )
    candidates = []
    for root in roots:
        try:
            candidates.extend(root.glob("Adobe Photoshop*/Photoshop.exe"))
            candidates.extend(root.glob("Photoshop*/Photoshop.exe"))
        except OSError:
            continue
    existing = [candidate for candidate in candidates if candidate.exists()]
    if existing:
        return str(sorted(existing, reverse=True)[0])

    desktop_roots = (
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
    )
    for desktop in desktop_roots:
        try:
            shortcuts = list(desktop.glob("*Photoshop*.lnk"))
        except OSError:
            continue
        for shortcut in shortcuts:
            target = resolve_windows_lnk(shortcut)
            if target and Path(target).name.lower() == "photoshop.exe" and Path(target).exists():
                return target
    return None


def fit_text_lines(text: str, font, max_width: int, max_lines: int = 2) -> str:
    """Wrap ``text`` to at most ``max_lines`` lines within ``max_width`` px, ellipsising overflow."""
    lines: list[str] = []
    current = ""
    for index, ch in enumerate(text):
        if font.measure(current + ch) <= max_width:
            current += ch
            continue
        lines.append(current)
        current = ch
        if len(lines) >= max_lines:
            last = lines[max_lines - 1]
            while last and font.measure(last + "…") > max_width:
                last = last[:-1]
            lines[max_lines - 1] = last + "…"
            return "\n".join(lines[:max_lines])
    if current:
        lines.append(current)
    return "\n".join(lines)


def virtual_screen_origin() -> tuple[int, int]:
    """Top-left of the virtual screen (can be negative with multiple monitors)."""
    if sys.platform != "win32":
        return 0, 0
    try:
        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        return user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    except Exception:
        return 0, 0


def enumerate_window_rects(exclude_hwnds=()) -> list[tuple[int, int, int, int]]:
    """Visible top-level window rectangles in virtual-screen pixels, topmost first.

    Uses the DWM extended frame bounds (true visible edges, no drop shadow) and
    skips minimised, cloaked and shell/desktop windows. Used for WeChat-style
    snap-to-window during screenshot capture.
    """
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    try:
        dwmapi = ctypes.windll.dwmapi
    except Exception:
        dwmapi = None
    exclude = {int(h) for h in exclude_hwnds if h}
    skip_classes = {"Progman", "WorkerW", "SysShadow", "Button"}
    DWMWA_CLOAKED = 14
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    results: list[tuple[int, int, int, int]] = []

    def is_cloaked(hwnd) -> bool:
        if not dwmapi:
            return False
        value = ctypes.c_int(0)
        try:
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            return False
        return value.value != 0

    def frame_rect(hwnd) -> _Rect | None:
        if dwmapi:
            rect = _Rect()
            try:
                if dwmapi.DwmGetWindowAttribute(
                    hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
                ) == 0 and (rect.right - rect.left) > 0:
                    return rect
            except Exception:
                pass
        rect = _Rect()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect
        return None

    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        try:
            if int(hwnd) in exclude:
                return True
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            class_buffer = ctypes.create_unicode_buffer(80)
            user32.GetClassNameW(hwnd, class_buffer, 80)
            if class_buffer.value in skip_classes:
                return True
            if is_cloaked(hwnd):
                return True
            rect = frame_rect(hwnd)
            if rect is None:
                return True
            if (rect.right - rect.left) <= 8 or (rect.bottom - rect.top) <= 8:
                return True
            results.append((rect.left, rect.top, rect.right, rect.bottom))
        except Exception:
            return True
        return True

    try:
        user32.EnumWindows(enum_proc_type(callback), 0)
    except Exception:
        return []
    return results


def load_font(size: int, bold: bool = False):
    if not PIL_AVAILABLE:
        return None
    candidates = []
    if APP_FONT_FILE:
        candidates.append(APP_FONT_FILE)
    candidates.extend(
        [
            APP_FONT_PREFERRED_BOLD_FILE if bold else APP_FONT_PREFERRED_FILE,
            APP_FONT_PREFERRED_FILE,
        ]
    )
    candidates.extend([
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / ("msyhbd.ttc" if bold else "msyh.ttc"),
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "simhei.ttf",
    ])
    for path in candidates:
        try:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    return ImageFont.load_default()


def make_badge(label: str, color: tuple[int, int, int]) -> "Image.Image":
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, 58, 58), radius=10, fill=color, outline=(255, 255, 255), width=2)
    font = load_font(24, bold=True)
    bbox = draw.textbbox((0, 0), label, font=font)
    x = (64 - (bbox[2] - bbox[0])) / 2
    y = (64 - (bbox[3] - bbox[1])) / 2 - 2
    draw.text((x, y), label, fill=(255, 255, 255), font=font)
    return image


def make_thumbnail(path: Path) -> "Image.Image | None":
    if not PIL_AVAILABLE or not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        return None
    try:
        with Image.open(path) as original:
            thumb = original.convert("RGBA")
            thumb.thumbnail((68, 68), Image.LANCZOS)
            canvas = Image.new("RGBA", (72, 72), (250, 250, 250, 0))
            x = (72 - thumb.width) // 2
            y = (72 - thumb.height) // 2
            canvas.alpha_composite(thumb, (x, y))
            draw = ImageDraw.Draw(canvas)
            draw.rounded_rectangle((1, 1, 70, 70), radius=8, outline=(180, 180, 180), width=1)
            return canvas
    except Exception:
        return None


class _ShFileInfo(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 1),
    ]


SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
BI_RGB = 0
DIB_RGB_COLORS = 0
DI_NORMAL = 0x0003


def _configure_shell_icon_api() -> bool:
    if sys.platform != "win32" or not PIL_AVAILABLE:
        return False

    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        shell32.SHGetFileInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(_ShFileInfo),
            wintypes.UINT,
            wintypes.UINT,
        ]
        shell32.SHGetFileInfoW.restype = ctypes.c_void_p

        user32.DestroyIcon.argtypes = [wintypes.HANDLE]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.DrawIconEx.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        user32.DrawIconEx.restype = wintypes.BOOL

        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteObject.restype = wintypes.BOOL
        return True
    except Exception:
        return False


SHELL_ICON_API_READY = _configure_shell_icon_api()
_SHELL_ICON_IMAGE_CACHE: dict[tuple[str, bool, bool], object] = {}
_SHELL_ICON_IMAGE_CACHE_LOCK = threading.Lock()
_SHELL_ICON_CACHE_MISSING = object()
_SHELL_ICON_CACHE_LIMIT = 256


def clear_shell_icon_cache() -> None:
    with _SHELL_ICON_IMAGE_CACHE_LOCK:
        _SHELL_ICON_IMAGE_CACHE.clear()


def _hicon_to_image(hicon, size: int = SHELL_ICON_SIZE) -> "Image.Image | None":
    if not SHELL_ICON_API_READY or not hicon:
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    screen_dc = user32.GetDC(None)
    mem_dc = None
    bitmap = None
    old_bitmap = None

    try:
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap_info = _BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = size
        bitmap_info.bmiHeader.biHeight = -size
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = BI_RGB

        bitmap = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits:
            return None

        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
        byte_count = size * size * 4
        ctypes.memset(bits, 0, byte_count)
        if not user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, None, DI_NORMAL):
            return None

        data = ctypes.string_at(bits, byte_count)
        image = Image.frombuffer("RGBA", (size, size), data, "raw", "BGRA", 0, 1).copy()
        return image
    finally:
        if old_bitmap:
            gdi32.SelectObject(mem_dc, old_bitmap)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        if screen_dc:
            user32.ReleaseDC(None, screen_dc)


def _shell_icon_image(path: str, is_folder: bool = False, use_attributes: bool = False) -> "Image.Image | None":
    if not SHELL_ICON_API_READY:
        return None

    key = (os.path.normcase(str(path)), bool(is_folder), bool(use_attributes))
    with _SHELL_ICON_IMAGE_CACHE_LOCK:
        cached = _SHELL_ICON_IMAGE_CACHE.get(key, _SHELL_ICON_CACHE_MISSING)
    if cached is not _SHELL_ICON_CACHE_MISSING:
        return cached.copy() if cached is not None else None

    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    info = _ShFileInfo()
    attributes = FILE_ATTRIBUTE_DIRECTORY if is_folder else FILE_ATTRIBUTE_NORMAL
    flags = SHGFI_ICON | SHGFI_LARGEICON
    if use_attributes:
        flags |= SHGFI_USEFILEATTRIBUTES

    result = shell32.SHGetFileInfoW(
        str(path),
        attributes,
        ctypes.byref(info),
        ctypes.sizeof(_ShFileInfo),
        flags,
    )
    if not result or not info.hIcon:
        with _SHELL_ICON_IMAGE_CACHE_LOCK:
            _SHELL_ICON_IMAGE_CACHE[key] = None
        return None

    try:
        image = _hicon_to_image(info.hIcon)
    finally:
        user32.DestroyIcon(info.hIcon)
    with _SHELL_ICON_IMAGE_CACHE_LOCK:
        if len(_SHELL_ICON_IMAGE_CACHE) >= _SHELL_ICON_CACHE_LIMIT:
            _SHELL_ICON_IMAGE_CACHE.pop(next(iter(_SHELL_ICON_IMAGE_CACHE)), None)
        _SHELL_ICON_IMAGE_CACHE[key] = image.copy() if image is not None else None
    return image


def _icon_request_for_item(item: DockItem) -> tuple[str, bool, bool]:
    if item.kind == "url":
        return ".url", False, True

    path = Path(item.target)
    if item.kind == "folder":
        return "folder", True, True

    if path.suffix.lower() == ".exe" and path.exists():
        return str(path), False, False

    # A shortcut file on disk: pull the real icon the shell shows for it (the
    # target's icon with the shortcut overlay) by reading the actual file
    # instead of USEFILEATTRIBUTES, which would only yield the generic .lnk icon.
    # This covers shortcuts whose target didn't resolve (e.g. Store/UWP apps),
    # which otherwise keep the .lnk as their target and render blank.
    if path.suffix.lower() in (".lnk", ".url") and path.exists():
        return str(path), False, False

    suffix = path.suffix or ".txt"
    return suffix, False, True


def image_for_item(item: DockItem):
    if not PIL_AVAILABLE:
        return None

    if item.kind == BUILTIN_TOOL_KIND:
        icon = builtin_tool_icon_image(item.target, 48)
        return ImageTk.PhotoImage(icon) if icon is not None else None
    if item.kind == "map_location":
        icon = builtin_tool_icon_image(BUILTIN_MAP_TARGET, 48)
        return ImageTk.PhotoImage(icon) if icon is not None else None

    # Image items show a thumbnail of the picture itself.
    if item.kind == "image":
        thumb = make_thumbnail(Path(item.target))
        if thumb is not None:
            return ImageTk.PhotoImage(thumb)

    path, is_folder, use_attributes = _icon_request_for_item(item)
    image = _shell_icon_image(path, is_folder=is_folder, use_attributes=use_attributes)
    if image is None and item.kind != "url":
        image = _shell_icon_image(Path(item.target).suffix or ".txt", use_attributes=True)
    return ImageTk.PhotoImage(image) if image is not None else None


def pil_icon_for_item(item: DockItem, size: int = 44) -> "Image.Image | None":
    """Return a square PIL icon for an item (used to compose group thumbnails)."""
    if not PIL_AVAILABLE:
        return None
    if item.kind == BUILTIN_TOOL_KIND:
        return builtin_tool_icon_image(item.target, size)
    if item.kind == "map_location":
        return builtin_tool_icon_image(BUILTIN_MAP_TARGET, size)
    image = None
    if item.kind == "image":
        image = make_thumbnail(Path(item.target))
    if image is None:
        path, is_folder, use_attributes = _icon_request_for_item(item)
        image = _shell_icon_image(path, is_folder=is_folder, use_attributes=use_attributes)
        if image is None and item.kind != "url":
            image = _shell_icon_image(Path(item.target).suffix or ".txt", use_attributes=True)
    if image is None:
        return None
    try:
        image = image.convert("RGBA")
        fitted = ImageOps.contain(image, (size, size))
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2), fitted)
        return canvas
    except Exception:
        return None


def group_icon_image(members: list[DockItem], box: int = 104):
    """Compose member icons into a compact 2x2 / 3x3 / 4x4 group thumbnail.

    The group tile draws its own full-size rounded frame, so this image carries
    only the member icons (no background) to sit centred inside that frame.
    """
    if not PIL_AVAILABLE:
        return None
    count = min(len(members), GROUP_MAX_MEMBERS)
    if count >= 10:
        cols = 4
        gap = 4
        inner_pad = 4
    elif count >= 5:
        cols = 3
        gap = 6
        inner_pad = 5
    else:
        cols = 2
        gap = 8
        inner_pad = 6
    rows = cols
    cell = max(1, (box - gap * (cols - 1)) // cols)
    used = cell * cols + gap * (cols - 1)
    margin = (box - used) // 2
    try:
        canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
        for index, member in enumerate(members[:GROUP_MAX_MEMBERS]):
            row, col = divmod(index, cols)
            if row >= rows:
                break
            px = margin + col * (cell + gap)
            py = margin + row * (cell + gap)
            icon_padding = min(inner_pad, max(0, cell - 4))
            icon = pil_icon_for_item(member, max(1, cell - icon_padding))
            if icon is None:
                continue
            canvas.paste(icon, (px + (cell - icon.width) // 2, py + (cell - icon.height) // 2), icon)
        return ImageTk.PhotoImage(canvas)
    except Exception:
        return None


def create_round_rect(canvas, x0, y0, x1, y1, radius=12, **kwargs):
    """Draw a rounded rectangle on a tk.Canvas and return its item id.

    Accepts the same ``outline`` / ``fill`` / ``width`` kwargs as
    ``create_rectangle`` so callers (and later ``itemconfigure`` calls) need no
    other changes. Implemented as a smoothed polygon, whose item id behaves like
    a normal canvas item.
    """
    radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    points = [
        x0 + radius, y0,
        x1 - radius, y0,
        x1, y0,
        x1, y0 + radius,
        x1, y1 - radius,
        x1, y1,
        x1 - radius, y1,
        x0 + radius, y1,
        x0, y1,
        x0, y1 - radius,
        x0, y0 + radius,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return (250, 62, 62)


def _draw_arrow_pil(draw, p0, p1, color, width) -> None:
    x0, y0 = p0
    x1, y1 = p1
    draw.line((x0, y0, x1, y1), fill=color, width=width)
    angle = math.atan2(y1 - y0, x1 - x0)
    head = max(10, width * 4)
    for delta in (math.radians(152), math.radians(-152)):
        hx = x1 + head * math.cos(angle + delta)
        hy = y1 + head * math.sin(angle + delta)
        draw.line((x1, y1, hx, hy), fill=color, width=width)
