import { useEffect, useRef } from "react";
import { rate } from "../format";
import { useT } from "../i18n";
import { setState, useStore } from "../store";
import { Gear, Logo, Plus } from "./icons";

/** Header band: live 5-minute wave of total download (teal) and upload (amber). */
export function TideBand() {
  const tx = useT();
  const stats = useStore((s) => s.stats);
  const history = useStore((s) => s.history);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (history.length < 2) return;

    const css = getComputedStyle(document.documentElement);
    const tide = css.getPropertyValue("--tide").trim();
    const dawn = css.getPropertyValue("--dawn").trim();
    const now = history[history.length - 1].t;
    // stretch the first minutes across the band, then settle into a 5 minute window
    const span = Math.min(300, Math.max(60, now - history[0].t));
    const max = Math.max(64 * 1024, ...history.map((p) => Math.max(p.dl, p.ul))) * 1.15;
    const x = (t: number) => w - ((now - t) / span) * w;
    const y = (v: number) => h - (v / max) * (h - 8);

    const wave = (key: "dl" | "ul", color: string, alpha: number) => {
      ctx.beginPath();
      ctx.moveTo(x(history[0].t), h);
      // smooth with midpoints so the band reads as water, not a step chart
      let px = x(history[0].t);
      let py = y(history[0][key]);
      ctx.lineTo(px, py);
      for (let i = 1; i < history.length; i++) {
        const nx = x(history[i].t);
        const ny = y(history[i][key]);
        ctx.quadraticCurveTo(px, py, (px + nx) / 2, (py + ny) / 2);
        px = nx;
        py = ny;
      }
      ctx.lineTo(px, py);
      ctx.lineTo(px, h);
      ctx.closePath();
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, withAlpha(color, alpha));
      g.addColorStop(1, withAlpha(color, 0.02));
      ctx.fillStyle = g;
      ctx.fill();
    };
    wave("ul", dawn, 0.22);
    wave("dl", tide, 0.32);
  }, [history]);

  return (
    <header className="tide">
      <canvas ref={canvas} aria-hidden="true" />
      <div className="tide-content">
        <div className="brand">
          <Logo />
          <span className="brand-name">{tx("app.brand")}</span>
        </div>
        <div className="readout dl" title={tx("tide.downTitle")}>
          <b>{rate(stats?.dl_rate ?? 0) || "0 B/s"}</b>
          <span>{tx("tide.down")}</span>
        </div>
        <div className="readout ul" title={tx("tide.upTitle")}>
          <b>{rate(stats?.ul_rate ?? 0) || "0 B/s"}</b>
          <span>{tx("tide.up")}</span>
        </div>
        <div className="tide-actions">
          <button className="btn primary" onClick={() => setState({ dialog: "add" })} title={tx("tide.addTitle")}>
            <Plus /> <span className="add-label">{tx("tide.addTorrents")}</span>
          </button>
          <button className="btn icon" onClick={() => setState({ dialog: "settings" })} title={tx("tide.settingsTitle")} aria-label={tx("tide.settingsAria")}>
            <Gear />
          </button>
        </div>
      </div>
    </header>
  );
}

function withAlpha(color: string, a: number): string {
  if (color.startsWith("#") && color.length === 7) {
    const n = parseInt(color.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  return color;
}
