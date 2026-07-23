from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import Passer
from passer_module_api import ModContext


MOD_SOURCE = """\
class Controller:
    def __init__(self):
        self.window = None
        self.closed = False

    def close(self):
        self.closed = True


def open_mod(context):
    context.write_status("mod opened")
    return Controller()
"""


class FakeRoot:
    def __init__(self):
        self.after_calls = {}
        self.cancelled = []
        self._serial = 0

    def after(self, delay_ms, callback):
        self._serial += 1
        token = f"after-{self._serial}"
        self.after_calls[token] = (delay_ms, callback)
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)
        self.after_calls.pop(token, None)


class FakeButton:
    def __init__(self, text, command):
        self.text = text
        self.command = command
        self.destroyed = False
        self.packed = False

    def pack(self, **_kwargs):
        self.packed = True

    def destroy(self):
        self.destroyed = True


class ModStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_mod_dir = Passer.MOD_DIR
        self.old_builtin_dir = Passer.BUILTIN_MODULE_DIR
        Passer.MOD_DIR = self.root / "Mods"
        Passer.BUILTIN_MODULE_DIR = self.root / "BuiltinModules"
        Passer.MOD_DIR.mkdir(parents=True)
        Passer.BUILTIN_MODULE_DIR.mkdir(parents=True)
        Passer.reload_installed_builtin_modules()

    def tearDown(self):
        Passer.MOD_DIR = self.old_mod_dir
        Passer.BUILTIN_MODULE_DIR = self.old_builtin_dir
        Passer.reload_installed_builtin_modules()
        self.temp.cleanup()

    @staticmethod
    def runtime_app():
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.module_windows = {}
        app.mod_runtimes = {}
        app.mod_runtime_tools = {}
        app.mod_tool_handlers = {}
        app.mod_toolbar_buttons = {}
        app.mod_ai_actions = {}
        app.mod_event_handlers = {}
        app.mod_runtime_errors = {}
        app.root = FakeRoot()
        app.clicker_theme = lambda: None
        app.place_tool_window_on_passer = lambda _controller: None
        app.status = []
        app.write_status = app.status.append
        app.add_entries = lambda _entries: None
        app.settings = {"sensitive": "not exposed to mods"}
        app.save = lambda: None
        app.render_items = lambda: None
        app.items = []
        app.selected_ids = set()
        app.tile_signatures = {}
        app.title_actions = object()
        app.utility_actions = object()
        app.title_action_buttons = []
        app.utility_action_buttons = []
        app._action_button = lambda _parent, text, command, dark=True: FakeButton(text, command)
        app._close_controller = lambda controller: controller.close() if hasattr(controller, "close") else None
        app.ai_permission = "full"
        app.run_on_ui_thread = lambda func, *args, timeout=60.0, **kwargs: func(*args, **kwargs)
        return app

    def test_create_edit_disable_enable_and_delete_mod(self):
        info = Passer.write_mod(
            "demo_mod",
            "Demo MOD",
            MOD_SOURCE,
            description="persistent demo",
            aliases=["demo", "演示"],
        )
        target = "passer-mod://demo_mod"
        self.assertEqual(info["target"], target)
        self.assertTrue(info["enabled"])
        self.assertIn(target, Passer.INSTALLED_BUILTIN_MODULES)
        self.assertIn(target, Passer.BUILTIN_TOOL_BY_TARGET)

        data_file = Passer.MOD_DIR / "demo_mod" / "data" / "keep.txt"
        data_file.write_text("keep", encoding="utf-8")
        Passer.write_mod(
            "demo_mod", "Demo MOD 2", MOD_SOURCE.replace("mod opened", "updated"),
            overwrite=True,
        )
        self.assertEqual(data_file.read_text(encoding="utf-8"), "keep")
        self.assertEqual(Passer.INSTALLED_MODS["demo_mod"]["title"], "Demo MOD 2")

        Passer.set_mod_enabled("demo_mod", False)
        self.assertFalse(Passer.INSTALLED_MODS["demo_mod"]["enabled"])
        self.assertNotIn(target, Passer.INSTALLED_BUILTIN_MODULES)
        self.assertNotIn(target, Passer.BUILTIN_TOOL_BY_TARGET)

        Passer.set_mod_enabled("demo_mod", True)
        self.assertIn(target, Passer.INSTALLED_BUILTIN_MODULES)
        self.assertEqual(Passer.delete_mod("demo_mod"), "demo_mod")
        self.assertNotIn("demo_mod", Passer.INSTALLED_MODS)
        self.assertFalse((Passer.MOD_DIR / "demo_mod").exists())

    def test_rejects_unsafe_id_missing_entry_and_incompatible_api(self):
        with self.assertRaises(ValueError):
            Passer.write_mod("../escape", "Bad", MOD_SOURCE)
        with self.assertRaises(ValueError):
            Passer.write_mod("bad_source", "Bad", "value = 1")

        broken_dir = Passer.MOD_DIR / "broken_mod"
        broken_dir.mkdir()
        (broken_dir / "mod.py").write_text(MOD_SOURCE, encoding="utf-8")
        (broken_dir / "manifest.json").write_text(json.dumps({
            "id": "broken_mod", "title": "Broken", "api": 999,
            "entry": "mod.py", "callable": "open_mod", "enabled": True,
        }), encoding="utf-8")
        records = Passer.list_installed_mods()
        broken = next(record for record in records if record["id"] == "broken_mod")
        self.assertIn("API", broken["error"])
        self.assertNotIn("passer-mod://broken_mod", Passer.BUILTIN_TOOL_BY_TARGET)

    def test_setup_only_mod_loads_at_startup_without_primary_tile(self):
        info = Passer.write_mod(
            "startup_mod",
            "Startup MOD",
            "def setup_mod(context):\n    context.write_status('started')\n",
        )
        target = "passer-mod://startup_mod"
        self.assertTrue(info["startup"])
        self.assertFalse(info["expose_tool"])
        self.assertIn("startup_mod", Passer.INSTALLED_MODS)
        self.assertNotIn(target, Passer.INSTALLED_BUILTIN_MODULES)
        self.assertNotIn(target, Passer.BUILTIN_TOOL_BY_TARGET)

        app = self.runtime_app()
        summary = app.reload_mods_runtime(render=False)
        self.assertEqual(summary["loaded"], 1)
        self.assertIn("started", app.status)

    def test_aira_mod_actions_create_list_and_disable(self):
        app = Passer.RelayDockApp.__new__(Passer.RelayDockApp)
        app.ai_permission = "full"
        app.run_on_ui_thread = lambda func, *args, timeout=60.0, **kwargs: func(*args, **kwargs)
        app._close_mod_controller = lambda _mod_id: None
        app._sync_mod_registry = lambda **_kwargs: None

        created = app.execute_ai_actions([{
            "action": "create_mod",
            "id": "aira_mod",
            "title": "Aira MOD",
            "description": "created through the AI action bridge",
            "code": MOD_SOURCE,
        }])
        self.assertIn("已保存 MOD", created[0])
        listed = app.execute_ai_actions([{"action": "list_mods"}])
        self.assertIn("aira_mod", listed[0])
        disabled = app.execute_ai_actions([{"action": "disable_mod", "id": "aira_mod"}])
        self.assertIn("已禁用", disabled[0])
        self.assertFalse(Passer.INSTALLED_MODS["aira_mod"]["enabled"])

    def test_runtime_loader_passes_mod_context_and_persists_state(self):
        source = """\
def open_mod(context):
    assert context.mod_id == "loader_mod"
    assert context.settings is None
    context.save_state({"opened": True})
    context.write_status("loader ok")
    return None
"""
        Passer.write_mod("loader_mod", "Loader MOD", source)

        app = self.runtime_app()

        self.assertTrue(app.open_installed_builtin_module("passer-mod://loader_mod"))
        state_path = Passer.MOD_DIR / "loader_mod" / "data" / "state.json"
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {"opened": True})
        self.assertIn("loader ok", app.status)

    def test_runtime_hooks_ai_action_event_and_hot_reload_cleanup(self):
        source_v1 = """\
def open_panel(context):
    state = context.load_state({})
    state["panel_opened"] = True
    context.save_state(state)

def on_search(payload, context):
    state = context.load_state({})
    state["query"] = payload["query"]
    context.save_state(state)

def echo(params, context):
    return {"echo": params.get("value"), "mod": context.mod_id}

def on_button(context):
    state = context.load_state({})
    state["button_clicked"] = True
    context.save_state(state)

def on_cleanup(context):
    state = context.load_state({})
    state["cleanup_count"] = state.get("cleanup_count", 0) + 1
    context.save_state(state)

def setup_mod(context):
    context.register_tool("panel", "Runtime Panel", open_panel, aliases=["panel"])
    context.register_toolbar_button("hello", "MOD", on_button)
    context.register_ai_action("echo", echo, requires_full=False)
    context.on("search_changed", on_search)
    context.call_later(60000, lambda ctx: ctx.write_status("timer fired"))
    context.register_cleanup(on_cleanup)
    context.write_status("setup v1")
"""
        Passer.write_mod(
            "runtime_mod", "Runtime MOD", source_v1,
            permissions=["ui", "state", "aira", "events"],
        )
        app = self.runtime_app()

        summary = app.reload_mods_runtime(render=False)
        self.assertEqual(summary["loaded"], 1)
        self.assertEqual(summary["tools"], 1)
        self.assertEqual(summary["ai_actions"], 1)
        self.assertIn("setup v1", app.status)
        tool_target = "passer-mod-tool://runtime_mod/panel"
        self.assertIn(tool_target, Passer.BUILTIN_TOOL_BY_TARGET)
        self.assertIn("mod.runtime_mod.echo", app.mod_ai_actions)
        self.assertEqual(len(app.mod_toolbar_buttons), 1)
        button = next(iter(app.mod_toolbar_buttons.values()))
        self.assertTrue(button.packed)

        app.emit_mod_event("search_changed", {"query": "minecraft"})
        self.assertTrue(app.open_runtime_mod_tool(tool_target))
        button.command()
        action_result = app.execute_ai_actions([{
            "action": "mod.runtime_mod.echo",
            "value": "hello",
        }])
        self.assertIn("hello", action_result[0])
        state_path = Passer.MOD_DIR / "runtime_mod" / "data" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["query"], "minecraft")
        self.assertTrue(state["panel_opened"])
        self.assertTrue(state["button_clicked"])

        old_runtime = app.mod_runtimes["runtime_mod"]
        old_module_name = old_runtime["module_name"]
        old_timer = next(iter(old_runtime["after_ids"]))
        source_v2 = """\
def open_new_panel(context):
    context.write_status("new panel")

def setup_mod(context):
    context.register_tool("new-panel", "New Runtime Panel", open_new_panel)
    context.write_status("setup v2")
"""
        Passer.write_mod(
            "runtime_mod", "Runtime MOD", source_v2, overwrite=True,
            permissions=["ui", "state"],
        )
        summary = app.reload_mods_runtime(render=False)
        self.assertEqual(summary["loaded"], 1)
        self.assertNotIn(tool_target, app.mod_tool_handlers)
        self.assertIn("passer-mod-tool://runtime_mod/new-panel", app.mod_tool_handlers)
        self.assertNotIn("mod.runtime_mod.echo", app.mod_ai_actions)
        self.assertNotIn(old_module_name, sys.modules)
        self.assertIn(old_timer, app.root.cancelled)
        self.assertTrue(button.destroyed)
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8"))["cleanup_count"],
            1,
        )
        self.assertIn("setup v2", app.status)

    def test_unhandled_runtime_error_schedules_auto_disable(self):
        source = """\
def fail(_payload, _context):
    raise RuntimeError("boom")

def setup_mod(context):
    context.on("search_changed", fail)
"""
        Passer.write_mod(
            "crash_mod", "Crash MOD", source,
            permissions=["ui", "state", "events"],
        )
        app = self.runtime_app()
        self.assertTrue(app.reload_mods_runtime(render=False)["loaded"])
        app.emit_mod_event("search_changed", {"query": "test"})
        pending = [callback for delay, callback in app.root.after_calls.values() if delay == 0]
        self.assertTrue(pending)
        pending[-1]()
        self.assertFalse(Passer.INSTALLED_MODS["crash_mod"]["enabled"])
        self.assertNotIn("crash_mod", app.mod_runtimes)
        self.assertTrue(any("自动停用" in message for message in app.status))


