import { bulk } from "../actions";
import { useT } from "../i18n";
import { setState, useStore } from "../store";
import { Down, Panel, Pause, Play, Search, Trash, Up } from "./icons";

export function Toolbar() {
  const tx = useT();
  const count = useStore((s) => s.selected.size);
  const search = useStore((s) => s.search);
  const detailOpen = useStore((s) => s.detailOpen);
  const none = count === 0;

  return (
    <div className="toolbar" role="toolbar" aria-label={tx("toolbar.aria")}>
      <button className="btn" disabled={none} onClick={() => bulk("resume")} title={tx("toolbar.resumeTitle")}>
        <Play /> {tx("toolbar.resume")}
      </button>
      <button className="btn" disabled={none} onClick={() => bulk("pause")} title={tx("toolbar.pauseTitle")}>
        <Pause /> {tx("toolbar.pause")}
      </button>
      <button className="btn" disabled={none} onClick={() => setState({ dialog: "remove" })} title={tx("toolbar.removeTitle")}>
        <Trash /> {tx("toolbar.remove")}
      </button>
      <span className="sep hide-narrow" />
      <button className="btn icon hide-narrow" disabled={none} onClick={() => bulk("up")} title={tx("toolbar.moveUp")} aria-label={tx("toolbar.moveUp")}>
        <Up />
      </button>
      <button className="btn icon hide-narrow" disabled={none} onClick={() => bulk("down")} title={tx("toolbar.moveDown")} aria-label={tx("toolbar.moveDown")}>
        <Down />
      </button>
      <span className="sep hide-narrow" />
      <button
        className={`btn icon hide-narrow ${detailOpen ? "on" : ""}`}
        onClick={() => setState((s) => ({ detailOpen: !s.detailOpen }))}
        title={tx("toolbar.toggleDetailTitle")}
        aria-label={tx("toolbar.toggleDetail")}
      >
        <Panel />
      </button>
      <label className="search">
        <Search />
        <input
          id="search"
          type="search"
          placeholder={tx("toolbar.searchPlaceholder")}
          value={search}
          onChange={(e) => setState({ search: e.target.value })}
          aria-label={tx("toolbar.searchAria")}
        />
      </label>
    </div>
  );
}
