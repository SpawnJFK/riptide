"""Watch folder adds .torrent files and moves them to added/."""

from __future__ import annotations

import os
from pathlib import Path

from riptide.engine import Engine
from riptide.watch_folder import WatchFolder


def _engine(tmp: Path) -> Engine:
    e = Engine(
        data_dir=tmp / "data",
        download_dir=tmp / "dl",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    e.ses.apply_settings({"enable_lsd": False})
    return e


def test_watch_folder_move_to_added(tmp_path: Path):
    watch_root = tmp_path / "watch"
    watch_root.mkdir()
    se = _engine(tmp_path / "seed")
    src = tmp_path / "payload"
    src.mkdir()
    (src / "f.bin").write_bytes(os.urandom(4096))
    tid, data = se.create_torrent(str(src), comment="watch-test")
    assert se.wait_added(tid)
    se.shutdown(2)

    e = _engine(tmp_path / "leech")
    e.meta.set_settings(
        {
            "watch_folder_enabled": True,
            "watch_folder_path": str(watch_root),
            "watch_folder_action": "move",
        }
    )
    drop = watch_root / "drop.torrent"
    drop.write_bytes(data)
    wf = WatchFolder(e, poll_seconds=60)
    wf.scan_once()
    assert e.wait_added(tid)
    assert not drop.exists()
    assert (watch_root / "added" / "drop.torrent").exists()
    e.shutdown(2)


def test_watch_folder_delete(tmp_path: Path):
    watch_root = tmp_path / "watch2"
    watch_root.mkdir()
    src = tmp_path / "payload2"
    src.mkdir()
    (src / "g.bin").write_bytes(os.urandom(2048))
    se = _engine(tmp_path / "seed2")
    tid, data = se.create_torrent(str(src), comment="watch-del")
    se.shutdown(2)

    e = _engine(tmp_path / "leech2")
    e.meta.set_settings(
        {
            "watch_folder_enabled": True,
            "watch_folder_path": str(watch_root),
            "watch_folder_action": "delete",
        }
    )
    drop = watch_root / "gone.torrent"
    drop.write_bytes(data)
    WatchFolder(e, poll_seconds=60).scan_once()
    assert not drop.exists()
    assert not (watch_root / "added").exists()
    assert e.wait_added(tid, timeout=5)
    assert any(t["id"] == tid for t in e.list())
    e.shutdown(2)
