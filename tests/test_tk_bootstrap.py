from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import Passer


class TkLibraryBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            key: os.environ.get(key) for key in ("TCL_LIBRARY", "TK_LIBRARY")
        }

    def tearDown(self):
        for key, value in self.original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @unittest.skipUnless(os.name == "nt", "Windows Python Tcl layout")
    def test_stale_mei_paths_are_replaced_with_installed_python_libraries(self):
        os.environ["TCL_LIBRARY"] = r"C:\Temp\_MEI-missing\_tcl_data"
        os.environ["TK_LIBRARY"] = r"C:\Temp\_MEI-missing\_tk_data"
        resolved = Passer._repair_tk_library_environment()
        self.assertTrue(Path(resolved["TCL_LIBRARY"], "init.tcl").is_file())
        self.assertTrue(Path(resolved["TK_LIBRARY"], "tk.tcl").is_file())
        self.assertNotIn("_MEI-missing", os.environ["TCL_LIBRARY"])

    def test_existing_valid_bundled_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tcl_dir = root / "_tcl_data"
            tk_dir = root / "_tk_data"
            tcl_dir.mkdir()
            tk_dir.mkdir()
            (tcl_dir / "init.tcl").write_text("# test", encoding="utf-8")
            (tk_dir / "tk.tcl").write_text("# test", encoding="utf-8")
            os.environ["TCL_LIBRARY"] = str(tcl_dir)
            os.environ["TK_LIBRARY"] = str(tk_dir)
            resolved = Passer._repair_tk_library_environment()
            self.assertEqual(resolved["TCL_LIBRARY"], str(tcl_dir))
            self.assertEqual(resolved["TK_LIBRARY"], str(tk_dir))

    def test_mei_paths_are_removed_before_passer_launches_child_programs(self):
        with tempfile.TemporaryDirectory() as temporary:
            mei = Path(temporary) / "_MEI12345"
            tcl_dir = mei / "_tcl_data"
            tk_dir = mei / "_tk_data"
            tcl_dir.mkdir(parents=True)
            tk_dir.mkdir(parents=True)
            (tcl_dir / "init.tcl").write_text("# bundled", encoding="utf-8")
            (tk_dir / "tk.tcl").write_text("# bundled", encoding="utf-8")
            os.environ["TCL_LIBRARY"] = str(tcl_dir)
            os.environ["TK_LIBRARY"] = str(tk_dir)

            preserved = Passer._repair_tk_library_environment()
            self.assertEqual(preserved["TCL_LIBRARY"], str(tcl_dir))
            removed = Passer._sanitize_child_process_tk_environment()
            self.assertEqual(removed["TCL_LIBRARY"], str(tcl_dir))
            self.assertEqual(removed["TK_LIBRARY"], str(tk_dir))
            self.assertNotIn("TCL_LIBRARY", os.environ)
            self.assertNotIn("TK_LIBRARY", os.environ)

    def test_onedir_runtime_paths_are_removed_before_launching_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "PasserRuntime"
            tcl_dir = runtime / "_tcl_data"
            tk_dir = runtime / "_tk_data"
            tcl_dir.mkdir(parents=True)
            tk_dir.mkdir(parents=True)
            (tcl_dir / "init.tcl").write_text("# bundled", encoding="utf-8")
            (tk_dir / "tk.tcl").write_text("# bundled", encoding="utf-8")
            os.environ["TCL_LIBRARY"] = str(tcl_dir)
            os.environ["TK_LIBRARY"] = str(tk_dir)

            with mock.patch.object(Passer.sys, "frozen", True, create=True), \
                    mock.patch.object(
                        Passer.sys, "_MEIPASS", str(runtime), create=True,
                    ):
                removed = Passer._sanitize_child_process_tk_environment()

            self.assertEqual(removed["TCL_LIBRARY"], str(tcl_dir))
            self.assertEqual(removed["TK_LIBRARY"], str(tk_dir))
            self.assertNotIn("TCL_LIBRARY", os.environ)
            self.assertNotIn("TK_LIBRARY", os.environ)

    @unittest.skipUnless(os.name == "nt", "Windows Python Tcl layout")
    def test_installed_python_paths_are_safe_for_source_children(self):
        os.environ["TCL_LIBRARY"] = str(Path(Passer.sys.base_prefix, "tcl", "tcl8.6"))
        os.environ["TK_LIBRARY"] = str(Path(Passer.sys.base_prefix, "tcl", "tk8.6"))
        removed = Passer._sanitize_child_process_tk_environment()
        self.assertEqual(removed, {})
        self.assertIn("TCL_LIBRARY", os.environ)
        self.assertIn("TK_LIBRARY", os.environ)

    @unittest.skipUnless(os.name == "nt", "Windows Python Tcl layout")
    def test_user_site_bootstrap_repairs_any_python_program(self):
        from support import passer_tk_env_fix

        os.environ["TCL_LIBRARY"] = r"C:\Temp\_MEI-expired\_tcl_data"
        os.environ["TK_LIBRARY"] = r"C:\Temp\_MEI-expired\_tk_data"
        repaired = passer_tk_env_fix.repair_tk_environment()
        self.assertTrue(Path(repaired["TCL_LIBRARY"], "init.tcl").is_file())
        self.assertTrue(Path(repaired["TK_LIBRARY"], "tk.tcl").is_file())


if __name__ == "__main__":
    unittest.main()
