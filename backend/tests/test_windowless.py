"""Every user-facing launch runs windowless and logs to a rotating file under the data dir.

Each registered command line is started the way Explorer starts it: CreateProcess with a new console for
console-subsystem programs (hidden here, so a regression never flashes a window). A console program would get
conhost.exe; pythonw.exe gets nothing. The test walks the process tree with Toolhelp and fails on any
python.exe or conhost.exe under the launch.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import pytest

from riptide import applog
from riptide.config import REPO_ROOT
from riptide.launch import command_line

win = pytest.mark.skipif(sys.platform != "win32", reason="Windows launch paths")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _process_table() -> dict[int, tuple[int, str]]:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    out: dict[int, tuple[int, str]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    ok = k32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        out[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
        ok = k32.Process32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return out


def _descendants(pid: int) -> list[tuple[int, str]]:
    table = _process_table()
    found, frontier = [], [pid]
    while frontier:
        cur = frontier.pop()
        for child, (parent, name) in table.items():
            if parent == cur and child != cur:
                found.append((child, name))
                frontier.append(child)
    return found


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(tmp: Path, port: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RIPTIDE_")}
    env.update(
        RIPTIDE_DATA_DIR=str(tmp / "data"),
        RIPTIDE_DOWNLOAD_DIR=str(tmp / "dl"),
        RIPTIDE_INCOMPLETE_DIR=str(tmp / "inc"),
        RIPTIDE_WATCH_DIR=str(tmp / "watch"),
        RIPTIDE_PORT=str(port),
        RIPTIDE_BT_PORT="0",
        RIPTIDE_DHT="0",
        RIPTIDE_UPNP="0",
        RIPTIDE_TRAY="0",  # these instances are killed; a live icon would linger as a ghost
    )
    return env


def _explorer_style_launch(cmdline: str, env: dict[str, str]) -> subprocess.Popen:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE: a console program would still get conhost.exe, which is what we detect
    return subprocess.Popen(cmdline, env=env, startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)


def _kill_tree(pid: int) -> None:
    for child, _ in reversed(_descendants(pid)):
        subprocess.run(["taskkill", "/F", "/PID", str(child)], capture_output=True)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


def _wait_health(port: int, timeout: float = 45) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(0.25)
    raise AssertionError(f"no health on {port}")


@win
@pytest.mark.parametrize("kind", ["handler", "autostart"])
def test_registered_command_runs_windowless_and_logs_to_file(tmp_path, kind):
    port = _free_port()
    env = _env(tmp_path, port)
    torrent_arg = str(tmp_path / "nothing.torrent")  # Windows substitutes %1; unsupported targets are logged
    cmd = command_line(kind).replace('"%1"', f'"{torrent_arg}"')
    assert "pythonw.exe" in cmd.lower() and "python.exe" not in cmd.lower().replace("pythonw.exe", "")
    proc = _explorer_style_launch(cmd, env)
    try:
        _wait_health(port)
        names = [name for _pid, name in [(proc.pid, _process_table().get(proc.pid, (0, "?"))[1])]]
        names += [name for _pid, name in _descendants(proc.pid)]
        assert "pythonw.exe" in names, names
        assert not {"conhost.exe", "python.exe", "cmd.exe", "powershell.exe"} & set(names), names
        log = applog.log_path(tmp_path / "data")
        end = time.monotonic() + 15
        while time.monotonic() < end and "engine started" not in (log.read_text("utf-8") if log.exists() else ""):
            time.sleep(0.2)
        text = log.read_text("utf-8")
        assert "windowless=True" in text and "engine started" in text, text[-800:]
    finally:
        _kill_tree(proc.pid)


@win
def test_start_bat_normal_mode_hands_off_to_pythonw(tmp_path):
    """start.ps1 normal mode runs `python -m riptide.launch`, which must return at once and leave only pythonw."""
    port = _free_port()
    env = _env(tmp_path, port)
    ps1 = (REPO_ROOT / "scripts" / "start.ps1").read_text("utf-8")
    assert "& $py -m riptide.launch" in ps1
    assert '-m riptide --no-browser"' in ps1  # dev mode keeps its console on purpose (developer terminal)
    venv_py = Path(sys.executable)
    t0 = time.monotonic()
    r = subprocess.run(
        [str(venv_py), "-m", "riptide.launch", "--no-browser"], env=env, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0 and time.monotonic() - t0 < 15, r.stderr
    pid = int(r.stdout.split("pid ")[1].split(")")[0])
    try:
        _wait_health(port)
        names = [_process_table().get(pid, (0, "?"))[1]] + [n for _p, n in _descendants(pid)]
        assert "pythonw.exe" in names and not {"conhost.exe", "python.exe"} & set(names), names
    finally:
        _kill_tree(pid)


def test_setup_logging_without_console_routes_print_to_rotating_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(applog, "MAX_BYTES", 2048)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = applog.setup_logging(tmp_path / "data")
        print("hello from a windowless print")
        assert path == tmp_path / "data" / "logs" / "riptide.log"
        assert "hello from a windowless print" in path.read_text("utf-8")
        for i in range(200):
            logging.getLogger("riptide.test").info("line %s %s", i, "x" * 40)
        backups = sorted(p.name for p in path.parent.glob("riptide.log.*"))
        assert backups == ["riptide.log.1", "riptide.log.2", "riptide.log.3"], backups
        assert path.stat().st_size <= 2048 + 200
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        sys.excepthook = sys.__excepthook__
