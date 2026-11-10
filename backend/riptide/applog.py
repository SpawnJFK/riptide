"""Logging for every launch: a rotating file under the data dir, plus the console when there is one.

Windowless launches run under pythonw.exe, where sys.stdout and sys.stderr are None: print() goes nowhere and
uvicorn's default formatter crashes on sys.stdout.isatty(). setup_logging() routes everything to the log file and
uvicorn is started with log_config=None so it only adds levels, never its own stream handlers.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

LOG_NAME = "riptide.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "logs" / LOG_NAME


class _LogStream:
    """File-like object for sys.stdout/sys.stderr under pythonw: each line becomes a log record."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger, self._level, self._buf = logger, level, ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _has_console(stream: TextIO | None) -> bool:
    return stream is not None and hasattr(stream, "write")


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Configure the root logger once per process. Returns the log file path."""
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_riptide", False):
            root.removeHandler(h)
            h.close()
    fmt = logging.Formatter(FORMAT)
    fh = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=False)
    fh.setFormatter(fmt)
    fh._riptide = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    windowless = not _has_console(sys.stderr) or not _has_console(sys.stdout)
    if windowless:
        out = logging.getLogger("riptide.stdout")
        sys.stdout = _LogStream(out, logging.INFO)  # type: ignore[assignment]
        sys.stderr = _LogStream(out, logging.ERROR)  # type: ignore[assignment]
    else:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        ch._riptide = True  # type: ignore[attr-defined]
        root.addHandler(ch)
    root.setLevel(level)

    def _excepthook(exc_type, exc, tb) -> None:
        logging.getLogger("riptide").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook
    threading.excepthook = lambda a: _excepthook(a.exc_type, a.exc_value, a.exc_traceback)
    logging.getLogger("riptide").info("logging to %s (windowless=%s)", path, windowless)
    return path
