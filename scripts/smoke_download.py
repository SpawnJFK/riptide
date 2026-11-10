"""Real-internet smoke test: add a public ISO torrent, watch stats, remove with data."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from riptide.config import default_cache_dir  # noqa: E402

# Official Ubuntu 24.04 LTS desktop torrent (project website).
TORRENT_URL = "https://releases.ubuntu.com/24.04.3/ubuntu-24.04.3-desktop-amd64.iso.torrent"
BASE = "http://127.0.0.1:8787"
CACHE = default_cache_dir() / "smoke"
TIMEOUT_S = 120.0
INTERVAL_S = 5.0


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    torrent_path = CACHE / "ubuntu.iso.torrent"
    if not torrent_path.exists():
        print(f"[smoke] fetching torrent {TORRENT_URL}", flush=True)
        torrent_path.write_bytes(httpx.get(TORRENT_URL, follow_redirects=True, timeout=60).content)

    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        health = c.get("/api/health").json()
        assert health["ok"] and health["contract"] == "1.0.0", health

        files = {"files": ("ubuntu.iso.torrent", torrent_path.read_bytes(), "application/x-bittorrent")}
        added = c.post("/api/torrents/file", files=files).json()
        if not added.get("added"):
            print(f"[smoke] add failed: {added}", flush=True)
            return 1
        tid = added["added"][0]
        detail = c.get(f"/api/torrents/{tid}").json()
        name = detail.get("name") or tid
        print(f"[smoke] added {name} id={tid}", flush=True)

        t0 = time.monotonic()
        peak_dl = 0
        last_dht = 0
        saw_speed = False
        while time.monotonic() - t0 < TIMEOUT_S:
            stats = c.get("/api/stats").json()["stats"]
            torrents = c.get("/api/torrents").json()
            t = next(x for x in torrents if x["id"] == tid)
            dht = int(stats.get("dht_nodes") or 0)
            peers = int(t.get("peers") or 0) + int(t.get("seeds") or 0)
            seeds = int(t.get("seeds") or 0)
            dl = int(t.get("dl_rate") or 0)
            peak_dl = max(peak_dl, dl)
            if dl > 0:
                saw_speed = True
            elapsed = time.monotonic() - t0
            pct = min(100.0, elapsed / TIMEOUT_S * 100.0)
            print(
                f"[smoke] elapsed={elapsed:.0f}s pct={pct:.0f}% "
                f"dht_nodes={dht} peers={peers} seeds={seeds} dl_rate={dl} peak_dl={peak_dl}",
                flush=True,
            )
            last_dht = dht
            if saw_speed and dht > 0 and peers > 0:
                break
            time.sleep(INTERVAL_S)

        c.delete(f"/api/torrents/{tid}", params={"delete_files": "true"})
        print(
            f"[smoke] done name={name!r} peak_dl={peak_dl} last_dht={last_dht} saw_speed={saw_speed}",
            flush=True,
        )
        if last_dht <= 0 or not saw_speed:
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
