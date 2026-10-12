import { useEffect, useMemo, useRef, useState } from "react";
import { Copy, Lock, Plus, Ruler, Save, Trash2, X } from "lucide-react";
import { Field } from "../Common";
import { weight } from "../../lib/format";
import { ACTUAL_SOURCES, signedWeight } from "./model";
import "../../styles/shipments.css";

const FIELDS = [
  { key: "length", label: "L", name: "length", max: 720 },
  { key: "width", label: "W", name: "width", max: 120 },
  { key: "height", label: "H", name: "height", max: 144 },
  { key: "weight", label: "Weight", name: "weight", max: 20000 },
  { key: "quantity", label: "Qty", name: "quantity", max: 60 },
];
const MAX_ROWS = 60;

let seq = 0;
const rowKey = () => `p${(seq += 1)}`;
export const emptyRow = () => ({ id: rowKey(), length: "", width: "", height: "", weight: "", quantity: "1", note: "" });
const text = (value) => (value === null || value === undefined ? "" : String(value));

export function rowsFromActual(actual) {
  return (actual?.pallets || []).map((pallet) => ({
    id: rowKey(), length: text(pallet.length), width: text(pallet.width), height: text(pallet.height),
    weight: text(pallet.weight), quantity: text(pallet.quantity || 1), note: pallet.note || "",
  }));
}

// Quote rows are one per pallet; identical pallets become one row with a quantity.
export function rowsFromQuoted(quotedRows) {
  const groups = new Map();
  for (const row of quotedRows || []) {
    const key = [row.length, row.width, row.height, row.weight].join("|");
    const existing = groups.get(key);
    if (existing) existing.quantity = String(Number(existing.quantity) + (row.quantity || 1));
    else groups.set(key, { id: rowKey(), length: text(row.length), width: text(row.width), height: text(row.height),
      weight: text(row.weight), quantity: String(row.quantity || 1), note: "" });
  }
  return [...groups.values()];
}

export function stateFromActual(actual) {
  const rows = rowsFromActual(actual);
  return {
    rows: rows.length ? rows : [emptyRow()],
    measured_on: actual?.measured_on || "",
    source: actual?.source || "warehouse",
    billed_weight: text(actual?.billed_weight),
    billed_class: actual?.billed_class || "",
    notes: actual?.notes || "",
  };
}

const isBlank = (row) => FIELDS.every(({ key }) => key === "quantity" || !String(row[key]).trim()) && !row.note.trim();

function fieldError(key, value) {
  const raw = String(value).trim();
  if (!raw) return "Required";
  const number = Number(raw);
  if (!Number.isFinite(number)) return "Number";
  const field = FIELDS.find((entry) => entry.key === key);
  if (key === "quantity") {
    if (!Number.isInteger(number) || number < 1) return "Whole number, 1 or more";
    if (number > field.max) return `At most ${field.max}`;
    return "";
  }
  if (number <= 0) return "More than 0";
  if (number > field.max) return `At most ${field.max.toLocaleString()}`;
  return "";
}

// Validate the editor state: {errors: {rowId: {field: msg}, billed_weight, form}, payload | null}.
export function validateActuals(state) {
  const errors = { rows: {} };
  const rows = state.rows.filter((row) => !isBlank(row));
  for (const row of rows) {
    const rowErrors = {};
    for (const { key } of FIELDS) {
      const message = fieldError(key, row[key]);
      if (message) rowErrors[key] = message;
    }
    if (Object.keys(rowErrors).length) errors.rows[row.id] = rowErrors;
  }
  if (rows.length > MAX_ROWS) errors.form = `At most ${MAX_ROWS} pallet rows.`;
  const billedText = String(state.billed_weight).trim();
  if (billedText) {
    const billed = Number(billedText);
    if (!Number.isFinite(billed) || billed <= 0) errors.billed_weight = "Enter a weight above 0";
    else if (billed > 100000) errors.billed_weight = "At most 100,000 lb";
  }
  if (state.billed_class.length > 10) errors.billed_class = "Up to 10 characters";
  if (!rows.length && !billedText) errors.form = "Enter at least one pallet or the billed weight.";
  const valid = !Object.keys(errors.rows).length && !errors.form && !errors.billed_weight && !errors.billed_class;
  const payload = valid ? {
    pallets: rows.map((row) => ({
      length: Number(row.length), width: Number(row.width), height: Number(row.height), weight: Number(row.weight),
      quantity: Number(row.quantity), note: row.note.trim(),
    })),
    measured_on: state.measured_on || null,
    source: state.source,
    billed_weight: billedText ? Number(billedText) : null,
    billed_class: state.billed_class.trim(),
    notes: state.notes.trim(),
  } : null;
  return { errors, payload, count: rows.length };
}

