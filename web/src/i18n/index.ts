import { useMemo, useSyncExternalStore } from "react";
import en from "./en.json";
import sr from "./sr.json";

export type Locale = "en" | "sr";

const STORAGE_KEY = "riptide.locale";
const bundles = { en, sr } as const;

type Dict = Record<string, unknown>;

let locale: Locale = "en";
const listeners = new Set<() => void>();

try {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "en" || saved === "sr") locale = saved;
} catch {
  /* storage unavailable */
}

function lookup(dict: Dict, key: string): string | undefined {
  let cur: unknown = dict;
  for (const part of key.split(".")) {
    if (!cur || typeof cur !== "object" || !(part in cur)) return undefined;
    cur = (cur as Dict)[part];
  }
  return typeof cur === "string" ? cur : undefined;
}

export function getLocale(): Locale {
  return locale;
}

export function setLocale(next: Locale) {
  if (next === locale) return;
  locale = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

function subscribeLocale(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** Translate a dotted key. Falls back to English, then the key itself. */
export function t(key: string, params?: Record<string, string | number>): string {
  let s = lookup(bundles[locale] as Dict, key) ?? lookup(bundles.en as Dict, key) ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replaceAll(`{${k}}`, String(v));
    }
  }
  return s;
}

export function useT() {
  const loc = useSyncExternalStore(subscribeLocale, getLocale, getLocale);
  return useMemo(() => (key: string, params?: Record<string, string | number>) => t(key, params), [loc]);
}

export function stateLabel(state: string): string {
  const key = `state.${state}`;
  const v = t(key);
  return v === key ? t("state.unknown") : v;
}

export function agoLocalized(ts: number): string {
  if (!ts) return "–";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return t("time.justNow");
  if (s < 3600) return t("time.minutesAgo", { n: Math.floor(s / 60) });
  if (s < 86400) return t("time.hoursAgo", { n: Math.floor(s / 3600) });
  return t("time.daysAgo", { n: Math.floor(s / 86400) });
}

export function columnLabel(id: string): string {
  return t(`columns.${id}`);
}
