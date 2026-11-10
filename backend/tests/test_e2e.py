"""End-to-end: two engines on loopback, one seeds a created torrent, the other downloads it.

No internet needed: DHT/UPnP off, peers connected directly.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from riptide.app import create_app
from riptide.config import Config
from riptide.engine import Engine


def _engine(tmp: Path, name: str) -> Engine:
    e = Engine(
        data_dir=tmp / name / "data",
        download_dir=tmp / name / "dl",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    e.start()
    e.ses.apply_settings({"enable_lsd": False, "allow_multiple_connections_per_ip": True})
    return e


def _wait(cond, timeout=30.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture()
def pair(tmp_path: Path):
    seeder, leecher = _engine(tmp_path, "seed"), _engine(tmp_path, "leech")
    yield seeder, leecher, tmp_path
    seeder.shutdown(3)
    leecher.shutdown(3)


def test_create_seed_download(pair):
    seeder, leecher, tmp = pair
    src = tmp / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("riptide " * 5000)

    tid, data = seeder.create_torrent(str(src), comment="test")
    assert tid and data.startswith(b"d")
    assert seeder.wait_added(tid)
    assert _wait(lambda: any(t["id"] == tid and t["state"] == "seeding" for t in seeder.list()))

    tid2 = leecher.add_torrent_bytes(data)
    assert tid2 == tid
    assert leecher.wait_added(tid)
    seeder.handle(tid).set_upload_limit(512 * 1024)  # per-torrent: loopback is exempt from global limits
    leecher.handle(tid).connect_peer(("127.0.0.1", seeder.ses.listen_port()))
    assert _wait(lambda: any(p["seed"] for p in leecher.peers(tid)), 15), "no live seed seen"
    live = leecher.peers(tid)[0]
    assert live["ip"] == "127.0.0.1" and live["seed"] is True and isinstance(live["client"], str)
    seeder.handle(tid).set_upload_limit(-1)

    expected = (src / "a.bin").read_bytes()
    got = Path(leecher.download_dir) / "payload" / "a.bin"

    def _payload_complete() -> bool:
        if not got.exists() or got.stat().st_size != len(expected):
            return False
        # On Windows, torrent progress can hit 1.0 before the last piece is flushed to disk.
        return got.read_bytes() == expected

    ok = _wait(_payload_complete, timeout=60)
    assert ok, leecher.list()
    files = leecher.files(tid)
    assert {f["path"] for f in files} == {"payload/a.bin", "payload/sub/b.txt"}
    assert all(f["progress"] == 1.0 for f in files)

    pm = leecher.pieces(tid)
    assert pm["total"] > 0 and all(b == 1.0 for b in pm["buckets"])
    assert isinstance(leecher.peers(tid), list)
    assert isinstance(leecher.trackers(tid), list)
    detail = leecher.get(tid)
    assert detail["magnet"].startswith("magnet:?") and detail["files_count"] == 2
    assert _wait(lambda: (leecher.torrent_dir / f"{tid}.torrent").exists(), 5)
    assert leecher.export_torrent(tid).startswith(b"d")

    # file priority roundtrip
    first = files[0]["index"]
    leecher.set_file_priorities(tid, {first: 0})
    assert _wait(lambda: next(f for f in leecher.files(tid) if f["index"] == first)["priority"] == 0, 5)


def test_resume_persists_across_restart(tmp_path: Path):
    e = _engine(tmp_path, "one")
    src = tmp_path / "x"
    src.mkdir()
    (src / "f.bin").write_bytes(os.urandom(200_000))
    tid, _ = e.create_torrent(str(src))
    assert e.wait_added(tid)
    e.update(tid, category="Linux", tags=["iso", "test"])
    e.shutdown(5)
    assert (e.resume_dir / f"{tid}.fastresume").exists()

    e2 = _engine(tmp_path, "one")
    assert e2.wait_added(tid)
    t = next(t for t in e2.list() if t["id"] == tid)
    assert t["category"] == "Linux" and t["tags"] == ["iso", "test"]
    e2.shutdown(3)


def test_api_roundtrip(tmp_path: Path):
    eng = _engine(tmp_path, "api")
    cfg = Config(host="127.0.0.1", data_dir=tmp_path / "api" / "data", download_dir=tmp_path / "api" / "dl")
    cfg.web_dist = tmp_path / "nope"
    app = create_app(cfg, engine=eng)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        magnet = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=demo"
        r = c.post("/api/torrents/magnet", json={"uris": [magnet, "not-a-magnet"], "paused": True})
        body = r.json()
        assert len(body["added"]) == 1 and len(body["errors"]) == 1
        tid = body["added"][0]
        assert eng.wait_added(tid)
        assert any(t["id"] == tid for t in c.get("/api/torrents").json())
        assert (
            c.patch(f"/api/torrents/{tid}", json={"category": "Movies", "tags": ["x"]}).json()["category"] == "Movies"
        )
        assert c.post("/api/torrents/bulk", json={"ids": [tid], "action": "resume"}).json()["ok"]
        assert c.get("/api/torrents/missing").status_code == 404
        dup = c.post("/api/torrents/magnet", json={"uris": [magnet]}).json()
        assert dup["errors"] and "already" in dup["errors"][0]["error"]
        s = c.patch("/api/settings", json={"download_rate_limit": 1048576}).json()
        assert s["download_rate_limit"] == 1048576
        with c.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
        assert c.delete(f"/api/torrents/{tid}").json()["ok"]
        assert _wait(lambda: not any(t["id"] == tid for t in eng.list()), 5)


def test_token_required_when_set(tmp_path: Path):
    eng = _engine(tmp_path, "auth")
    cfg = Config(host="127.0.0.1", token="secret", data_dir=tmp_path / "auth" / "data", download_dir=tmp_path / "dl")
    cfg.web_dist = tmp_path / "nope"
    with TestClient(create_app(cfg, engine=eng)) as c:
        assert c.get("/api/torrents").status_code == 401
        assert c.get("/api/torrents", headers={"x-riptide-token": "secret"}).status_code == 200
