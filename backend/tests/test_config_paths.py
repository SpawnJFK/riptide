"""Path defaults come from the user's profile or RIPTIDE_* env vars, never from a hardcoded drive letter."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from riptide import config
from riptide.config import Config

PATH_VARS = (
    "RIPTIDE_DATA_DIR",
    "RIPTIDE_DOWNLOAD_DIR",
    "RIPTIDE_INCOMPLETE_DIR",
    "RIPTIDE_WATCH_DIR",
    "RIPTIDE_CACHE_DIR",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in PATH_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return tmp_path


def test_defaults_follow_user_profile(clean_env):
    cfg = Config()
    downloads = clean_env / "home" / "Downloads" / "Riptide"
    assert cfg.download_dir == downloads
    assert cfg.incomplete_dir == downloads / "Incomplete"
    assert cfg.watch_dir == downloads / "Watch"
    assert cfg.data_dir == config.app_root() / "data" or cfg.data_dir == config.app_root()
    assert clean_env in cfg.data_dir.parents
    assert clean_env in config.default_cache_dir().parents


def test_env_overrides_win(clean_env, monkeypatch):
    monkeypatch.setenv("RIPTIDE_DATA_DIR", str(clean_env / "d"))
    monkeypatch.setenv("RIPTIDE_CACHE_DIR", str(clean_env / "c"))
    assert Config().data_dir == clean_env / "d"
    assert config.default_cache_dir() == clean_env / "c"


def test_empty_env_value_counts_as_unset(clean_env, monkeypatch):
    """An empty RIPTIDE_DATA_DIR must not turn into Path("") (the current folder, a stray data/ in the repo)."""
    for var in PATH_VARS:
        monkeypatch.setenv(var, "  ")
    cfg = Config()
    for p in (cfg.data_dir, cfg.download_dir, cfg.incomplete_dir, cfg.watch_dir, config.default_cache_dir()):
        assert p != Path("") and p.is_absolute(), p


def test_no_drive_letter_literals_in_code():
    """Relocation guard: code and scripts never hardcode a drive letter."""
    repo = Path(__file__).resolve().parents[2]
    drive = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:(\\\\|\\|/)[A-Za-z.%_]")
    roots = [repo / "backend" / "riptide", repo / "scripts", repo / "web" / "src"]
    hits = []
    for root in roots:
        for f in root.rglob("*"):
            # Code only: locale JSON may show an example path to the user, which is copy, not a default.
            if f.suffix not in (".py", ".ps1", ".ts", ".tsx") or not f.is_file():
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if drive.search(line) and "://" not in line:
                    hits.append(f"{f.relative_to(repo).as_posix()}:{n}: {line.strip()[:80]}")
    assert not hits, "\n".join(hits)