function totals(rows) {
  let pallets = 0;
  let total = 0;
  for (const row of rows) {
    const quantity = Number(row.quantity);
    const each = Number(row.weight);
    if (!Number.isFinite(quantity) || !Number.isFinite(each) || isBlank(row)) continue;
    pallets += quantity;
    total += each * quantity;
  }
  return { pallets, total: Math.round(total * 10) / 10 };
}

// Actual pallets and billed weight for a shipment → PUT /freight-shipments/{id}/actual (onSave), DELETE (onClear).
export default function ActualsEditor({ actual, quoted, locked = false, busy = false, onSave, onClear, version }) {
  const [state, setState] = useState(() => stateFromActual(actual));
  const [touched, setTouched] = useState(false);
  const [showErrors, setShowErrors] = useState(false);
  const gridRef = useRef(null);
  const focusNext = useRef(null);

  useEffect(() => { setState(stateFromActual(actual)); setTouched(false); setShowErrors(false); }, [version]);
  useEffect(() => {
    if (!focusNext.current || !gridRef.current) return;
    const { rowId, field } = focusNext.current;
    focusNext.current = null;
    gridRef.current.querySelector(`[data-row="${rowId}"][data-field="${field}"]`)?.focus();
  });

  const result = useMemo(() => validateActuals(state), [state]);
  const sum = useMemo(() => totals(state.rows), [state.rows]);
  const update = (patch) => { setState((current) => ({ ...current, ...patch })); setTouched(true); };
  const setRow = (id, key, value) => update({ rows: state.rows.map((row) => (row.id === id ? { ...row, [key]: value } : row)) });
  const addRow = (focus = true) => {
    if (state.rows.length >= MAX_ROWS) return;
    const row = emptyRow();
    update({ rows: [...state.rows, row] });
    if (focus) focusNext.current = { rowId: row.id, field: "length" };
  };
  const removeRow = (id) => {
    const rows = state.rows.filter((row) => row.id !== id);
    update({ rows: rows.length ? rows : [emptyRow()] });
  };
  const copyQuoted = () => {
    const filled = state.rows.filter((row) => !isBlank(row));
    if (filled.length && !window.confirm("Replace the pallets you entered with the quoted pallets?")) return;
    const rows = rowsFromQuoted(quoted?.rows);
    update({ rows });
    if (rows[0]) focusNext.current = { rowId: rows[0].id, field: "weight" };
  };
  const onKeyDown = (event, rowIndex, field) => {
    if (event.key !== "Enter" || event.nativeEvent?.isComposing) return;
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) { save(); return; }
    const next = state.rows[rowIndex + 1];
    if (next) gridRef.current?.querySelector(`[data-row="${next.id}"][data-field="${field}"]`)?.focus();
    else addRow();
  };
  const save = () => {
    setShowErrors(true);
    if (!result.payload) return;
    onSave?.(result.payload);
  };
  const rowsShown = state.rows;
  const errorsVisible = showErrors;
  // Before a save attempt only show format problems in fields that have something typed.
  const visibleRowErrors = (row) => {
    const all = result.errors.rows[row.id] || {};
    if (showErrors) return all;
    return Object.fromEntries(Object.entries(all).filter(([key]) => String(row[key]).trim()));
  };
  const quotedWeight = quoted?.total_weight || null;
  const diff = quotedWeight && sum.total ? Math.round((sum.total - quotedWeight) * 10) / 10 : null;

  if (locked) {
    return (
      <section className="card" aria-labelledby="actuals-title">
        <h2 className="card-title" id="actuals-title">Actual measurements</h2>
        <p className="card-description"><Lock size={13} aria-hidden="true" /> This shipment is locked. Reopen it to change the measurements.</p>
      </section>
    );
  }

  return (
    <section className="card actuals-card" aria-labelledby="actuals-title">
      <header className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="actuals-title">Actual measurements</h2>
          <p className="card-description">What really shipped: measured pallets and the carrier's billed weight. Enter moves to the next pallet and adds one at the end.</p>
        </div>
        <div className="card-actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={copyQuoted} disabled={!quoted?.rows?.length}
            title={quoted?.rows?.length ? `Copy ${quoted.number}'s pallets, then correct them` : "The quote has no pallet rows"}><Copy size={14} />Start from quoted pallets</button>
        </div>
      </header>

      <div className="actuals-grid" ref={gridRef} role="group" aria-label="Actual pallets">
        {rowsShown.length > 0 && <div className="actuals-row actuals-head" aria-hidden="true">
          <span>#</span><span>L (in)</span><span>W (in)</span><span>H (in)</span><span>Weight (lb)</span><span>Qty</span><span>Note</span><span />
        </div>}
        {rowsShown.map((row, index) => {
          const rowErrors = visibleRowErrors(row);
          return (
            <div key={row.id} className="actuals-row">
              <span className="actuals-index">{index + 1}</span>
              {FIELDS.map(({ key, label }) => (
                <label key={key} className={`actuals-cell ${rowErrors[key] ? "has-error" : ""}`}>
                  <span className="actuals-cell-label">{label}</span>
                  <input inputMode="decimal" value={row[key]} data-row={row.id} data-field={key}
                    aria-label={`Pallet ${index + 1} ${key}`} aria-invalid={rowErrors[key] ? "true" : undefined}
                    title={rowErrors[key] || undefined}
                    onChange={(event) => setRow(row.id, key, event.target.value)} onKeyDown={(event) => onKeyDown(event, index, key)} />
                  {rowErrors[key] && <small className="field-error">{rowErrors[key]}</small>}
                </label>
              ))}
              <label className="actuals-cell actuals-note">
                <span className="actuals-cell-label">Note</span>
                <input value={row.note} maxLength={200} data-row={row.id} data-field="note" aria-label={`Pallet ${index + 1} note`}
                  onChange={(event) => setRow(row.id, "note", event.target.value)} onKeyDown={(event) => onKeyDown(event, index, "note")} />
              </label>
              <button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove pallet ${index + 1}`} onClick={() => removeRow(row.id)}><X size={15} /></button>
            </div>
          );
        })}
        <div className="actuals-foot">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => addRow()} disabled={state.rows.length >= MAX_ROWS}><Plus size={14} />Add pallet</button>
          <span className="actuals-sum" aria-live="polite">
            {sum.pallets ? <>{sum.pallets} pallet{sum.pallets === 1 ? "" : "s"} · <b>{weight(sum.total)}</b>{diff !== null && <span className={`variance ${diff > 0 ? "is-bad" : ""}`}> {signedWeight(diff)} vs quote</span>}</> : <span className="muted">No pallets entered</span>}
          </span>
        </div>
      </div>

      <div className="form-grid form-grid-wide actuals-meta">
        <Field label="Measured on"><input type="date" value={state.measured_on} onChange={(event) => update({ measured_on: event.target.value })} /></Field>
        <Field label="Source"><select value={state.source} onChange={(event) => update({ source: event.target.value })}>
          {Object.entries(ACTUAL_SOURCES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        <Field label="Billed weight (lb)" error={result.errors.billed_weight || ""} hint="From the carrier bill or reweigh">
          <input inputMode="decimal" value={state.billed_weight} onChange={(event) => update({ billed_weight: event.target.value })} /></Field>
        <Field label="Billed class" error={result.errors.billed_class || ""}>
          <input value={state.billed_class} maxLength={10} list="freight-classes" onChange={(event) => update({ billed_class: event.target.value })} /></Field>
        <Field label="Notes" wide><input value={state.notes} maxLength={1000} onChange={(event) => update({ notes: event.target.value })} placeholder="e.g. reweighed at terminal" /></Field>
      </div>
      <datalist id="freight-classes">{["50", "55", "60", "65", "70", "77.5", "85", "92.5", "100", "110", "125", "150", "175", "200", "250", "300", "400", "500"].map((value) => <option key={value} value={value} />)}</datalist>

      {errorsVisible && result.errors.form && <p className="field-error" role="alert">{result.errors.form}</p>}
      {errorsVisible && Object.keys(result.errors.rows).length > 0 && <p className="field-error" role="alert">Check the highlighted pallet fields.</p>}
      <div className="card-actions-row">
        <button type="button" className="btn btn-primary" disabled={busy || !touched} onClick={save}><Save size={15} />Save measurements</button>
        {actual && <button type="button" className="btn btn-ghost btn-ghost-danger" disabled={busy}
          onClick={() => { if (window.confirm("Clear the actual measurements for this shipment?")) onClear?.(); }}><Trash2 size={15} />Clear</button>}
        {touched && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => { setState(stateFromActual(actual)); setTouched(false); setShowErrors(false); }}>Discard changes</button>}
        {!actual && !touched && <small className="muted"><Ruler size={13} aria-hidden="true" /> Nothing recorded yet.</small>}
      </div>
    </section>
  );
}
