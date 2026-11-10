"""Local demo swarm: a throttled seeder feeding the running Riptide over loopback.

Usage (Riptide already running):
    python scripts/demo_swarm.py [--api http://127.0.0.1:8787/api] [--mb 400] [--count 4] [--rate 3]
    python scripts/demo_swarm.py --exit-after-add   # seed and return (for screenshots)
No internet needed. Payloads live under <cache>/demo, by default %LOCALAPPDATA%\\Riptide\\cache\\demo
(RIPTIDE_CACHE_DIR moves the cache root, RIPTIDE_DEMO_CACHE or --cache-root this folder). Never %TEMP%.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from riptide.config import default_cache_dir
from riptide.engine import Engine

API = "http://127.0.0.1:8787/api"


def post(path: str, body: dict | None = None, files: bytes | None = None) -> dict:
    if files is not None:
        boundary = "----riptide"
        data = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="demo.torrent"\r\n'
                "Content-Type: application/x-bittorrent\r\n\r\n"
            ).encode()
            + files
            + f"\r\n--{boundary}--\r\n".encode()
        )
        req = urllib.request.Request(
            API + path, data=data, headers={"content-type": f"multipart/form-data; boundary={boundary}"}
        )
    else:
        req = urllib.request.Request(
            API + path, data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"}
        )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main() -> None:
    global API
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=API, help="Riptide API base URL, e.g. http://127.0.0.1:8787/api")
    ap.add_argument("--mb", type=int, default=300)
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--rate", type=float, default=3.0, help="seeder upload MB/s per torrent")
    ap.add_argument("--exit-after-add", action="store_true", help="Return after adding torrents (seeder keeps running until killed)")
    ap.add_argument("--cache-root", default=os.environ.get("RIPTIDE_DEMO_CACHE", str(default_cache_dir() / "demo")))
    args = ap.parse_args()
    API = args.api.rstrip("/")

    cache = Path(args.cache_root)
    cache.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="swarm-", dir=str(cache)))
    seeder = Engine(
        root / "seed-data",
        root / "seed-dl",
        listen_port=0,
        enable_dht=False,
        enable_upnp=False,
        listen_interface="127.0.0.1",
    )
    seeder.start()
    seeder.ses.apply_settings({"enable_lsd": False, "upload_rate_limit": int(args.rate * args.count * 1024 * 1024)})
    names = [
        "Ubuntu 26.04 LTS Desktop amd64",
        "Blender Open Movie - Wing It 4K",
        "Debian 14 netinst",
        "Creative Commons Music Pack 2026",
        "Fedora Workstation 45",
        "Internet Archive - Prelinger Collection",
    ]
    port = seeder.ses.listen_port()
    for i in range(args.count):
        d = root / names[i % len(names)]
        d.mkdir()
        size = int(args.mb * 1024 * 1024 * (0.4 + 0.6 * ((i * 37) % 10) / 10))
        with open(d / "data.bin", "wb") as f:
            f.write(os.urandom(size))
        (d / "README.txt").write_text("Riptide demo payload\n")
        tid, data = seeder.create_torrent(str(d))
        seeder.wait_added(tid)
        # Per-torrent limit: loopback peers are exempt from the session-wide limit, which let the swarm finish
        # in seconds and left screenshots at 100% seeding.
        seeder.handle(tid).set_upload_limit(int(args.rate * 1024 * 1024))
        r = post("/torrents/file", files=data)
        print("added", d.name, r)
        time.sleep(0.5)
        added_id = r["added"][0] if r.get("added") else tid
        post(f"/torrents/{added_id}/peers", {"ip": "127.0.0.1", "port": port})
    if args.exit_after_add:
        print("demo_swarm: added", args.count, "torrents; seeder pid", os.getpid(), flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            seeder.shutdown(2)
        return
    print("seeding; Ctrl+C to stop")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        seeder.shutdown(2)


if __name__ == "__main__":
    main()
