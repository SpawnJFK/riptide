export type TorrentState =
  | "downloading"
  | "seeding"
  | "paused"
  | "completed"
  | "queued"
  | "checking"
  | "checking_resume"
  | "metadata"
  | "allocating"
  | "finished"
  | "error"
  | "unknown";

export interface Torrent {
  id: string;
  name: string;
  state: TorrentState;
  progress: number;
  size: number;
  done: number;
  dl_rate: number;
  ul_rate: number;
  eta: number;
  peers: number;
  seeds: number;
  peers_total: number;
  seeds_total: number;
  ratio: number;
  uploaded: number;
  downloaded: number;
  save_path: string;
  queue: number;
  added: number;
  completed: number;
  sequential: boolean;
  auto_managed: boolean;
  has_metadata: boolean;
  error: string;
  category: string;
  tags: string[];
  dl_limit: number;
  ul_limit: number;
  availability: number;
  tracker: string;
}

export interface TorrentDetail extends Torrent {
  pieces?: number;
  piece_size?: number;
  comment?: string;
  creator?: string;
  created?: number;
  private?: boolean;
  files_count?: number;
  magnet: string;
}

export interface FileEntry {
  index: number;
  path: string;
  size: number;
  done: number;
  progress: number;
  priority: number;
}

export interface Peer {
  ip: string;
  port: number;
  client: string;
  progress: number;
  dl_rate: number;
  ul_rate: number;
  downloaded: number;
  uploaded: number;
  seed: boolean;
  encrypted: boolean;
  utp: boolean;
  source: string;
}

export interface Tracker {
  url: string;
  tier: number;
  status: string;
  message: string;
  seeds: number;
  peers: number;
}

export interface Stats {
  dl_rate: number;
  ul_rate: number;
  dht_nodes: number;
  peers: number;
  torrents: number;
  active: number;
  listening: boolean;
  port: number;
  paused: boolean;
  dl_limit: number;
  ul_limit: number;
  free_space: number;
}

export interface EngineEvent {
  ts: number;
  level: "info" | "success" | "error";
  text: string;
  id: string | null;
}

export type WsMessage =
  | { type: "hello"; torrents: Torrent[]; stats: Stats; events: EngineEvent[]; history: [number, number, number][] }
  | { type: "tick"; torrents: Torrent[]; stats: Stats; t: number }
  | { type: "event"; event: EngineEvent }
  | { type: "added" | "removed" | "finished"; id: string };
