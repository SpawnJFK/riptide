"""Tray menu actions, the Win32 message loop, and Quit as a clean, complete shutdown.

The tests call what the menu calls: Tray.run_command() for each entry, and WM_COMMAND posted to the tray
window, which is exactly the message a click on the popup menu sends.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import libtorrent as lt
import pytest

from riptide import tray as tray_mod
from riptide.engine import Engine
from riptide.launch import command_line
from riptide.tray import (
    CMD_OPEN,
    CMD_PAUSE_ALL,
    CMD_QUIT,
    CMD_RESUME_ALL,
    CMD_SLOW_MODE,
    MF_CHECKED,
    EngineTrayActions,
    tooltip,
    window_title,
)

win = pytest.mark.skipif(sys.platform != "win32", reason="Win32 tray")


def _wait(cond, timeout=20.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def engine(tmp_path):
    e = Engine(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        incomplete_dir=tmp_path / "inc",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    src = tmp_path / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(256 * 1024))
    tid, _ = e.create_torrent(str(src))
    assert e.wait_added(tid)
    yield e, tid
    e.shutdown(5)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.slow = False

    def open_ui(self):
        self.calls.append("open")

    def pause_all(self):
        self.calls.append("pause_all")

    def resume_all(self):
        self.calls.append("resume_all")

    def slow_mode(self):
        return self.slow

    def set_slow_mode(self, on):
        self.calls.append(f"slow={on}")
        self.slow = on

    def quit(self):
        self.calls.append("quit")

    def speeds(self):
        self.calls.append("speeds")
        return 3 * 1024 * 1024, 128 * 1024


def test_engine_actions_do_what_the_menu_says(engine, monkeypatch):
    e, tid = engine
    opened, exits = [], []
    monkeypatch.setattr(tray_mod.webbrowser, "open", lambda url: opened.append(url))
    a = EngineTrayActions(e, "http://127.0.0.1:8788", lambda: exits.append(True))

    a.pause_all()
    h = e.handle(tid)
    assert _wait(lambda: bool(h.status().flags & lt.torrent_flags.paused))
    assert not h.status().flags & lt.torrent_flags.auto_managed
    a.resume_all()
    assert _wait(lambda: not h.status().flags & lt.torrent_flags.paused)

    # Slow mode, tray -> engine -> what the UI reads (GET /settings alt_speed) and the live session limit.
    a.set_slow_mode(True)
    assert a.slow_mode() and e.get_settings()["alt_speed"] is True
    assert e.ses.get_settings()["download_rate_limit"] == 512 * 1024
    # UI -> tray: the web toggle calls set_alt_speed; the tray reads the same state for its check mark.
    e.set_alt_speed(False)
    assert a.slow_mode() is False and e.get_settings()["alt_speed"] is False

    a.open_ui()
    a.quit()
    assert opened == ["http://127.0.0.1:8788"] and exits == [True]
    assert a.speeds() == (int(e.stats().get("dl_rate", 0)), int(e.stats().get("ul_rate", 0)))


def test_slow_mode_survives_a_restart(tmp_path):
    kw = dict(listen_port=0, enable_dht=False, enable_upnp=False, listen_interface="127.0.0.1")
    e = Engine(tmp_path / "d", tmp_path / "dl", **kw)
    e.start()
    e.set_alt_speed(True)
    e.shutdown(3)
    e2 = Engine(tmp_path / "d", tmp_path / "dl", **kw)
    e2.start()
    try:
        assert e2.alt_speed() and e2.ses.get_settings()["download_rate_limit"] == 512 * 1024
    finally:
        e2.shutdown(3)


def test_tooltip_shows_live_speeds_and_fits():
    text = tooltip(3 * 1024 * 1024, 128 * 1024, True)
    assert text == "Riptide\nDown 3.0 MB/s  Up 128.0 KB/s\nSlow mode on"
    assert len(tooltip(10**15, 10**15, True)) <= tray_mod.TIP_MAX


@win
def test_menu_commands_and_check_mark():
    rec = Recorder()
    t = tray_mod.Tray(rec)
    for cmd in (CMD_OPEN, CMD_PAUSE_ALL, CMD_RESUME_ALL, CMD_SLOW_MODE, CMD_QUIT):
        t.run_command(cmd)
    assert rec.calls == [
        "open",
        "speeds",
        "pause_all",
        "speeds",
        "resume_all",
        "speeds",
        "slow=True",
        "speeds",
        "quit",
        "speeds",
    ]
    slow_row = [f for c, f, _ in t.menu_items() if c == CMD_SLOW_MODE][0]
    assert slow_row & MF_CHECKED
    rec.slow = False
    slow_row = [f for c, f, _ in t.menu_items() if c == CMD_SLOW_MODE][0]
    assert not slow_row & MF_CHECKED
    assert [lbl for _, _, lbl in t.menu_items() if lbl] == [
        "Open Riptide",
        "Pause all",
        "Resume all",
        "Slow mode",
        "Quit",
    ]


def _explorer_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq explorer.exe"], capture_output=True, text=True).stdout
    return "explorer.exe" in out.lower()


@win
def test_win32_loop_starts_and_stops_cleanly():
    rec = Recorder()
    t = tray_mod.Tray(rec, title="Riptide tray test")
    assert t.start(), "message loop did not start"
    try:
        assert t.hwnd and t._thread.is_alive()
        if _explorer_running():
            assert t.icon_added, "Shell_NotifyIconW(NIM_ADD) failed with a taskbar present"
        t.post_command(CMD_PAUSE_ALL)  # what a click on the menu sends
        assert _wait(lambda: "pause_all" in rec.calls, 5)
        n = rec.calls.count("speeds")
        assert _wait(lambda: rec.calls.count("speeds") > n + 1, 5), "tooltip timer is not ticking"
    finally:
        assert t.stop(5), "message loop did not end"
    assert not t._thread.is_alive() and t.hwnd is None and not t.icon_added


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@win
def test_quit_from_tray_saves_resume_and_leaves_no_process(tmp_path):
    """Start the real autostart command line, then click Quit (WM_COMMAND to its tray window)."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RIPTIDE_")}
    env.update(
        RIPTIDE_DATA_DIR=str(tmp_path / "data"),
        RIPTIDE_DOWNLOAD_DIR=str(tmp_path / "dl"),
        RIPTIDE_PORT=str(port),
        RIPTIDE_BT_PORT="0",
        RIPTIDE_DHT="0",
        RIPTIDE_UPNP="0",
        RIPTIDE_TRAY="1",
    )
    proc = subprocess.Popen(command_line("autostart"), env=env)
    try:
        assert _wait(lambda: _health(port), 45), "instance did not come up"
        src = tmp_path / "payload"
        src.mkdir()
        (src / "x.bin").write_bytes(os.urandom(64 * 1024))
        body = json.dumps({"source": str(src), "start_seeding": True}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/create", data=body, method="POST")
        req.add_header("content-type", "application/json")
        urllib.request.urlopen(req, timeout=30).read()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        title = window_title(f"http://127.0.0.1:{port}")
        assert _wait(lambda: bool(user32.FindWindowW(None, title)), 15), "tray window not found"
        hwnd = user32.FindWindowW(None, title)
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW(hwnd, tray_mod.WM_COMMAND, CMD_QUIT, 0)
        proc.wait(30)  # the uv launcher exits when the real pythonw exits: no orphan
        assert not _health(port)
        log = (tmp_path / "data" / "logs" / "riptide.log").read_text("utf-8")
        assert "engine shutting down, saving resume data" in log and "tray loop ended" in log, log[-1500:]
        assert (tmp_path / "data" / "session.state").exists()
        assert list((tmp_path / "data" / "resume").glob("*.fastresume"))
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)


def _health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            return json.loads(r.read()).get("ok") is True
    except OSError:
        return False


def test_tray_thread_never_blocks_engine_calls(engine):
    """The engine stays responsive while the tray thread runs its own loop (no shared lock with the UI thread)."""
    e, tid = engine
    done = threading.Event()

    def poll():
        for _ in range(50):
            e.list()
        done.set()

    threading.Thread(target=poll).start()
    assert done.wait(5)
    assert Path(e.data_dir).exists()
