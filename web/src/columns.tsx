import type { ReactNode } from "react";
import { bytes, date, eta, pct, rate } from "./format";
import { agoLocalized, stateLabel } from "./i18n";
import type { Torrent } from "./types";

export type ColumnId =
  | "queue"
  | "name"
  | "size"
  | "progress"
  | "state"
  | "dl_rate"
  | "ul_rate"
  | "eta"
  | "seeds"
  | "peers"
  | "ratio"
  | "added"
  | "save_path"
  | "category"
  | "tags"
  | "completed"
  | "availability"
  | "uploaded"
  | "downloaded"
  | "active"
  | "tracker"
  | "remaining";

export interface ColumnDef {
  id: ColumnId;
  defaultWidth: number;
  minWidth: number;
  num?: boolean;
  narrow?: boolean;
  sortable: boolean;
  defaultVisible: boolean;
  cell: (t: Torrent) => ReactNode;
}

function activeFor(t: Torrent): string {
  if (!t.added) return "–";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t.added));
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

export const COLUMN_DEFS: ColumnDef[] = [
  { id: "queue", defaultWidth: 46, minWidth: 36, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => (t.queue >= 0 ? t.queue + 1 : "") },
  {
    id: "name",
    defaultWidth: 360,
    minWidth: 160,
    sortable: true,
    defaultVisible: true,
    cell: (t) => (
      <>
        <span className={`dot ${t.state}`} title={stateLabel(t.state)} />
        <span className="label" title={t.name}>
          {t.name}
        </span>
      </>
    ),
  },
  { id: "size", defaultWidth: 86, minWidth: 64, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => (t.has_metadata ? bytes(t.size) : "") },
  {
    id: "progress",
    defaultWidth: 150,
    minWidth: 100,
    narrow: true,
    sortable: true,
    defaultVisible: true,
    cell: (t) => (
      <div className={`bar ${t.state} ${t.progress >= 1 ? "done" : ""}`} role="progressbar" aria-valuenow={Math.round(t.progress * 100)}>
        <i style={{ width: `${t.progress * 100}%` }} />
        <span>{pct(t.progress)}</span>
      </div>
    ),
  },
  { id: "state", defaultWidth: 132, minWidth: 96, narrow: true, sortable: true, defaultVisible: true, cell: (t) => <span className="muted">{t.error ? `Error: ${t.error}` : stateLabel(t.state)}</span> },
  { id: "dl_rate", defaultWidth: 92, minWidth: 72, num: true, sortable: true, defaultVisible: true, cell: (t) => <span className="dl">{t.progress < 1 ? rate(t.dl_rate) : ""}</span> },
  { id: "ul_rate", defaultWidth: 92, minWidth: 72, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => <span className="ul">{rate(t.ul_rate)}</span> },
  { id: "eta", defaultWidth: 84, minWidth: 64, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => (t.progress < 1 && t.dl_rate > 0 ? eta(t.eta) : "") },
  { id: "remaining", defaultWidth: 92, minWidth: 72, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => bytes(Math.max(0, t.size - t.done)) },
  { id: "seeds", defaultWidth: 78, minWidth: 64, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => `${t.seeds} (${Math.max(0, t.seeds_total)})` },
  { id: "peers", defaultWidth: 78, minWidth: 64, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => `${t.peers} (${Math.max(0, t.peers_total)})` },
  { id: "ratio", defaultWidth: 64, minWidth: 56, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => t.ratio.toFixed(2) },
  { id: "save_path", defaultWidth: 200, minWidth: 120, sortable: true, defaultVisible: false, cell: (t) => <span className="label" title={t.save_path}>{t.save_path}</span> },
  { id: "category", defaultWidth: 100, minWidth: 72, sortable: true, defaultVisible: false, cell: (t) => t.category || "" },
  { id: "tags", defaultWidth: 120, minWidth: 72, sortable: true, defaultVisible: false, cell: (t) => (t.tags.length ? t.tags.map((g) => `#${g}`).join(" ") : "") },
  { id: "completed", defaultWidth: 140, minWidth: 100, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => date(t.completed) },
  { id: "availability", defaultWidth: 88, minWidth: 64, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => (t.availability > 0 ? t.availability.toFixed(2) : "") },
  { id: "uploaded", defaultWidth: 92, minWidth: 72, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => bytes(t.uploaded) },
  { id: "downloaded", defaultWidth: 92, minWidth: 72, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => bytes(t.downloaded) },
  { id: "active", defaultWidth: 96, minWidth: 72, num: true, narrow: true, sortable: true, defaultVisible: false, cell: (t) => activeFor(t) },
  { id: "tracker", defaultWidth: 180, minWidth: 100, sortable: true, defaultVisible: false, cell: (t) => <span className="label" title={t.tracker}>{t.tracker}</span> },
  { id: "added", defaultWidth: 104, minWidth: 80, num: true, narrow: true, sortable: true, defaultVisible: true, cell: (t) => agoLocalized(t.added) },
];

const DEF_BY_ID = Object.fromEntries(COLUMN_DEFS.map((c) => [c.id, c])) as Record<ColumnId, ColumnDef>;

export interface ColumnLayout {
  order: ColumnId[];
  widths: Partial<Record<ColumnId, number>>;
  hidden: ColumnId[];
}

const STORAGE_KEY = "riptide-columns-v1";

function defaultLayout(): ColumnLayout {
  return {
    order: COLUMN_DEFS.map((c) => c.id),
    widths: {},
    hidden: COLUMN_DEFS.filter((c) => !c.defaultVisible).map((c) => c.id),
  };
}

export function loadColumnLayout(): ColumnLayout {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultLayout();
    const parsed = JSON.parse(raw) as ColumnLayout;
    const order = (parsed.order ?? []).filter((id) => id in DEF_BY_ID) as ColumnId[];
    for (const c of COLUMN_DEFS) {
      if (!order.includes(c.id)) order.push(c.id);
    }
    return {
      order,
      widths: parsed.widths ?? {},
      hidden: (parsed.hidden ?? []).filter((id) => id in DEF_BY_ID),
    };
  } catch {
    return defaultLayout();
  }
}

export function saveColumnLayout(layout: ColumnLayout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* ignore quota */
  }
}

export function visibleColumns(layout: ColumnLayout): ColumnDef[] {
  const hidden = new Set(layout.hidden);
  return layout.order.filter((id) => !hidden.has(id)).map((id) => DEF_BY_ID[id]);
}

export function widthFor(col: ColumnDef, layout: ColumnLayout): number {
  return layout.widths[col.id] ?? col.defaultWidth;
}

export { DEF_BY_ID };
