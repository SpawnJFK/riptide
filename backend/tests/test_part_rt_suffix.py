"""Part-file .!rt suffix via rename_file while downloading, original names when done."""

from __future__ import annotations

import os
import time
from pathlib import Path

import libtorrent as lt
import pytest

from riptide.engine import PART_RT_SUFFIX, Engine


def _engine(tmp: Path, name: str, *, data_dir: Path | None = None, download_dir: Path | None = None) -> Engine:
    e = Engine(
        data_dir=data_dir or tmp / name / "data",
        download_dir=download_dir or tmp / name / "dl",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    e.meta.set_settings({"part_file_rt_suffix": True, "use_incomplete_folder": False})
    e.ses.apply_settings({"enable_lsd": False, "allow_multiple_connections_per_ip": True})
    return e


def _wait(cond, timeout=45.0, step=0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _lt_paths(h: lt.torrent_handle) -> list[str]:
    ti = h.torrent_file()
    if ti is None:
        return []
    fs = ti.layout() if hasattr(ti, "layout") else ti.files()
    out: list[str] = []
    for i in range(fs.num_files()):
        if fs.file_flags(i) & lt.file_storage.flag_pad_file:
            continue
        out.append(fs.file_path(i).replace("\\", "/"))
    return out


def _disk_relative_paths(save: Path) -> list[str]:
    if not save.is_dir():
        return []
    rel: list[str] = []
    for p in save.rglob("*"):
        if p.is_file() and PART_RT_SUFFIX not in p.name:
            rel.append(p.relative_to(save).as_posix())
    return sorted(rel)


def _assert_lt_matches_disk(leecher: Engine, tid: str) -> None:
    h = leecher.handle(tid)
    if h.status().is_finished:
        leecher._reconcile_part_rt_paths(h, tid)
        leecher._part_rt_disk_restore(h, tid)
    save = Path(h.status().save_path)
    lt_p = sorted(_lt_paths(h))
    disk_p = _disk_relative_paths(save)
    assert lt_p == disk_p, f"lt={lt_p} disk={disk_p} all_disk={list(save.rglob('*'))}"
    assert not any(p.endswith(PART_RT_SUFFIX) for p in lt_p)
    assert not any(p.name.endswith(PART_RT_SUFFIX) for p in save.rglob("*") if p.is_file())


def _download_complete(leecher: Engine, seeder: Engine, tid: str, data: bytes, tmp: Path) -> bytes:
    src = tmp / "payload"
    expected = (src / "a.bin").read_bytes()
    leecher.add_torrent_bytes(data)
    assert leecher.wait_added(tid)
    seeder.handle(tid).set_upload_limit(2 * 1024 * 1024)
    leecher.handle(tid).set_download_limit(96 * 1024)
    leecher.handle(tid).connect_peer(("127.0.0.1", seeder.ses.listen_port()))
    assert _wait(lambda: any(p["seed"] for p in leecher.peers(tid)), 60)

    def saw_part() -> bool:
        st = leecher.handle(tid).status()
        return (
            st.progress > 0.02
            and st.progress < 0.98
            and (
                any(p.endswith(PART_RT_SUFFIX) for p in _lt_paths(leecher.handle(tid)))
                or any(p.name.endswith(PART_RT_SUFFIX) for p in Path(st.save_path).rglob("*") if p.is_file())
            )
        )

    assert _wait(saw_part, 60), "expected .!rt phase"
    leecher.handle(tid).set_download_limit(-1)

    def done() -> bool:
        st = leecher.handle(tid).status()
        if not st.is_finished:
            return False
        try:
            _assert_lt_matches_disk(leecher, tid)
        except AssertionError:
            return False
        hits = list(Path(st.save_path).rglob("a.bin"))
        return len(hits) == 1 and hits[0].read_bytes() == expected

    assert _wait(done, 120), (
        f"stuck: lt={_lt_paths(leecher.handle(tid))} disk={list(Path(leecher.handle(tid).status().save_path).rglob('*'))}"
    )
    return expected


@pytest.fixture()
def trio(tmp_path: Path):
    seeder = _engine(tmp_path, "seed")
    leecher = _engine(tmp_path, "leech")
    yield seeder, leecher, tmp_path
    seeder.shutdown(3)
    leecher.shutdown(3)


def test_part_rt_while_downloading_then_original_on_complete(trio):
    seeder, leecher, tmp = trio
    src = tmp / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(8 * 1024 * 1024 + 11))

    tid, data = seeder.create_torrent(str(src), comment="part-rt")
    assert seeder.wait_added(tid)
    assert _wait(lambda: any(t["id"] == tid and t["state"] == "seeding" for t in seeder.list()))
    _download_complete(leecher, seeder, tid, data, tmp)


def test_part_rt_libtorrent_stays_in_sync_after_disk_fallback(trio):
    """Prove disk fallback does not leave libtorrent on .!rt names (recheck + resume + re-seed)."""
    seeder, leecher, tmp = trio
    src = tmp / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(6 * 1024 * 1024 + 19))

    tid, data = seeder.create_torrent(str(src), comment="part-rt-sync")
    assert seeder.wait_added(tid)
    expected = _download_complete(leecher, seeder, tid, data, tmp)

    # 1) libtorrent file list matches disk (checked in _download_complete)
    _assert_lt_matches_disk(leecher, tid)

    # 2) force_recheck completes cleanly FROM DISK ALONE. Previously libtorrent was left on .!rt names, so the
    #    recheck found nothing and had to re-download from the seeder (this step used to require a peer). Now
    #    the names are right, the recheck reaches 100% locally, and two seeds have no reason to stay connected.
    leecher.handle(tid).force_recheck()

    def recheck_done() -> bool:
        st = leecher.handle(tid).status()
        return st.progress >= 1.0 and st.state in (4, 5)

    if not _wait(recheck_done, 180):
        st = leecher.handle(tid).status()
        pytest.fail(
            f"recheck stuck state={st.state} progress={st.progress} lt={_lt_paths(leecher.handle(tid))} "
            f"peers={len(leecher.peers(tid))} events={leecher.events()[-3:]}"
        )
    st_re = leecher.handle(tid).status()
    assert st_re.progress >= 1.0 and st_re.state in (4, 5)
    errs = [e for e in leecher.events() if e.get("level") == "error" and e.get("id") == tid]
    assert not any("file error" in (e.get("text") or "").lower() for e in errs), errs[-3:]

    data_dir = tmp / "leech" / "data"
    dl = tmp / "leech" / "dl"
    assert _wait(lambda: bool(leecher.meta.get(tid).get("completed_at")), 30), "completed_at not set before shutdown"
    seeder.shutdown(3)
    leecher.shutdown(5)

    # 3) restart from resume
    leecher2 = _engine(tmp, "leech2", data_dir=data_dir, download_dir=dl)
    assert _wait(lambda: tid in {t["id"] for t in leecher2.list()}, 25)

    def resumed_ok() -> bool:
        h = leecher2.handle(tid)
        leecher2._reconcile_part_rt_paths(h, tid)
        st = h.status()
        return st.progress >= 1.0 and st.is_finished

    assert _wait(resumed_ok, 180), (
        f"resume st={leecher2.handle(tid).status().progress} state={leecher2.handle(tid).status().state}"
    )
    _assert_lt_matches_disk(leecher2, tid)

    # 4) third peer downloads only from restarted leecher
    leecher2.handle(tid).set_upload_limit(512 * 1024)
    peer = _engine(tmp, "peer")
    peer.add_torrent_bytes(data)
    assert peer.wait_added(tid)
    peer.handle(tid).connect_peer(("127.0.0.1", leecher2.ses.listen_port()))
    got = peer.download_dir / "payload" / "a.bin"

    def peer_done() -> bool:
        return got.is_file() and got.read_bytes() == expected

    assert _wait(peer_done, 90), f"peer progress={peer.handle(tid).status().progress}"
    peer.shutdown(3)
    leecher2.shutdown(3)
