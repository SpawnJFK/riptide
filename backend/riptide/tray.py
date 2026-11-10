"""Windows notification-area (tray) icon, pure ctypes on the Win32 API. No dependency: pystray is LGPL-3.0.

Runs inside the engine process on its own thread with its own Win32 message loop, so it never blocks the engine
or the HTTP server. The menu and the tests call the same TrayActions methods through Tray.run_command().

    Open Riptide | Pause all | Resume all | Slow mode (checked when on) | Quit

Tooltip: live down/up speed, refreshed every second. Quit sets uvicorn's should_exit: the normal lifespan
shutdown saves resume data, then the tray thread removes the icon and ends.
"""

from __future__ import annotations

import ctypes
import itertools
import logging
import math
import os
import struct
import sys
import threading
import webbrowser
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, Protocol

log = logging.getLogger("riptide.tray")

CMD_OPEN, CMD_PAUSE_ALL, CMD_RESUME_ALL, CMD_SLOW_MODE, CMD_QUIT = 1001, 1002, 1003, 1004, 1005
MENU_LABELS = {
    CMD_OPEN: "Open Riptide",
    CMD_PAUSE_ALL: "Pause all",
    CMD_RESUME_ALL: "Resume all",
    CMD_SLOW_MODE: "Slow mode",
    CMD_QUIT: "Quit",
}

# Win32 constants (winuser.h, shellapi.h).
WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_TIMER, WM_NULL = 0x0002, 0x0010, 0x0111, 0x0113, 0x0000
WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU, WM_APP = 0x0202, 0x0205, 0x007B, 0x8000
WM_TRAY = WM_APP + 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RIGHTBUTTON, TPM_BOTTOMALIGN = 0x2, 0x20
TIMER_ID = 1
TIP_MAX = 127  # szTip is WCHAR[128]
_class_ids = itertools.count(1)


def window_title(url: str) -> str:
    """Title of the hidden tray window; tests find a running instance's tray by it (FindWindowW)."""
    return f"Riptide tray {url}"


LRESULT = ctypes.c_ssize_t
# WINFUNCTYPE (stdcall) exists only on Windows; elsewhere the module still imports for the action/tooltip tests,
# and Tray() refuses to start.
_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _FUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class TrayActions(Protocol):
    def open_ui(self) -> None: ...
    def pause_all(self) -> None: ...
    def resume_all(self) -> None: ...
    def slow_mode(self) -> bool: ...
    def set_slow_mode(self, on: bool) -> None: ...
    def quit(self) -> None: ...
    def speeds(self) -> tuple[int, int]: ...


class EngineTrayActions:
    """What each menu entry does, on the running engine and server. Shared by the menu and the tests."""

    def __init__(self, engine: Any, url: str, request_exit: Callable[[], None]) -> None:
        self.engine, self.url, self._request_exit = engine, url, request_exit

    def open_ui(self) -> None:
        webbrowser.open(self.url)

    def pause_all(self) -> None:
        self.engine.pause_all()

    def resume_all(self) -> None:
        self.engine.resume_all()

    def slow_mode(self) -> bool:
        return self.engine.alt_speed()

    def set_slow_mode(self, on: bool) -> None:
        self.engine.set_alt_speed(on)

    def quit(self) -> None:
        self._request_exit()

    def speeds(self) -> tuple[int, int]:
        st = self.engine.stats()
        return int(st.get("dl_rate", 0)), int(st.get("ul_rate", 0))


