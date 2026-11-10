"""Per-user (HKCU) magnet: and .torrent handler registration for Windows."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("riptide.win_registry")

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment,misc]


@dataclass
class HandlerPaths:
    """Registry paths under HKCU. Override for tests (never touch real magnet: in tests)."""

    magnet: str = r"Software\Classes\magnet"
    torrent_ext: str = r"Software\Classes\.torrent"
    torrent_prog: str = r"Software\Classes\Riptide.torrent"


@dataclass
class HandlerRegistry:
    backup_path: Path
    paths: HandlerPaths = field(default_factory=HandlerPaths)

    def open_command(self) -> str:
        from .launch import command_line

        return command_line("handler")


def _read_key_values(root: int, subkey: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
            i = 0
            while True:
                try:
                    name, value, _typ = winreg.EnumValue(key, i)
                    if isinstance(value, str):
                        out[name or ""] = value
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def _read_tree(root: int, subkey: str) -> dict[str, Any]:
    tree: dict[str, Any] = {"values": _read_key_values(root, subkey)}
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
            i = 0
            while True:
                try:
                    child = winreg.EnumKey(key, i)
                    tree[child] = _read_tree(root, subkey + "\\" + child)
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return tree


def _write_tree(root: int, subkey: str, tree: dict[str, Any]) -> None:
    values = tree.get("values") or {}
    access = winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY
    with winreg.CreateKeyEx(root, subkey, 0, access) as key:
        for name, value in values.items():
            if value is None:
                continue
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    for child, child_tree in tree.items():
        if child == "values":
            continue
        _write_tree(root, subkey + "\\" + child, child_tree)


def _delete_tree(root: int, subkey: str) -> None:
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                    _delete_tree(root, subkey + "\\" + child)
                except OSError:
                    break
    except OSError:
        return
    try:
        winreg.DeleteKey(root, subkey)
    except OSError:
        pass


def _load_backup(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        log.warning("handler backup unreadable at %s", path)
        return {}


def _save_backup(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def register_handlers(reg: HandlerRegistry) -> None:
    if winreg is None:
        raise OSError("Windows registry is only available on win32")
    backup = _load_backup(reg.backup_path)
    root = winreg.HKEY_CURRENT_USER
    cmd = reg.open_command()

    for label, subkey in (
        ("magnet", reg.paths.magnet),
        ("torrent_ext", reg.paths.torrent_ext),
        ("torrent_prog", reg.paths.torrent_prog),
    ):
        if label not in backup:
            backup[label] = _read_tree(root, subkey)

    magnet_tree = {
        "values": {"": "URL:Magnet Protocol", "URL Protocol": ""},
        "shell": {"open": {"command": {"values": {"": cmd}}}},
    }
    torrent_prog_tree = {
        "values": {"": "BitTorrent File"},
        "shell": {"open": {"command": {"values": {"": cmd}}}},
    }
    torrent_ext_tree = {"values": {"": "Riptide.torrent"}}

    _write_tree(root, reg.paths.magnet, magnet_tree)
    _write_tree(root, reg.paths.torrent_prog, torrent_prog_tree)
    _write_tree(root, reg.paths.torrent_ext, torrent_ext_tree)

    _save_backup(reg.backup_path, backup)
    log.info("registered magnet and .torrent handlers (HKCU)")


def unregister_handlers(reg: HandlerRegistry) -> None:
    if winreg is None:
        raise OSError("Windows registry is only available on win32")
    backup = _load_backup(reg.backup_path)
    if not backup:
        log.warning("no handler backup at %s; removing Riptide handler keys only", reg.backup_path)
        backup = {}

    root = winreg.HKEY_CURRENT_USER
    for label, subkey in (
        ("magnet", reg.paths.magnet),
        ("torrent_ext", reg.paths.torrent_ext),
        ("torrent_prog", reg.paths.torrent_prog),
    ):
        saved = backup.get(label)
        if saved:
            _delete_tree(root, subkey)
            _write_tree(root, subkey, saved)
        else:
            _delete_tree(root, subkey)

    if reg.backup_path.exists():
        reg.backup_path.unlink()
    log.info("unregistered handlers and restored prior HKCU values where backed up")


def unregister_all(reg: HandlerRegistry, autostart) -> None:  # autostart: win_autostart.Autostart
    """`--unregister`: restore the prior magnet:/.torrent handlers and remove start with Windows."""
    unregister_handlers(reg)
    autostart.set(False)
