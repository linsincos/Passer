"""图片取字（OCR）后端：基于 Windows 内置 OCR 引擎（Windows.Media.Ocr）。

无需联网、无需下载模型，识别结果带有逐词的边界框（image 坐标系），
供图片查看器实现「微信式」框选复制。仅在 Windows 上可用；缺少 winsdk
或对应语言包时优雅降级（ocr_available() 返回 False）。
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

# winsdk 为可选依赖：缺失时整个模块降级，调用方据 ocr_available() 判断。
try:
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    _WINSDK_OK = True
except Exception:  # pragma: no cover - 取决于运行环境
    _WINSDK_OK = False


# 优先尝试的识别语言（简体中文 → 英文 → 用户配置语言）。
_PREFERRED_LANGS = ("zh-Hans-CN", "zh-Hans", "en-US")


@dataclass(frozen=True)
class OcrWord:
    """一个可选中的文字单元；CJK 通常为单字，拉丁文为单词。坐标为图片像素坐标。"""

    text: str
    x: float
    y: float
    w: float
    h: float
    line: int  # 所在行序号（从 0 起）

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def ocr_available() -> bool:
    """当前环境是否可用 OCR（已安装 winsdk 且系统存在可用 OCR 引擎）。"""
    if not _WINSDK_OK:
        return False
    try:
        return _build_engine() is not None
    except Exception:
        return False


def unavailable_reason() -> str:
    """OCR 不可用时给用户的提示文案。"""
    if not _WINSDK_OK:
        return "未安装 OCR 依赖（winsdk）。请运行：pip install winsdk"
    return (
        "系统未安装可用的 OCR 语言包。\n"
        "可在「设置 → 时间和语言 → 语言 → 添加语言的可选功能」中安装"
        "“光学字符识别（OCR）”，或在管理员 PowerShell 运行：\n"
        "Add-WindowsCapability -Online -Name Language.OCR~~~zh-CN~0.0.1.0"
    )


def _build_engine():
    """按偏好语言创建 OcrEngine；都不可用时回退到用户配置语言。"""
    for tag in _PREFERRED_LANGS:
        try:
            lang = Language(tag)
        except Exception:
            continue
        if OcrEngine.is_language_supported(lang):
            engine = OcrEngine.try_create_from_language(lang)
            if engine is not None:
                return engine
    return OcrEngine.try_create_from_user_profile_languages()


async def _recognize_async(png_bytes: bytes) -> list[OcrWord]:
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()

    engine = _build_engine()
    if engine is None:
        return []
    result = await engine.recognize_async(bitmap)

    words: list[OcrWord] = []
    for line_index, line in enumerate(result.lines):
        for word in line.words:
            rect = word.bounding_rect
            text = word.text or ""
            if not text.strip():
                continue
            words.append(OcrWord(
                text=text,
                x=float(rect.x), y=float(rect.y),
                w=float(rect.width), h=float(rect.height),
                line=line_index,
            ))
    return words


def recognize_words(image) -> list[OcrWord]:
    """对 PIL 图片做 OCR，返回阅读顺序排序后的文字单元列表。

    内部自建事件循环，可安全地在工作线程中调用（避免阻塞 UI）。
    识别失败或无文字时返回空列表。
    """
    if not _WINSDK_OK or image is None:
        return []

    rgb = image.convert("RGB") if image.mode not in ("RGB", "L") else image
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    loop = asyncio.new_event_loop()
    try:
        words = loop.run_until_complete(_recognize_async(png_bytes))
    finally:
        loop.close()

    # 阅读顺序：先按行，再按行内从左到右。
    words.sort(key=lambda item: (item.line, item.x))
    return words


def _is_wordish(text: str) -> bool:
    """是否为拉丁字母/数字单词（需要用空格分隔），区别于 CJK 单字。"""
    return any(ch.isascii() and ch.isalnum() for ch in text)


def join_words(words: list[OcrWord]) -> str:
    """把（已按阅读顺序排好的）文字单元拼回文本：换行处插入 \\n，
    相邻拉丁单词间补空格，CJK 之间不补空格。"""
    parts: list[str] = []
    prev_line = None
    prev_wordish = False
    for word in words:
        if prev_line is not None and word.line != prev_line:
            parts.append("\n")
            prev_wordish = False
        elif prev_line is not None and prev_wordish and _is_wordish(word.text):
            parts.append(" ")
        parts.append(word.text)
        prev_line = word.line
        prev_wordish = _is_wordish(word.text)
    return "".join(parts).strip()