def human_rate(n: int) -> str:
    for unit, size in (("MB/s", 1024 * 1024), ("KB/s", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B/s"


def tooltip(dl: int, ul: int, slow: bool) -> str:
    text = f"Riptide\nDown {human_rate(dl)}  Up {human_rate(ul)}" + ("\nSlow mode on" if slow else "")
    return text[:TIP_MAX]


def icon_bits(size: int = 32) -> bytes:
    """The favicon drawn in memory: sea tile, tide wave over a dawn wave (DESIGN.md tokens), as an icon resource."""
    sea, tide, dawn = (0x0E, 0x1C, 0x2A), (0x4F, 0xD1, 0xC1), (0xF2, 0xB5, 0x44)
    s = size / 32.0
    rows = []
    for y in range(size - 1, -1, -1):  # bottom-up DIB
        row = bytearray()
        for x in range(size):
            px, py = (x + 0.5) / s, (y + 0.5) / s
            r = 7.0  # rounded corners
            dx = max(r - px, 0.0, px - (32 - r))
            dy = max(r - py, 0.0, py - (32 - r))
            inside = math.hypot(dx, dy) <= r
            color, alpha = sea, 255 if inside else 0
            for base, amp, width, c in ((16.5, 2.5, 1.6, tide), (23.0, 2.0, 1.3, dawn)):
                wy = base + amp * math.sin((px - 4.0) / 8.0 * 2 * math.pi + math.pi / 2)
                if 3.0 <= px <= 29.0 and abs(py - wy) <= width and inside:
                    color = c
            row += bytes((color[2], color[1], color[0], alpha))
        rows.append(bytes(row))
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    mask_row = ((size + 31) // 32) * 4
    return header + b"".join(rows) + bytes(mask_row * size)  # AND mask all zero: alpha decides


class Tray:
    """One notification-area icon with its own thread and message loop."""

    def __init__(self, actions: TrayActions, title: str = "Riptide tray") -> None:
        if sys.platform != "win32":
            raise OSError("the tray icon is Windows-only")
        self.actions, self.title = actions, title
        self.hwnd: int | None = None
        self.icon_added = False
        self.loop_exited = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._wndproc = WNDPROC(self._proc)  # keep a reference for the window's lifetime
        self._hicon = None
        self._taskbar_created = 0
        self._class_name = f"RiptideTray-{os.getpid()}-{next(_class_ids)}"
        self._u32 = self._s32 = self._k32 = None

    # ---------------------------------------------------------------- public
    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="riptide-tray", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout) and self.hwnd is not None

    def stop(self, timeout: float = 5.0) -> bool:
        if self.hwnd and self._u32 is not None:
            self._u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout)
        return self.loop_exited.is_set()

    def post_command(self, cmd: int) -> None:
        """Deliver a menu command through the message loop, exactly as a click on the menu does (WM_COMMAND)."""
        if self.hwnd and self._u32 is not None:
            self._u32.PostMessageW(self.hwnd, WM_COMMAND, cmd, 0)

    def run_command(self, cmd: int) -> None:
        a = self.actions
        try:
            if cmd == CMD_OPEN:
                a.open_ui()
            elif cmd == CMD_PAUSE_ALL:
                a.pause_all()
            elif cmd == CMD_RESUME_ALL:
                a.resume_all()
            elif cmd == CMD_SLOW_MODE:
                a.set_slow_mode(not a.slow_mode())
            elif cmd == CMD_QUIT:
                a.quit()
            self.refresh_tooltip()
        except Exception:
            log.exception("tray command %s failed", cmd)

    def menu_items(self) -> list[tuple[int, int, str | None]]:
        """(command, flags, label) in menu order. Slow mode carries a check mark when it is on."""
        slow = MF_CHECKED if self.actions.slow_mode() else 0
        rows = [(CMD_OPEN, 0), (0, MF_SEPARATOR), (CMD_PAUSE_ALL, 0), (CMD_RESUME_ALL, 0), (CMD_SLOW_MODE, slow)]
        rows += [(0, MF_SEPARATOR), (CMD_QUIT, 0)]
        return [(cmd, MF_STRING | flags, MENU_LABELS.get(cmd)) for cmd, flags in rows]

    def refresh_tooltip(self) -> str:
        dl, ul = self.actions.speeds()
        text = tooltip(dl, ul, self.actions.slow_mode())
        if self.icon_added:
            self._notify(NIM_MODIFY, NIF_TIP, text)
        return text

    # ---------------------------------------------------------------- Win32
    def _bind(self) -> None:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        s32 = ctypes.WinDLL("shell32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        u32.RegisterClassExW.restype = wintypes.ATOM
        u32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        u32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.DestroyWindow.argtypes = [wintypes.HWND]
        u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u32.GetMessageW.restype = ctypes.c_int
        u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u32.DispatchMessageW.restype = LRESULT
        u32.PostQuitMessage.argtypes = [ctypes.c_int]
        u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        u32.RegisterWindowMessageW.restype = wintypes.UINT
        u32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
        u32.SetTimer.restype = ctypes.c_size_t
        u32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        u32.CreatePopupMenu.restype = wintypes.HMENU
        u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPVOID,
        ]
        u32.DestroyMenu.argtypes = [wintypes.HMENU]
        u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.CreateIconFromResourceEx.argtypes = [
            ctypes.c_char_p,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        u32.CreateIconFromResourceEx.restype = wintypes.HICON
        u32.DestroyIcon.argtypes = [wintypes.HICON]
        s32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        s32.Shell_NotifyIconW.restype = wintypes.BOOL
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        self._u32, self._s32, self._k32 = u32, s32, k32

    def _run(self) -> None:
        try:
            self._bind()
            user32, shell32, kernel32 = self._u32, self._s32, self._k32
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = self._class_name
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            # A hidden top-level window, never shown. Not HWND_MESSAGE: message-only windows miss the
            # TaskbarCreated broadcast that tells us to re-add the icon after Explorer restarts.
            self.hwnd = user32.CreateWindowExW(0, self._class_name, self.title, 0, 0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            bits = icon_bits(32)
            self._hicon = user32.CreateIconFromResourceEx(bits, len(bits), True, 0x00030000, 32, 32, 0)
            self.icon_added = self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP, tooltip(0, 0, False))
            if not self.icon_added:
                log.warning("Shell_NotifyIconW(NIM_ADD) failed (no taskbar in this session?)")
            user32.SetTimer(self.hwnd, TIMER_ID, 1000, None)
            _ = shell32  # bound above; used through self._notify
        except Exception:
            log.exception("tray failed to start")
            self._ready.set()
            self.loop_exited.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnregisterClassW(self._class_name, hinst)
        if self._hicon:
            user32.DestroyIcon(self._hicon)
        self.hwnd = None
        self.loop_exited.set()
        log.info("tray loop ended")

    def _notify(self, op: int, flags: int, tip: str | None = None) -> bool:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        if tip is not None:
            nid.szTip = tip[:TIP_MAX]
        return bool(self._s32.Shell_NotifyIconW(op, ctypes.byref(nid)))

    def _show_menu(self) -> None:
        user32 = self._u32
        menu = user32.CreatePopupMenu()
        for cmd, flags, label in self.menu_items():
            user32.AppendMenuW(menu, flags, cmd, label)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # The owner must be foreground or the menu never closes when you click elsewhere; WM_NULL afterwards
        # makes the menu go away on the second click (documented notification-area menu behaviour).
        user32.SetForegroundWindow(self.hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    def _proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif event == WM_LBUTTONUP:
                    self.run_command(CMD_OPEN)
                return 0
            if msg == WM_COMMAND:
                self.run_command(wparam & 0xFFFF)
                return 0
            if msg == WM_TIMER and wparam == TIMER_ID:
                self.refresh_tooltip()
                return 0
            if self._taskbar_created and msg == self._taskbar_created:
                self.icon_added = self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP, tooltip(0, 0, False))
                return 0
            if msg == WM_CLOSE:
                self._u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._u32.KillTimer(hwnd, TIMER_ID)
                if self.icon_added:
                    self._notify(NIM_DELETE, 0)
                    self.icon_added = False
                self._u32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("tray window procedure failed (msg 0x%x)", msg)
        return self._u32.DefWindowProcW(hwnd, msg, wparam, lparam)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]
