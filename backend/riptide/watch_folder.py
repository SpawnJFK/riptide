"""Poll a folder for .torrent drops and add them to the engine (stdlib only)."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from .engine import Engine, EngineError

log = logging.getLogger("riptide.watch")


class WatchFolder:
    def __init__(self, engine: Engine, poll_seconds: float = 2.0) -> None:
        self.engine = engine
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handled: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="riptide-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _settings(self) -> tuple[bool, Path, str]:
        saved = self.engine.meta.get_settings()
        enabled = bool(saved.get("watch_folder_enabled", False))
        root = Path(saved.get("watch_folder_path") or self.engine.download_dir.parent / "Watch")
        action = str(saved.get("watch_folder_action") or "move")
        if action not in ("move", "delete"):
            action = "move"
        return enabled, root, action

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self._scan_once()
            except Exception:  # pragma: no cover
                log.exception("watch folder scan failed")

    def scan_once(self) -> None:
        """Run one scan (for tests)."""
        self._scan_once()

    def _scan_once(self) -> None:
        enabled, root, action = self._settings()
        if not enabled:
            return
        root.mkdir(parents=True, exist_ok=True)
        added_dir = root / "added"
        if action == "move":
            added_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(root.glob("*.torrent")):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            key = f"{path.resolve()}:{st.st_mtime_ns}:{st.st_size}"
            if key in self._handled:
                continue
            try:
                data = path.read_bytes()
                self.engine.add_torrent_bytes(data)
                self._handled.add(key)
                self.engine._event("info", f"watch folder added: {path.name}")
                if action == "delete":
                    path.unlink(missing_ok=True)
                else:
                    dest = added_dir / path.name
                    if dest.exists():
                        dest.unlink()
                    os.replace(path, dest)
            except EngineError as e:
                if e.status == 409:
                    self._handled.add(key)
                    log.info("watch folder skip duplicate: %s", path.name)
                else:
                    log.warning("watch folder add failed for %s: %s", path.name, e)
            except Exception:
                log.exception("watch folder failed on %s", path)
