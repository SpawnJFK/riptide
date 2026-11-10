import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { matchesStatus, openDialog, setState, type StatusFilter, useStore } from "../store";

const STATUS_KEYS: { key: StatusFilter; labelKey: string; dot?: string }[] = [
  { key: "all", labelKey: "sidebar.statusAll" },
  { key: "downloading", labelKey: "sidebar.statusDownloading", dot: "downloading" },
  { key: "seeding", labelKey: "sidebar.statusSeeding", dot: "seeding" },
  { key: "completed", labelKey: "sidebar.statusCompleted" },
  { key: "paused", labelKey: "sidebar.statusPaused" },
  { key: "active", labelKey: "sidebar.statusActive", dot: "downloading" },
  { key: "inactive", labelKey: "sidebar.statusInactive" },
  { key: "stalled", labelKey: "sidebar.statusStalled" },
  { key: "errored", labelKey: "sidebar.statusErrored", dot: "error" },
];

export function Sidebar() {
  const tx = useT();
  const torrents = useStore((s) => s.torrents);
  const status = useStore((s) => s.status);
  const category = useStore((s) => s.category);
  const tag = useStore((s) => s.tag);
  const [cats, setCats] = useState<string[]>([]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of STATUS_KEYS) c[f.key] = torrents.filter((t) => matchesStatus(t, f.key)).length;
    return c;
  }, [torrents]);

  const catCounts = useMemo(() => {
    const m = new Map<string, number>();
    torrents.forEach((t) => t.category && m.set(t.category, (m.get(t.category) ?? 0) + 1));
    return m;
  }, [torrents]);

  const tagCounts = useMemo(() => {
    const m = new Map<string, number>();
    torrents.forEach((t) => t.tags.forEach((g) => m.set(g, (m.get(g) ?? 0) + 1)));
    return m;
  }, [torrents]);

  const refreshCats = () =>
    api
      .categories()
      .then((r) => setCats(Object.keys(r.categories).sort()))
      .catch(() => undefined);

  const dialog = useStore((s) => s.dialog);

  useEffect(() => {
    refreshCats();
  }, [catCounts.size]);

  useEffect(() => {
    if (dialog === null) refreshCats();
  }, [dialog]);

  const allCats = [...new Set([...cats, ...catCounts.keys()])].sort();

  return (
    <nav className="sidebar" aria-label={tx("sidebar.aria")}>
      <div className="side-group">
        <div className="side-title">{tx("sidebar.status")}</div>
        {STATUS_KEYS.map((f) => (
          <button key={f.key} className={`side-item ${status === f.key ? "active" : ""}`} onClick={() => setState({ status: f.key })}>
            <span className={`dot ${f.dot ?? ""}`} />
            {tx(f.labelKey)}
            <span className="count">{counts[f.key] || ""}</span>
          </button>
        ))}
      </div>

      <div className="side-group">
        <div className="side-title">
          {tx("sidebar.categories")}
          <button onClick={() => openDialog("categoryCreate")} title={tx("sidebar.newCategory")} aria-label={tx("sidebar.newCategory")}>
            +
          </button>
        </div>
        <button className={`side-item ${category === null ? "active" : ""}`} onClick={() => setState({ category: null })}>
          {tx("sidebar.anyCategory")}
        </button>
        <button className={`side-item ${category === "" ? "active" : ""}`} onClick={() => setState({ category: "" })}>
          {tx("sidebar.uncategorized")}
          <span className="count">{torrents.filter((t) => !t.category).length || ""}</span>
        </button>
        {allCats.map((c) => (
          <button
            key={c}
            className={`side-item ${category === c ? "active" : ""}`}
            onClick={() => setState({ category: c })}
            onContextMenu={(e) => {
              e.preventDefault();
              openDialog("categoryDelete", { categoryName: c });
            }}
          >
            {c}
            <span className="count">{catCounts.get(c) || ""}</span>
          </button>
        ))}
      </div>

      {tagCounts.size > 0 && (
        <div className="side-group">
          <div className="side-title">{tx("sidebar.tags")}</div>
          <button className={`side-item ${tag === null ? "active" : ""}`} onClick={() => setState({ tag: null })}>
            {tx("sidebar.anyTag")}
          </button>
          {[...tagCounts.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([g, n]) => (
              <button key={g} className={`side-item ${tag === g ? "active" : ""}`} onClick={() => setState({ tag: g })}>
                #{g}
                <span className="count">{n}</span>
              </button>
            ))}
        </div>
      )}
    </nav>
  );
}
