"""Riptide torrent engine: a thin, thread-safe owner of one libtorrent session.

Rules:
- Only this module talks to libtorrent. The API layer calls Engine methods.
- All libtorrent calls happen on handles or the session, which are thread-safe.
- Snapshots (plain dicts) are produced on the alert thread and read by the API.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import libtorrent as lt

from .store import MetaStore

log = logging.getLogger("riptide.engine")

STATE_NAMES = {
    0: "queued",  # unused in lt 2.x, kept for safety
    1: "checking",
    2: "metadata",
    3: "downloading",
    4: "finished",
    5: "seeding",
    6: "allocating",
    7: "checking_resume",
}

_DHT_NODES_IDX = lt.find_metric_idx("dht.dht_nodes")
# Not every binding build exports the uTP flag; treat missing as "unknown" (0).
_UTP_FLAG = int(getattr(lt.peer_info, "utp_socket", 0))

PRIORITY_SKIP = 0
PRIORITY_NORMAL = 4
PRIORITY_HIGH = 7

DEFAULT_TRACKERS: list[str] = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]

PART_RT_SUFFIX = ".!rt"


def part_rt_path(rel: str) -> str:
    return rel.replace("\\", "/") + PART_RT_SUFFIX


def _alert_field(alert: Any, name: str) -> str:
    val = getattr(alert, name, None)
    if callable(val):
        val = val()
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val) if val is not None else ""


def original_from_part_rt(path: str) -> str | None:
    p = path.replace("\\", "/")
    if p.endswith(PART_RT_SUFFIX):
        return p[: -len(PART_RT_SUFFIX)]
    return None


def tid_of(handle_or_status: Any) -> str:
    """Stable torrent id: best info-hash as hex (v1 sha1 or truncated v2)."""
    ih = handle_or_status.info_hashes
    if callable(ih):  # torrent_handle exposes a method, torrent_status an attribute
        ih = ih()
    return str(ih.get_best())


class EngineError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Engine:
    def __init__(
        self,
        data_dir: Path,
        download_dir: Path,
        incomplete_dir: Path | None = None,
        listen_port: int = 6881,
        enable_dht: bool = True,
        enable_upnp: bool = True,
        listen_interface: str = "0.0.0.0",
        autostart: Any = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        # Start-with-Windows switch (win_autostart.Autostart), injected so this module stays registry-free.
        self.autostart = autostart
        self.resume_dir = self.data_dir / "resume"
        self.torrent_dir = self.data_dir / "torrents"
        self.download_dir = Path(download_dir)
        self.incomplete_dir = Path(incomplete_dir) if incomplete_dir else self.download_dir.parent / "Incomplete"
        for d in (self.data_dir, self.resume_dir, self.torrent_dir, self.download_dir, self.incomplete_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.meta = MetaStore(self.data_dir / "meta.json")
        self._listen_port = listen_port
        self._listen_interface = listen_interface
        self._enable_dht = enable_dht
        self._enable_upnp = enable_upnp

        self.ses: lt.session | None = None
        self._lock = threading.RLock()
        self._status: dict[str, dict[str, Any]] = {}
        self._handles: dict[str, lt.torrent_handle] = {}
        self._stats: dict[str, Any] = {}
        self._history: deque[tuple[float, int, int]] = deque(maxlen=600)
        self._events: deque[dict[str, Any]] = deque(maxlen=500)
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_resume = 0
        self._dht_nodes = 0
        self._resume_cv = threading.Condition()
        self._defer_final_move: set[str] = set()
        # Pieces each torrent's fastresume claimed at load, and its run state, so a rejected
        # resume (files deleted or moved) is reported as missing files instead of seeding or re-downloading.
        self._resume_claims: dict[str, dict[str, Any]] = {}
        self._rejected: dict[str, str] = {}
        # rename_file() calls libtorrent has not confirmed yet. While any are in flight for a torrent, the .!rt
        # disk fallback must not touch its files: renaming on disk under libtorrent made its own rename fail,
        # left libtorrent on .!rt names and made move_storage move nothing (the bug seed_mode used to hide).
        self._renames_pending: dict[str, int] = {}

    # ------------------------------------------------------------------ life

    def start(self) -> None:
        params = self._load_session_params()
        settings = params.settings
        settings.update(self._base_settings())
        params.settings = settings
        self.ses = lt.session(params)
        if self.alt_speed():
            self.set_alt_speed(True)  # slow mode survives a restart (the flag was saved but not re-applied)
        self._load_resume_files()
        self._thread = threading.Thread(target=self._alert_loop, name="riptide-alerts", daemon=True)
        self._thread.start()
        log.info("engine started, listen %s:%s", self._listen_interface, self._listen_port)

    def shutdown(self, timeout: float = 10.0) -> None:
        if not self.ses:
            return
        log.info("engine shutting down, saving resume data")
        self.ses.pause()
        end = time.monotonic() + min(timeout, 5.0)
        for h in self._all_handles():
            if not h.is_valid() or h.torrent_file() is None:
                continue
            tid = tid_of(h)
            while time.monotonic() < end:
                self._reconcile_part_rt_paths(h, tid)
                _orig, active = self._part_rt_meta(tid)
                if not active:
                    break
                time.sleep(0.05)
        self.save_all_resume(wait=timeout)
        try:
            buf = lt.write_session_params_buf(self.ses.session_state())
            _atomic_write(self.data_dir / "session.state", buf)
        except Exception:  # pragma: no cover - best effort
            log.exception("failed to save session state")
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.meta.flush()
        self.ses = None

    def _base_settings(self) -> dict[str, Any]:
        iface = self._listen_interface
        port = self._listen_port
        listen = f"{iface}:{port}"
        if iface in ("0.0.0.0", ""):
            listen = f"0.0.0.0:{port},[::]:{port}"
        s: dict[str, Any] = {
            "user_agent": "Riptide/0.1 libtorrent/" + lt.__version__,
            "peer_fingerprint": lt.generate_fingerprint("RT", 0, 1, 0, 0),
            "listen_interfaces": listen,
            "alert_mask": (
                lt.alert_category.error
                | lt.alert_category.status
                | lt.alert_category.storage
                | lt.alert_category.tracker
                | lt.alert_category.port_mapping
                | lt.alert_category.stats
            ),
            "alert_queue_size": 10000,
            "enable_dht": self._enable_dht,
            "enable_lsd": True,
            "enable_upnp": self._enable_upnp,
            "enable_natpmp": self._enable_upnp,
            "dht_bootstrap_nodes": "router.bittorrent.com:6881,dht.transmissionbt.com:6881,router.utorrent.com:6881,dht.libtorrent.org:25401",
        }
        saved = self.meta.get_settings()
        s.update({k: v for k, v in saved.items() if k in SETTABLE})
        return s

    def _load_session_params(self) -> lt.session_params:
        path = self.data_dir / "session.state"
        if path.exists():
            try:
                return lt.read_session_params(path.read_bytes())
            except Exception:
                log.warning("session.state unreadable, starting fresh")
        return lt.session_params()

    def _load_resume_files(self) -> None:
        """Every torrent loads from its fastresume with normal checking (no seed_mode).

        libtorrent verifies that files behind the pieces the resume claims exist with the right size; if not it
        posts fastresume_rejected_alert and rechecks. _after_check turns a short recheck into "missing files".
        """
        assert self.ses
        for f in sorted(self.resume_dir.glob("*.fastresume")):
            tid = f.stem
            tfile = self.torrent_dir / (f.stem + ".torrent")
            meta = self.meta.get(tid)
            try:
                atp = lt.read_resume_data(f.read_bytes())
                if atp.ti is None and tfile.exists():
                    atp.ti = lt.torrent_info(str(tfile))
                if meta.get("completed_at"):
                    self._repair_completed_resume(atp, meta)
                self._resume_claims[tid] = {
                    "pieces": sum(1 for x in atp.have_pieces if x),
                    "paused": bool(atp.flags & lt.torrent_flags.paused),
                    "auto": bool(atp.flags & lt.torrent_flags.auto_managed),
                }
                self.ses.async_add_torrent(atp)
            except Exception:
                log.exception("failed to load resume file %s", f.name)

    def _repair_completed_resume(self, atp: Any, meta: dict[str, Any]) -> None:
        """Resume files written by older versions could predate the final move and the .!rt renames.

        Point a completed torrent at its final folder when its files are there, and drop .!rt renames whose
        original file exists. Anything still missing after this is real and is reported as missing files.
        """
        if atp.ti is None:
            return
        fs = atp.ti.layout() if hasattr(atp.ti, "layout") else atp.ti.files()
        final = meta.get("final_save_path")
        if final:
            dest = Path(final).expanduser()
            real = [i for i in range(fs.num_files()) if not fs.file_flags(i) & lt.file_storage.flag_pad_file]
            if real and (dest / fs.file_path(real[0])).exists():
                atp.save_path = str(dest)
        renamed = dict(atp.renamed_files or {})
        for idx, name in list(renamed.items()):
            orig = original_from_part_rt(str(name))
            if orig and (Path(atp.save_path) / orig).exists():
                renamed.pop(idx)
        atp.renamed_files = renamed

    # ------------------------------------------------------------ listeners

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return unsubscribe

    def _emit(self, msg: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(msg)
            except Exception:  # pragma: no cover
                log.exception("listener failed")

    def _event(self, level: str, text: str, tid: str | None = None) -> None:
        ev = {"ts": time.time(), "level": level, "text": text, "id": tid}
        self._events.append(ev)
        self._emit({"type": "event", "event": ev})

    # ------------------------------------------------------------ alert loop

    def _alert_loop(self) -> None:
        last_tick = 0.0
        while not self._stop.is_set():
            ses = self.ses
            if ses is None:
                break
            ses.wait_for_alert(250)
            batch = list(ses.pop_alerts())
            deferred: list[Any] = []
            for a in batch:
                if isinstance(a, lt.torrent_finished_alert):
                    deferred.append(a)
                    continue
                try:
                    self._handle_alert(a)
                except Exception:  # pragma: no cover
                    log.exception("alert handler failed: %s", type(a).__name__)
            for a in deferred:
                try:
                    self._handle_alert(a)
                except Exception:  # pragma: no cover
                    log.exception("alert handler failed: %s", type(a).__name__)
            now = time.monotonic()
            if now - last_tick >= 1.0:
                last_tick = now
                ses.post_torrent_updates()
                self._tick()

    def _handle_alert(self, a: Any) -> None:
        if isinstance(a, lt.state_update_alert):
            with self._lock:
                for st in a.status:
                    self._status[tid_of(st)] = self._snapshot(st)
        elif isinstance(a, lt.add_torrent_alert):
            if a.error.value():
                self._event("error", f"add failed: {a.error.message()}")
                return
            h = a.handle
            tid = tid_of(h)
            with self._lock:
                self._handles[tid] = h
                self._status[tid] = self._snapshot(h.status())
            self.meta.ensure(tid)
            h.save_resume_data(lt.save_resume_flags_t.save_info_dict)
            self._pending_add_done(tid)
            self._sync_part_rt_for_handle(h, tid)
            self._reconcile_part_rt_paths(h, tid)
            self._emit({"type": "added", "id": tid})
        elif isinstance(a, lt.metadata_received_alert):
            h = a.handle
            tid = tid_of(h)
            self._save_torrent_file(h)
            self._sync_part_rt_for_handle(h, tid)
            self._event("info", f"metadata received: {h.status().name}", tid)
        elif isinstance(a, lt.file_progress_alert):
            h = a.handle
            if self._part_rt_enabled() and not h.status().is_finished:
                self._sync_part_rt_for_handle(h, tid_of(h))
        elif isinstance(a, lt.file_renamed_alert):
            h = a.handle
            tid = tid_of(h)
            self._rename_landed(tid)
            idx = str(int(a.index))
            new_name = _alert_field(a, "new_name").replace("\\", "/")
            rec = self.meta.get(tid)
            overrides = dict(rec.get("file_path_overrides") or {})
            overrides[idx] = new_name
            orig_map, active = self._part_rt_meta(tid)
            if new_name.endswith(PART_RT_SUFFIX):
                active.add(idx)
            elif idx in orig_map and new_name.replace("\\", "/") == orig_map[idx].replace("\\", "/"):
                active.discard(idx)
                overrides.pop(idx, None)
            self.meta.update(tid, file_path_overrides=overrides)
            self._save_part_rt_meta(tid, orig_map, active)
            h.save_resume_data()  # the rename has landed now; saving right after rename_file() was too early
            self._maybe_complete_deferred(h, tid)
        elif isinstance(a, lt.file_completed_alert):
            h = a.handle
            tid = tid_of(h)
            self._restore_part_rt_file(h, tid, int(a.index))
        elif isinstance(a, lt.torrent_finished_alert):
            h = a.handle
            tid = tid_of(h)
            self._restore_all_part_rt(h, tid)
            _orig, active = self._part_rt_meta(tid)
            if active:
                self._defer_final_move.add(tid)
            else:
                self._complete_torrent(h, tid)
        elif isinstance(a, lt.save_resume_data_alert):
            tid = tid_of(a.handle)
            try:
                _atomic_write(self.resume_dir / f"{tid}.fastresume", lt.write_resume_data_buf(a.params))
                self._write_torrent_from_params(tid, a.params)
            finally:
                self._resume_done()
        elif isinstance(a, lt.save_resume_data_failed_alert):
            self._resume_done()
        elif isinstance(a, lt.storage_moved_alert):
            # move_storage() is asynchronous: only now does the resume data carry the new save path.
            a.handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        elif isinstance(a, lt.storage_moved_failed_alert):
            self._event("error", f"move failed: {a.message()}", tid_of(a.handle))
        elif isinstance(a, lt.fastresume_rejected_alert):
            tid = tid_of(a.handle)
            self._rejected[tid] = a.message()
            log.warning("fastresume rejected for %s: %s", tid, self._rejected[tid])
            a.handle.set_flags(lt.torrent_flags.stop_when_ready)  # force-stop after the recheck, decide there
        elif isinstance(a, lt.torrent_checked_alert):
            self._after_check(a.handle)
        elif isinstance(a, lt.torrent_removed_alert):
            tid = str(a.info_hashes.get_best())
            with self._lock:
                self._handles.pop(tid, None)
                self._status.pop(tid, None)
            for p in (self.resume_dir / f"{tid}.fastresume", self.torrent_dir / f"{tid}.torrent"):
                p.unlink(missing_ok=True)
            self.meta.remove(tid)
            self._emit({"type": "removed", "id": tid})
        elif isinstance(a, lt.torrent_deleted_alert):
            # "Remove with data" finished deleting files: drop the per-torrent work folder Riptide made (cog).
            self._prune_work_dir(str(a.info_hashes.get_best()))
        elif isinstance(a, lt.session_stats_alert):
            vals = a.values
            if isinstance(vals, dict):
                self._dht_nodes = int(vals.get("dht.dht_nodes", 0))
            else:
                self._dht_nodes = int(vals[_DHT_NODES_IDX]) if _DHT_NODES_IDX >= 0 else 0
        elif isinstance(a, lt.torrent_error_alert):
            self._event("error", f"{a.torrent_name}: {a.error.message()}", tid_of(a.handle))
        elif isinstance(a, lt.file_rename_failed_alert):
            self._rename_landed(tid_of(a.handle))
            self._event("error", f"rename failed: {a.message()}", tid_of(a.handle))
        elif isinstance(a, lt.file_error_alert):
            self._event("error", f"file error: {a.message()}", tid_of(a.handle))
        elif isinstance(a, lt.listen_failed_alert):
            self._event("error", f"listen failed: {a.message()}")
        elif isinstance(a, lt.listen_succeeded_alert):
            self._event("info", f"listening on {a.address}:{a.port}")
        elif isinstance(a, lt.portmap_alert):
            self._event("info", a.message())
        elif isinstance(a, lt.tracker_error_alert):
            log.debug("tracker error: %s", a.message())

    def _tick(self) -> None:
        for tid in list(self._defer_final_move):
            h = self._handles.get(tid)
            if h is not None and h.is_valid():
                self._part_rt_disk_restore(h, tid)
        for tid, h in list(self._handles.items()):
            if h.is_valid() and self.meta.get(tid).get("part_rt_active"):
                self._reconcile_part_rt_paths(h, tid)
        with self._lock:
            sts = list(self._status.values())
        dl = sum(s["dl_rate"] for s in sts)
        ul = sum(s["ul_rate"] for s in sts)
        self._history.append((time.time(), dl, ul))
        ses = self.ses
        if ses:
            ses.post_session_stats()
        self._stats = {
            "dl_rate": dl,
            "ul_rate": ul,
            "dht_nodes": self._dht_nodes,
            "peers": sum(s["peers"] + s["seeds"] for s in sts),
            "torrents": len(sts),
            "active": sum(1 for s in sts if s["dl_rate"] > 0 or s["ul_rate"] > 0),
            "listening": bool(ses and ses.is_listening()),
            "port": ses.listen_port() if ses else 0,
            "paused": bool(ses and ses.is_paused()),
            "dl_limit": self.meta.get_settings().get("download_rate_limit", 0),
            "ul_limit": self.meta.get_settings().get("upload_rate_limit", 0),
            "free_space": _free_space(self.download_dir),
        }
        self._emit({"type": "tick", "torrents": sts, "stats": self._stats, "t": time.time()})
        # Periodically persist resume data for torrents that changed.
        if int(time.time()) % 60 == 0:
            for h in self._all_handles():
                if h.need_save_resume_data():
                    h.save_resume_data()

    # ------------------------------------------------------------ snapshots

    def _snapshot(self, st: Any) -> dict[str, Any]:
        tid = tid_of(st)
        m = self.meta.get(tid)
        flags = st.flags
        paused = bool(flags & lt.torrent_flags.paused)
        auto = bool(flags & lt.torrent_flags.auto_managed)
        state = STATE_NAMES.get(int(st.state), "unknown")
        missing = m.get("missing_files") or ""
        if st.errc.value() or missing:
            state = "error"
        elif paused and not auto:
            state = "completed" if st.is_finished else "paused"
        elif paused and auto:
            state = "queued"
        elif state == "seeding" and st.upload_payload_rate == 0 and st.num_peers == 0:
            state = "seeding"  # idle seeding, UI can dim it
        remaining = max(0, st.total_wanted - st.total_wanted_done)
        rate = st.download_payload_rate
        eta = int(remaining / rate) if rate > 0 and remaining > 0 else -1
        size = st.total_wanted
        uploaded = st.all_time_upload
        downloaded = st.all_time_download or st.total_wanted_done
        return {
            "id": tid,
            "name": st.name or tid,
            "state": state,
            "progress": round(st.progress, 6),
            "size": size,
            "done": st.total_wanted_done,
            "dl_rate": rate,
            "ul_rate": st.upload_payload_rate,
            "eta": eta,
            "peers": st.num_peers - st.num_seeds,
            "seeds": st.num_seeds,
            "peers_total": max(0, st.list_peers - st.list_seeds),
            "seeds_total": st.list_seeds,
            "ratio": round(uploaded / downloaded, 3) if downloaded > 0 else 0.0,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "save_path": st.save_path,
            "queue": st.queue_position,
            "added": st.added_time,
            "completed": st.completed_time,
            "sequential": bool(flags & lt.torrent_flags.sequential_download),
            "auto_managed": auto,
            "has_metadata": st.has_metadata,
            "error": st.errc.message() if st.errc.value() else missing,
            "category": m.get("category", ""),
            "tags": m.get("tags", []),
            "dl_limit": st.handle.download_limit() if st.handle.is_valid() else 0,
            "ul_limit": st.handle.upload_limit() if st.handle.is_valid() else 0,
            "availability": round(st.distributed_copies, 3) if st.distributed_copies >= 0 else 0,
            "tracker": st.current_tracker,
        }

    # ------------------------------------------------------------- queries

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._status.values())

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def history(self) -> list[tuple[float, int, int]]:
        return list(self._history)

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def _all_handles(self) -> list[lt.torrent_handle]:
        with self._lock:
            return list(self._handles.values())

    def handle(self, tid: str) -> lt.torrent_handle:
        with self._lock:
            h = self._handles.get(tid)
        if h is None or not h.is_valid():
            raise EngineError(f"torrent {tid} not found", 404)
        return h

    def get(self, tid: str) -> dict[str, Any]:
        h = self.handle(tid)
        snap = self._snapshot(h.status())
        with self._lock:
            self._status[tid] = snap
        ti = h.torrent_file()
        snap = dict(snap)
        if ti is not None:
            snap.update(
                {
                    "pieces": ti.num_pieces(),
                    "piece_size": ti.piece_length(),
                    "comment": ti.comment(),
                    "creator": ti.creator(),
                    "created": ti.creation_date(),
                    "private": ti.priv(),
                    "files_count": _real_file_count(ti),
                }
            )
        snap["magnet"] = lt.make_magnet_uri(h)
        return snap

    def files(self, tid: str) -> list[dict[str, Any]]:
        h = self.handle(tid)
        ti = h.torrent_file()
        if ti is None:
            return []
        fs = ti.layout() if hasattr(ti, "layout") else ti.files()
        progress = h.file_progress(flags=1)  # piece_granularity: cheap
        prios = h.get_file_priorities()
        overrides = self.meta.get(tid).get("file_path_overrides") or {}
        out = []
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            size = fs.file_size(i)
            path = overrides.get(str(i)) or fs.file_path(i).replace("\\", "/")
            out.append(
                {
                    "index": i,
                    "path": path.replace("\\", "/"),
                    "size": size,
                    "done": progress[i] if i < len(progress) else 0,
                    "progress": (progress[i] / size) if size and i < len(progress) else 1.0,
                    "priority": prios[i] if i < len(prios) else PRIORITY_NORMAL,
                }
            )
        return out

    def peers(self, tid: str) -> list[dict[str, Any]]:
        h = self.handle(tid)
        out = []
        for p in h.get_peer_info():
            ip, port = p.ip
            out.append(
                {
                    "ip": ip,
                    "port": port,
                    "client": p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) else str(p.client),
                    "progress": round(p.progress, 4),
                    "dl_rate": p.payload_down_speed,
                    "ul_rate": p.payload_up_speed,
                    "downloaded": p.total_download,
                    "uploaded": p.total_upload,
                    "seed": bool(p.flags & lt.peer_info.seed) or p.progress >= 1,
                    "encrypted": bool(p.flags & (lt.peer_info.rc4_encrypted | lt.peer_info.plaintext_encrypted)),
                    "utp": bool(p.flags & _UTP_FLAG),
                    "kind": {1: "web seed", 2: "http seed"}.get(int(p.connection_type), "peer"),
                    "source": _peer_source(p.source),
                }
            )
        out.sort(key=lambda x: x["dl_rate"] + x["ul_rate"], reverse=True)
        return out

    def trackers(self, tid: str) -> list[dict[str, Any]]:
        h = self.handle(tid)
        out = []
        for t in h.trackers():
            eps = t.get("endpoints", []) if isinstance(t, dict) else []
            msg, status, seeds, peers, fails = "", "not contacted", -1, -1, 0
            for ep in eps:
                for ih in ep.get("info_hashes", []):
                    if ih.get("message"):
                        msg = ih["message"]
                    fails = max(fails, ih.get("fails", 0))
                    seeds = max(seeds, ih.get("scrape_complete", -1))
                    peers = max(peers, ih.get("scrape_incomplete", -1))
                    if ih.get("updating"):
                        status = "updating"
                    elif ih.get("last_error", {}).get("value", 0) if isinstance(ih.get("last_error"), dict) else False:
                        status = "error"
                    elif status == "not contacted" and ih.get("start_sent"):
                        status = "working"
            if fails and status != "updating":
                status = "error"
            out.append(
                {
                    "url": t["url"],
                    "tier": t["tier"],
                    "status": status,
                    "message": msg,
                    "seeds": seeds,
                    "peers": peers,
                }
            )
        return out

    def pieces(self, tid: str) -> dict[str, Any]:
        """Piece map compressed to at most 1024 buckets (0..1 fill per bucket)."""
        h = self.handle(tid)
        st = h.status(lt.status_flags_t.query_pieces) if hasattr(lt, "status_flags_t") else h.status()
        bits = list(st.pieces)
        n = len(bits)
        if n == 0:
            return {"total": 0, "buckets": []}
        buckets = min(1024, n)
        step = n / buckets
        out = []
        for b in range(buckets):
            lo, hi = int(b * step), max(int((b + 1) * step), int(b * step) + 1)
            seg = bits[lo:hi]
            out.append(round(sum(1 for x in seg if x) / len(seg), 3))
        return {"total": n, "buckets": out}

    # ------------------------------------------------------------- mutations

    def add_magnet(self, uri: str, **opts: Any) -> str:
        assert self.ses
        try:
            atp = lt.parse_magnet_uri(uri.strip())
        except Exception as e:
            raise EngineError(f"invalid magnet link: {e}") from e
        return self._add(atp, **opts)

    def add_torrent_bytes(self, data: bytes, **opts: Any) -> str:
        assert self.ses
        try:
            atp = lt.load_torrent_buffer(data)
        except Exception as e:
            raise EngineError(f"invalid .torrent file: {e}") from e
        return self._add(atp, **opts)

    def _add(
        self,
        atp: Any,
        save_path: str | None = None,
        paused: bool = False,
        sequential: bool = False,
        category: str = "",
        tags: list[str] | None = None,
        skip_check: bool = False,
        add_default_trackers: bool = False,
    ) -> str:
        assert self.ses
        tid = str(atp.info_hashes.get_best()) if atp.ti is None else str(atp.ti.info_hashes().get_best())
        with self._lock:
            if tid in self._handles:
                raise EngineError("torrent already added", 409)
        final_path = Path(save_path).expanduser() if save_path else self._default_save_path(category)
        final_path.mkdir(parents=True, exist_ok=True)
        work_path, final_resolved = self._download_work_path(final_path, tid, force_final=skip_check)
        atp.save_path = str(work_path)
        flags = lt.torrent_flags.default_flags | lt.torrent_flags.duplicate_is_error
        if paused:
            flags |= lt.torrent_flags.paused
            flags &= ~lt.torrent_flags.auto_managed
        if sequential:
            flags |= lt.torrent_flags.sequential_download
        if skip_check:
            flags |= lt.torrent_flags.seed_mode
        atp.flags = flags
        if add_default_trackers:
            atp.trackers = list(atp.trackers) + [t for t in DEFAULT_TRACKERS if t not in atp.trackers]
        self.meta.update(
            tid,
            category=category,
            tags=tags or [],
            added_at=time.time(),
            final_save_path=str(final_resolved),
        )
        self.ses.async_add_torrent(atp)
        return tid

    def _download_work_path(self, final_path: Path, tid: str, *, force_final: bool = False) -> tuple[Path, Path]:
        """Pick where libtorrent writes until the torrent finishes (incomplete vs final)."""
        final = final_path.expanduser().resolve()
        if force_final:
            return final, final
        saved = self.meta.get_settings()
        use_inc = bool(saved.get("use_incomplete_folder", True))
        inc_root = Path(saved.get("incomplete_path") or self.incomplete_dir)
        if use_inc:
            work = inc_root / tid[:16]
            work.mkdir(parents=True, exist_ok=True)
            return work, final
        return final, final

    def _prune_work_dir(self, tid: str) -> None:
        """Remove <incomplete>/<id16> and its empty subfolders. Only empty directories: never a file."""
        root = Path(self.meta.get_settings().get("incomplete_path") or self.incomplete_dir)
        work = root / tid[:16]
        if not work.is_dir():
            return
        for d in sorted((p for p in work.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()  # fails (and is skipped) if anything is still inside
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            log.info("kept %s: not empty after remove with data", work)

    def _part_rt_enabled(self) -> bool:
        return bool(self.meta.get_settings().get("part_file_rt_suffix", False))

    def _after_check(self, h: lt.torrent_handle) -> None:
        """After the recheck that follows a rejected fastresume: fewer pieces than claimed means missing files."""
        tid = tid_of(h)
        reason = self._rejected.pop(tid, None)
        claim = self._resume_claims.pop(tid, None)
        if reason is None or claim is None:
            return
        have = h.status().num_pieces
        if have < claim["pieces"]:
            msg = f"Missing files: {have} of {claim['pieces']} pieces found on disk"
            self.meta.update(tid, missing_files=msg)
            h.unset_flags(lt.torrent_flags.auto_managed | lt.torrent_flags.stop_when_ready)
            h.pause(lt.torrent_handle.graceful_pause)
            self._event("error", f"{h.status().name}: {msg} ({reason})", tid)
        else:
            # Stale resume (old path or names) but every piece is there: back to how it was.
            h.unset_flags(lt.torrent_flags.stop_when_ready)
            if not claim["paused"]:
                if claim["auto"]:
                    h.set_flags(lt.torrent_flags.auto_managed)
                h.resume()
        h.save_resume_data()

    def _complete_torrent(self, h: lt.torrent_handle, tid: str) -> None:
        moving = self._move_to_final_save_path(h, tid)
        rec = self.meta.get(tid)
        self.meta.update(
            tid, completed_at=time.time(), final_save_path=rec.get("final_save_path") or h.status().save_path
        )
        if not moving:
            h.save_resume_data()  # when moving, storage_moved_alert saves once the files are there
        self._event("success", f"finished: {h.status().name}", tid)
        self._emit({"type": "finished", "id": tid})

    def _maybe_complete_deferred(self, h: lt.torrent_handle, tid: str) -> None:
        if tid not in self._defer_final_move:
            return
        _orig, active = self._part_rt_meta(tid)
        if active:
            return
        self._defer_final_move.discard(tid)
        self._complete_torrent(h, tid)

    def _storage_files(self, h: lt.torrent_handle) -> list[tuple[int, str, int, int, int]]:
        ti = h.torrent_file()
        if ti is None:
            return []
        fs = ti.layout() if hasattr(ti, "layout") else ti.files()
        progress = list(h.file_progress(flags=1))
        prios = list(h.get_file_priorities())
        out: list[tuple[int, str, int, int, int]] = []
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            size = int(fs.file_size(i))
            done = int(progress[i]) if i < len(progress) else 0
            prio = int(prios[i]) if i < len(prios) else PRIORITY_NORMAL
            out.append((i, fs.file_path(i).replace("\\", "/"), size, done, prio))
        return out

    def _current_path(self, h: lt.torrent_handle, tid: str, index: int) -> str | None:
        """The name libtorrent uses for a file right now.

        torrent_file() returns the torrent's original metadata in libtorrent 2.x; renames live only in its
        storage. The renamed name is what file_renamed_alert reported, kept in meta file_path_overrides.
        Reading layout() here made every rename back from .!rt look like a no-op.
        """
        ti = h.torrent_file()
        if ti is None:
            return None
        fs = ti.layout() if hasattr(ti, "layout") else ti.files()
        if index < 0 or index >= fs.num_files():
            return None
        override = (self.meta.get(tid).get("file_path_overrides") or {}).get(str(index))
        return str(override or fs.file_path(index)).replace("\\", "/")

    def _rename_storage_file(self, h: lt.torrent_handle, index: int, new_path: str) -> None:
        existing = self._current_path(h, tid_of(h), index)
        if existing is None or existing == new_path.replace("\\", "/"):
            return
        try:
            h.rename_file(index, new_path)  # resume data is saved on file_renamed_alert, once it has landed
            tid = tid_of(h)
            with self._lock:
                self._renames_pending[tid] = self._renames_pending.get(tid, 0) + 1
        except Exception:
            log.exception("rename_file idx=%s path=%s", index, new_path)

    def _part_rt_meta(self, tid: str) -> tuple[dict[str, str], set[str]]:
        rec = self.meta.get(tid)
        orig = {str(k): str(v) for k, v in (rec.get("part_rt_original") or {}).items()}
        active = {str(x) for x in (rec.get("part_rt_active") or [])}
        return orig, active

    def _save_part_rt_meta(self, tid: str, orig: dict[str, str], active: set[str]) -> None:
        self.meta.update(tid, part_rt_original=orig, part_rt_active=sorted(active))

    def _restore_part_rt_file(self, h: lt.torrent_handle, tid: str, index: int) -> None:
        orig_map, active = self._part_rt_meta(tid)
        key = str(index)
        if key in orig_map and key in active:
            self._rename_storage_file(h, index, orig_map[key])

    def _rename_landed(self, tid: str) -> None:
        with self._lock:
            left = self._renames_pending.get(tid, 0) - 1
            if left > 0:
                self._renames_pending[tid] = left
            else:
                self._renames_pending.pop(tid, None)

    def _renaming(self, tid: str) -> bool:
        with self._lock:
            return self._renames_pending.get(tid, 0) > 0

    def _restore_all_part_rt(self, h: lt.torrent_handle, tid: str) -> None:
        """Ask libtorrent to give every .!rt file its real name; the move waits for file_renamed_alert."""
        orig_map, active = self._part_rt_meta(tid)
        for key in list(active):
            if key in orig_map:
                self._rename_storage_file(h, int(key), orig_map[key])

    def _part_rt_disk_restore(self, h: lt.torrent_handle, tid: str) -> None:
        """If libtorrent did not emit file_renamed after finish, rename on disk (never while renames are in flight)."""
        orig_map, active = self._part_rt_meta(tid)
        if not active or self._renaming(tid):
            return
        save = Path(h.status().save_path)
        for key in list(active):
            orig = orig_map.get(key)
            if not orig:
                continue
            part = save / part_rt_path(orig)
            final = save / orig
            if part.is_file() and not final.exists():
                final.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(part, final)
                except FileNotFoundError:
                    pass
        self._reconcile_part_rt_paths(h, tid)

    def _reconcile_part_rt_paths(self, h: lt.torrent_handle, tid: str) -> None:
        """Keep libtorrent file paths consistent with bytes on disk (post .!rt fallback)."""
        ti = h.torrent_file()
        if ti is None or self._renaming(tid):
            return
        orig_map, active = self._part_rt_meta(tid)
        if not orig_map:
            return
        save = Path(h.status().save_path)
        fs = ti.layout() if hasattr(ti, "layout") else ti.files()
        overrides = dict(self.meta.get(tid).get("file_path_overrides") or {})
        changed = False
        for key, orig in orig_map.items():
            idx = int(key)
            if idx < 0 or idx >= fs.num_files():
                continue
            if fs.file_flags(idx) & lt.file_storage.flag_pad_file:
                continue
            cur = self._current_path(h, tid, idx) or fs.file_path(idx).replace("\\", "/")
            final = save / orig
            part = save / part_rt_path(orig)
            finished = h.status().is_finished
            if part.is_file() and not final.is_file() and not finished:
                pass
            elif part.is_file() and not final.is_file():
                final.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(part, final)
                except FileNotFoundError:
                    pass
            if final.is_file():
                if cur != orig:
                    self._rename_storage_file(h, idx, orig)
                    changed = True
                active.discard(key)
                overrides.pop(key, None)
            elif finished and part.is_file():
                if not final.is_file():
                    final.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.replace(part, final)
                    except FileNotFoundError:
                        pass
                else:
                    part.unlink(missing_ok=True)
                if cur != orig:
                    self._rename_storage_file(h, idx, orig)
                active.discard(key)
                overrides.pop(key, None)
                changed = True
            elif (
                self._part_rt_enabled()
                and not h.status().is_finished
                and part.is_file()
                and key in active
                and cur != part_rt_path(orig)
            ):
                self._rename_storage_file(h, idx, part_rt_path(orig))
                changed = True
        if h.status().is_finished:
            for part in save.rglob("*"):
                if not part.is_file() or not part.name.endswith(PART_RT_SUFFIX):
                    continue
                orig_name = original_from_part_rt(part.name)
                if not orig_name:
                    continue
                final = part.parent / orig_name
                if final.is_file():
                    part.unlink(missing_ok=True)
                else:
                    try:
                        os.replace(part, final)
                    except FileNotFoundError:
                        pass
                changed = True
        if changed or active != set(self.meta.get(tid).get("part_rt_active") or []):
            self.meta.update(tid, file_path_overrides=overrides)
            self._save_part_rt_meta(tid, orig_map, active)
            h.save_resume_data()
            self._maybe_complete_deferred(h, tid)

    def _sync_part_rt_for_handle(self, h: lt.torrent_handle, tid: str) -> None:
        if h.torrent_file() is None:
            return
        enabled = self._part_rt_enabled()
        orig_map, active = self._part_rt_meta(tid)
        changed = False
        for i, path, size, done, prio in self._storage_files(h):
            key = str(i)
            logical = original_from_part_rt(path) or path
            if key not in orig_map:
                orig_map[key] = logical
                changed = True
            complete = size > 0 and done >= size
            wanted = prio > PRIORITY_SKIP
            if complete or not wanted:
                if key in active:
                    self._rename_storage_file(h, i, orig_map[key])
                    active.discard(key)
                    changed = True
                continue
            if enabled:
                if key not in active:
                    self._rename_storage_file(h, i, part_rt_path(orig_map[key]))
                    active.add(key)
                    changed = True
            elif key in active:
                self._rename_storage_file(h, i, orig_map[key])
                active.discard(key)
                changed = True
        if changed:
            self._save_part_rt_meta(tid, orig_map, active)

    def _move_to_final_save_path(self, h: lt.torrent_handle, tid: str) -> bool:
        """Start the move to the final folder. True when a move was issued (storage_moved_alert follows)."""
        rec = self.meta.get(tid)
        final = rec.get("final_save_path")
        if not final:
            return False
        try:
            cur = Path(h.status().save_path).expanduser().resolve()
        except Exception:
            return False
        dest = Path(final).expanduser().resolve()
        if cur == dest:
            return False
        dest.mkdir(parents=True, exist_ok=True)
        try:
            h.move_storage(str(dest))
            return True
        except Exception:
            log.exception("move_storage to %s failed for %s", dest, tid)
            return False

    def _default_save_path(self, category: str) -> Path:
        if category:
            cat = self.meta.categories().get(category)
            if cat and cat.get("save_path"):
                return Path(cat["save_path"])
        return Path(self.meta.get_settings().get("save_path") or self.download_dir)

    def wait_added(self, tid: str, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if tid in self._handles:
                    return True
            time.sleep(0.05)
        return False

    def _pending_add_done(self, tid: str) -> None:  # hook for future waiters
        pass

    def pause(self, tid: str) -> None:
        h = self.handle(tid)
        h.unset_flags(lt.torrent_flags.auto_managed)
        h.pause(lt.torrent_handle.graceful_pause)
        h.save_resume_data()

    def _clear_missing(self, tid: str) -> None:
        if self.meta.get(tid).get("missing_files"):
            self.meta.update(tid, missing_files="")

    def resume(self, tid: str, force: bool = False) -> None:
        h = self.handle(tid)
        self._clear_missing(tid)  # resuming a torrent with missing files downloads them again, by choice
        if force:
            h.unset_flags(lt.torrent_flags.auto_managed)
        else:
            h.set_flags(lt.torrent_flags.auto_managed)
        h.resume()

    def remove(self, tid: str, delete_files: bool = False) -> None:
        assert self.ses
        h = self.handle(tid)
        opt = lt.session.delete_files if delete_files else 0
        self.ses.remove_torrent(h, opt)

    def connect_peer(self, tid: str, ip: str, port: int) -> None:
        if not (0 < port < 65536):
            raise EngineError("port out of range")
        self.handle(tid).connect_peer((ip, port))

    def recheck(self, tid: str) -> None:
        h = self.handle(tid)
        self._clear_missing(tid)  # the recheck result is the new truth; progress shows what is really there
        h.force_recheck()

    def reannounce(self, tid: str) -> None:
        h = self.handle(tid)
        h.force_reannounce(0, -1)
        h.force_dht_announce()

    def move(self, tid: str, dest: str) -> None:
        p = Path(dest).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        self.handle(tid).move_storage(str(p))

    def queue(self, tid: str, where: str) -> None:
        h = self.handle(tid)
        {
            "top": h.queue_position_top,
            "up": h.queue_position_up,
            "down": h.queue_position_down,
            "bottom": h.queue_position_bottom,
        }[where]()

    def set_file_priorities(self, tid: str, priorities: dict[int, int]) -> None:
        h = self.handle(tid)
        cur = list(h.get_file_priorities())
        for idx, pr in priorities.items():
            if 0 <= idx < len(cur):
                cur[idx] = max(0, min(7, int(pr)))
        h.prioritize_files(cur)
        self._sync_part_rt_for_handle(h, tid)

    def rename_file(self, tid: str, index: int, new_path: str) -> None:
        if ".." in Path(new_path).parts:
            raise EngineError("path traversal not allowed")
        self.handle(tid).rename_file(index, new_path)

    def update(self, tid: str, **fields: Any) -> None:
        h = self.handle(tid)
        if "sequential" in fields and fields["sequential"] is not None:
            if fields["sequential"]:
                h.set_flags(lt.torrent_flags.sequential_download)
            else:
                h.unset_flags(lt.torrent_flags.sequential_download)
        if fields.get("dl_limit") is not None:
            h.set_download_limit(int(fields["dl_limit"]) or -1)
        if fields.get("ul_limit") is not None:
            h.set_upload_limit(int(fields["ul_limit"]) or -1)
        if fields.get("super_seeding") is not None:
            if fields["super_seeding"]:
                h.set_flags(lt.torrent_flags.super_seeding)
            else:
                h.unset_flags(lt.torrent_flags.super_seeding)
        meta_fields = {k: fields[k] for k in ("category", "tags") if fields.get(k) is not None}
        if meta_fields:
            self.meta.update(tid, **meta_fields)
        if "add_trackers" in fields and fields["add_trackers"]:
            for url in fields["add_trackers"]:
                h.add_tracker({"url": url, "tier": 0})
        h.save_resume_data()
        with self._lock:
            self._status[tid] = self._snapshot(h.status())

    def pause_all(self) -> None:
        for h in self._all_handles():
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause(lt.torrent_handle.graceful_pause)

    def resume_all(self) -> None:
        for h in self._all_handles():
            self._clear_missing(tid_of(h))
            h.set_flags(lt.torrent_flags.auto_managed)
            h.resume()

    def save_all_resume(self, wait: float = 0.0) -> None:
        hs = [h for h in self._all_handles() if h.is_valid()]
        with self._resume_cv:
            self._pending_resume += len(hs)
        for h in hs:
            h.save_resume_data(lt.save_resume_flags_t.save_info_dict)
        if wait <= 0 or self.ses is None:
            return
        # Alert thread may be paused during shutdown; drain alerts here too.
        end = time.monotonic() + wait
        while time.monotonic() < end:
            with self._resume_cv:
                if self._pending_resume <= 0:
                    return
            if self._thread and self._thread.is_alive():
                with self._resume_cv:
                    self._resume_cv.wait(0.1)
            else:
                self.ses.wait_for_alert(100)
                for a in self.ses.pop_alerts():
                    self._handle_alert(a)

    def _resume_done(self) -> None:
        with self._resume_cv:
            self._pending_resume = max(0, self._pending_resume - 1)
            self._resume_cv.notify_all()

    def _save_torrent_file(self, h: lt.torrent_handle) -> None:
        """Ask for resume data including the info dict; the alert handler writes the .torrent."""
        if h.torrent_file() is not None:
            h.save_resume_data(lt.save_resume_flags_t.save_info_dict)

    def _write_torrent_from_params(self, tid: str, params: Any) -> None:
        path = self.torrent_dir / f"{tid}.torrent"
        if path.exists() or params.ti is None:
            return
        try:
            # write_torrent_file_buf has a py-binding bug (decodes bytes as utf-8), so bencode the entry
            _atomic_write(path, lt.bencode(lt.write_torrent_file(params, 0)))
        except Exception:
            log.exception("could not persist .torrent for %s", tid)

    def export_torrent(self, tid: str) -> bytes:
        self.handle(tid)
        p = self.torrent_dir / f"{tid}.torrent"
        if not p.exists():
            raise EngineError("metadata not available yet", 409)
        return p.read_bytes()

    # ------------------------------------------------------------- settings

    def get_settings(self) -> dict[str, Any]:
        assert self.ses
        live = self.ses.get_settings()
        out = {k: live.get(k) for k in SETTABLE if k in live}
        saved = self.meta.get_settings()
        out["save_path"] = saved.get("save_path") or str(self.download_dir)
        out["incomplete_path"] = saved.get("incomplete_path") or str(self.incomplete_dir)
        out["use_incomplete_folder"] = bool(saved.get("use_incomplete_folder", True))
        out["part_file_rt_suffix"] = bool(saved.get("part_file_rt_suffix", False))
        out["watch_folder_enabled"] = bool(saved.get("watch_folder_enabled", False))
        out["watch_folder_path"] = saved.get("watch_folder_path") or str(self.download_dir.parent / "Watch")
        out["watch_folder_action"] = saved.get("watch_folder_action") or "move"
        out["listen_port"] = self.ses.listen_port()
        out["alt_speed"] = self.alt_speed()
        out["autostart_supported"] = bool(self.autostart is not None and self.autostart.supported())
        out["autostart"] = bool(out["autostart_supported"] and self.autostart.enabled())
        return out

    def set_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        assert self.ses
        apply: dict[str, Any] = {}
        persist: dict[str, Any] = {}
        for k, v in values.items():
            if k == "save_path":
                Path(v).expanduser().mkdir(parents=True, exist_ok=True)
                persist[k] = str(Path(v).expanduser())
            elif k == "incomplete_path":
                Path(v).expanduser().mkdir(parents=True, exist_ok=True)
                persist[k] = str(Path(v).expanduser())
            elif k in ("use_incomplete_folder", "part_file_rt_suffix", "watch_folder_enabled"):
                persist[k] = bool(v)
            elif k == "watch_folder_path":
                Path(v).expanduser().mkdir(parents=True, exist_ok=True)
                persist[k] = str(Path(v).expanduser())
            elif k == "watch_folder_action":
                action = str(v)
                if action not in ("move", "delete"):
                    raise EngineError("watch_folder_action must be move or delete")
                persist[k] = action
            elif k == "alt_speed":
                self.set_alt_speed(bool(v))
            elif k == "autostart":
                if self.autostart is None or not self.autostart.supported():
                    raise EngineError("start with Windows is only available on Windows")
                self.autostart.set(bool(v))
            elif k == "listen_port":
                port = int(v)
                if not (0 <= port <= 65535):
                    raise EngineError("port out of range")
                self._listen_port = port
                apply["listen_interfaces"] = self._base_settings()["listen_interfaces"]
                persist["listen_port"] = port
            elif k in SETTABLE:
                apply[k] = v
                persist[k] = v
            else:
                raise EngineError(f"unknown setting: {k}")
        if apply:
            self.ses.apply_settings(apply)
        self.meta.set_settings(persist)
        if "part_file_rt_suffix" in persist:
            for h in self._all_handles():
                if h.is_valid() and h.torrent_file() is not None:
                    self._sync_part_rt_for_handle(h, tid_of(h))
        return self.get_settings()

    def alt_speed(self) -> bool:
        return bool(self.meta.get_settings().get("alt_speed", False))

    def set_alt_speed(self, on: bool) -> None:
        s = self.meta.get_settings()
        if on:
            self.ses.apply_settings(  # type: ignore[union-attr]
                {
                    "download_rate_limit": int(s.get("alt_dl_limit", 512 * 1024)),
                    "upload_rate_limit": int(s.get("alt_ul_limit", 128 * 1024)),
                }
            )
        else:
            self.ses.apply_settings(  # type: ignore[union-attr]
                {
                    "download_rate_limit": int(s.get("download_rate_limit", 0)),
                    "upload_rate_limit": int(s.get("upload_rate_limit", 0)),
                }
            )
        self.meta.set_settings({"alt_speed": on})

    # ------------------------------------------------------------- creator

    def create_torrent(
        self,
        source: str,
        trackers: list[str] | None = None,
        comment: str = "",
        private: bool = False,
        piece_size: int = 0,
        web_seeds: list[str] | None = None,
        start_seeding: bool = True,
    ) -> tuple[str, bytes]:
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise EngineError(f"path does not exist: {src}", 404)
        entries = lt.list_files(str(src))
        if not entries:
            raise EngineError("no files found at path")
        ct = lt.create_torrent(entries, piece_size)
        for tier, url in enumerate(trackers or []):
            ct.add_tracker(url, tier)
        for ws in web_seeds or []:
            ct.add_url_seed(ws)
        if comment:
            ct.set_comment(comment)
        ct.set_creator("Riptide")
        ct.set_priv(private)
        lt.set_piece_hashes(ct, str(src.parent))
        data = lt.bencode(ct.generate())
        tid = ""
        if start_seeding:
            tid = self.add_torrent_bytes(data, save_path=str(src.parent), skip_check=True)
        return tid, data


# Settings the UI may change. Keys are libtorrent settings_pack names.
SETTABLE = {
    "download_rate_limit",
    "upload_rate_limit",
    "active_downloads",
    "active_seeds",
    "active_limit",
    "connections_limit",
    "unchoke_slots_limit",
    "enable_dht",
    "enable_lsd",
    "enable_upnp",
    "enable_natpmp",
    "enable_incoming_utp",
    "enable_outgoing_utp",
    "out_enc_policy",
    "in_enc_policy",
    "allowed_enc_level",
    "anonymous_mode",
    "proxy_type",
    "proxy_hostname",
    "proxy_port",
    "proxy_username",
    "proxy_password",
    "proxy_peer_connections",
    "proxy_tracker_connections",
    "share_ratio_limit",
    "seed_time_limit",
    "dont_count_slow_torrents",
    "announce_to_all_trackers",
    "announce_to_all_tiers",
}


def _peer_source(src: int) -> str:
    names = []
    for bit, name in (
        (lt.peer_info.tracker, "tracker"),
        (lt.peer_info.dht, "dht"),
        (lt.peer_info.pex, "pex"),
        (lt.peer_info.lsd, "lsd"),
        (lt.peer_info.resume_data, "resume"),
        (32, "incoming"),
    ):
        if src & int(bit):
            names.append(name)
    return ",".join(names) or "?"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _free_space(path: Path) -> int:
    try:
        import shutil

        return shutil.disk_usage(path).free
    except Exception:
        return -1


def _real_file_count(ti: Any) -> int:
    fs = ti.layout() if hasattr(ti, "layout") else ti.files()
    return sum(1 for i in range(fs.num_files()) if not fs.file_flags(i) & lt.file_storage.flag_pad_file)
