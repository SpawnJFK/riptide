import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { copy } from "../actions";
import { bytes, date, eta, pct, rate } from "../format";
import { stateLabel, t, useT } from "../i18n";
import { openDialog, toast, useStore } from "../store";
import type { FileEntry, Peer, TorrentDetail, Tracker } from "../types";

type Tab = "general" | "files" | "peers" | "trackers";

const TAB_LABEL: Record<Tab, string> = {
  general: "detail.tabGeneral",
  files: "detail.tabFiles",
  peers: "detail.tabPeers",
  trackers: "detail.tabTrackers",
};

function usePoll<T>(fn: (() => Promise<T>) | null, deps: unknown[], ms = 2000): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!fn) return;
    let live = true;
    const load = () =>
      fn()
        .then((d) => live && setData(d))
        .catch(() => undefined);
    load();
    const t = setInterval(load, ms);
    return () => {
      live = false;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return [data, () => setN((x) => x + 1)];
}

export function DetailPanel() {
  const tx = useT();
  const focused = useStore((s) => s.focused);
  const torrent = useStore((s) => (s.focused ? s.byId.get(s.focused) : undefined));
  const [tab, setTab] = useState<Tab>("general");
  const startDrag = useResize();

  return (
    <section className="detail" aria-label={tx("detail.aria")}>
      <div className="detail-resize" onMouseDown={startDrag} aria-hidden="true" />
      <div className="tabs" role="tablist">
        {(["general", "files", "peers", "trackers"] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            data-testid={`detail-tab-${t}`}
            className={`tab ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {tx(TAB_LABEL[t])}
          </button>
        ))}
        <span className="spacer" />
        {torrent && <span className="title">{torrent.name}</span>}
      </div>
      {!focused || !torrent ? (
        <div className="pane">
          <p style={{ color: "var(--mist)", margin: 0 }}>{tx("detail.selectHint")}</p>
        </div>
      ) : tab === "general" ? (
        <General id={focused} />
      ) : tab === "files" ? (
        <Files id={focused} />
      ) : tab === "peers" ? (
        <Peers id={focused} />
      ) : (
        <Trackers id={focused} />
      )}
    </section>
  );
}

function useResize() {
  return (e: React.MouseEvent) => {
    e.preventDefault();
    const work = (e.target as HTMLElement).closest(".work") as HTMLElement | null;
    if (!work) return;
    const bottom = work.getBoundingClientRect().bottom;
    const move = (ev: MouseEvent) => {
      const h = Math.min(Math.max(bottom - ev.clientY, 140), window.innerHeight * 0.75);
      work.style.setProperty("--detail-h", `${h}px`);
    };
    const up = () => {
      removeEventListener("mousemove", move);
      removeEventListener("mouseup", up);
    };
    addEventListener("mousemove", move);
    addEventListener("mouseup", up);
  };
}

function General({ id }: { id: string }) {
  const tx = useT();
  const [d] = usePoll<TorrentDetail>(() => api.detail(id), [id], 2000);
  const [pieces] = usePoll(() => api.pieces(id), [id], 3000);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvas.current;
    if (!c || !pieces) return;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const dpr = devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const css = getComputedStyle(document.documentElement);
    ctx.fillStyle = css.getPropertyValue("--sea").trim();
    ctx.fillRect(0, 0, w, h);
    const tide = css.getPropertyValue("--tide").trim();
    const n = pieces.buckets.length;
    pieces.buckets.forEach((v, i) => {
      if (v <= 0) return;
      ctx.globalAlpha = 0.25 + v * 0.75;
      ctx.fillStyle = tide;
      ctx.fillRect((i / n) * w, 0, Math.ceil(w / n), h);
    });
    ctx.globalAlpha = 1;
  }, [pieces, d !== null]);

  if (!d) return <div className="pane" />;
  const rows: [string, React.ReactNode, boolean?][] = [
    [tx("detail.fieldStatus"), d.error ? t("common.errorPrefix", { msg: d.error }) : stateLabel(d.state)],
    [tx("detail.fieldProgress"), t("detail.progressOf", { pct: pct(d.progress), size: bytes(d.size) })],
    [tx("detail.fieldDownloaded"), bytes(d.downloaded)],
    [tx("detail.fieldUploaded"), t("detail.uploadedRatio", { up: bytes(d.uploaded), ratio: d.ratio.toFixed(2) })],
    [
      tx("detail.fieldSpeed"),
      t("detail.speedPair", { dl: rate(d.dl_rate) || "0 B/s", ul: rate(d.ul_rate) || "0 B/s" }),
    ],
    [tx("detail.fieldTimeLeft"), d.progress < 1 ? eta(d.eta) : tx("common.doneWord")],
    [tx("detail.fieldSeeds"), t("detail.seedsConnected", { n: d.seeds, total: Math.max(0, d.seeds_total) })],
    [tx("detail.fieldPeers"), t("detail.peersConnected", { n: d.peers, total: Math.max(0, d.peers_total) })],
    [tx("detail.fieldAvailability"), d.availability ? d.availability.toFixed(2) : "–"],
    [
      tx("detail.fieldPieces"),
      d.pieces ? t("detail.piecesCount", { pieces: d.pieces, size: bytes(d.piece_size ?? 0) }) : tx("detail.waitingMetadata"),
    ],
    [tx("detail.fieldSavePath"), d.save_path, true],
    [tx("detail.fieldAdded"), date(d.added)],
    [tx("detail.fieldCompleted"), date(d.completed)],
    [
      tx("detail.fieldCreated"),
      d.created ? (d.creator ? t("detail.createdBy", { date: date(d.created), creator: d.creator }) : date(d.created)) : "–",
    ],
    [tx("detail.fieldPrivate"), d.private ? tx("detail.privateYes") : tx("detail.privateNo")],
    [tx("detail.fieldInfoHash"), d.id, true],
    [tx("detail.fieldComment"), d.comment || "–", true],
  ];
  return (
    <div className="pane">
      <canvas ref={canvas} className="pieces" aria-label={tx("detail.piecesAria")} />
      <dl className="kv">
        {rows.map(([k, v, wrap]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className={wrap ? "wrap" : ""}>{v}</dd>
          </div>
        ))}
      </dl>
      <p style={{ marginTop: 14 }}>
        <button className="btn" onClick={() => copy(d.magnet, t("copy.magnet"))}>
          {tx("detail.copyMagnet")}
        </button>{" "}
        <button className="btn" onClick={() => copy(d.id, t("copy.hash"))}>
          {tx("detail.copyHash")}
        </button>
      </p>
    </div>
  );
}

interface Node {
  name: string;
  path: string;
  size: number;
  done: number;
  children: Map<string, Node>;
  file?: FileEntry;
}

function buildTree(files: FileEntry[]): Node {
  const root: Node = { name: "", path: "", size: 0, done: 0, children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let cur = root;
    cur.size += f.size;
    cur.done += f.done;
    parts.forEach((p, i) => {
      const path = parts.slice(0, i + 1).join("/");
      let next = cur.children.get(p);
      if (!next) {
        next = { name: p, path, size: 0, done: 0, children: new Map() };
        cur.children.set(p, next);
      }
      next.size += f.size;
      next.done += f.done;
      if (i === parts.length - 1) next.file = f;
      cur = next;
    });
  }
  return root;
}

function leaves(n: Node): FileEntry[] {
  return n.file ? [n.file] : [...n.children.values()].flatMap(leaves);
}

const PRIO_KEYS = [
  { v: 0, key: "prio.skip" },
  { v: 1, key: "prio.low" },
  { v: 4, key: "prio.normal" },
  { v: 7, key: "prio.high" },
] as const;

function prioLabel(files: FileEntry[]): number | "mixed" {
  const set = new Set(files.map((f) => (f.priority === 0 ? 0 : f.priority >= 6 ? 7 : f.priority <= 2 ? 1 : 4)));
  return set.size === 1 ? [...set][0] : "mixed";
}

function Files({ id }: { id: string }) {
  const tx = useT();
  const [files, reload] = usePoll<FileEntry[]>(() => api.files(id), [id], 2500);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const tree = useMemo(() => (files ? buildTree(files) : null), [files]);

  if (!files) return <div className="pane" />;
  if (!files.length) return <div className="pane">{tx("detail.filesWaiting")}</div>;

  const setPrio = async (node: Node, v: number) => {
    const map: Record<number, number> = {};
    leaves(node).forEach((f) => (map[f.index] = v));
    try {
      await api.setPriorities(id, map);
      reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const rows: { node: Node; depth: number }[] = [];
  const walk = (n: Node, depth: number) => {
    const kids = [...n.children.values()].sort((a, b) => Number(!!a.file) - Number(!!b.file) || a.name.localeCompare(b.name, undefined, { numeric: true }));
    for (const k of kids) {
      rows.push({ node: k, depth });
      if (!k.file && !collapsed.has(k.path)) walk(k, depth + 1);
    }
  };
  walk(tree!, 0);

  return (
    <div className="pane flush">
      <table className="mini">
        <thead>
          <tr>
            <th>{tx("detail.thName")}</th>
            <th className="num">{tx("detail.thSize")}</th>
            <th style={{ width: 160 }}>{tx("detail.thProgress")}</th>
            <th style={{ width: 110 }}>{tx("detail.thPriority")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node, depth }) => {
            const p = prioLabel(leaves(node));
            const progress = node.size ? node.done / node.size : 1;
            return (
              <tr key={node.path} className="tree-row" style={{ ["--depth" as string]: depth }}>
                <td className="wrap">
                  {!node.file && (
                    <button
                      className="tree-toggle"
                      aria-label={collapsed.has(node.path) ? tx("detail.expandFolder") : tx("detail.collapseFolder")}
                      onClick={() =>
                        setCollapsed((s) => {
                          const n = new Set(s);
                          if (n.has(node.path)) n.delete(node.path);
                          else n.add(node.path);
                          return n;
                        })
                      }
                    >
                      {collapsed.has(node.path) ? "▸" : "▾"}
                    </button>
                  )}
                  {node.name}
                </td>
                <td className="num">{bytes(node.size)}</td>
                <td>
                  <div className={`bar ${progress >= 1 ? "done" : ""}`}>
                    <i style={{ width: `${progress * 100}%` }} />
                    <span>{pct(progress)}</span>
                  </div>
                </td>
                <td>
                  <select
                    className={`prio ${p === 0 ? "skip" : p === 7 ? "high" : ""}`}
                    value={p === "mixed" ? "" : p}
                    onChange={(e) => setPrio(node, Number(e.target.value))}
                    aria-label={tx("detail.priorityFor", { name: node.name })}
                  >
                    {p === "mixed" && <option value="">{tx("common.mixed")}</option>}
                    {PRIO_KEYS.map((o) => (
                      <option key={o.v} value={o.v}>
                        {tx(o.key)}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Peers({ id }: { id: string }) {
  const tx = useT();
  const [peers] = usePoll<Peer[]>(() => api.peers(id), [id], 2000);
  if (!peers) return <div className="pane" />;
  if (!peers.length) return <div className="pane">{tx("detail.peersEmpty")}</div>;
  return (
    <div className="pane flush">
      <table className="mini">
        <thead>
          <tr>
            <th>{tx("detail.thAddress")}</th>
            <th>{tx("detail.thClient")}</th>
            <th className="num">{tx("detail.thHas")}</th>
            <th className="num">{tx("detail.thDown")}</th>
            <th className="num">{tx("detail.thUp")}</th>
            <th className="num">{tx("detail.thDownloaded")}</th>
            <th className="num">{tx("detail.thUploaded")}</th>
            <th>{tx("detail.thConnection")}</th>
            <th>{tx("detail.thFoundVia")}</th>
          </tr>
        </thead>
        <tbody>
          {peers.map((p) => (
            <tr key={`${p.ip}:${p.port}`}>
              <td>
                {p.ip}:{p.port}
              </td>
              <td>{p.client || tx("common.unknown")}</td>
              <td className="num">{pct(p.progress)}</td>
              <td className="num" style={{ color: "var(--tide)" }}>
                {rate(p.dl_rate)}
              </td>
              <td className="num" style={{ color: "var(--dawn)" }}>
                {rate(p.ul_rate)}
              </td>
              <td className="num">{bytes(p.downloaded)}</td>
              <td className="num">{bytes(p.uploaded)}</td>
              <td>
                {p.utp ? "uTP" : "TCP"}
                {p.encrypted ? tx("detail.encrypted") : ""}
              </td>
              <td>{p.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Trackers({ id }: { id: string }) {
  const tx = useT();
  const dialog = useStore((s) => s.dialog);
  const [trackers, reload] = usePoll<Tracker[]>(() => api.trackers(id), [id], 4000);
  useEffect(() => {
    if (dialog === null) reload();
  }, [dialog, reload]);
  const add = () => openDialog("trackersAdd", { torrentId: id });
  if (!trackers) {
    return (
      <div className="pane">
        <p style={{ color: "var(--mist)", margin: "0 0 8px" }}>{tx("detail.loadingTrackers")}</p>
        <button className="btn" type="button" data-testid="trackers-add" onClick={add}>
          {tx("detail.addTrackers")}
        </button>
      </div>
    );
  }
  return (
    <div className="pane flush">
      <table className="mini">
        <thead>
          <tr>
            <th>{tx("detail.thTier")}</th>
            <th>{tx("detail.thUrl")}</th>
            <th>{tx("detail.thStatus")}</th>
            <th className="num">{tx("detail.fieldSeeds")}</th>
            <th className="num">{tx("detail.fieldPeers")}</th>
            <th>{tx("detail.thMessage")}</th>
          </tr>
        </thead>
        <tbody>
          {trackers.map((t) => (
            <tr key={t.url}>
              <td>{t.tier}</td>
              <td className="wrap">{t.url}</td>
              <td style={{ color: t.status === "error" ? "var(--flare)" : t.status === "working" ? "var(--tide)" : undefined }}>{t.status}</td>
              <td className="num">{t.seeds >= 0 ? t.seeds : ""}</td>
              <td className="num">{t.peers >= 0 ? t.peers : ""}</td>
              <td className="wrap">{t.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ padding: "8px 10px", margin: 0 }}>
        <button className="btn" type="button" data-testid="trackers-add" onClick={add}>
          {tx("detail.addTrackers")}
        </button>
      </p>
    </div>
  );
}
