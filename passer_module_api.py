from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path, PurePosixPath
from typing import Any, Callable


API_VERSION = 1
MOD_EVENTS = frozenset({
    "app_ready", "items_changed", "before_item_open", "after_item_open",
    "search_changed", "theme_changed", "shutdown",
})
MOD_PERMISSIONS = frozenset({
    "ui", "state", "items", "aira", "events",
    "filesystem", "network", "process", "browser",
})
MOD_DEFAULT_PERMISSIONS = frozenset({"ui", "state"})


@dataclass(frozen=True)
class BuiltinToolContext:
    """Stable API passed to an installed built-in tool's open_tool(context)."""

    root: Any
    theme: Any
    app_font: Callable[..., Any]
    colors: dict[str, str]
    module_id: str
    module_dir: Path
    _place_window: Callable[[Any], None]
    _write_status: Callable[[str], None]
    _add_paths: Callable[[list[str]], None]
    settings: dict[str, Any] | None = None
    _save_settings: Callable[[], None] | None = None

    def place_window(self, controller: Any) -> None:
        """Center a controller's .window over Passer and keep it above Passer."""
        self._place_window(controller)

    def write_status(self, text: str) -> None:
        self._write_status(str(text))

    def add_paths(self, *paths: str | Path) -> None:
        values = [str(path) for path in paths if str(path)]
        if values:
            self._add_paths(values)

    def save_settings(self) -> None:
        if self._save_settings is not None:
            self._save_settings()


@dataclass(frozen=True)
class ModContext(BuiltinToolContext):
    """Stable API passed to a user/AI-authored MOD's ``open_mod(context)``.

    MOD code lives outside the executable.  The context deliberately exposes a
    small, versioned surface instead of Passer's private application object so
    MODs can survive normal Passer upgrades.
    """

    manifest: dict[str, Any] = field(default_factory=dict)
    data_dir: Path | None = None
    permissions: frozenset[str] = field(default_factory=lambda: MOD_DEFAULT_PERMISSIONS)
    _register_tool: Callable[..., str] | None = None
    _register_toolbar_button: Callable[..., str] | None = None
    _register_ai_action: Callable[..., str] | None = None
    _subscribe_event: Callable[..., str] | None = None
    _register_cleanup: Callable[[Callable[..., Any]], None] | None = None
    _call_later: Callable[[int, Callable[..., Any]], Any] | None = None

    @property
    def mod_id(self) -> str:
        return self.module_id

    def has_permission(self, permission: str) -> bool:
        return str(permission or "").strip().casefold() in self.permissions

    def require_permission(self, permission: str, action: str = "此操作") -> None:
        permission = str(permission or "").strip().casefold()
        if permission not in MOD_PERMISSIONS:
            raise ValueError(f"未知的 MOD 权限：{permission}")
        if permission not in self.permissions:
            raise PermissionError(
                f"MOD {self.mod_id} 未声明 `{permission}` 权限，不能执行{action}。"
            )

    def place_window(self, controller: Any) -> None:
        self.require_permission("ui", "窗口显示")
        super().place_window(controller)

    def write_status(self, text: str) -> None:
        self.require_permission("ui", "状态栏更新")
        super().write_status(text)

    def add_paths(self, *paths: str | Path) -> None:
        self.require_permission("items", "向 Passer 添加项目")
        super().add_paths(*paths)

    def data_path(self, relative: str | Path = "state.json") -> Path:
        """Return a path inside this MOD's persistent data directory."""
        self.require_permission("state", "读取或写入 MOD 状态")
        if self.data_dir is None:
            raise RuntimeError("该上下文没有 MOD 数据目录。")
        value = str(relative or "state.json").replace("\\", "/").strip("/")
        parts = PurePosixPath(value).parts
        if not parts or any(part in ("", ".", "..") or ":" in part for part in parts):
            raise ValueError("MOD 数据路径必须是安全的相对路径。")
        return self.data_dir.joinpath(*parts)

    def load_state(self, default: Any = None, filename: str | Path = "state.json") -> Any:
        """Read JSON state stored beside the MOD, returning *default* if absent."""
        path = self.data_path(filename)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def save_state(self, value: Any, filename: str | Path = "state.json") -> Path:
        """Atomically persist JSON state beside the MOD and return its path."""
        path = self.data_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def register_tool(self, tool_id: str, title: str, open_handler: Callable[..., Any], *,
                      aliases=(), description: str = "", color: str = "#7c3aed") -> str:
        """Add a searchable/pinnable tool implemented by this MOD at runtime."""
        self.require_permission("ui", "注册工具")
        if self._register_tool is None:
            raise RuntimeError("当前 Passer 不支持 MOD 工具注册。")
        if not callable(open_handler):
            raise TypeError("open_handler 必须可调用。")
        return self._register_tool(
            str(tool_id), str(title), open_handler,
            aliases=aliases, description=str(description), color=str(color),
        )

    def register_toolbar_button(self, button_id: str, text: str, handler: Callable[..., Any], *,
                                side: str = "right") -> str:
        """Add a button to Passer's main title bar until the MOD is unloaded."""
        self.require_permission("ui", "注册标题栏按钮")
        if self._register_toolbar_button is None:
            raise RuntimeError("当前 Passer 不支持 MOD 标题栏按钮。")
        if not callable(handler):
            raise TypeError("handler 必须可调用。")
        return self._register_toolbar_button(str(button_id), str(text), handler, side=str(side))

    def register_ai_action(self, name: str, handler: Callable[..., Any], *,
                           description: str = "", requires_full: bool = True) -> str:
        """Expose a namespaced action to Aira, e.g. ``mod.demo.do_work``."""
        self.require_permission("aira", "注册 Aira 动作")
        if self._register_ai_action is None:
            raise RuntimeError("当前 Passer 不支持 MOD AI 动作。")
        if not callable(handler):
            raise TypeError("handler 必须可调用。")
        return self._register_ai_action(
            str(name), handler, description=str(description), requires_full=bool(requires_full),
        )

    def on(self, event: str, handler: Callable[..., Any]) -> str:
        """Subscribe to a stable Passer lifecycle/event hook for this MOD."""
        self.require_permission("events", "订阅 Passer 事件")
        event = str(event or "").strip().casefold()
        if event not in MOD_EVENTS:
            raise ValueError(f"不支持的 MOD 事件：{event}。可用：{', '.join(sorted(MOD_EVENTS))}")
        if self._subscribe_event is None:
            raise RuntimeError("当前 Passer 不支持 MOD 事件订阅。")
        if not callable(handler):
            raise TypeError("handler 必须可调用。")
        return self._subscribe_event(event, handler)

    def register_cleanup(self, callback: Callable[..., Any]) -> None:
        """Register cleanup work to run during disable, reload, or Passer shutdown."""
        if self._register_cleanup is None:
            raise RuntimeError("当前 Passer 不支持 MOD 清理回调。")
        if not callable(callback):
            raise TypeError("callback 必须可调用。")
        self._register_cleanup(callback)

    def call_later(self, delay_ms: int, callback: Callable[..., Any]) -> Any:
        """Schedule a callback on Passer's UI thread; it is cancelled on unload."""
        self.require_permission("events", "调度延迟回调")
        if self._call_later is None:
            raise RuntimeError("当前 Passer 不支持 MOD UI 调度。")
        if not callable(callback):
            raise TypeError("callback 必须可调用。")
        return self._call_later(max(0, int(delay_ms)), callback)
