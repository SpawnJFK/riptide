import { useEffect, useState } from "react";
import { connect } from "./api";
import { addFromClipboardText, addTorrentFiles, bulk } from "./actions";
import { DetailPanel } from "./components/DetailPanel";
import { Dialogs } from "./components/Dialogs";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { TideBand } from "./components/TideBand";
import { Toolbar } from "./components/Toolbar";
import { TorrentTable } from "./components/TorrentTable";
import { useT } from "./i18n";
import { applyMessage, getState, setState, useStore, visibleTorrents } from "./store";

export function App() {
  const tx = useT();
  const online = useStore((s) => s.online);
  const toasts = useStore((s) => s.toasts);
  const detailOpen = useStore((s) => s.detailOpen);
  const [dragging, setDragging] = useState(false);

  useEffect(() => connect(applyMessage, (o) => setState({ online: o })), []);

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = getState();
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "o") {
        e.preventDefault();
        setState({ dialog: "add" });
        return;
      }
      if (mod && e.key === ",") {
        e.preventDefault();
        setState({ dialog: "settings" });
        return;
      }
      if (typing || s.dialog) return;
      if (mod && e.key.toLowerCase() === "n") {
        e.preventDefault();
        setState({ dialog: "create" });
      } else if (mod && e.key.toLowerCase() === "i") {
        e.preventDefault();
        setState({ detailOpen: !s.detailOpen });
      } else if (mod && e.key.toLowerCase() === "a") {
        e.preventDefault();
        setState({ selected: new Set(visibleTorrents(s).map((t) => t.id)) });
      } else if (e.key === "Delete" && s.selected.size) {
        setState({ dialog: "remove" });
      } else if (e.key === " " && s.selected.size) {
        e.preventDefault();
        const anyRunning = [...s.selected].some((id) => {
          const t = s.byId.get(id);
          return t && t.state !== "paused" && t.state !== "completed";
        });
        bulk(anyRunning ? "pause" : "resume");
      } else if (e.key === "/") {
        e.preventDefault();
        document.getElementById("search")?.focus();
      } else if (e.key === "?") {
        setState({ dialog: "shortcuts" });
      } else if (e.key === "Escape") {
        setState({ selected: new Set(), focused: null });
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const rows = visibleTorrents(s);
        if (!rows.length) return;
        const idx = rows.findIndex((r) => r.id === s.focused);
        const next = rows[Math.min(rows.length - 1, Math.max(0, idx + (e.key === "ArrowDown" ? 1 : -1)))] ?? rows[0];
        if (e.shiftKey) {
          const sel = new Set(s.selected);
          sel.add(next.id);
          setState({ selected: sel, focused: next.id });
        } else setState({ selected: new Set([next.id]), anchor: next.id, focused: next.id });
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, []);

  // paste magnets anywhere
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const t = e.target as HTMLElement;
      if (/^(INPUT|TEXTAREA)$/.test(t.tagName) || getState().dialog) return;
      const text = e.clipboardData?.getData("text") ?? "";
      if (text) addFromClipboardText(text);
    };
    addEventListener("paste", onPaste);
    return () => removeEventListener("paste", onPaste);
  }, []);

  // drop .torrent files anywhere
  useEffect(() => {
    let depth = 0;
    const enter = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files") || getState().dialog) return;
      depth++;
      setDragging(true);
    };
    const leave = () => {
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const over = (e: DragEvent) => e.preventDefault();
    const drop = (e: DragEvent) => {
      e.preventDefault();
      depth = 0;
      setDragging(false);
      if (getState().dialog) return;
      const files = [...(e.dataTransfer?.files ?? [])];
      if (files.length) addTorrentFiles(files);
      else {
        const text = e.dataTransfer?.getData("text") ?? "";
        if (text) addFromClipboardText(text);
      }
    };
    addEventListener("dragenter", enter);
    addEventListener("dragleave", leave);
    addEventListener("dragover", over);
    addEventListener("drop", drop);
    return () => {
      removeEventListener("dragenter", enter);
      removeEventListener("dragleave", leave);
      removeEventListener("dragover", over);
      removeEventListener("drop", drop);
    };
  }, []);

  return (
    <div className="app">
      <TideBand />
      <div className="main">
        <Sidebar />
        <div className={`work ${detailOpen ? "with-detail" : ""}`}>
          <Toolbar />
          <TorrentTable />
          {detailOpen && <DetailPanel />}
        </div>
      </div>
      <StatusBar />
      <Dialogs />
      {dragging && <div className="dropzone">{tx("app.dropzone")}</div>}
      {!online && <div className="offline">{tx("app.offline")}</div>}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.level}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
