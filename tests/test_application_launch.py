from __future__ import annotations

import contextlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import Passer


class ApplicationLaunchTests(unittest.TestCase):
    def test_selected_application_windows_can_be_toggled_topmost(self):
        targets = [Path(r"C:\Apps\Workbench\Workbench.exe")]
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(
                    Passer, "target_window_handles", return_value=[101, 202],
                ), \
                mock.patch.object(
                    Passer,
                    "_set_external_window_topmost",
                    side_effect=[True, False],
                ) as set_topmost:
            changed = Passer.set_target_windows_topmost(targets, True)

        self.assertEqual(changed, 1)
        self.assertEqual(
            set_topmost.call_args_list,
            [mock.call(101, True), mock.call(202, True)],
        )

    def test_topmost_state_reports_matching_windows(self):
        targets = [Path(r"C:\Apps\Workbench\Workbench.exe")]
        with mock.patch.object(
            Passer, "target_window_handles", return_value=[101, 202],
        ), mock.patch.object(
            Passer,
            "_external_window_is_topmost",
            side_effect=[False, True],
        ):
            state = Passer.target_windows_topmost_state(targets)

        self.assertEqual(state, (2, True))

    def test_frozen_external_launch_temporarily_resets_dll_directory(self):
        calls: list[str | None] = []
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(Passer.sys, "frozen", True, create=True), \
                mock.patch.object(
                    Passer.sys, "_MEIPASS", r"C:\Temp\_MEI27122", create=True,
                ), \
                mock.patch.object(
                    Passer,
                    "_set_windows_dll_directory",
                    side_effect=lambda value: calls.append(value) or True,
                ):
            with Passer._external_program_launch_environment():
                self.assertEqual(calls, [None])

        self.assertEqual(calls, [None, r"C:\Temp\_MEI27122"])

    def test_executable_launch_prefers_direct_spawn_from_its_own_folder(self):
        target = Path(r"C:\Apps\Workbench\Workbench.exe")
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(
                    Passer,
                    "_external_program_launch_environment",
                    return_value=contextlib.nullcontext(),
                ), \
                mock.patch.object(Passer, "allow_any_foreground"), \
                mock.patch.object(Passer.subprocess, "Popen") as popen:
            Passer.launch_executable(target)

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], [str(target)])
        self.assertEqual(popen.call_args.kwargs["cwd"], str(target.parent))
        self.assertTrue(popen.call_args.kwargs["close_fds"])

    def test_store_app_fallback_aumid_is_validated_before_use(self):
        family = "OpenAI.Codex_2p2nqsd0c76g0"
        Passer.WINDOWSAPPS_AUMID_CACHE.pop(family, None)
        completed = subprocess.CompletedProcess(
            ["powershell.exe"], 0, stdout="", stderr="",
        )
        try:
            with mock.patch.object(Passer.sys, "platform", "win32"), \
                    mock.patch.object(
                        Passer,
                        "state_repository_app_user_model_ids",
                        return_value=(),
                    ), \
                    mock.patch.object(
                        Passer.subprocess, "run", return_value=completed,
                    ), \
                    mock.patch.object(
                        Passer,
                        "windows_shell_item_exists",
                        side_effect=lambda value: value.endswith("!App"),
                    ):
                aumid = Passer.resolve_app_user_model_id(family)
        finally:
            Passer.WINDOWSAPPS_AUMID_CACHE.pop(family, None)

        self.assertEqual(aumid, f"{family}!App")

    def test_state_repository_aumid_avoids_slow_start_apps_lookup(self):
        family = "OpenAI.Codex_2p2nqsd0c76g0"
        expected = f"{family}!App"
        Passer.WINDOWSAPPS_AUMID_CACHE.pop(family, None)
        try:
            with mock.patch.object(Passer.sys, "platform", "win32"), \
                    mock.patch.object(
                        Passer,
                        "state_repository_app_user_model_ids",
                        return_value=(expected,),
                    ), \
                    mock.patch.object(Passer.subprocess, "run") as run, \
                    mock.patch.object(
                        Passer, "windows_shell_item_exists",
                    ) as shell_item_exists:
                aumid = Passer.resolve_app_user_model_id(family)
        finally:
            Passer.WINDOWSAPPS_AUMID_CACHE.pop(family, None)

        self.assertEqual(aumid, expected)
        run.assert_not_called()
        shell_item_exists.assert_not_called()

    def test_store_update_repairs_versioned_executable_path(self):
        stale = Path(
            r"C:\Program Files\WindowsApps"
            r"\OpenAI.Codex_1.0.0.0_x64__publisher\app\Codex.exe"
        )
        with tempfile.TemporaryDirectory() as folder:
            current_root = Path(folder) / "OpenAI.Codex_2.0.0.0_x64__publisher"
            current_target = current_root / "app" / "Codex.exe"
            current_target.parent.mkdir(parents=True)
            current_target.write_bytes(b"test")
            cache_key = str(stale).casefold()
            Passer.WINDOWSAPPS_CURRENT_TARGET_CACHE.pop(cache_key, None)
            try:
                with mock.patch.object(
                    Passer,
                    "windowsapps_install_paths",
                    return_value=(current_root,),
                ):
                    resolved = Passer.resolve_current_windowsapps_target(stale)
            finally:
                Passer.WINDOWSAPPS_CURRENT_TARGET_CACHE.pop(cache_key, None)

        self.assertEqual(resolved, current_target)

    def test_running_full_trust_package_supplies_current_install_root(self):
        family = "OpenAI.Codex_publisher"
        current_process = (
            r"C:\Program Files\WindowsApps"
            r"\OpenAI.Codex_2.0_x64__publisher\app\ChatGPT.exe"
        )
        Passer.WINDOWSAPPS_INSTALL_PATH_CACHE.pop(family, None)
        try:
            with mock.patch.object(Passer.sys, "platform", "win32"), \
                    mock.patch.object(
                        Passer,
                        "running_processes_detailed",
                        return_value=[{"path": current_process}],
                    ), \
                    mock.patch.dict(Passer.os.environ, {"PATH": ""}):
                roots = Passer.windowsapps_install_paths(family)
        finally:
            Passer.WINDOWSAPPS_INSTALL_PATH_CACHE.pop(family, None)

        self.assertEqual(
            roots,
            (
                Path(
                    r"C:\Program Files\WindowsApps"
                    r"\OpenAI.Codex_2.0_x64__publisher"
                ),
            ),
        )

    def test_open_store_app_prefers_package_activation(self):
        stale = Path(
            r"C:\Program Files\WindowsApps"
            r"\Package_1.0_x64__publisher\app\App.exe"
        )
        current = Path(
            r"C:\Program Files\WindowsApps"
            r"\Package_2.0_x64__publisher\app\App.exe"
        )
        item = Passer.new_item("file", str(stale))
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(
                    Passer,
                    "resolve_current_windowsapps_target",
                    return_value=current,
                ) as resolve_current, \
                mock.patch.object(Passer, "launch_executable") as launch, \
                mock.patch.object(
                    Passer, "launch_packaged_app", return_value=True,
                ) as packaged:
            Passer.open_target(item)

        packaged.assert_called_once_with(stale)
        resolve_current.assert_not_called()
        launch.assert_not_called()

    def test_store_app_direct_launch_remains_last_resort(self):
        stale = Path(
            r"C:\Program Files\WindowsApps"
            r"\Package_1.0_x64__publisher\app\App.exe"
        )
        current = Path(
            r"C:\Program Files\WindowsApps"
            r"\Package_2.0_x64__publisher\app\App.exe"
        )
        item = Passer.new_item("file", str(stale))
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(
                    Passer,
                    "resolve_current_windowsapps_target",
                    return_value=current,
                ) as resolve_current, \
                mock.patch.object(Passer, "launch_executable") as launch, \
                mock.patch.object(
                    Passer, "launch_packaged_app", return_value=False,
                ) as packaged:
            Passer.open_target(item)

        packaged.assert_called_once_with(stale)
        resolve_current.assert_called_once_with(stale)
        launch.assert_called_once_with(current)

    def test_reveal_file_uses_shell_selection_api(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Application with spaces.exe"
            path.write_bytes(b"test")
            item = Passer.new_item("file", str(path))
            with mock.patch.object(Passer.sys, "platform", "win32"), \
                    mock.patch.object(
                        Passer, "select_windows_shell_item", return_value=True,
                    ) as select, \
                    mock.patch.object(Passer.subprocess, "Popen") as popen:
                Passer.reveal_target(item)

        select.assert_called_once_with(str(path.resolve()))
        popen.assert_not_called()

    def test_reveal_store_app_never_sends_stale_path_to_explorer(self):
        item = Passer.new_item(
            "file",
            r"C:\Program Files\WindowsApps\Package_1.0_x64__publisher\app\App.exe",
        )
        aumid = "Package_publisher!App"
        with mock.patch.object(Passer.sys, "platform", "win32"), \
                mock.patch.object(
                    Passer, "windowsapps_aumid_for_path", return_value=aumid,
                ), \
                mock.patch.object(
                    Passer, "select_windows_shell_item", return_value=True,
                ) as select, \
                mock.patch.object(Passer.subprocess, "Popen") as popen:
            Passer.reveal_target(item)

        select.assert_called_once_with(f"shell:AppsFolder\\{aumid}")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