class ModContextTests(unittest.TestCase):
    def test_json_state_is_persistent_and_cannot_escape_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            data_dir = Path(folder) / "data"
            statuses: list[str] = []
            context = ModContext(
                root=None,
                theme=None,
                app_font=lambda *args: args,
                colors={},
                module_id="demo_mod",
                module_dir=Path(folder),
                _place_window=lambda _controller: None,
                _write_status=statuses.append,
                _add_paths=lambda _paths: None,
                manifest={"id": "demo_mod"},
                data_dir=data_dir,
            )
            saved = context.save_state({"count": 3})
            self.assertEqual(saved, data_dir / "state.json")
            self.assertEqual(context.load_state(), {"count": 3})
            self.assertEqual(context.mod_id, "demo_mod")
            with self.assertRaises(ValueError):
                context.data_path("../outside.json")
            with self.assertRaises(PermissionError):
                context.add_paths("C:/demo.txt")

    def test_sensitive_source_requires_declared_permission(self):
        source = """\
from pathlib import Path

def open_mod(context):
    return Path(__file__).read_text(encoding="utf-8")
"""
        with tempfile.TemporaryDirectory() as folder:
            old_mod_dir = Passer.MOD_DIR
            old_builtin_dir = Passer.BUILTIN_MODULE_DIR
            try:
                Passer.MOD_DIR = Path(folder) / "Mods"
                Passer.BUILTIN_MODULE_DIR = Path(folder) / "BuiltinModules"
                Passer.MOD_DIR.mkdir(parents=True)
                Passer.BUILTIN_MODULE_DIR.mkdir(parents=True)
                with self.assertRaises(PermissionError):
                    Passer.write_mod("file_mod", "File MOD", source)
                info = Passer.write_mod(
                    "file_mod", "File MOD", source, permissions=["ui", "state", "filesystem"],
                )
                self.assertIn("filesystem", info["permissions"])
                updated = Passer.write_mod(
                    "file_mod", "File MOD 2", source, overwrite=True,
                )
                self.assertEqual(
                    set(updated["permissions"]), {"ui", "state", "filesystem"},
                )
                text_only = Passer.write_mod(
                    "text_mod", "Text MOD",
                    "def open_mod(context):\n    return 'old'.replace('old', 'new')\n",
                )
                self.assertEqual(set(text_only["permissions"]), {"ui", "state"})
                process_source = (
                    "import os\n\n"
                    "def open_mod(context):\n    return os.system('echo passer')\n"
                )
                with self.assertRaises(PermissionError):
                    Passer.write_mod("process_mod", "Process MOD", process_source)
            finally:
                Passer.MOD_DIR = old_mod_dir
                Passer.BUILTIN_MODULE_DIR = old_builtin_dir
                Passer.reload_installed_builtin_modules()


if __name__ == "__main__":
    unittest.main()
