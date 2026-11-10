"""Run Riptide: `python -m riptide` (or the `riptide` script)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app, engine_from_config
from .applog import setup_logging
from .config import Config, ConfigError, app_root


def load_dotenv() -> None:
    """Minimal .env loader (repo root), so no extra dependency is needed. Real env vars win."""
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="riptide", description="Riptide torrent client (web UI)")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--register", action="store_true", help="Register HKCU magnet: and .torrent handlers (Windows)")
    p.add_argument("--unregister", action="store_true", help="Restore prior handlers and remove Riptide registration")
    p.add_argument("--open", dest="open_target", metavar="URL", help="Magnet URI or .torrent path (also used by shell)")
    p.add_argument("payload", nargs="?", help="Magnet URI or .torrent path from the shell handler")
    p.add_argument("--print-paths", action="store_true", help="Print the resolved folders as JSON and exit")
    args = p.parse_args()

    try:
        cfg = Config()
    except ConfigError as e:
        if sys.stderr is None:  # windowless launch: nobody sees stderr, so leave the reason in the default log
            setup_logging(app_root() / "data")
        print(f"[riptide] {e}", file=sys.stderr)
        sys.exit(2)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    cfg.__post_init__()

    if args.print_paths:
        keys = ("data_dir", "download_dir", "incomplete_dir", "watch_dir")
        print(json.dumps({k: str(getattr(cfg, k)) for k in keys} | {"port": cfg.port}))
        return

    setup_logging(cfg.data_dir)

    if args.register or args.unregister:
        if sys.platform != "win32":
            print("[riptide] --register and --unregister are Windows-only", file=sys.stderr)
            sys.exit(1)
        from .win_registry import HandlerRegistry, register_handlers

        reg = HandlerRegistry(backup_path=cfg.data_dir / "handler_registry_backup.json")
        if args.register:
            register_handlers(reg)
            print("[riptide] registered magnet: and .torrent handlers (current user)")
        else:
            from .win_autostart import Autostart
            from .win_registry import unregister_all

            unregister_all(reg, Autostart())
            print("[riptide] unregistered handlers and start with Windows; prior handler values restored")
        return

    open_target = args.open_target or args.payload

    from .single_instance import primary_instance_lock, running_url, try_forward_if_running

    if try_forward_if_running(cfg, open_target):
        if not open_target and not args.no_browser:
            webbrowser.open(running_url(cfg))  # start.bat again while running: show the open instance
        sys.exit(0)

    url = f"http://{cfg.host}:{cfg.port}"
    print(f"[riptide] web UI: {url}")
    print(f"[riptide] data: {cfg.data_dir}  downloads: {cfg.download_dir}")

    try:
        with primary_instance_lock(cfg):
            engine = engine_from_config(cfg)
            app = create_app(cfg, engine=engine)
            if open_target:
                app.state.open_on_start = open_target
            # log_config=None: uvicorn keeps our file logging and never touches sys.stdout (None under pythonw)
            server = uvicorn.Server(
                uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning", log_config=None)
            )
            tray = _start_tray(cfg, engine, url, server)
            if not args.no_browser and not open_target:
                webbrowser.open(url)
            try:
                server.run()  # Quit in the tray sets should_exit: lifespan shutdown saves resume data
            finally:
                if tray is not None:
                    tray.stop()
    except RuntimeError:
        if open_target and try_forward_if_running(cfg, open_target):
            sys.exit(0)
        print("[riptide] another instance is already running", file=sys.stderr)
        sys.exit(1)


def _start_tray(cfg: Config, engine, url: str, server: uvicorn.Server):
    if sys.platform != "win32" or not cfg.tray:
        return None
    from .tray import EngineTrayActions, Tray, window_title

    def request_exit() -> None:
        server.should_exit = True

    tray = Tray(EngineTrayActions(engine, url, request_exit), title=window_title(url))
    if not tray.start():
        logging.getLogger("riptide").warning("tray icon unavailable; quit by closing the process")
    return tray


if __name__ == "__main__":
    main()
