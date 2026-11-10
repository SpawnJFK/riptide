import type { FileEntry, Peer, TorrentDetail, Tracker, WsMessage } from "./types";

const TOKEN_KEY = "riptide.token";

function token(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const t = token();
  if (t) headers["x-riptide-token"] = t;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["content-type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, { method, headers, body: payload });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(msg, res.status);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("json") ? res.json() : res.blob()) as Promise<T>;
}

export type BulkAction =
  | "pause"
  | "resume"
  | "force_resume"
  | "recheck"
  | "reannounce"
  | "remove"
  | "remove_data"
  | "top"
  | "up"
  | "down"
  | "bottom";

export interface AddOptions {
  save_path?: string;
  paused?: boolean;
  sequential?: boolean;
  category?: string;
  tags?: string[];
}

export interface AddResult {
  added: string[];
  errors: { uri?: string; file?: string; error: string }[];
}

export const api = {
  addMagnets: (uris: string[], opts: AddOptions = {}) =>
    req<AddResult>("POST", "/torrents/magnet", { uris, ...opts }),
  addFiles: (files: File[], opts: AddOptions = {}) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    if (opts.save_path) fd.append("save_path", opts.save_path);
    fd.append("paused", String(!!opts.paused));
    fd.append("sequential", String(!!opts.sequential));
    fd.append("category", opts.category ?? "");
    fd.append("tags", (opts.tags ?? []).join(","));
    return req<AddResult>("POST", "/torrents/file", fd);
  },
  bulk: (ids: string[], action: BulkAction) => req<{ ok: boolean }>("POST", "/torrents/bulk", { ids, action }),
  pauseAll: () => req("POST", "/torrents/pause-all"),
  resumeAll: () => req("POST", "/torrents/resume-all"),
  detail: (id: string) => req<TorrentDetail>("GET", `/torrents/${id}`),
  patch: (id: string, body: Record<string, unknown>) => req<TorrentDetail>("PATCH", `/torrents/${id}`, body),
  files: (id: string) => req<FileEntry[]>("GET", `/torrents/${id}/files`),
  setPriorities: (id: string, priorities: Record<number, number>) =>
    req<FileEntry[]>("PUT", `/torrents/${id}/files`, { priorities }),
  peers: (id: string) => req<Peer[]>("GET", `/torrents/${id}/peers`),
  trackers: (id: string) => req<Tracker[]>("GET", `/torrents/${id}/trackers`),
  pieces: (id: string) => req<{ total: number; buckets: number[] }>("GET", `/torrents/${id}/pieces`),
  move: (id: string, path: string) => req("POST", `/torrents/${id}/move`, { path }),
  exportUrl: (id: string) => `/api/torrents/${id}/export${token() ? `?token=${encodeURIComponent(token())}` : ""}`,
  settings: () => req<Record<string, unknown>>("GET", "/settings"),
  saveSettings: (body: Record<string, unknown>) => req<Record<string, unknown>>("PATCH", "/settings", body),
  altSpeed: (on: boolean) => req("POST", `/alt-speed?on=${on}`),
  categories: () =>
    req<{ categories: Record<string, { save_path: string }>; tags: string[] }>("GET", "/categories"),
  addCategory: (name: string, save_path = "") => req("POST", "/categories", { name, save_path }),
  deleteCategory: (name: string) => req("DELETE", `/categories/${encodeURIComponent(name)}`),
  history: () => req<{ history: [number, number, number][] }>("GET", "/stats"),
  createTorrent: (body: Record<string, unknown>) => req<Blob>("POST", "/create", body),
};

/** Websocket with automatic reconnect. Returns a disposer. */
export function connect(onMessage: (m: WsMessage) => void, onStatus: (online: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 500;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const t = token();
    ws = new WebSocket(`${proto}://${location.host}/ws${t ? `?token=${encodeURIComponent(t)}` : ""}`);
    ws.onopen = () => {
      retry = 500;
      onStatus(true);
    };
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data));
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onclose = () => {
      onStatus(false);
      if (closed) return;
      timer = setTimeout(open, retry);
      retry = Math.min(retry * 2, 8000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}
