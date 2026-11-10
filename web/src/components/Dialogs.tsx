import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { removeSelected } from "../actions";
import { bytes, parseRate } from "../format";
import { t, useT } from "../i18n";
import { closeDialog, getState, setState, toast, useStore } from "../store";

function Dialog({ title, sub, children, footer, wide, onClose }: { title: string; sub?: string; children: ReactNode; footer: ReactNode; wide?: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", esc);
    ref.current?.querySelector<HTMLElement>("[autofocus], textarea, input, button.primary")?.focus();
    return () => removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header>
          <h2>{title}</h2>
          {sub && <p>{sub}</p>}
        </header>
        <div className="body">{children}</div>
        <footer>{footer}</footer>
      </div>
    </div>
  );
}

const close = () => closeDialog();

// ------------------------------------------------------------------ add

export function AddDialog() {
  const tx = useT();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [savePath, setSavePath] = useState("");
  const [category, setCategory] = useState("");
  const [tags, setTags] = useState("");
  const [paused, setPaused] = useState(false);
  const [sequential, setSequential] = useState(false);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cats, setCats] = useState<Record<string, { save_path: string }>>({});
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.categories().then((r) => setCats(r.categories)).catch(() => undefined);
    api.settings().then((s) => setSavePath(String(s.save_path ?? ""))).catch(() => undefined);
    navigator.clipboard
      ?.readText?.()
      .then((t) => /^magnet:\?/i.test(t.trim()) && setText((cur) => cur || t.trim()))
      .catch(() => undefined);
  }, []);

  const uris = text.split(/\s+/).filter((s) => /^magnet:\?/i.test(s) || /^[a-f0-9]{40}$/i.test(s));
  const count = uris.length + files.length;

  const submit = async () => {
    setBusy(true);
    const opts = {
      save_path: savePath.trim() || undefined,
      category: category.trim(),
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      paused,
      sequential,
    };
    let added = 0;
    const errors: string[] = [];
    try {
      if (uris.length) {
        const r = await api.addMagnets(uris.map((u) => (u.startsWith("magnet:") ? u : `magnet:?xt=urn:btih:${u}`)), opts);
        added += r.added.length;
        r.errors.forEach((e) => errors.push(e.error));
      }
      if (files.length) {
        const r = await api.addFiles(files, opts);
        added += r.added.length;
        r.errors.forEach((e) => errors.push(`${e.file}: ${e.error}`));
      }
    } catch (e) {
      errors.push((e as Error).message);
    }
    setBusy(false);
    if (added) toast(added === 1 ? t("toast.addedOne") : t("toast.addedMany", { count: added }), "success");
    errors.forEach((e) => toast(e, "error"));
    if (!errors.length) close();
  };

  const pickCategory = (c: string) => {
    setCategory(c);
    if (cats[c]?.save_path) setSavePath(cats[c].save_path);
  };

  return (
    <Dialog
      title={tx("dialog.addTitle")}
      sub={tx("dialog.addSub")}
      onClose={close}
      footer={
        <>
          <span className="left">{count ? tx("dialog.addReady", { count }) : ""}</span>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!count || busy} onClick={submit}>
            {busy ? tx("common.adding") : count > 1 ? tx("dialog.addMany", { count }) : tx("dialog.addOne")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.magnets")}</span>
        <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder={tx("dialog.magnetPlaceholder")} spellCheck={false} />
      </label>
      <div
        className={`drop ${over ? "over" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOver(false);
          setFiles((f) => [...f, ...[...e.dataTransfer.files].filter((x) => x.name.toLowerCase().endsWith(".torrent"))]);
        }}
      >
        {tx("dialog.dropTorrents")}
        <input ref={input} type="file" accept=".torrent,application/x-bittorrent" multiple hidden onChange={(e) => setFiles((f) => [...f, ...(e.target.files ? [...e.target.files] : [])])} />
      </div>
      {files.length > 0 && (
        <ul className="filelist">
          {files.map((f, i) => (
            <li key={`${f.name}${i}`}>
              <span>{f.name}</span>
              <button className="link" onClick={() => setFiles((x) => x.filter((_, j) => j !== i))}>
                {tx("common.remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <label className="field">
        <span>{tx("dialog.saveTo")}</span>
        <input type="text" value={savePath} onChange={(e) => setSavePath(e.target.value)} spellCheck={false} />
      </label>
      <div className="row">
        <label className="field">
          <span>{tx("dialog.category")}</span>
          <input type="text" list="cats" value={category} onChange={(e) => pickCategory(e.target.value)} placeholder={tx("common.none")} />
          <datalist id="cats">
            {Object.keys(cats).map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>
        <label className="field">
          <span>{tx("dialog.tags")}</span>
          <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder={tx("dialog.commaSeparated")} />
        </label>
      </div>
      <label className="check">
        <input type="checkbox" checked={paused} onChange={(e) => setPaused(e.target.checked)} /> {tx("dialog.addPaused")}
      </label>
      <label className="check">
        <input type="checkbox" checked={sequential} onChange={(e) => setSequential(e.target.checked)} /> {tx("dialog.sequential")}
      </label>
    </Dialog>
  );
}

// ------------------------------------------------------------------ remove

export function RemoveDialog() {
  const tx = useT();
  const ids = useStore((s) => s.selected);
  const byId = useStore((s) => s.byId);
  const [withData, setWithData] = useState(false);
  const list = [...ids].map((id) => byId.get(id)).filter(Boolean);
  const size = list.reduce((a, t) => a + (t?.done ?? 0), 0);
  return (
    <Dialog
      title={list.length === 1 ? tx("dialog.removeOne") : tx("dialog.removeMany", { count: list.length })}
      sub={list.length === 1 ? list[0]!.name : undefined}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className={`btn ${withData ? "danger" : "primary"}`} onClick={() => removeSelected(withData)}>
            {withData ? tx("dialog.removeAndData") : tx("common.remove")}
          </button>
        </>
      }
    >
      <label className="check">
        <input type="checkbox" checked={withData} onChange={(e) => setWithData(e.target.checked)} />
        {tx("dialog.deleteData", { size: bytes(size) })}
      </label>
      {withData && <p style={{ color: "var(--flare)", fontSize: "var(--fs-s)" }}>{tx("dialog.deleteWarn")}</p>}
    </Dialog>
  );
}

// ------------------------------------------------------------------ category / bulk / trackers (in-app, no prompt/confirm)

export function CategoryCreateDialog() {
  const tx = useT();
  const [name, setName] = useState("");
  const [savePath, setSavePath] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    try {
      await api.addCategory(n, savePath.trim());
      toast(t("toast.categoryCreated", { name: n }), "success");
      close();
    } catch (e) {
      toast(t("toast.categoryCreateFail", { msg: (e as Error).message }), "error");
    }
    setBusy(false);
  };
  return (
    <Dialog
      title={tx("dialog.catCreateTitle")}
      sub={tx("dialog.catCreateSub")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!name.trim() || busy} onClick={submit}>
            {tx("dialog.createCategory")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("common.name")}</span>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={tx("dialog.moviesPlaceholder")} />
      </label>
      <label className="field">
        <span>{tx("dialog.saveFolder")}</span>
        <input type="text" value={savePath} onChange={(e) => setSavePath(e.target.value)} placeholder={tx("dialog.saveFolderPlaceholder")} spellCheck={false} />
        <small>{tx("dialog.saveFolderHint")}</small>
      </label>
    </Dialog>
  );
}

export function CategoryDeleteDialog() {
  const tx = useT();
  const name = getState().dialogCtx?.categoryName ?? "";
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await api.deleteCategory(name);
      if (getState().category === name) setState({ category: null });
      toast(t("toast.categoryDeleted", { name }), "success");
      close();
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setBusy(false);
  };
  return (
    <Dialog
      title={tx("dialog.catDeleteTitle", { name })}
      sub={tx("dialog.catDeleteSub")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn danger" disabled={busy} onClick={submit}>
            {tx("dialog.deleteCategory")}
          </button>
        </>
      }
    >
      <p style={{ margin: 0, fontSize: "var(--fs-m)" }}>{tx("dialog.catDeleteBody")}</p>
    </Dialog>
  );
}

function BulkCategoryDialog() {
  const tx = useT();
  const ctx = getState().dialogCtx;
  const ids = ctx?.torrentIds ?? [];
  const [value, setValue] = useState(ctx?.initialText ?? "");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    const cat = value.trim();
    await Promise.all(ids.map((id) => api.patch(id, { category: cat })));
    toast(cat ? t("toast.movedTo", { name: cat }) : t("toast.categoryCleared"), "success");
    setBusy(false);
    close();
  };
  return (
    <Dialog
      title={ids.length === 1 ? tx("dialog.setCategory") : tx("dialog.setCategoryMany", { count: ids.length })}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {tx("common.save")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.category")}</span>
        <input autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder={tx("dialog.emptyToClear")} />
      </label>
    </Dialog>
  );
}

function BulkTagsDialog() {
  const tx = useT();
  const ids = getState().dialogCtx?.torrentIds ?? [];
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const add = raw.split(",").map((s) => s.trim()).filter(Boolean);
    if (!add.length) return;
    setBusy(true);
    const byId = getState().byId;
    await Promise.all(ids.map((id) => api.patch(id, { tags: [...new Set([...(byId.get(id)?.tags ?? []), ...add])] })));
    toast(t("toast.tagsUpdated"), "success");
    setBusy(false);
    close();
  };
  return (
    <Dialog
      title={tx("dialog.addTagsTitle")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!raw.trim() || busy} onClick={submit}>
            {tx("dialog.addTagsTitle")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.tagsToAdd")}</span>
        <input autoFocus value={raw} onChange={(e) => setRaw(e.target.value)} placeholder={tx("dialog.commaSeparated")} />
      </label>
    </Dialog>
  );
}

function BulkLimitDialog() {
  const tx = useT();
  const ctx = getState().dialogCtx;
  const field = ctx?.limitField ?? "dl_limit";
  const ids = ctx?.torrentIds ?? [];
  const [raw, setRaw] = useState("0");
  const [busy, setBusy] = useState(false);
  const label = field === "dl_limit" ? t("bulk.download") : t("bulk.upload");
  const submit = async () => {
    const kb = Math.max(0, Number(raw) || 0);
    setBusy(true);
    await Promise.all(ids.map((id) => api.patch(id, { [field]: kb * 1024 })));
    toast(kb ? t("toast.limitSet", { label, kb }) : t("toast.limitRemoved"), "success");
    setBusy(false);
    close();
  };
  return (
    <Dialog
      title={field === "dl_limit" ? tx("dialog.dlLimitTitle") : tx("dialog.ulLimitTitle")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {tx("common.apply")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.limitKb")}</span>
        <input autoFocus type="number" min={0} value={raw} onChange={(e) => setRaw(e.target.value)} />
        <small>{tx("dialog.limitHint")}</small>
      </label>
    </Dialog>
  );
}

function BulkMoveDialog() {
  const tx = useT();
  const ctx = getState().dialogCtx;
  const ids = ctx?.torrentIds ?? [];
  const [dest, setDest] = useState(ctx?.initialText ?? "");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const path = dest.trim();
    if (!path) return;
    setBusy(true);
    await Promise.all(ids.map((id) => api.move(id, path)));
    toast(t("toast.movingData"), "success");
    setBusy(false);
    close();
  };
  return (
    <Dialog
      title={tx("dialog.moveTitle")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!dest.trim() || busy} onClick={submit}>
            {tx("dialog.moveBtn")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.destFolder")}</span>
        <input autoFocus type="text" value={dest} onChange={(e) => setDest(e.target.value)} spellCheck={false} />
      </label>
    </Dialog>
  );
}

export function TrackersAddDialog() {
  const tx = useT();
  const id = getState().dialogCtx?.torrentId ?? "";
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const urls = raw.split(/\s+/).filter((u) => /^(udp|https?|wss?):\/\//i.test(u));
    if (!urls.length) {
      toast(t("toast.noTrackers"), "error");
      return;
    }
    setBusy(true);
    try {
      await api.patch(id, { add_trackers: urls });
      toast(urls.length === 1 ? t("toast.trackersAddedOne") : t("toast.trackersAddedMany", { count: urls.length }), "success");
      close();
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setBusy(false);
  };
  return (
    <Dialog
      title={tx("dialog.trackersTitle")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!raw.trim() || busy} onClick={submit}>
            {tx("dialog.trackersTitle")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.trackerUrls")}</span>
        <textarea autoFocus value={raw} onChange={(e) => setRaw(e.target.value)} placeholder={tx("dialog.trackerPlaceholder")} spellCheck={false} rows={4} />
      </label>
    </Dialog>
  );
}

// ------------------------------------------------------------------ settings

type Section = "downloads" | "speed" | "connection" | "queue" | "privacy" | "appearance" | "system";

const fmtRate = (v: unknown) => (Number(v) ? String(Math.round(Number(v) / 1024)) : "");

export function SettingsDialog() {
  const tx = useT();
  const [s, setS] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [section, setSection] = useState<Section>("downloads");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.settings().then(setS).catch((e) => toast(e.message, "error"));
  }, []);

  const v = (k: string) => (k in draft ? draft[k] : s?.[k]);
  const set = (k: string, val: unknown) => setDraft((d) => ({ ...d, [k]: val }));

  const save = async () => {
    setSaving(true);
    try {
      if (Object.keys(draft).length) await api.saveSettings(draft);
      toast(t("toast.settingsSaved"), "success");
      close();
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setSaving(false);
  };

  const num = (k: string, label: string, hint?: string) => (
    <label className="field">
      <span>{label}</span>
      <input type="number" min={-1} value={String(v(k) ?? "")} onChange={(e) => set(k, Number(e.target.value))} />
      {hint && <small>{hint}</small>}
    </label>
  );
  const kb = (k: string, label: string) => (
    <label className="field">
      <span>{label}</span>
      <input type="text" placeholder={tx("common.unlimited")} defaultValue={fmtRate(s?.[k])} onChange={(e) => set(k, parseRate(e.target.value))} />
      <small>{tx("settings.kbHint")}</small>
    </label>
  );
  const bool = (k: string, label: string) => (
    <label className="check">
      <input type="checkbox" checked={!!v(k)} onChange={(e) => set(k, e.target.checked)} /> {label}
    </label>
  );

  const NAV: [Section, string][] = [
    ["downloads", tx("settings.navDownloads")],
    ["speed", tx("settings.navSpeed")],
    ["connection", tx("settings.navConnection")],
    ["queue", tx("settings.navQueue")],
    ["privacy", tx("settings.navPrivacy")],
    ["appearance", tx("settings.navAppearance")],
    ["system", tx("settings.navSystem")],
  ];

  return (
    <Dialog
      title={tx("dialog.settingsTitle")}
      wide
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={saving || !s} onClick={save}>
            {tx("dialog.saveSettings")}
          </button>
        </>
      }
    >
      {!s ? (
        <p>{tx("dialog.settingsLoading")}</p>
      ) : (
        <div className="settings-grid">
          <div className="settings-nav">
            {NAV.map(([k, label]) => (
              <button
                key={k}
                data-testid={`settings-nav-${k}`}
                className={`side-item ${section === k ? "active" : ""}`}
                onClick={() => setSection(k)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="settings-pane">
            {section === "downloads" && (
              <>
                <h3>{tx("settings.downloads")}</h3>
                <label className="field">
                  <span>{tx("settings.defaultSave")}</span>
                  <input type="text" value={String(v("save_path") ?? "")} onChange={(e) => set("save_path", e.target.value)} spellCheck={false} />
                </label>
                <label className="field">
                  <span>{tx("settings.incompletePath")}</span>
                  <input
                    type="text"
                    value={String(v("incomplete_path") ?? "")}
                    onChange={(e) => set("incomplete_path", e.target.value)}
                    spellCheck={false}
                  />
                </label>
                <label className="check">
                  <input type="checkbox" checked={!!v("use_incomplete_folder")} onChange={(e) => set("use_incomplete_folder", e.target.checked)} />{" "}
                  {tx("settings.useIncompleteFolder")}
                </label>
                <label className="check">
                  <input type="checkbox" checked={!!v("part_file_rt_suffix")} onChange={(e) => set("part_file_rt_suffix", e.target.checked)} />{" "}
                  {tx("settings.partFileRt")}
                </label>
                <small>{tx("settings.partFileRtHint")}</small>
                <h3 style={{ marginTop: 18 }}>{tx("settings.watchFolder")}</h3>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!v("watch_folder_enabled")}
                    onChange={(e) => set("watch_folder_enabled", e.target.checked)}
                  />{" "}
                  {tx("settings.watchFolderEnable")}
                </label>
                <label className="field">
                  <span>{tx("settings.watchFolderPath")}</span>
                  <input
                    type="text"
                    value={String(v("watch_folder_path") ?? "")}
                    onChange={(e) => set("watch_folder_path", e.target.value)}
                    spellCheck={false}
                  />
                </label>
                <label className="field">
                  <span>{tx("settings.watchFolderAction")}</span>
                  <select value={String(v("watch_folder_action") ?? "move")} onChange={(e) => set("watch_folder_action", e.target.value)}>
                    <option value="move">{tx("settings.watchFolderMove")}</option>
                    <option value="delete">{tx("settings.watchFolderDelete")}</option>
                  </select>
                </label>
              </>
            )}
            {section === "speed" && (
              <>
                <h3>{tx("settings.globalLimits")}</h3>
                <div className="row">
                  {kb("download_rate_limit", tx("settings.dlLimit"))}
                  {kb("upload_rate_limit", tx("settings.ulLimit"))}
                </div>
              </>
            )}
            {section === "connection" && (
              <>
                <h3>{tx("settings.connection")}</h3>
                <div className="row">
                  {num("listen_port", tx("settings.incomingPort"), tx("settings.portHint"))}
                  {num("connections_limit", tx("settings.maxConnections"))}
                </div>
                {num("unchoke_slots_limit", tx("settings.uploadSlots"), tx("settings.unlimitedHint"))}
                {bool("enable_upnp", tx("settings.upnp"))}
                {bool("enable_natpmp", tx("settings.natpmp"))}
                {bool("enable_dht", tx("settings.dht"))}
                {bool("enable_lsd", tx("settings.lsd"))}
                {bool("enable_incoming_utp", tx("settings.utpIn"))}
                {bool("enable_outgoing_utp", tx("settings.utpOut"))}
              </>
            )}
            {section === "queue" && (
              <>
                <h3>{tx("settings.queue")}</h3>
                <div className="row">
                  {num("active_downloads", tx("settings.activeDownloads"), tx("settings.unlimitedHint"))}
                  {num("active_seeds", tx("settings.activeSeeds"))}
                </div>
                {num("active_limit", tx("settings.activeLimit"))}
                {bool("dont_count_slow_torrents", tx("settings.dontCountSlow"))}
              </>
            )}
            {section === "privacy" && (
              <>
                <h3>{tx("settings.privacy")}</h3>
                <label className="field">
                  <span>{tx("settings.encryption")}</span>
                  <select value={String(v("out_enc_policy") ?? 1)} onChange={(e) => (set("out_enc_policy", Number(e.target.value)), set("in_enc_policy", Number(e.target.value)))}>
                    <option value={0}>{tx("settings.encRequire")}</option>
                    <option value={1}>{tx("settings.encPrefer")}</option>
                    <option value={2}>{tx("settings.encOff")}</option>
                  </select>
                </label>
                {bool("anonymous_mode", tx("settings.anonymous"))}
                <h3 style={{ marginTop: 18 }}>{tx("settings.proxy")}</h3>
                <div className="row">
                  <label className="field">
                    <span>{tx("settings.type")}</span>
                    <select value={String(v("proxy_type") ?? 0)} onChange={(e) => set("proxy_type", Number(e.target.value))}>
                      <option value={0}>{tx("settings.proxyNone")}</option>
                      <option value={1}>{tx("settings.socks4")}</option>
                      <option value={2}>{tx("settings.socks5")}</option>
                      <option value={3}>{tx("settings.socks5Login")}</option>
                      <option value={4}>{tx("settings.http")}</option>
                      <option value={5}>{tx("settings.httpLogin")}</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>{tx("settings.host")}</span>
                    <input type="text" value={String(v("proxy_hostname") ?? "")} onChange={(e) => set("proxy_hostname", e.target.value)} />
                  </label>
                </div>
                <div className="row">
                  {num("proxy_port", tx("settings.port"))}
                  <label className="field">
                    <span>{tx("settings.username")}</span>
                    <input type="text" value={String(v("proxy_username") ?? "")} onChange={(e) => set("proxy_username", e.target.value)} />
                  </label>
                </div>
                {bool("proxy_peer_connections", tx("settings.proxyPeers"))}
                {bool("proxy_tracker_connections", tx("settings.proxyTrackers"))}
              </>
            )}
            {section === "appearance" && <Appearance />}
            {section === "system" && (
              <>
                <h3>{tx("settings.system")}</h3>
                {s.autostart_supported ? (
                  <>
                    <label className="check">
                      <input
                        type="checkbox"
                        data-testid="settings-autostart"
                        checked={!!v("autostart")}
                        onChange={(e) => set("autostart", e.target.checked)}
                      />{" "}
                      {tx("settings.autostart")}
                    </label>
                    <small>{tx("settings.autostartHint")}</small>
                    <h3 style={{ marginTop: 18 }}>{tx("settings.trayTitle")}</h3>
                    <small>{tx("settings.trayHint")}</small>
                  </>
                ) : (
                  <small>{tx("settings.systemUnsupported")}</small>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Appearance() {
  const tx = useT();
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "dark");
  const apply = (t: string) => {
    document.documentElement.dataset.theme = t;
    try {
      localStorage.setItem("riptide.theme", t);
    } catch {
      /* ignore */
    }
    setTheme(t);
  };
  const notify = async () => {
    if (!("Notification" in window)) return toast(t("toast.notifyUnsupported"), "error");
    const r = await Notification.requestPermission();
    toast(r === "granted" ? t("toast.notifyGranted") : t("toast.notifyBlocked"));
  };
  return (
    <>
      <h3>{tx("settings.appearance")}</h3>
      <label className="check">
        <input type="radio" name="theme" checked={theme === "dark"} onChange={() => apply("dark")} /> {tx("settings.themeDark")}
      </label>
      <label className="check">
        <input type="radio" name="theme" checked={theme === "light"} onChange={() => apply("light")} /> {tx("settings.themeLight")}
      </label>
      <h3 style={{ marginTop: 18 }}>{tx("settings.notifications")}</h3>
      <button className="btn" onClick={notify}>
        {tx("settings.notifyBtn")}
      </button>
    </>
  );
}

// ------------------------------------------------------------------ create

export function CreateDialog() {
  const tx = useT();
  const [source, setSource] = useState("");
  const [trackers, setTrackers] = useState("");
  const [comment, setComment] = useState("");
  const [priv, setPriv] = useState(false);
  const [seed, setSeed] = useState(true);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const blob = await api.createTorrent({
        source: source.trim(),
        trackers: trackers.split(/\s+/).filter(Boolean),
        comment,
        private: priv,
        start_seeding: seed,
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${source.trim().split(/[\\/]/).filter(Boolean).pop() ?? "created"}.torrent`;
      a.click();
      toast(seed ? t("toast.torrentCreatedSeed") : t("toast.torrentCreated"), "success");
      close();
    } catch (e) {
      toast((e as Error).message, "error");
    }
    setBusy(false);
  };

  return (
    <Dialog
      title={tx("dialog.createTitle")}
      sub={tx("dialog.createSub")}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {tx("common.cancel")}
          </button>
          <button className="btn primary" disabled={!source.trim() || busy} onClick={submit}>
            {busy ? tx("common.hashing") : tx("dialog.createBtn")}
          </button>
        </>
      }
    >
      <label className="field">
        <span>{tx("dialog.sourcePath")}</span>
        <input type="text" autoFocus value={source} onChange={(e) => setSource(e.target.value)} placeholder={tx("dialog.sourcePlaceholder")} spellCheck={false} />
      </label>
      <label className="field">
        <span>{tx("dialog.trackersField")}</span>
        <textarea value={trackers} onChange={(e) => setTrackers(e.target.value)} placeholder={tx("dialog.trackersPlaceholder")} style={{ minHeight: 70 }} />
      </label>
      <label className="field">
        <span>{tx("dialog.comment")}</span>
        <input type="text" value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} /> {tx("dialog.privateTorrent")}
      </label>
      <label className="check">
        <input type="checkbox" checked={seed} onChange={(e) => setSeed(e.target.checked)} /> {tx("dialog.startSeeding")}
      </label>
    </Dialog>
  );
}

