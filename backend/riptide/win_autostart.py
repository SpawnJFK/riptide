"""Start with Windows: one HKCU Run value with the windowless launcher. Off by default.

The registry is the source of truth: Settings shows enabled() and toggles set(). `--unregister` removes it.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

log = logging.getLogger("riptide.win_autostart")

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment,misc]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Riptide"


@dataclass
class Autostart:
    """HKCU Run entry. Tests pass their own key so the real Run key is never touched."""

    key: str = field(default_factory=lambda: RUN_KEY)
    name: str = VALUE_NAME

    @staticmethod
    def supported() -> bool:
        return winreg is not None

    def command(self) -> str:
        from .launch import command_line

        return command_line("autostart")

    def current(self) -> str | None:
        if winreg is None:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_READ) as k:
                value, _typ = winreg.QueryValueEx(k, self.name)
                return str(value)
        except OSError:
            return None

    def enabled(self) -> bool:
        return self.current() is not None

    def set(self, on: bool) -> bool:
        if winreg is None:
            raise OSError("start with Windows is only available on Windows")
        if on:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, self.name, 0, winreg.REG_SZ, self.command())
            log.info("autostart on: %s", self.command())
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, self.name)
                log.info("autostart off")
            except FileNotFoundError:
                pass
        return self.enabled()
