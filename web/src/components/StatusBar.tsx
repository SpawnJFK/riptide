import { useEffect } from "react";
import { api } from "../api";
import { bytes, rate } from "../format";
import { t, useT } from "../i18n";
import { setState, toast, useStore } from "../store";

export function StatusBar() {
  const tx = useT();
  const stats = useStore((s) => s.stats);
  const online = useStore((s) => s.online);
  const alt = useStore((s) => s.altSpeed);
  const count = useStore((s) => s.torrents.length);
  const selected = useStore((s) => s.selected.size);

  // Slow mode can change outside this tab (tray menu, another tab): read the engine's state on load, when the
  // window regains focus, and every 10 s, so the toggle and the tray check mark always agree.
  useEffect(() => {
    const sync = () =>
      api
        .settings()
        .then((s) => setState({ altSpeed: !!s.alt_speed }))
        .catch(() => {
          /* offline: the reconnect indicator already says so */
        });
    sync();
    const timer = window.setInterval(sync, 10_000);
    window.addEventListener("focus", sync);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", sync);
    };
  }, []);

  const toggleAlt = async () => {
    try {
      await api.altSpeed(!alt);
      setState({ altSpeed: !alt });
      toast(!alt ? t("toast.altOn") : t("toast.altOff"));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <footer className="statusbar">
      <span>
        {count === 1 ? tx("status.torrents", { count }) : tx("status.torrentsPlural", { count })}
        {selected ? `, ${tx("status.selected", { count: selected })}` : ""}
      </span>
      <span className="hide-narrow">{stats ? tx("status.peers", { count: stats.peers }) : ""}</span>
      <span className="hide-narrow">{stats ? tx("status.dht", { count: stats.dht_nodes }) : ""}</span>
      <div className="right">
        {stats && stats.free_space >= 0 && <span className="hide-narrow">{tx("status.free", { size: bytes(stats.free_space) })}</span>}
        <span className={stats?.listening ? "" : "off"}>
          {stats ? (stats.listening ? tx("status.port", { port: stats.port }) : tx("status.notListening")) : ""}
        </span>
        <button className={`link ${alt ? "on" : ""}`} onClick={toggleAlt} title={tx("status.slowTitle")}>
          {alt ? tx("status.slowModeOn") : tx("status.slowMode")}
        </button>
        <button className="link" onClick={() => setState({ dialog: "create" })}>
          {tx("status.createTorrent")}
        </button>
        <button className="link" type="button" data-testid="open-shortcuts" onClick={() => setState({ dialog: "shortcuts" })}>
          {tx("status.shortcuts")}
        </button>
        {!online && <span className="off">{tx("status.reconnecting")}</span>}
        {online && stats && (stats.dl_limit || stats.ul_limit) ? (
          <span className="hide-narrow">
            {tx("status.limits", { dl: rate(stats.dl_limit) || "∞", ul: rate(stats.ul_limit) || "∞" })}
          </span>
        ) : null}
      </div>
    </footer>
  );
}
