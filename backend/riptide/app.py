"""HTTP + WebSocket API. Thin layer over Engine; no libtorrent imports here."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import CONTRACT_VERSION, __version__
from .config import Config
from .engine import Engine, EngineError
from .watch_folder import WatchFolder

log = logging.getLogger("riptide.api")


# ---------------------------------------------------------------- models


class AddMagnet(BaseModel):
    uris: list[str] = Field(min_length=1, max_length=500)
    save_path: str | None = None
    paused: bool = False
    sequential: bool = False
    category: str = ""
    tags: list[str] = []
    add_default_trackers: bool = False


class Patch(BaseModel):
    sequential: bool | None = None
    dl_limit: int | None = Field(default=None, ge=0)
    ul_limit: int | None = Field(default=None, ge=0)
    super_seeding: bool | None = None
    category: str | None = None
    tags: list[str] | None = None
    add_trackers: list[str] | None = None


class Bulk(BaseModel):
    ids: list[str] = Field(min_length=1)
    action: Literal[
        "pause",
        "resume",
        "force_resume",
        "recheck",
        "reannounce",
        "remove",
        "remove_data",
        "top",
        "up",
        "down",
        "bottom",
    ]


class FilePrio(BaseModel):
    priorities: dict[int, int]


class RenameFile(BaseModel):
    index: int
    path: str


class Move(BaseModel):
    path: str


class PeerAddr(BaseModel):
    ip: str
    port: int


class Category(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    save_path: str = ""


class CreateTorrent(BaseModel):
    source: str
    trackers: list[str] = []
    web_seeds: list[str] = []
    comment: str = ""
    private: bool = False
    piece_size: int = 0
    start_seeding: bool = True


# ---------------------------------------------------------------- app


def engine_from_config(cfg: Config) -> Engine:
    """The one way the app builds its engine (used by create_app and by __main__, which also starts the tray)."""
    autostart = None
    if sys.platform == "win32":
        from .win_autostart import Autostart

        autostart = Autostart()
    return Engine(
        data_dir=cfg.data_dir,
        download_dir=cfg.download_dir,
        incomplete_dir=cfg.incomplete_dir,
        listen_port=cfg.bt_port,
        enable_dht=cfg.enable_dht,
        enable_upnp=cfg.enable_upnp,
        listen_interface=cfg.bt_interface,
        autostart=autostart,
    )


def create_app(cfg: Config | None = None, engine: Engine | None = None) -> FastAPI:
    cfg = cfg or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine or engine_from_config(cfg)
        if eng.ses is None:
            eng.start()
        if not eng.meta.get_settings().get("watch_folder_path"):
            eng.meta.set_settings({"watch_folder_path": str(cfg.watch_dir)})
        app.state.engine = eng
        app.state.hub = Hub(asyncio.get_running_loop())
        unsub = eng.subscribe(app.state.hub.publish)
        watch = WatchFolder(eng)
        watch.start()
        app.state.watch = watch
        pending = getattr(app.state, "open_on_start", None)
        if pending:
            from .single_instance import apply_open_on_engine

            try:
                apply_open_on_engine(eng, pending)
            except EngineError as ex:
                log.error("open on start failed: %s", ex)
        try:
            yield
        finally:
            watch.stop()
            unsub()
            await asyncio.to_thread(eng.shutdown)

    app = FastAPI(title="Riptide", version=__version__, lifespan=lifespan)
    app.state.cfg = cfg

    def auth(request: Request) -> None:
        if not cfg.token:
            return
        tok = request.headers.get("x-riptide-token") or request.query_params.get("token")
        if tok != cfg.token:
            raise HTTPException(401, "unauthorized")

    def eng(request: Request) -> Engine:
        return request.app.state.engine

    @app.exception_handler(EngineError)
    async def _engine_error(_: Request, exc: EngineError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    api = "/api"
    dep = [Depends(auth)]

    @app.get(api + "/health")
    def health(request: Request) -> dict[str, Any]:
        import libtorrent

        return {
            "ok": True,
            "version": __version__,
            "contract": CONTRACT_VERSION,
            "libtorrent": libtorrent.__version__,
            "auth": bool(cfg.token),
        }

    @app.get(api + "/torrents", dependencies=dep)
    def list_torrents(e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        return e.list()

    @app.post(api + "/torrents/magnet", dependencies=dep)
    def add_magnet(body: AddMagnet, e: Engine = Depends(eng)) -> dict[str, Any]:
        added, errors = [], []
        for uri in body.uris:
            uri = uri.strip()
            if not uri:
                continue
            try:
                tid = e.add_magnet(
                    uri,
                    save_path=body.save_path,
                    paused=body.paused,
                    sequential=body.sequential,
                    category=body.category,
                    tags=body.tags,
                    add_default_trackers=body.add_default_trackers,
                )
                added.append(tid)
            except EngineError as ex:
                errors.append({"uri": uri[:120], "error": str(ex)})
        return {"added": added, "errors": errors}

    @app.post(api + "/torrents/file", dependencies=dep)
    async def add_files(
        files: list[UploadFile] = File(...),
        save_path: str | None = Form(None),
        paused: bool = Form(False),
        sequential: bool = Form(False),
        category: str = Form(""),
        tags: str = Form(""),
        e: Engine = Depends(eng),
    ) -> dict[str, Any]:
        added, errors = [], []
        tag_list = [t for t in tags.split(",") if t.strip()]
        for f in files:
            data = await f.read()
            if len(data) > 50 * 1024 * 1024:
                errors.append({"file": f.filename, "error": "file too large"})
                continue
            try:
                added.append(
                    e.add_torrent_bytes(
                        data,
                        save_path=save_path or None,
                        paused=paused,
                        sequential=sequential,
                        category=category,
                        tags=tag_list,
                    )
                )
            except EngineError as ex:
                errors.append({"file": f.filename, "error": str(ex)})
        return {"added": added, "errors": errors}

    @app.post(api + "/torrents/bulk", dependencies=dep)
    def bulk(body: Bulk, e: Engine = Depends(eng)) -> dict[str, Any]:
        failed = []
        for tid in body.ids:
            try:
                a = body.action
                if a == "pause":
                    e.pause(tid)
                elif a == "resume":
                    e.resume(tid)
                elif a == "force_resume":
                    e.resume(tid, force=True)
                elif a == "recheck":
                    e.recheck(tid)
                elif a == "reannounce":
                    e.reannounce(tid)
                elif a == "remove":
                    e.remove(tid, False)
                elif a == "remove_data":
                    e.remove(tid, True)
                else:
                    e.queue(tid, a)
            except EngineError as ex:
                failed.append({"id": tid, "error": str(ex)})
        return {"ok": not failed, "failed": failed}

    @app.post(api + "/torrents/pause-all", dependencies=dep)
    def pause_all(e: Engine = Depends(eng)) -> dict[str, bool]:
        e.pause_all()
        return {"ok": True}

    @app.post(api + "/torrents/resume-all", dependencies=dep)
    def resume_all(e: Engine = Depends(eng)) -> dict[str, bool]:
        e.resume_all()
        return {"ok": True}

    @app.get(api + "/torrents/{tid}", dependencies=dep)
    def get_torrent(tid: str, e: Engine = Depends(eng)) -> dict[str, Any]:
        return e.get(tid)

    @app.patch(api + "/torrents/{tid}", dependencies=dep)
    def patch_torrent(tid: str, body: Patch, e: Engine = Depends(eng)) -> dict[str, Any]:
        e.update(tid, **body.model_dump())
        return e.get(tid)

    @app.delete(api + "/torrents/{tid}", dependencies=dep)
    def delete_torrent(tid: str, delete_files: bool = False, e: Engine = Depends(eng)) -> dict[str, bool]:
        e.remove(tid, delete_files)
        return {"ok": True}

    @app.get(api + "/torrents/{tid}/files", dependencies=dep)
    def files(tid: str, e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        return e.files(tid)

    @app.put(api + "/torrents/{tid}/files", dependencies=dep)
    def set_prio(tid: str, body: FilePrio, e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        e.set_file_priorities(tid, body.priorities)
        return e.files(tid)

    @app.post(api + "/torrents/{tid}/rename", dependencies=dep)
    def rename(tid: str, body: RenameFile, e: Engine = Depends(eng)) -> dict[str, bool]:
        e.rename_file(tid, body.index, body.path)
        return {"ok": True}

    @app.post(api + "/torrents/{tid}/move", dependencies=dep)
    def move(tid: str, body: Move, e: Engine = Depends(eng)) -> dict[str, bool]:
        e.move(tid, body.path)
        return {"ok": True}

    @app.get(api + "/torrents/{tid}/peers", dependencies=dep)
    def peers(tid: str, e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        return e.peers(tid)

    @app.post(api + "/torrents/{tid}/peers", dependencies=dep)
    def add_peer(tid: str, body: PeerAddr, e: Engine = Depends(eng)) -> dict[str, bool]:
        e.connect_peer(tid, body.ip, body.port)
        return {"ok": True}

    @app.get(api + "/torrents/{tid}/trackers", dependencies=dep)
    def trackers(tid: str, e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        return e.trackers(tid)

    @app.get(api + "/torrents/{tid}/pieces", dependencies=dep)
    def pieces(tid: str, e: Engine = Depends(eng)) -> dict[str, Any]:
        return e.pieces(tid)

    @app.get(api + "/torrents/{tid}/export", dependencies=dep)
    def export(tid: str, e: Engine = Depends(eng)) -> Response:
        data = e.export_torrent(tid)
        return Response(
            data,
            media_type="application/x-bittorrent",
            headers={"content-disposition": f'attachment; filename="{tid}.torrent"'},
        )

    @app.get(api + "/stats", dependencies=dep)
    def stats(e: Engine = Depends(eng)) -> dict[str, Any]:
        return {"stats": e.stats(), "history": e.history()[-300:]}

    @app.get(api + "/events", dependencies=dep)
    def events(e: Engine = Depends(eng)) -> list[dict[str, Any]]:
        return e.events()

    @app.get(api + "/settings", dependencies=dep)
    def get_settings(e: Engine = Depends(eng)) -> dict[str, Any]:
        return e.get_settings()

    @app.patch(api + "/settings", dependencies=dep)
    def patch_settings(body: dict[str, Any], e: Engine = Depends(eng)) -> dict[str, Any]:
        return e.set_settings(body)

    @app.post(api + "/alt-speed", dependencies=dep)
    def alt_speed(on: bool, e: Engine = Depends(eng)) -> dict[str, bool]:
        e.set_alt_speed(on)
        return {"ok": True, "on": on}

    @app.get(api + "/categories", dependencies=dep)
    def categories(e: Engine = Depends(eng)) -> dict[str, Any]:
        return {"categories": e.meta.categories(), "tags": e.meta.tags()}

    @app.post(api + "/categories", dependencies=dep)
    def add_category(body: Category, e: Engine = Depends(eng)) -> dict[str, Any]:
        e.meta.set_category(body.name, body.save_path)
        return {"categories": e.meta.categories()}

    @app.delete(api + "/categories/{name}", dependencies=dep)
    def del_category(name: str, e: Engine = Depends(eng)) -> dict[str, Any]:
        e.meta.delete_category(name)
        return {"categories": e.meta.categories()}

    @app.post(api + "/create", dependencies=dep)
    def create(body: CreateTorrent, e: Engine = Depends(eng)) -> Response:
        tid, data = e.create_torrent(**body.model_dump())
        return Response(
            data,
            media_type="application/x-bittorrent",
            headers={"x-riptide-id": tid, "content-disposition": 'attachment; filename="created.torrent"'},
        )

    # -------------------------------------------------------------- ws

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if cfg.token and socket.query_params.get("token") != cfg.token:
            await socket.close(code=4401)
            return
        await socket.accept()
        hub: Hub = socket.app.state.hub
        e: Engine = socket.app.state.engine
        q = hub.join()
        try:
            await socket.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "torrents": e.list(),
                        "stats": e.stats(),
                        "events": e.events()[-50:],
                        "history": e.history()[-300:],
                    }
                )
            )
            while True:
                msg = await q.get()
                await socket.send_text(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.leave(q)

    # -------------------------------------------------------------- static

    dist = cfg.web_dist
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


class Hub:
    """Fan-out of engine messages to websocket queues, bridging threads to asyncio."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queues: set[asyncio.Queue[str]] = set()

    def join(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self.queues.add(q)
        return q

    def leave(self, q: asyncio.Queue[str]) -> None:
        self.queues.discard(q)

    def publish(self, msg: dict[str, Any]) -> None:
        if not self.queues:
            return
        text = json.dumps(msg, default=str)
        self.loop.call_soon_threadsafe(self._fan, text)

    def _fan(self, text: str) -> None:
        for q in list(self.queues):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest, slow client
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(text)
