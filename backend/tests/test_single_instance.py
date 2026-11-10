"""Single-instance forward to the running HTTP API."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn

from riptide.app import create_app
from riptide.config import Config
from riptide.engine import Engine
from riptide.single_instance import forward_open, write_instance_file


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(cfg: Config, eng: Engine) -> None:
    app = create_app(cfg, engine=eng)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="error")


@pytest.fixture()
def server(tmp_path: Path) -> Config:
    port = _free_port()
    cfg = Config(
        host="127.0.0.1",
        port=port,
        token="secret",
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
    )
    eng = Engine(data_dir=cfg.data_dir, download_dir=cfg.download_dir, listen_port=0, enable_dht=False)
    eng.start()
    thread = Thread(target=_serve, args=(cfg, eng), daemon=True)
    thread.start()
    for _ in range(50):
        try:
            with socket.create_connection((cfg.host, cfg.port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail("server did not start")
    write_instance_file(cfg)
    yield cfg
    eng.shutdown(2)


def test_forward_magnet_with_token(server: Config) -> None:
    uri = "magnet:?xt=urn:btih:0101010101010101010101010101010101010101&dn=test"
    assert forward_open(server, uri)


def test_forward_torrent_file(server: Config, tmp_path: Path) -> None:
    src = tmp_path / "payload"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 1000)
    # Build torrent with a throwaway engine; running server receives it via HTTP forward.
    te = Engine(data_dir=tmp_path / "te" / "data", download_dir=tmp_path / "te" / "dl", listen_port=0, enable_dht=False)
    te.start()
    _tid, data = te.create_torrent(str(src))
    te.shutdown(1)
    tpath = tmp_path / "sample.torrent"
    tpath.write_bytes(data)
    assert forward_open(server, str(tpath))
