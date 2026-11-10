"""One data dir for every launch path: handler, autostart, start.bat, dev mode, from any working folder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from riptide.config import REPO_ROOT, Config, ConfigError
from riptide.launch import DEV_ARGS, LAUNCH_ARGS

PATH_VARS = ("RIPTIDE_DATA_DIR", "RIPTIDE_DOWNLOAD_DIR", "RIPTIDE_INCOMPLETE_DIR", "RIPTIDE_WATCH_DIR")


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("RIPTIDE_")}
    # Empty values block any repo .env (load_dotenv uses setdefault) and count as unset in Config.
    env.update({k: "" for k in PATH_VARS})
    env.update(
        LOCALAPPDATA=str(tmp / "local"),
        USERPROFILE=str(tmp / "home"),
        HOME=str(tmp / "home"),
        XDG_DATA_HOME=str(tmp / "xdg"),
        # The interpreter's own bytecode cache is not Riptide data; on a fresh checkout (CI) it would be written.
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.update(extra)
    return env


def _paths(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    args = [a if a != "%1" else "magnet:?xt=urn:btih:" + "0" * 40 for a in args]
    return subprocess.run(
        [sys.executable, "-m", "riptide", *args, "--print-paths"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _repo_untracked() -> set[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--ignored", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    return {ln[3:] for ln in out.splitlines() if ln.startswith(("??", "!!"))}


def test_every_launch_path_resolves_the_same_folders(tmp_path):
    env = _env(tmp_path)
    before = _repo_untracked()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    seen = {}
    for kind, args in {**LAUNCH_ARGS, "dev": DEV_ARGS}.items():
        for cwd in (REPO_ROOT, elsewhere):
            r = _paths(args, cwd, env)
            assert r.returncode == 0, (kind, cwd, r.stderr)
            seen[(kind, str(cwd))] = json.loads(r.stdout.strip().splitlines()[-1])
    values = {json.dumps(v, sort_keys=True) for v in seen.values()}
    assert len(values) == 1, seen
    paths = next(iter(seen.values()))
    assert Path(paths["data_dir"]) == tmp_path / "local" / "Riptide" / "data" or sys.platform != "win32"
    for key in ("data_dir", "download_dir", "incomplete_dir", "watch_dir"):
        assert not Path(paths[key]).resolve().is_relative_to(REPO_ROOT), (key, paths[key])
    new = {p for p in _repo_untracked() - before if not p.startswith(".pytest_cache")}
    assert not new, f"a launch path wrote inside the repo: {sorted(new)}"


def test_relative_data_dir_never_lands_in_the_working_folder(tmp_path):
    r = _paths(LAUNCH_ARGS["start"], REPO_ROOT, _env(tmp_path, RIPTIDE_DATA_DIR="data"))
    assert r.returncode == 0, r.stderr
    data_dir = Path(json.loads(r.stdout.strip().splitlines()[-1])["data_dir"])
    assert not data_dir.resolve().is_relative_to(REPO_ROOT)
    assert not (REPO_ROOT / "data").exists()


def test_data_dir_inside_repo_is_refused(tmp_path):
    r = _paths(LAUNCH_ARGS["handler"], tmp_path, _env(tmp_path, RIPTIDE_DATA_DIR=str(REPO_ROOT / "data")))
    assert r.returncode == 2 and "inside the repository" in r.stderr, (r.returncode, r.stderr)
    assert not (REPO_ROOT / "data").exists()
    for field in ("download_dir", "incomplete_dir", "watch_dir"):
        with pytest.raises(ConfigError):
            Config(**{field: REPO_ROOT / "x"})


def test_gitignore_blocks_repo_data_folders():
    for rel in ("data/meta.json", "backend/data/x", "downloads/a.iso", "Incomplete/x"):
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT)
        assert r.returncode == 0, f"{rel} is not ignored"
