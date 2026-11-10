"""HKCU handler registration (uses throwaway Classes keys, not live magnet:)."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from riptide.win_registry import HandlerPaths, HandlerRegistry, register_handlers, unregister_handlers

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry tests")


def _winreg():
    import winreg

    return winreg


def _read_default(subkey: str) -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def _command(subkey: str) -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey + r"\shell\open\command", 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


@pytest.fixture()
def test_registry(tmp_path: Path) -> HandlerRegistry:
    winreg = _winreg()
    tag = "RiptideTest_" + uuid.uuid4().hex[:12]
    paths = HandlerPaths(
        magnet=rf"Software\Classes\{tag}.magnet",
        torrent_ext=rf"Software\Classes\{tag}.torrentext",
        torrent_prog=rf"Software\Classes\{tag}.Riptide.torrent",
    )
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, paths.magnet)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, paths.magnet) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Prior Magnet")
    return HandlerRegistry(backup_path=tmp_path / "backup.json", paths=paths)


def test_register_unregister_roundtrip(test_registry: HandlerRegistry) -> None:
    reg = test_registry
    prior = _read_default(reg.paths.magnet)
    assert prior == "URL:Prior Magnet"

    register_handlers(reg)
    assert "riptide" in (_command(reg.paths.magnet) or "").lower()
    assert _read_default(reg.paths.torrent_ext) == "Riptide.torrent"

    register_handlers(reg)
    assert test_registry.backup_path.exists()

    unregister_handlers(reg)
    assert _read_default(reg.paths.magnet) == "URL:Prior Magnet"
