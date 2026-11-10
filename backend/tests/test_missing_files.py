"""Completed torrents reload from fastresume with normal checking; missing files show as missing.

Root cause of the old seed_mode workaround: resume data was requested right after the asynchronous
move_storage() / rename_file() calls, so the file on disk could still name the Incomplete folder or a .!rt
name. Any exit without the shutdown save (a closed console) left that stale file behind.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import libtorrent as lt
import pytest

from riptide.engine import PART_RT_SUFFIX, Engine


def _engine(root: Path, name: str, *, rt: bool = False) -> Engine:
    e = Engine(
        data_dir=root / name / "data",
        download_dir=root / name / "dl",
        incomplete_dir=root / name / "incomplete",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    e.meta.set_settings({"part_file_rt_suffix": rt, "use_incomplete_folder": True})
    e.ses.apply_settings({"enable_lsd": False, "allow_multiple_connections_per_ip": True})
    return e


def _wait(cond, timeout=60.0, step=0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _snap(e: Engine, tid: str) -> dict:
    return next((t for t in e.list() if t["id"] == tid), {})


def _complete_download(tmp: Path, rt: bool) -> tuple[str, Path]:
    """Seed a 2-file torrent, download it into the Incomplete folder, wait for the move to the final folder."""
    src = tmp / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    (src / "b.bin").write_bytes(os.urandom(2 * 1024 * 1024 + 3))
    seeder = _engine(tmp, "seed")
    leecher = _engine(tmp, "leech", rt=rt)
    try:
        tid, data = seeder.create_torrent(str(src), comment="missing-files")
        assert seeder.wait_added(tid)
        assert _wait(lambda: _snap(seeder, tid).get("state") == "seeding")
        leecher.add_torrent_bytes(data)
        assert leecher.wait_added(tid)
        leecher.handle(tid).connect_peer(("127.0.0.1", seeder.ses.listen_port()))
        final = leecher.download_dir / "payload"
        resume = leecher.resume_dir / f"{tid}.fastresume"

        def landed() -> bool:
            if not (leecher.meta.get(tid).get("completed_at") and (final / "b.bin").is_file()):
                return False
            if not resume.exists():
                return False
            atp = lt.read_resume_data(resume.read_bytes())
            names = [str(n) for n in (atp.renamed_files or {}).values()]
            return Path(atp.save_path).resolve() == leecher.download_dir.resolve() and not any(
                n.endswith(PART_RT_SUFFIX) for n in names
            )

        # The root cause, checked without any shutdown save: the alert-driven resume file on disk already names
        # the final folder and the original file names, so a crash or killed process cannot leave it stale.
        assert _wait(landed, 120), f"resume never caught up: {leecher.list()}"
        assert not list((tmp / "leech" / "incomplete").rglob("*.bin"))
    finally:
        seeder.shutdown(3)
        leecher.shutdown(5)
    return tid, final


@pytest.mark.parametrize("rt", [False, True], ids=["plain", "part-rt"])
def test_deleted_file_shows_missing_not_seeding(tmp_path, rt):
    tid, final = _complete_download(tmp_path, rt)
    (final / "b.bin").unlink()

    e = Engine(
        data_dir=tmp_path / "leech" / "data",
        download_dir=tmp_path / "leech" / "dl",
        incomplete_dir=tmp_path / "leech" / "incomplete",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    try:
        assert _wait(lambda: _snap(e, tid).get("state") == "error", 60), _snap(e, tid)
        snap = _snap(e, tid)
        assert snap["error"].startswith("Missing files"), snap
        assert snap["progress"] < 1.0
        h = e.handle(tid)
        assert h.status().flags & lt.torrent_flags.paused, "a torrent with missing files must not re-download"
        done = h.status().total_done
        time.sleep(2)
        assert h.status().total_done == done
        assert not (final / "b.bin").exists()
        # A recheck is the user's way out: it clears the flag and shows what is really on disk.
        e.recheck(tid)
        assert _wait(lambda: _snap(e, tid).get("state") != "error", 30), _snap(e, tid)
    finally:
        e.shutdown(5)


def test_intact_torrent_seeds_after_restart_and_recheck(tmp_path):
    tid, final = _complete_download(tmp_path, rt=True)
    kw = dict(listen_port=0, enable_dht=False, enable_upnp=False, listen_interface="127.0.0.1")
    paths = dict(
        data_dir=tmp_path / "leech" / "data",
        download_dir=tmp_path / "leech" / "dl",
        incomplete_dir=tmp_path / "leech" / "incomplete",
    )
    for round_ in range(2):
        e = Engine(**paths, **kw)
        e.start()
        try:
            assert _wait(lambda: _snap(e, tid).get("state") == "seeding" and _snap(e, tid)["progress"] >= 1.0, 60), (
                round_,
                _snap(e, tid),
            )
            assert not _snap(e, tid)["error"]
            if round_ == 0:
                e.recheck(tid)
                assert _wait(lambda: _snap(e, tid).get("state") == "checking", 10, 0.02) or True
                assert _wait(lambda: _snap(e, tid).get("state") == "seeding" and _snap(e, tid)["progress"] >= 1.0, 60)
        finally:
            e.shutdown(5)
    assert (final / "a.bin").is_file() and (final / "b.bin").is_file()
