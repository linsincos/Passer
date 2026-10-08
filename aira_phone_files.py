from __future__ import annotations

"""Bounded, LAN-only file storage for the Aira mobile bridge."""

import base64
import hashlib
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any


PHONE_FILE_ACTIONS = (
    "file_share_list",
    "file_share_download_chunk",
    "file_share_upload_begin",
    "file_share_upload_chunk",
    "file_share_upload_finish",
    "file_share_upload_cancel",
)
MAX_FILE_BYTES = 500 * 1024 * 1024
# Base64 + JSON stays below the bridge's dedicated 700 KiB file-parameter cap.
CHUNK_BYTES = 480 * 1024
MAX_VISIBLE_FILES = 200
UPLOAD_TTL_SECONDS = 60 * 60
_PART_PREFIX = ".aira-upload-"
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_phone_filename(value: str) -> str:
    """Return one safe Windows filename, never a path."""
    text = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(
        "_" if ord(char) < 32 or char in '<>:"/\\|?*' else char
        for char in text
    ).strip(" .")
    text = text[:120].rstrip(" .") or "received.bin"
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        text = "_" + text
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class AiraPhoneFileStore:
    """Expose only files inside a dedicated directory to a paired LAN phone."""

    def __init__(self, folder: str | os.PathLike) -> None:
        self.folder = Path(folder)
        self._lock = threading.RLock()
        self._uploads: dict[str, dict[str, Any]] = {}
        self._hash_cache: dict[str, str] = {}

    def _ensure_folder(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)

    def _file_id(self, path: Path, stat: os.stat_result) -> str:
        payload = f"{path.name}\0{stat.st_size}\0{stat.st_mtime_ns}".encode(
            "utf-8", errors="surrogatepass"
        )
        return hashlib.sha256(payload).hexdigest()

    def _files(self) -> list[dict[str, Any]]:
        self._ensure_folder()
        values: list[dict[str, Any]] = []
        for path in self.folder.iterdir():
            if path.name.startswith(_PART_PREFIX) or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file() or stat.st_size > MAX_FILE_BYTES:
                continue
            values.append({
                "id": self._file_id(path, stat),
                "name": path.name,
                "size": int(stat.st_size),
                "modified": int(stat.st_mtime),
                "_path": path,
            })
        values.sort(key=lambda item: (-int(item["modified"]), str(item["name"]).casefold()))
        return values[:MAX_VISIBLE_FILES]

    def _find_file(self, file_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", str(file_id or "")):
            raise ValueError("文件标识无效。")
        for item in self._files():
            if secrets.compare_digest(str(item["id"]), str(file_id)):
                return item
        raise FileNotFoundError("电脑上的文件已移动、修改或删除，请刷新列表。")

    def _unique_path(self, name: str) -> Path:
        candidate = self.folder / safe_phone_filename(name)
        if not candidate.exists():
            return candidate
        stem = candidate.stem or "received"
        suffix = candidate.suffix
        for index in range(2, 10_000):
            alternate = self.folder / f"{stem} ({index}){suffix}"
            if not alternate.exists():
                return alternate
        raise OSError("共享文件夹中重名文件过多。")

    def _cleanup_stale_uploads(self) -> None:
        now = time.monotonic()
        stale: list[dict[str, Any]] = []
        with self._lock:
            for upload_id, session in list(self._uploads.items()):
                if now - float(session.get("updated", now)) > UPLOAD_TTL_SECONDS:
                    stale.append(self._uploads.pop(upload_id))
        for session in stale:
            try:
                Path(session["temporary"]).unlink(missing_ok=True)
            except OSError:
                pass
        # A process exit forgets the in-memory session map. Clean its old part
        # files as well, while leaving currently tracked uploads alone.
        self._ensure_folder()
        with self._lock:
            active = {Path(item["temporary"]) for item in self._uploads.values()}
        cutoff = time.time() - UPLOAD_TTL_SECONDS
        for temporary in self.folder.glob(f"{_PART_PREFIX}*.part"):
            if temporary in active or temporary.is_symlink():
                continue
            try:
                if temporary.stat().st_mtime < cutoff:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def execute(self, action: str, params: dict[str, Any], device_id: str) -> dict[str, Any]:
        self._cleanup_stale_uploads()
        if action == "file_share_list":
            return self.list_files()
        if action == "file_share_download_chunk":
            return self.download_chunk(params)
        if action == "file_share_upload_begin":
            return self.upload_begin(params, device_id)
        if action == "file_share_upload_chunk":
            return self.upload_chunk(params, device_id)
        if action == "file_share_upload_finish":
            return self.upload_finish(params, device_id)
        if action == "file_share_upload_cancel":
            return self.upload_cancel(params, device_id)
        raise ValueError("未知的手机文件动作。")

    def list_files(self) -> dict[str, Any]:
        files = []
        for item in self._files():
            files.append({key: item[key] for key in ("id", "name", "size", "modified")})
        return {
            "files": files,
            "count": len(files),
            "max_file_bytes": MAX_FILE_BYTES,
            "chunk_bytes": CHUNK_BYTES,
        }

    def download_chunk(self, params: dict[str, Any]) -> dict[str, Any]:
        item = self._find_file(str(params.get("file_id") or ""))
        try:
            offset = int(params.get("offset") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("下载偏移量无效。") from exc
        size = int(item["size"])
        if offset < 0 or offset > size:
            raise ValueError("下载偏移量超出文件范围。")
        path = Path(item["_path"])
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(CHUNK_BYTES)
        next_offset = offset + len(data)
        file_id = str(item["id"])
        with self._lock:
            full_sha256 = self._hash_cache.get(file_id, "")
        if not full_sha256:
            full_sha256 = _sha256_file(path)
            # Refuse a file that changed while it was being read/hashed.
            current = self._find_file(file_id)
            if current["id"] != file_id:
                raise RuntimeError("文件在下载期间发生了变化，请重新开始。")
            with self._lock:
                self._hash_cache[file_id] = full_sha256
        return {
            "file_id": file_id,
            "name": item["name"],
            "size": size,
            "offset": offset,
            "next_offset": next_offset,
            "data": base64.b64encode(data).decode("ascii"),
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "sha256": full_sha256,
            "eof": next_offset >= size,
        }

    def upload_begin(self, params: dict[str, Any], device_id: str) -> dict[str, Any]:
        name = safe_phone_filename(str(params.get("name") or ""))
        try:
            size = int(params.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError("上传文件大小无效。") from exc
        sha256 = str(params.get("sha256") or "").lower()
        if size < 0 or size > MAX_FILE_BYTES:
            raise ValueError("单个文件最大支持 500 MB。")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("上传文件摘要无效。")
        self._ensure_folder()
        upload_id = secrets.token_urlsafe(18)
        final = self._unique_path(name)
        temporary = self.folder / f"{_PART_PREFIX}{upload_id}.part"
        with temporary.open("xb"):
            pass
        session = {
            "device_id": str(device_id),
            "temporary": temporary,
            "final": final,
            "name": final.name,
            "size": size,
            "sha256": sha256,
            "written": 0,
            "updated": time.monotonic(),
        }
        with self._lock:
            self._uploads[upload_id] = session
        return {
            "upload_id": upload_id,
            "name": final.name,
            "size": size,
            "offset": 0,
            "chunk_bytes": CHUNK_BYTES,
        }

    def _upload_session(self, upload_id: str, device_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,40}", str(upload_id or "")):
            raise ValueError("上传标识无效。")
        with self._lock:
            session = self._uploads.get(str(upload_id))
        if session is None:
            raise FileNotFoundError("上传会话已过期，请重新选择文件。")
        if not secrets.compare_digest(str(session["device_id"]), str(device_id)):
            raise PermissionError("上传会话不属于当前手机。")
        return session

    def upload_chunk(self, params: dict[str, Any], device_id: str) -> dict[str, Any]:
        upload_id = str(params.get("upload_id") or "")
        session = self._upload_session(upload_id, device_id)
        try:
            offset = int(params.get("offset"))
        except (TypeError, ValueError) as exc:
            raise ValueError("上传偏移量无效。") from exc
        encoded = str(params.get("data") or "")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("上传分块编码无效。") from exc
        if not data or len(data) > CHUNK_BYTES:
            raise ValueError("上传分块大小无效。")
        chunk_sha256 = str(params.get("chunk_sha256") or "").lower()
        actual_chunk_sha256 = hashlib.sha256(data).hexdigest()
        if not secrets.compare_digest(chunk_sha256, actual_chunk_sha256):
            raise ValueError("上传分块校验失败。")
        with self._lock:
            current = int(session["written"])
            if offset != current:
                raise ValueError(f"上传偏移量不连续，应从 {current} 继续。")
            if current + len(data) > int(session["size"]):
                raise ValueError("上传内容超过声明的文件大小。")
            temporary = Path(session["temporary"])
            with temporary.open("r+b") as stream:
                stream.seek(current)
                stream.write(data)
            session["written"] = current + len(data)
            session["updated"] = time.monotonic()
            written = int(session["written"])
        return {"upload_id": upload_id, "offset": written, "size": int(session["size"])}

    def upload_finish(self, params: dict[str, Any], device_id: str) -> dict[str, Any]:
        upload_id = str(params.get("upload_id") or "")
        session = self._upload_session(upload_id, device_id)
        temporary = Path(session["temporary"])
        if int(session["written"]) != int(session["size"]):
            raise ValueError("文件尚未上传完整。")
        actual_sha256 = _sha256_file(temporary)
        if not secrets.compare_digest(str(session["sha256"]), actual_sha256):
            self.upload_cancel({"upload_id": upload_id}, device_id)
            raise ValueError("完整文件 SHA-256 校验失败，临时文件已删除。")
        final = Path(session["final"])
        os.replace(temporary, final)
        with self._lock:
            self._uploads.pop(upload_id, None)
        stat = final.stat()
        file_id = self._file_id(final, stat)
        with self._lock:
            self._hash_cache[file_id] = actual_sha256
        return {
            "id": file_id,
            "name": final.name,
            "size": int(stat.st_size),
            "sha256": actual_sha256,
        }

    def upload_cancel(self, params: dict[str, Any], device_id: str) -> dict[str, Any]:
        upload_id = str(params.get("upload_id") or "")
        session = self._upload_session(upload_id, device_id)
        with self._lock:
            self._uploads.pop(upload_id, None)
        try:
            Path(session["temporary"]).unlink(missing_ok=True)
        except OSError:
            pass
        return {"cancelled": True, "upload_id": upload_id}
