"""Repo hygiene: Riptide app code must not write via %TEMP%."""

from __future__ import annotations

from pathlib import Path


def test_backend_riptide_avoids_tempfile_module():
    root = Path(__file__).resolve().parents[1] / "riptide"
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "tempfile" not in text, py.as_posix()
