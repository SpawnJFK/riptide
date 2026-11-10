"""Single running instance: mutex plus HTTP forward to the primary process."""

from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import Config
from .engine import Engine, EngineError

log = logging.getLogger("riptide.single_instance")

INSTANCE_FILE = "instance.json"
MUTEX_PREFIX = "Local\\Riptide-"


def instance_path(cfg: Config) -> Path:
    return cfg.data_dir / INSTANCE_FILE


def write_instance_file(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {"host": cfg.host, "port": cfg.port, "token": cfg.token}
    path = instance_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def clear_instance_file(cfg: Config) -> None:
    path = instance_path(cfg)
    if path.exists():
        path.unlink()


def _mutex_name(cfg: Config) -> str:
    key = str(cfg.data_dir.resolve()).lower()
    digest = sha256(key.encode("utf-8")).hexdigest()[:16]
    return MUTEX_PREFIX + digest


def _load_instance(cfg: Config) -> dict[str, Any] | None:
    path = instance_path(cfg)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def running_url(cfg: Config) -> str:
    """URL of the instance that holds the mutex (from instance.json), or this config's URL."""
    inst = _load_instance(cfg) or {}
    return f"http://{inst.get('host') or cfg.host}:{int(inst.get('port') or cfg.port)}"


def apply_open_on_engine(eng: Engine, target: str) -> None:
    """Add magnet or .torrent file on the primary process (engine already running)."""
    target = target.strip().strip('"')
    if target.lower().startswith("magnet:"):
        eng.add_magnet(target)
        return
    path = Path(target)
    if path.suffix.lower() == ".torrent" and path.is_file():
        eng.add_torrent_bytes(path.read_bytes())
        return
    raise EngineError(f"unsupported open target: {target[:120]}")


def forward_open(cfg: Config, target: str) -> bool:
    """POST magnet or .torrent to the primary instance. Returns True if forwarded."""
    inst = _load_instance(cfg)
    if not inst:
        return False
    host = inst.get("host") or cfg.host
    port = int(inst.get("port") or cfg.port)
    token = inst.get("token") or cfg.token
    headers: dict[str, str] = {}
    if token:
        headers["x-riptide-token"] = str(token)

    target = target.strip().strip('"')
    if target.lower().startswith("magnet:"):
        body = json.dumps({"uris": [target]}).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://{host}:{port}/api/torrents/magnet",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
            log.info("forwarded magnet to primary instance")
            return True
        except urllib.error.HTTPError as e:
            log.error("forward magnet failed: %s", e)
            return False
        except Exception as e:
            log.error("forward magnet failed: %s", e)
            return False

    path = Path(target)
    if path.suffix.lower() == ".torrent" and path.is_file():
        data = path.read_bytes()
        boundary = "----RiptideBoundary7MA4YWxkTrZu0gW"
        parts = [
            f"--{boundary}\r\n",
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n',
            "Content-Type: application/x-bittorrent\r\n\r\n",
        ]
        body = "".join(parts).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(
            f"http://{host}:{port}/api/torrents/file",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
            log.info("forwarded torrent file to primary instance")
            return True
        except Exception as e:
            log.error("forward torrent file failed: %s", e)
            return False

    log.warning("unsupported --open target: %s", target[:120])
    return False


def try_forward_if_running(cfg: Config, open_target: str | None) -> bool:
    """If another instance holds the mutex, forward and return True (caller should exit)."""
    if sys.platform != "win32":
        return False
    if not _mutex_held_by_other(cfg):
        return False
    if open_target:
        if forward_open(cfg, open_target):
            return True
        return True
    return True


def _mutex_held_by_other(cfg: Config) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    name = _mutex_name(cfg)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    last = kernel32.GetLastError()
    if last == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return True
    kernel32.CloseHandle(handle)
    return False


@contextmanager
def primary_instance_lock(cfg: Config) -> Iterator[None]:
    """Hold the single-instance mutex for the process lifetime (Windows only)."""
    if sys.platform != "win32":
        yield
        return

    import ctypes

    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    name = _mutex_name(cfg)
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        yield
        return
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError("another Riptide instance holds the mutex")
    try:
        write_instance_file(cfg)
        yield
    finally:
        clear_instance_file(cfg)
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
