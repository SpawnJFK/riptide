"""Start with Windows is one HKCU Run value, off by default, toggled in Settings, removed by
--unregister. Tests use throwaway keys under HKCU\\Software\\Riptide-test; the real Run key is never touched."""

from __future__ import annotations

import sys
import uuid

import pytest

from riptide.engine import Engine, EngineError
from riptide.launch import command_line
from riptide.win_autostart import RUN_KEY, Autostart

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")


@pytest.fixture
def test_key():
    import winreg

    base = r"Software\Riptide-test"
    key = rf"{base}\{uuid.uuid4().hex}\Run"
    yield key
    from riptide.win_registry import _delete_tree

    _delete_tree(winreg.HKEY_CURRENT_USER, base)


def test_run_value_is_the_windowless_launcher(test_key):
    a = Autostart(key=test_key)
    assert a.key != RUN_KEY
    assert not a.enabled()
    assert a.set(True)
    cmd = a.current()
    assert cmd == command_line("autostart")
    assert "pythonw.exe" in cmd.lower() and "--no-browser" in cmd
    assert not a.set(False) and a.current() is None
    assert not a.set(False)  # turning off twice is fine


def test_settings_toggle_is_off_by_default_and_writes_the_registry(tmp_path, test_key):
    a = Autostart(key=test_key)
    e = Engine(tmp_path / "d", tmp_path / "dl", listen_port=0, enable_dht=False, enable_upnp=False, autostart=a)
    e.start()
    try:
        s = e.get_settings()
        assert s["autostart_supported"] is True and s["autostart"] is False
        assert e.set_settings({"autostart": True})["autostart"] is True and a.enabled()
        assert e.set_settings({"autostart": False})["autostart"] is False and not a.enabled()
    finally:
        e.shutdown(3)


def test_setting_does_nothing_when_unsupported(tmp_path):
    e = Engine(tmp_path / "d", tmp_path / "dl", listen_port=0, enable_dht=False, enable_upnp=False)
    e.start()
    try:
        assert e.get_settings()["autostart_supported"] is False
        with pytest.raises(EngineError):
            e.set_settings({"autostart": True})
    finally:
        e.shutdown(3)


def test_unregister_removes_autostart_and_restores_handlers(tmp_path, test_key):
    from riptide.win_registry import HandlerPaths, HandlerRegistry, register_handlers, unregister_all

    base = test_key.rsplit("\\", 1)[0]
    reg = HandlerRegistry(
        backup_path=tmp_path / "backup.json",
        paths=HandlerPaths(magnet=rf"{base}\magnet", torrent_ext=rf"{base}\.torrent", torrent_prog=rf"{base}\Prog"),
    )
    a = Autostart(key=test_key)
    register_handlers(reg)
    a.set(True)
    unregister_all(reg, a)
    assert not a.enabled()
    assert not reg.backup_path.exists()
