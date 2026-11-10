import { useSyncExternalStore } from "react";
import type { ColumnId } from "./columns";
import { t as tr } from "./i18n";
import type { EngineEvent, Stats, Torrent, WsMessage } from "./types";

export type StatusFilter =
  | "all"
  | "downloading"
  | "seeding"
  | "completed"
  | "paused"
  | "active"
  | "inactive"
  | "stalled"
  | "errored";

export type SortKey = ColumnId;

export type Dialog =
  | null
  | "add"
  | "settings"
  | "remove"
  | "create"
  | "shortcuts"
  | "categoryCreate"
  | "categoryDelete"
  | "bulkCategory"
  | "bulkTags"
  | "bulkLimit"
  | "bulkMove"
  | "trackersAdd";

export interface DialogContext {
  categoryName?: string;
  torrentIds?: string[];
  torrentId?: string;
  initialText?: string;
  limitField?: "dl_limit" | "ul_limit";
}

export interface Toast {
  id: number;
  level: "info" | "success" | "error";
  text: string;
}

export interface State {
  online: boolean;
  torrents: Torrent[];
  byId: Map<string, Torrent>;
  stats: Stats | null;
  history: { t: number; dl: number; ul: number }[];
  events: EngineEvent[];
  selected: Set<string>;
  anchor: string | null;
  focused: string | null;
  status: StatusFilter;
  category: string | null;
  tag: string | null;
  search: string;
  sort: { key: SortKey; dir: 1 | -1 };
  dialog: Dialog;
  dialogCtx: DialogContext | null;
  toasts: Toast[];
  altSpeed: boolean;
  detailOpen: boolean;
}

const HISTORY_SECONDS = 300;

let state: State = {
  online: false,
  torrents: [],
  byId: new Map(),
  stats: null,
  history: [],
  events: [],
  selected: new Set(),
  anchor: null,
  focused: null,
  status: "all",
  category: null,
  tag: null,
  search: "",
  sort: { key: "queue", dir: 1 },
  dialog: null,
  dialogCtx: null,
  toasts: [],
  altSpeed: false,
  detailOpen: typeof window === "undefined" || window.innerWidth > 900,
};

const listeners = new Set<() => void>();

export function getState(): State {
  return state;
}

export function openDialog(dialog: Dialog, dialogCtx: DialogContext | null = null) {
  setState({ dialog, dialogCtx });
}

export function closeDialog() {
  setState({ dialog: null, dialogCtx: null });
}

export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...p };
  listeners.forEach((l) => l());
}

export function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state));
}

let toastId = 0;
export function toast(text: string, level: Toast["level"] = "info") {
  const id = ++toastId;
  setState((s) => ({ toasts: [...s.toasts.slice(-4), { id, level, text }] }));
  setTimeout(() => setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), level === "error" ? 7000 : 3500);
}

function setTorrents(list: Torrent[]): Partial<State> {
  const byId = new Map(list.map((t) => [t.id, t]));
  const selected = new Set([...state.selected].filter((id) => byId.has(id)));
  const focused = state.focused && byId.has(state.focused) ? state.focused : null;
  return { torrents: list, byId, selected, focused };
}

export function applyMessage(m: WsMessage) {
  switch (m.type) {
    case "hello":
      setState({
        ...setTorrents(m.torrents),
        stats: m.stats,
        events: m.events,
        history: (m.history ?? []).map(([t, dl, ul]) => ({ t, dl, ul })),
      });
      break;
    case "tick": {
      const point = { t: m.t, dl: m.stats.dl_rate, ul: m.stats.ul_rate };
      const history = [...state.history, point].filter((p) => p.t > m.t - HISTORY_SECONDS);
      setState({ ...setTorrents(m.torrents), stats: m.stats, history });
      break;
    }
    case "event":
      setState((s) => ({ events: [...s.events.slice(-199), m.event] }));
      if (m.event.level !== "info") toast(m.event.text, m.event.level);
      break;
    case "finished": {
      const torrent = state.byId.get(m.id);
      notifyFinished(torrent?.name ?? tr("notification.finishedBodyDefault"));
      break;
    }
    default:
      break;
  }
}

function notifyFinished(name: string) {
  try {
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification(tr("notification.finishedTitle"), { body: name, icon: "/favicon.svg" });
    }
  } catch {
    /* notifications unavailable */
  }
}

// ---------------------------------------------------------------- selectors

export function matchesStatus(t: Torrent, f: StatusFilter): boolean {
  switch (f) {
    case "all":
      return true;
    case "downloading":
      return ["downloading", "metadata", "queued", "allocating"].includes(t.state) && t.progress < 1;
    case "seeding":
      return t.state === "seeding";
    case "completed":
      return t.progress >= 1;
    case "paused":
      return t.state === "paused" || t.state === "completed";
    case "active":
      return t.dl_rate > 0 || t.ul_rate > 0;
    case "inactive":
      return t.dl_rate === 0 && t.ul_rate === 0;
    case "stalled":
      return t.state === "downloading" && t.dl_rate === 0;
    case "errored":
      return t.state === "error";
  }
}

export function visibleTorrents(s: State): Torrent[] {
  const q = s.search.trim().toLowerCase();
  const terms = q ? q.split(/\s+/) : [];
  const out = s.torrents.filter(
    (t) =>
      matchesStatus(t, s.status) &&
      (s.category === null || t.category === s.category) &&
      (s.tag === null || t.tags.includes(s.tag)) &&
      terms.every((term) => t.name.toLowerCase().includes(term)),
  );
  const { key, dir } = s.sort;
  const sortVal = (t: Torrent, k: SortKey): string | number => {
    if (k === "remaining") return Math.max(0, t.size - t.done);
    if (k === "active") return t.added || 0;
    if (k === "tags") return t.tags.join(",");
    return t[k as keyof Torrent] as string | number;
  };
  out.sort((a, b) => {
    let av: string | number = sortVal(a, key);
    let bv: string | number = sortVal(b, key);
    if (key === "eta") {
      av = (av as number) < 0 ? Infinity : av;
      bv = (bv as number) < 0 ? Infinity : bv;
    }
    if (key === "queue") {
      av = (av as number) < 0 ? Infinity : av;
      bv = (bv as number) < 0 ? Infinity : bv;
    }
    if (typeof av === "string") return av.localeCompare(bv as string, undefined, { numeric: true }) * dir;
    if (av === bv) return a.name.localeCompare(b.name);
    return ((av as number) - (bv as number)) * dir;
  });
  return out;
}

export function selectedIds(): string[] {
  return [...state.selected];
}
