"""Repair stale PyInstaller Tcl/Tk paths before application imports tkinter.

Installed through a user-site .pth file so ordinary Python 3.12 programs get
the repair even when their parent process passed an expired _MEI directory.
Valid Tcl/Tk paths, including a live PyInstaller extraction, are preserved.
"""

from __future__ import annotations

import os
import sys


def repair_tk_environment() -> dict[str, str]:
    executable_dir = os.path.dirname(os.path.abspath(sys.executable))
    prefixes = tuple(dict.fromkeys(
        os.path.abspath(str(value))
        for value in (
            getattr(sys, "base_prefix", ""),
            getattr(sys, "prefix", ""),
            executable_dir,
        )
        if value
    ))
    candidates = {
        "TCL_LIBRARY": [os.path.join(prefix, "tcl", "tcl8.6") for prefix in prefixes],
        "TK_LIBRARY": [os.path.join(prefix, "tcl", "tk8.6") for prefix in prefixes],
    }
    sentinels = {"TCL_LIBRARY": "init.tcl", "TK_LIBRARY": "tk.tcl"}
    repaired: dict[str, str] = {}
    for variable, paths in candidates.items():
        sentinel = sentinels[variable]
        configured = str(os.environ.get(variable) or "").strip().strip('"')
        if configured and os.path.isfile(os.path.join(configured, sentinel)):
            continue
        os.environ.pop(variable, None)
        for candidate in paths:
            if os.path.isfile(os.path.join(candidate, sentinel)):
                os.environ[variable] = candidate
                repaired[variable] = candidate
                break
    return repaired


REPAIRED_TK_PATHS = repair_tk_environment()
