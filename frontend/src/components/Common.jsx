import { useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, Inbox, LoaderCircle, RefreshCw, Search, X } from "lucide-react";
import useModalFocus from "../useModalFocus";
import { ORDER_STATUS, QUOTE_STATUS, SHIPMENT_STATUS } from "../lib/format";

export function PageHeader({ title, lede, actions, eyebrow, children }) {
  return (
    <header className="page-head">
      <div className="page-head-text">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {lede && <p className="page-lede">{lede}</p>}
        {children}
      </div>
      {actions && <div className="page-head-actions">{actions}</div>}
    </header>
  );
}

export function Badge({ tone = "neutral", children, title }) {
  return <span className={`badge badge-${tone}`} title={title}>{children}</span>;
}

const STATUS_MAPS = { order: ORDER_STATUS, quote: QUOTE_STATUS, shipment: SHIPMENT_STATUS };

export function StatusBadge({ kind, status }) {
  const entry = STATUS_MAPS[kind]?.[status] || { label: status, tone: "neutral" };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

export function EmptyState({ icon: Icon = Inbox, title, children, action }) {
  return (
    <div className="empty-state">
      <Icon size={28} aria-hidden="true" />
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading…" }) {
  return <div className="loading-block" role="status"><LoaderCircle className="spin" size={18} />{label}</div>;
}

export function ErrorBlock({ message, onRetry }) {
  return (
    <div className="callout callout-danger" role="alert">
      <AlertTriangle size={18} />
      <span>{message}</span>
      {onRetry && <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}><RefreshCw size={14} />Try again</button>}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder = "Search", label, autoFocus = false }) {
  return (
    <label className="search-field">
      <Search size={16} aria-hidden="true" />
      <span className="sr-only">{label || placeholder}</span>
      <input type="search" value={value} autoFocus={autoFocus} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function FilterChips({ label, value, options, onChange }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([key, text, count]) => (
        <button key={key} type="button" aria-pressed={value === key} className={value === key ? "active" : ""} onClick={() => onChange(key)}>
          {text}{count !== undefined && <span className="seg-count">{count}</span>}
        </button>
      ))}
    </div>
  );
}