// ------------------------------------------------------------------ shortcuts

const KEY_BINDINGS: [string, string][] = [
  ["Ctrl+O", "shortcuts.add"],
  ["Ctrl+V", "shortcuts.pasteMagnet"],
  ["Ctrl+N", "shortcuts.create"],
  ["Space", "shortcuts.pauseResume"],
  ["Delete", "shortcuts.remove"],
  ["Ctrl+A", "shortcuts.selectAll"],
  ["Up / Down", "shortcuts.moveSelection"],
  ["/", "shortcuts.filter"],
  ["Ctrl+I", "shortcuts.details"],
  ["Ctrl+,", "shortcuts.settings"],
  ["?", "shortcuts.thisList"],
];

export function ShortcutsDialog() {
  const tx = useT();
  return (
    <Dialog
      title={tx("dialog.shortcutsTitle")}
      onClose={close}
      footer={
        <button className="btn primary" onClick={close}>
          {tx("common.done")}
        </button>
      }
    >
      <table className="mini">
        <tbody>
          {KEY_BINDINGS.map(([k, d]) => (
            <tr key={k}>
              <td style={{ width: 130 }}>
                {k.split(" / ").map((x, i) => (
                  <span key={x}>
                    {i > 0 && " / "}
                    <kbd className="key">{x}</kbd>
                  </span>
                ))}
              </td>
              <td>{tx(d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  );
}

export function Dialogs() {
  const dialog = useStore((s) => s.dialog);
  if (dialog === "add") return <AddDialog />;
  if (dialog === "remove" && getState().selected.size) return <RemoveDialog />;
  if (dialog === "settings") return <SettingsDialog />;
  if (dialog === "create") return <CreateDialog />;
  if (dialog === "shortcuts") return <ShortcutsDialog />;
  if (dialog === "categoryCreate") return <CategoryCreateDialog />;
  if (dialog === "categoryDelete") return <CategoryDeleteDialog />;
  if (dialog === "bulkCategory") return <BulkCategoryDialog />;
  if (dialog === "bulkTags") return <BulkTagsDialog />;
  if (dialog === "bulkLimit") return <BulkLimitDialog />;
  if (dialog === "bulkMove") return <BulkMoveDialog />;
  if (dialog === "trackersAdd") return <TrackersAddDialog />;
  return null;
}
