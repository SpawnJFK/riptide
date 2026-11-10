"""Every way Riptide is started outside a developer terminal, defined in one place.

- `handler`   the magnet: / .torrent command Windows runs (HKCU, `--register`)
- `autostart` the HKCU Run entry (Settings toggle, removed by `--unregister`)
- `start`     start.bat / scripts/start.ps1 normal mode (opens the browser)
- `dev`       scripts/start.ps1 -Dev (developer console, next to Vite)

User-facing launches use the GUI interpreter (`pythonw.exe` next to the venv `python.exe`), so Windows never
creates a console window; output goes to the rotating log under the data dir. None of them passes a working
directory or a path: every one resolves the same folders through `Config`.

    python -m riptide.launch            # start.ps1 normal mode: spawn the windowless app and return
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LAUNCH_ARGS: dict[str, list[str]] = {
    "handler": ["--no-browser", "--open", "%1"],
    "autostart": ["--no-browser"],
    "start": [],
}
DEV_ARGS: list[str] = ["--no-browser"]

# CreateProcess flags (Win32 process creation flags).
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def gui_python(executable: str | None = None) -> Path:
    """pythonw.exe beside the running interpreter (Windows); the interpreter itself elsewhere."""
    exe = Path(executable or sys.executable)
    if sys.platform != "win32":
        return exe
    if exe.name.lower() == "pythonw.exe":
        return exe
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def argv(kind: str, executable: str | None = None) -> list[str]:
    return [str(gui_python(executable)), "-m", "riptide", *LAUNCH_ARGS[kind]]


def command_line(kind: str, executable: str | None = None) -> str:
    """The exact string written to the registry. %1 stays quoted so paths with spaces survive."""
    parts = argv(kind, executable)
    return " ".join(f'"{p}"' if (" " in p or p == "%1" or p.endswith(".exe")) else p for p in parts)


def spawn_windowless(args: list[str], env: dict[str, str] | None = None) -> int:
    """Start the GUI interpreter detached from any console; returns the pid. Used by start.ps1 normal mode."""
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [str(gui_python()), "-m", "riptide", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
        env=env if env is not None else os.environ.copy(),
    )
    return proc.pid


def main() -> int:
    pid = spawn_windowless(LAUNCH_ARGS["start"] + sys.argv[1:])
    print(f"[riptide] started in the background (pid {pid}); use the tray icon to open or quit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
