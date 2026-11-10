"""Runtime configuration from environment variables (with sane local defaults)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STORAGE_FIELDS = ("data_dir", "download_dir", "incomplete_dir", "watch_dir")


class ConfigError(ValueError):
    """A storage path that would break the one-data-dir rule."""


def _env_path(name: str, default: Path) -> Path:
    """A RIPTIDE_* path override, or the default. An empty value counts as unset (Path("") would be the cwd).

    Relative values resolve against the app folder, never the current working directory: Windows starts the
    handler and autostart commands in arbitrary folders, and a launch from the repo root once created a stray
    `data/` there. Every launch path therefore resolves to the same absolute folders.
    """
    value = os.path.expandvars(os.environ.get(name, "").strip())
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else app_root() / p


def inside_repo(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def app_root() -> Path:
    """Per-user app folder: %LOCALAPPDATA%/Riptide on Windows, $XDG_DATA_HOME/riptide elsewhere.

    Every default below derives from this or from the user's home folder. No drive letter is ever hardcoded;
    overrides come from RIPTIDE_* environment variables (see .env.example).
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Riptide"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "riptide"


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / "Riptide"


def default_cache_dir() -> Path:
    """Scratch payloads for dev scripts (demo swarm, smoke). Never %TEMP%."""
    if os.name == "nt":
        fallback = app_root() / "cache"
    else:
        fallback = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "riptide"
    return _env_path("RIPTIDE_CACHE_DIR", fallback)


@dataclass
class Config:
    host: str = field(default_factory=lambda: os.environ.get("RIPTIDE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("RIPTIDE_PORT", "8787")))
    data_dir: Path = field(
        default_factory=lambda: _env_path("RIPTIDE_DATA_DIR", app_root() / "data" if os.name == "nt" else app_root())
    )
    download_dir: Path = field(default_factory=lambda: _env_path("RIPTIDE_DOWNLOAD_DIR", default_download_dir()))
    incomplete_dir: Path = field(
        default_factory=lambda: _env_path("RIPTIDE_INCOMPLETE_DIR", default_download_dir() / "Incomplete")
    )
    watch_dir: Path = field(default_factory=lambda: _env_path("RIPTIDE_WATCH_DIR", default_download_dir() / "Watch"))
    bt_port: int = field(default_factory=lambda: int(os.environ.get("RIPTIDE_BT_PORT", "6881")))
    enable_dht: bool = field(default_factory=lambda: os.environ.get("RIPTIDE_DHT", "1") != "0")
    enable_upnp: bool = field(default_factory=lambda: os.environ.get("RIPTIDE_UPNP", "1") != "0")
    bt_interface: str = field(default_factory=lambda: os.environ.get("RIPTIDE_BT_INTERFACE", "0.0.0.0"))
    # Tray icon (Windows). Dev and test instances set RIPTIDE_TRAY=0 so they never add icons.
    tray: bool = field(default_factory=lambda: os.environ.get("RIPTIDE_TRAY", "1") != "0")
    # Auth: empty token means no auth, allowed ONLY when bound to loopback.
    token: str = field(default_factory=lambda: os.environ.get("RIPTIDE_TOKEN", ""))
    web_dist: Path = field(
        default_factory=lambda: Path(os.environ.get("RIPTIDE_WEB_DIST", str(REPO_ROOT / "web" / "dist")))
    )

    def __post_init__(self) -> None:
        for name in STORAGE_FIELDS:
            path = Path(getattr(self, name)).expanduser()
            if not path.is_absolute():
                path = app_root() / path
            if inside_repo(path):
                raise ConfigError(
                    f"{name} {path} is inside the repository {REPO_ROOT}. Runtime data never lives in the repo; "
                    f"unset RIPTIDE_{name.upper()} or point it outside (default {app_root()})."
                )
            setattr(self, name, path)
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.token:
            # Never expose an unauthenticated torrent client on the network.
            self.token = secrets.token_urlsafe(24)
            print(f"[riptide] non-loopback bind without RIPTIDE_TOKEN; generated token: {self.token}")