// Sortable, keyboard-friendly table. columns: [{ key, label, render(row), sort(row), align, width, className }]
export function DataTable({
  columns, rows, rowKey = (row) => row.id, onRowClick, empty, initialSort, selectable = false,
  selected, onSelectedChange, caption, rowClassName, dense = false, maxHeight,
}) {
  const [sort, setSort] = useState(initialSort || null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column) return rows;
    const value = column.sort || ((row) => row[column.key]);
    const copy = [...rows];
    copy.sort((a, b) => {
      const left = value(a);
      const right = value(b);
      if (left === right) return 0;
      if (left === null || left === undefined || left === "") return 1;
      if (right === null || right === undefined || right === "") return -1;
      const order = typeof left === "number" && typeof right === "number" ? left - right : String(left).localeCompare(String(right), undefined, { numeric: true });
      return sort.direction === "asc" ? order : -order;
    });
    return copy;
  }, [rows, sort, columns]);

  const selectedSet = selected instanceof Set ? selected : new Set(selected || []);
  const allSelected = selectable && rows.length > 0 && rows.every((row) => selectedSet.has(rowKey(row)));
  const toggle = (key) => {
    const next = new Set(selectedSet);
    if (next.has(key)) next.delete(key); else next.add(key);
    onSelectedChange?.(next);
  };

  if (!rows.length && empty) return empty;
  return (
    <div className={`table-wrap ${maxHeight ? "table-scroll" : ""}`} style={maxHeight ? { maxHeight } : undefined}>
      <table className={`data-table grid-table ${dense ? "is-dense" : ""}`}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {selectable && <th scope="col" className="select-col">
              <input type="checkbox" aria-label="Select all rows" checked={allSelected}
                onChange={() => onSelectedChange?.(allSelected ? new Set() : new Set(rows.map(rowKey)))} />
            </th>}
            {columns.map((column) => {
              const active = sort?.key === column.key;
              const sortable = column.sortable !== false;
              return (
                <th key={column.key} scope="col" className={`${column.align === "right" ? "num" : ""} ${column.className || ""}`}
                  style={column.width ? { width: column.width } : undefined}
                  aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}>
                  {sortable ? (
                    <button type="button" className="sort-btn" onClick={() => setSort(active && sort.direction === "asc"
                      ? { key: column.key, direction: "desc" } : { key: column.key, direction: active ? "asc" : (column.defaultDirection || "asc") })}>
                      {column.label}
                      {active ? (sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ArrowUpDown size={12} className="sort-idle" />}
                    </button>
                  ) : column.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const key = rowKey(row);
            return (
              <tr key={key} className={`${onRowClick ? "is-clickable" : ""} ${selectedSet.has(key) ? "is-selected" : ""} ${rowClassName?.(row) || ""}`}
                onClick={onRowClick ? (event) => { if (!event.target.closest("button, a, input, select, label")) onRowClick(row); } : undefined}>
                {selectable && <td className="select-col"><input type="checkbox" aria-label={`Select ${row.label || key}`} checked={selectedSet.has(key)} onChange={() => toggle(key)} /></td>}
                {columns.map((column, index) => {
                  const content = column.render ? column.render(row) : row[column.key];
                  const className = `${column.align === "right" ? "num" : ""} ${column.className || ""}`;
                  if (index === 0 && onRowClick && !column.plain) {
                    return <td key={column.key} className={className}>
                      <button type="button" className="row-link" onClick={() => onRowClick(row)}>{content}</button>
                    </td>;
                  }
                  return <td key={column.key} className={className}>{content}</td>;
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Right-hand panel for details and editing; traps focus and closes with Escape.
export function Drawer({ open, title, subtitle, onClose, children, footer, wide = false, labelId = "drawer-title" }) {
  const dialogRef = useRef(null);
  useModalFocus(open, onClose, dialogRef);
  if (!open) return null;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className={`drawer ${wide ? "drawer-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <header className="drawer-head">
          <div>
            <h2 id={labelId}>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button type="button" className="icon-btn" aria-label="Close panel" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="drawer-body">{children}</div>
        {footer && <footer className="drawer-foot">{footer}</footer>}
      </section>
    </div>
  );
}

export function Modal({ open, title, description, onClose, children, footer, tone }) {
  const dialogRef = useRef(null);
  useModalFocus(open, onClose, dialogRef);
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation">
      <section ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <header className="modal-head">
          {tone === "danger" && <span className="modal-icon"><AlertTriangle size={20} /></span>}
          <div>
            <h2 id="modal-title">{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot modal-actions">{footer}</footer>}
      </section>
    </div>
  );
}

export function Kpi({ label, value, hint, tone = "default", icon: Icon, href }) {
  const body = (
    <>
      <span className="kpi-label">{Icon && <Icon size={15} aria-hidden="true" />}{label}</span>
      <strong className="kpi-value">{value}</strong>
      {hint && <span className="kpi-hint">{hint}</span>}
    </>
  );
  return href ? <a className={`kpi kpi-${tone}`} href={href}>{body}</a> : <div className={`kpi kpi-${tone}`}>{body}</div>;
}

export function Field({ label, hint, children, wide = false, error }) {
  return (
    <label className={`field ${wide ? "field-wide" : ""} ${error ? "has-error" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && !error && <small className="field-hint">{hint}</small>}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

export function Timeline({ entries }) {
  if (!entries?.length) return <p className="muted">No changes recorded yet.</p>;
  return (
    <ol className="timeline">
      {[...entries].reverse().map((entry) => (
        <li key={entry.id || `${entry.at}-${entry.action}`}>
          <span className="timeline-dot" aria-hidden="true" />
          <div>
            <b>{entry.summary || entry.action.replaceAll("_", " ")}</b>
            <small>{new Date(entry.at).toLocaleString()} · {entry.action.replaceAll("_", " ")}</small>
          </div>
        </li>
      ))}
    </ol>
  );
}
