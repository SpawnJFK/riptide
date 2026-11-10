import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent } from "react";
import { api } from "../api";
import { bulk, copy } from "../actions";
import {
  COLUMN_DEFS,
  DEF_BY_ID,
  loadColumnLayout,
  saveColumnLayout,
  visibleColumns,
  widthFor,
  type ColumnDef,
  type ColumnId,
  type ColumnLayout,
} from "../columns";
import { columnLabel, useT } from "../i18n";
import { getState, openDialog, setState, type SortKey, useStore, visibleTorrents } from "../store";
import type { Torrent } from "../types";

const ROW = 30;

export function TorrentTable() {
  const tx = useT();
  const torrents = useStore((s) => s.torrents);
  const status = useStore((s) => s.status);
  const category = useStore((s) => s.category);
  const tag = useStore((s) => s.tag);
  const search = useStore((s) => s.search);
  const sort = useStore((s) => s.sort);
  const selected = useStore((s) => s.selected);
  const focused = useStore((s) => s.focused);

  const [layout, setLayout] = useState<ColumnLayout>(() => loadColumnLayout());
  const cols = useMemo(() => visibleColumns(layout), [layout]);

  const persist = useCallback((next: ColumnLayout) => {
    setLayout(next);
    saveColumnLayout(next);
  }, []);

  const rows = useMemo(() => visibleTorrents(getState()), [torrents, status, category, tag, search, sort]);
  const wrap = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState({ top: 0, height: 600 });
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [headerMenu, setHeaderMenu] = useState<{ x: number; y: number; col: ColumnId } | null>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  const dialog = useStore((s) => s.dialog);
  useEffect(() => {
    if (dialog) setMenu(null);
  }, [dialog]);

  const [resize, setResize] = useState<{ id: ColumnId; startX: number; startW: number } | null>(null);
  useEffect(() => {
    if (!resize) return;
    const move = (e: MouseEvent | globalThis.MouseEvent) => {
      const dx = e.clientX - resize.startX;
      const min = DEF_BY_ID[resize.id].minWidth;
      const w = Math.max(min, resize.startW + dx);
      persist({ ...layout, widths: { ...layout.widths, [resize.id]: w } });
    };
    const up = () => setResize(null);
    addEventListener("mousemove", move as never);
    addEventListener("mouseup", up);
    return () => {
      removeEventListener("mousemove", move as never);
      removeEventListener("mouseup", up);
    };
  }, [resize, layout, persist]);

  const [dragCol, setDragCol] = useState<ColumnId | null>(null);

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setScroll((s) => ({ ...s, height: el.clientHeight })));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = wrap.current;
    if (!el || !focused) return;
    const idx = rows.findIndex((r) => r.id === focused);
    if (idx < 0) return;
    const top = idx * ROW;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + ROW * 2 > el.scrollTop + el.clientHeight) el.scrollTop = top + ROW * 2 - el.clientHeight;
  }, [focused, rows]);

  const first = Math.max(0, Math.floor(scroll.top / ROW) - 8);
  const last = Math.min(rows.length, Math.ceil((scroll.top + scroll.height) / ROW) + 8);

  const click = (e: MouseEvent, t: Torrent) => {
    const s = getState();
    if (e.shiftKey && s.anchor) {
      const a = rows.findIndex((r) => r.id === s.anchor);
      const b = rows.findIndex((r) => r.id === t.id);
      const [lo, hi] = a < b ? [a, b] : [b, a];
      const next = e.ctrlKey || e.metaKey ? new Set(s.selected) : new Set<string>();
      rows.slice(lo, hi + 1).forEach((r) => next.add(r.id));
      setState({ selected: next, focused: t.id });
    } else if (e.ctrlKey || e.metaKey) {
      const next = new Set(s.selected);
      if (next.has(t.id)) next.delete(t.id);
      else next.add(t.id);
      setState({ selected: next, anchor: t.id, focused: t.id });
    } else {
      setState({ selected: new Set([t.id]), anchor: t.id, focused: t.id });
    }
  };

  const onContext = (e: MouseEvent, t: Torrent) => {
    e.preventDefault();
    if (!getState().selected.has(t.id)) setState({ selected: new Set([t.id]), anchor: t.id, focused: t.id });
    setMenu({ x: Math.min(e.clientX, innerWidth - 230), y: Math.min(e.clientY, innerHeight - 380) });
  };

  const sortBy = (key: SortKey) =>
    setState((s) => ({ sort: { key, dir: s.sort.key === key ? (s.sort.dir === 1 ? -1 : 1) : key === "name" || key === "queue" ? 1 : -1 } }));

  const totalWidth = cols.reduce((a, c) => a + widthFor(c, layout), 0);

  const moveColumn = (id: ColumnId, dir: -1 | 1) => {
    const order = [...layout.order];
    const i = order.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return;
    order.splice(i, 1);
    order.splice(j, 0, id);
    persist({ ...layout, order });
  };

  const toggleColumn = (id: ColumnId, show: boolean) => {
    const hidden = new Set(layout.hidden);
    if (show) hidden.delete(id);
    else hidden.add(id);
    persist({ ...layout, hidden: [...hidden] });
  };

  const onHeaderDrop = (target: ColumnId) => {
    if (!dragCol || dragCol === target) return;
    const order = [...layout.order];
    const from = order.indexOf(dragCol);
    const to = order.indexOf(target);
    if (from < 0 || to < 0) return;
    order.splice(from, 1);
    order.splice(to, 0, dragCol);
    persist({ ...layout, order });
    setDragCol(null);
  };

  if (torrents.length === 0) {
    return (
      <div className="table-wrap">
        <div className="empty">
          <div>
            <h2>{tx("empty.title")}</h2>
            <p>{tx("empty.body")}</p>
            <button className="btn primary" onClick={() => setState({ dialog: "add" })}>
              {tx("empty.add")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="table-wrap"
      ref={wrap}
      tabIndex={0}
      aria-label={tx("table.aria")}
      onScroll={(e) => setScroll({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight })}
      onClick={(e) => {
        if (e.target === e.currentTarget) setState({ selected: new Set(), focused: null });
      }}
    >
      <div className="table" role="grid" aria-rowcount={rows.length} style={{ width: totalWidth }}>
        <div className="thead" role="row">
          {cols.map((c) => (
            <HeaderCell
              key={c.id}
              col={c}
              width={widthFor(c, layout)}
              sorted={sort.key === c.id}
              sortDir={sort.dir}
              onSort={() => c.sortable && sortBy(c.id)}
              onResizeStart={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setResize({ id: c.id, startX: e.clientX, startW: widthFor(c, layout) });
              }}
              onContextMenu={(e) => {
                e.preventDefault();
                setHeaderMenu({ x: e.clientX, y: e.clientY, col: c.id });
              }}
              draggable
              onDragStart={() => setDragCol(c.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => onHeaderDrop(c.id)}
              label={columnLabel(c.id)}
            />
          ))}
        </div>
        <div style={{ height: rows.length * ROW, position: "relative" }}>
          {rows.slice(first, last).map((t, i) => (
            <div
              key={t.id}
              role="row"
              aria-selected={selected.has(t.id)}
              className={`tr ${selected.has(t.id) ? "sel" : ""} ${focused === t.id ? "focus" : ""}`}
              style={{ position: "absolute", top: (first + i) * ROW, left: 0, right: 0, width: totalWidth }}
              onClick={(e) => click(e, t)}
              onDoubleClick={() => bulk(t.state === "paused" || t.state === "completed" ? "resume" : "pause", [t.id])}
              onContextMenu={(e) => onContext(e, t)}
            >
              {cols.map((c) => (
                <div
                  key={c.id}
                  role="gridcell"
                  className={`td ${c.num ? "num" : ""} ${c.id === "name" ? "name" : ""} ${c.narrow ? "hide-narrow" : ""}`}
                  style={{ width: widthFor(c, layout) }}
                >
                  {c.cell(t)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      {menu && <ContextMenu x={menu.x} y={menu.y} onClose={closeMenu} tx={tx} />}
      {headerMenu && (
        <HeaderColumnMenu
          x={headerMenu.x}
          y={headerMenu.y}
          col={headerMenu.col}
          layout={layout}
          onClose={() => setHeaderMenu(null)}
          onMove={moveColumn}
          onToggle={toggleColumn}
          tx={tx}
        />
      )}
    </div>
  );
}

function HeaderCell({
  col,
  width,
  sorted,
  sortDir,
  onSort,
  onResizeStart,
  onContextMenu,
  draggable,
  onDragStart,
  onDragOver,
  onDrop,
  label,
}: {
  col: ColumnDef;
  width: number;
  sorted: boolean;
  sortDir: 1 | -1;
  onSort: () => void;
  onResizeStart: (e: MouseEvent) => void;
  onContextMenu: (e: MouseEvent) => void;
  draggable?: boolean;
  onDragStart?: () => void;
  onDragOver?: (e: DragEvent) => void;
  onDrop?: () => void;
  label: string;
}) {
  return (
    <div
      role="columnheader"
      className={`th-wrap ${col.id === "name" ? "name-col" : ""} ${col.narrow ? "hide-narrow" : ""}`}
      style={{ width }}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        onDragStart?.();
      }}
      onDragOver={onDragOver}
      onDrop={(e) => {
        e.preventDefault();
        onDrop?.();
      }}
    >
      <button
        type="button"
        className={`th ${col.num ? "num" : ""} ${sorted ? "sorted" : ""}`}
        onClick={onSort}
        onContextMenu={onContextMenu}
        aria-sort={sorted ? (sortDir === 1 ? "ascending" : "descending") : "none"}
      >
        {label}
        {sorted ? (sortDir === 1 ? " ▲" : " ▼") : ""}
      </button>
      <span className="col-resize" onMouseDown={onResizeStart} aria-hidden="true" />
    </div>
  );
}

function HeaderColumnMenu({
  x,
  y,
  col,
  layout,
  onClose,
  onMove,
  onToggle,
  tx,
}: {
  x: number;
  y: number;
  col: ColumnId;
  layout: ColumnLayout;
  onClose: () => void;
  onMove: (id: ColumnId, dir: -1 | 1) => void;
  onToggle: (id: ColumnId, show: boolean) => void;
  tx: (key: string, params?: Record<string, string | number>) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const hidden = layout.hidden.includes(col);
  useEffect(() => {
    const close = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    addEventListener("mousedown", close);
    return () => removeEventListener("mousedown", close);
  }, [onClose]);

  return (
    <div className="menu col-menu" ref={ref} style={{ left: x, top: y }} role="menu">
      <button type="button" onClick={() => { onMove(col, -1); onClose(); }}>
        {tx("headerMenu.moveLeft")}
      </button>
      <button type="button" onClick={() => { onMove(col, 1); onClose(); }}>
        {tx("headerMenu.moveRight")}
      </button>
      <hr />
      <div className="menu-label">{tx("headerMenu.columns")}</div>
      {COLUMN_DEFS.map((c) => {
        const vis = !layout.hidden.includes(c.id);
        return (
          <button
            key={c.id}
            type="button"
            className={vis ? "on" : ""}
            onClick={() => onToggle(c.id, !vis)}
          >
            {vis ? "✓ " : "  "}
            {columnLabel(c.id)}
          </button>
        );
      })}
      <hr />
      <button type="button" onClick={() => { onToggle(col, !hidden); onClose(); }}>
        {hidden ? tx("headerMenu.showColumn") : tx("headerMenu.hideColumn")}
      </button>
    </div>
  );
}

function ContextMenu({
  x,
  y,
  onClose,
  tx,
}: {
  x: number;
  y: number;
  onClose: () => void;
  tx: (key: string, params?: Record<string, string | number>) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const ids = [...getState().selected];
  const one = ids.length === 1 ? getState().byId.get(ids[0]) : undefined;

  useEffect(() => {
    const close = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    addEventListener("mousedown", close);
    addEventListener("keydown", esc, true);
    addEventListener("blur", onClose);
    return () => {
      removeEventListener("mousedown", close);
      removeEventListener("keydown", esc, true);
      removeEventListener("blur", onClose);
    };
  }, [onClose]);

  const run = (fn: () => unknown) => () => {
    onClose();
    fn();
  };

  return (
    <div className="menu" ref={ref} style={{ left: x, top: y }} role="menu">
      <button onClick={run(() => bulk("resume", ids))}>
        {tx("contextMenu.resume")} <kbd>Space</kbd>
      </button>
      <button onClick={run(() => bulk("pause", ids))}>{tx("contextMenu.pause")}</button>
      <button onClick={run(() => bulk("force_resume", ids))}>{tx("contextMenu.forceStart")}</button>
      <hr />
      {one && (
        <button onClick={run(() => api.patch(one.id, { sequential: !one.sequential }))}>
          {one.sequential ? tx("contextMenu.seqOff") : tx("contextMenu.seqOn")}
        </button>
      )}
      <button onClick={run(() => openDialog("bulkCategory", { torrentIds: ids, initialText: one?.category ?? "" }))}>{tx("contextMenu.setCategory")}</button>
      <button onClick={run(() => openDialog("bulkTags", { torrentIds: ids }))}>{tx("contextMenu.addTags")}</button>
      <button onClick={run(() => openDialog("bulkLimit", { torrentIds: ids, limitField: "dl_limit" }))}>{tx("contextMenu.limitDl")}</button>
      <button onClick={run(() => openDialog("bulkLimit", { torrentIds: ids, limitField: "ul_limit" }))}>{tx("contextMenu.limitUl")}</button>
      <button onClick={run(() => openDialog("bulkMove", { torrentIds: ids, initialText: one?.save_path ?? "" }))}>{tx("contextMenu.moveData")}</button>
      <hr />
      <button onClick={run(() => bulk("recheck", ids))}>{tx("contextMenu.recheck")}</button>
      <button onClick={run(() => bulk("reannounce", ids))}>{tx("contextMenu.reannounce")}</button>
      {one && (
        <button onClick={run(() => api.detail(one.id).then((d) => copy(d.magnet, tx("copy.magnet"))))}>
          {tx("contextMenu.copyMagnet")}
        </button>
      )}
      {one && one.has_metadata && (
        <button onClick={run(() => window.open(api.exportUrl(one.id)))}>{tx("contextMenu.export")}</button>
      )}
      <hr />
      <button className="danger" onClick={run(() => setState({ dialog: "remove" }))}>
        {tx("contextMenu.remove")} <kbd>Del</kbd>
      </button>
    </div>
  );
}
