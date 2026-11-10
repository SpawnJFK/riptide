"""Small JSON store for things libtorrent does not keep: categories, tags, app settings."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class MetaStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._dirty = False
        self._last_flush = 0.0
        self._data: dict[str, Any] = {
            "torrents": {},
            "categories": {},
            "settings": {},
            "rss": {"feeds": {}, "rules": {}},
        }
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text("utf-8"))
                for k in self._data:
                    if k in loaded:
                        self._data[k] = loaded[k]
            except Exception:
                backup = self.path.with_suffix(".corrupt.json")
                os.replace(self.path, backup)

    # torrents
    def get(self, tid: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["torrents"].get(tid, {}))

    def ensure(self, tid: str) -> None:
        with self._lock:
            if tid not in self._data["torrents"]:
                self._data["torrents"][tid] = {"added_at": time.time(), "category": "", "tags": []}
                self._mark()

    def update(self, tid: str, **fields: Any) -> None:
        with self._lock:
            entry = self._data["torrents"].setdefault(tid, {"category": "", "tags": []})
            for k, v in fields.items():
                if k == "tags" and v is not None:
                    v = sorted({str(t).strip() for t in v if str(t).strip()})
                entry[k] = v
            if fields.get("category"):
                self._data["categories"].setdefault(fields["category"], {"save_path": ""})
            self._mark()

    def remove(self, tid: str) -> None:
        with self._lock:
            if self._data["torrents"].pop(tid, None) is not None:
                self._mark()

    # categories
    def categories(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._data["categories"]))

    def set_category(self, name: str, save_path: str = "") -> None:
        with self._lock:
            self._data["categories"][name] = {"save_path": save_path}
            self._mark()

    def delete_category(self, name: str) -> None:
        with self._lock:
            self._data["categories"].pop(name, None)
            for t in self._data["torrents"].values():
                if t.get("category") == name:
                    t["category"] = ""
            self._mark()

    def tags(self) -> list[str]:
        with self._lock:
            out: set[str] = set()
            for t in self._data["torrents"].values():
                out.update(t.get("tags", []))
            return sorted(out)

    # settings
    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["settings"])

    def set_settings(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data["settings"].update(values)
            self._mark(force=True)

    # rss feeds (reserved for RSS auto-download)
    def rss(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data["rss"]))

    def set_rss(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._data["rss"] = value
            self._mark(force=True)

    # persistence
    def _mark(self, force: bool = False) -> None:
        self._dirty = True
        if force or time.monotonic() - self._last_flush > 2:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), "utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
            self._last_flush = time.monotonic()
