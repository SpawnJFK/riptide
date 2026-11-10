const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function bytes(n: number, digits = 1): string {
  if (!Number.isFinite(n) || n < 0) return "–";
  if (n < 1024) return `${n} B`;
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : digits)} ${UNITS[i]}`;
}

export function rate(n: number): string {
  if (!n) return "";
  return `${bytes(n)}/s`;
}

export function eta(sec: number): string {
  if (sec < 0 || !Number.isFinite(sec)) return "∞";
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${sec % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  if (d > 365) return "∞";
  return `${d}d ${h % 24}h`;
}

export function pct(p: number): string {
  if (p >= 1) return "100%";
  return `${(Math.floor(p * 1000) / 10).toFixed(1)}%`;
}

export function date(ts: number): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString();
}

/** Parse "512K", "2 MB", "1.5m" etc into bytes/sec. Empty or 0 means unlimited. */
export function parseRate(input: string): number {
  const m = input.trim().toLowerCase().match(/^([\d.]+)\s*(b|k|kb|m|mb|g|gb)?(\/s)?$/);
  if (!m) return 0;
  const v = parseFloat(m[1]);
  const mult = { b: 1, k: 1024, kb: 1024, m: 1024 ** 2, mb: 1024 ** 2, g: 1024 ** 3, gb: 1024 ** 3 }[m[2] ?? "k"] ?? 1024;
  return Math.round(v * mult);
}
