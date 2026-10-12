import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Search, Truck } from "lucide-react";
import { getJson } from "../../api";
import { useDebounced } from "../../lib/useResource";
import { date } from "../../lib/format";
import { StatusBadge } from "../Common";
import { companyQuery } from "./shared";

const LOCKED = new Set(["closed", "cancelled"]);

// Searchable list of shipments (server search on number, orders, name). Closed and cancelled
// shipments are shown but cannot be picked: bills and revenue need an open shipment.
export default function ShipmentPicker({ company, multiCompany, value, onChange, autoFocus = true }) {
  const [term, setTerm] = useState("");
  const settled = useDebounced(term.trim(), 200);
  const [state, setState] = useState({ loading: true, rows: [], error: "" });
  const generation = useRef(0);
  const list = useRef(null);

  useEffect(() => {
    const token = ++generation.current;
    setState((current) => ({ ...current, loading: true, error: "" }));
    getJson(`/api/freight-shipments${companyQuery(company, true, { search: settled })}`)
      .then((rows) => { if (token === generation.current) setState({ loading: false, rows, error: "" }); })
      .catch((error) => { if (token === generation.current) setState({ loading: false, rows: [], error: error.message }); });
  }, [settled, company, multiCompany]);

  const rows = [...state.rows].sort((a, b) => Number(LOCKED.has(a.status)) - Number(LOCKED.has(b.status)));
  const move = (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const buttons = [...(list.current?.querySelectorAll("button:not(:disabled)") || [])];
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? Math.min(buttons.length - 1, index + 1) : Math.max(0, index - 1);
    buttons[index === -1 ? 0 : next].focus();
  };

  return (
    <div className="ins-picker" onKeyDown={move}>
      <label className="search-field">
        <Search size={16} aria-hidden="true" />
        <span className="sr-only">Find a shipment</span>
        <input type="search" value={term} autoFocus={autoFocus} placeholder="Shipment number, order or name" onChange={(event) => setTerm(event.target.value)} />
      </label>
      <div className="ins-picker-status" role="status">
        {state.loading ? <><LoaderCircle size={14} className="spin" />Searching…</> : state.error ? <span className="danger-text">{state.error}</span>
          : `${rows.length}${rows.length >= 300 ? "+" : ""} shipment${rows.length === 1 ? "" : "s"}${settled ? ` matching “${settled}”` : ", newest first"}`}
      </div>
      <ul ref={list} className="ins-picker-list" aria-label="Shipments">
        {rows.map((shipment) => {
          const locked = LOCKED.has(shipment.status);
          const selected = value?.id === shipment.id;
          return (
            <li key={shipment.id}>
              <button type="button" disabled={locked} aria-pressed={selected} className={selected ? "is-selected" : ""} onClick={() => onChange(shipment)}>
                <span className="ins-picker-icon" aria-hidden="true">{selected ? <Check size={15} /> : <Truck size={15} />}</span>
                <span className="ins-picker-main">
                  <b>{shipment.number} · {shipment.name || (shipment.order_numbers || []).join(", ")}</b>
                  <small>
                    {(shipment.order_numbers || []).join(", ") || "no orders"}
                    {shipment.carrier ? ` · ${shipment.carrier}` : ""}
                    {shipment.pickup_date ? ` · pickup ${date(shipment.pickup_date)}` : ""}
                    {locked ? " · reopen it to add money" : ""}
                  </small>
                </span>
                <StatusBadge kind="shipment" status={shipment.status} />
              </button>
            </li>
          );
        })}
        {!state.loading && !rows.length && !state.error && <li className="ins-picker-empty">No shipment matches. Try a shipment or order number.</li>}
      </ul>
    </div>
  );
}
