import { api, type BulkAction } from "./api";
import { t } from "./i18n";
import { getState, setState, toast } from "./store";

const PAST_KEY: Partial<Record<BulkAction, string>> = {
  pause: "toast.paused",
  resume: "toast.resumed",
  force_resume: "toast.forceStarted",
  recheck: "toast.recheck",
  reannounce: "toast.reannounce",
};

export async function bulk(action: BulkAction, ids = [...getState().selected]) {
  if (!ids.length) return;
  try {
    const r = await api.bulk(ids, action);
    if (!r.ok) toast(t("toast.bulkFail"), "error");
    else if (PAST_KEY[action]) {
      const tail = ids.length === 1 ? t("toast.bulkTorrentOne") : t("toast.bulkTorrentMany", { count: ids.length });
      toast(`${t(PAST_KEY[action]!)} ${tail}`);
    }
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

export async function removeSelected(deleteFiles: boolean) {
  const ids = [...getState().selected];
  await bulk(deleteFiles ? "remove_data" : "remove", ids);
  const base = ids.length === 1 ? t("toast.removedOne") : t("toast.removedMany", { count: ids.length });
  toast(`${base}${deleteFiles ? t("toast.removedWithData") : ""}`);
  setState({ selected: new Set(), focused: null, dialog: null, dialogCtx: null });
}

export async function addFromClipboardText(text: string) {
  const uris = text
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => /^magnet:\?/i.test(s) || /^[a-f0-9]{40}$/i.test(s) || /^[a-z2-7]{32}$/i.test(s))
    .map((s) => (s.startsWith("magnet:") ? s : `magnet:?xt=urn:btih:${s}`));
  if (!uris.length) return false;
  try {
    const r = await api.addMagnets(uris);
    if (r.added.length)
      toast(
        r.added.length === 1 ? t("toast.addedClipboardOne") : t("toast.addedClipboardMany", { count: r.added.length }),
        "success",
      );
    r.errors.forEach((e) => toast(e.error, "error"));
  } catch (e) {
    toast((e as Error).message, "error");
  }
  return true;
}

export async function addTorrentFiles(files: File[]) {
  const torrents = files.filter((f) => f.name.toLowerCase().endsWith(".torrent"));
  if (!torrents.length) {
    toast(t("toast.dropHint"), "error");
    return;
  }
  try {
    const r = await api.addFiles(torrents);
    if (r.added.length)
      toast(r.added.length === 1 ? t("toast.addedOne") : t("toast.addedMany", { count: r.added.length }), "success");
    r.errors.forEach((e) => toast(`${e.file}: ${e.error}`, "error"));
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

export function copy(text: string, what: string) {
  navigator.clipboard
    .writeText(text)
    .then(() => toast(t("toast.copied", { what })))
    .catch(() => toast(t("toast.clipboardFail"), "error"));
}
