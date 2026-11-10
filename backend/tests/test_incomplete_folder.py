"""Incomplete folder: downloads land in incomplete, move to final save path on completion."""

from __future__ import annotations

import os
import time
from pathlib import Path

import libtorrent as lt
import pytest

from riptide.engine import Engine


def _engine(tmp: Path, name: str, fin: Path, inc: Path) -> Engine:
    e = Engine(
        data_dir=tmp / name / "data",
        download_dir=fin,
        incomplete_dir=inc,
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    e.meta.set_settings(
        {
            "save_path": str(fin),
            "incomplete_path": str(inc),
            "use_incomplete_folder": True,
        }
    )
    e.ses.apply_settings({"enable_lsd": False, "allow_multiple_connections_per_ip": True})
    return e


def _wait(cond, timeout=45.0, step=0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture()
def pair(tmp_path: Path):
    fin = tmp_path / "finished"
    inc = tmp_path / "incomplete"
    fin.mkdir()
    inc.mkdir()
    seeder = _engine(tmp_path, "seed", fin, inc)
    leecher = _engine(tmp_path, "leech", fin, inc)
    yield seeder, leecher, tmp_path, fin, inc
    seeder.shutdown(3)
    leecher.shutdown(3)


def test_incomplete_then_move_to_final(pair):
    seeder, leecher, tmp, fin, inc = pair
    src = tmp / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(256 * 1024 + 3))

    tid, data = seeder.create_torrent(str(src), comment="inc-test")
    assert seeder.wait_added(tid)
    assert _wait(lambda: any(t["id"] == tid and t["state"] == "seeding" for t in seeder.list()))

    tid2 = leecher.add_torrent_bytes(data)
    assert tid2 == tid
    assert leecher.wait_added(tid)
    seeder.handle(tid).set_upload_limit(512 * 1024)
    leecher.handle(tid).connect_peer(("127.0.0.1", seeder.ses.listen_port()))
    assert _wait(lambda: any(p["seed"] for p in leecher.peers(tid)), 15)

    expected = (src / "a.bin").read_bytes()
    final_file = fin / "payload" / "a.bin"

    def done() -> bool:
        st = leecher.handle(tid).status()
        if not st.is_finished:
            return False
        return final_file.exists() and final_file.read_bytes() == expected

    assert _wait(done, 60), f"expected file at {final_file}, save_path={leecher.handle(tid).status().save_path}"

    # While incomplete folder is enabled, work should not remain the only copy under inc after finish.
    st = leecher.handle(tid).status()
    assert Path(st.save_path).resolve() == fin.resolve()


def test_remove_with_data_leaves_no_empty_work_folder(tmp_path):
    """riptide-cog: remove with data must not leave <incomplete>/<id16> behind; other files are never touched."""
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
    try:
        src = tmp_path / "src" / "pack"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "a.bin").write_bytes(os.urandom(64 * 1024))
        ct = lt.create_torrent(lt.list_files(str(src)), 0)
        lt.set_piece_hashes(ct, str(src.parent))
        data = lt.bencode(ct.generate())
        tid = e.add_torrent_bytes(data)
        assert e.wait_added(tid)
        work = tmp_path / "inc" / tid[:16]
        assert work.is_dir()
        (tmp_path / "inc" / "keep-me.txt").write_text("not ours")
        e.remove(tid, delete_files=True)
        assert _wait(lambda: not work.exists(), 15), list(work.rglob("*"))
        assert (tmp_path / "inc" / "keep-me.txt").exists()
    finally:
        e.shutdown(3)
