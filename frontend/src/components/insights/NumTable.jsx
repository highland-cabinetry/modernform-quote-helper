import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

// Sortable table for figures: right-aligned tabular numbers, a sticky header and totals footer,
// and row windowing above `virtualizeAbove` rows so a few thousand shipments scroll smoothly.
// columns: [{ key, label, render(row), sort(row) | false, num, width, className, title }]
const OVERSCAN = 8;

function compare(left, right) {
  if (left === right) return 0;
  if (left === null || left === undefined || left === "") return 1;
  if (right === null || right === undefined || right === "") return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}

export default function NumTable({
  columns, rows, rowKey = (row) => row.id, initialSort = null, sort: controlledSort, onSortChange,
  footer, caption, empty, rowClassName, rowHeight = 46, maxHeight = "min(70vh, 720px)", virtualizeAbove = 120,
  minWidth, label,
}) {
  const [ownSort, setOwnSort] = useState(initialSort);
  const sort = controlledSort !== undefined ? controlledSort : ownSort;
  const setSort = onSortChange || setOwnSort;
  const sorted = useMemo(() => {
    const column = sort && columns.find((entry) => entry.key === sort.key);
    if (!column) return rows;
    const value = column.sort || ((row) => row[column.key]);
    const decorated = rows.map((row, index) => [value(row), index, row]);
    decorated.sort((a, b) => {
      const nullsLast = (a[0] === null || a[0] === undefined || a[0] === "") !== (b[0] === null || b[0] === undefined || b[0] === "");
      const order = compare(a[0], b[0]);
      if (nullsLast) return order; // blanks stay at the bottom in both directions
      return (sort.direction === "asc" ? order : -order) || a[1] - b[1];
    });
    return decorated.map((entry) => entry[2]);
  }, [rows, sort, columns]);

  const virtual = sorted.length > virtualizeAbove;
  const scroller = useRef(null);
  const [view, setView] = useState({ top: 0, height: 720 });
  const frame = useRef(0);
  const onScroll = useCallback(() => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const element = scroller.current;
      if (element) setView({ top: element.scrollTop, height: element.clientHeight || 720 });
    });
  }, []);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  useEffect(() => {
    if (virtual && scroller.current) setView({ top: scroller.current.scrollTop, height: scroller.current.clientHeight || 720 });
  }, [virtual, sorted.length]);

  let start = 0;
  let end = sorted.length;
  if (virtual) {
    start = Math.max(0, Math.floor(view.top / rowHeight) - OVERSCAN);
    end = Math.min(sorted.length, Math.ceil((view.top + view.height) / rowHeight) + OVERSCAN);
  }
  const visible = virtual ? sorted.slice(start, end) : sorted;

  if (!rows.length && empty) return empty;
  const cellClass = (column) => `${column.num ? "num" : ""} ${column.className || ""}`.trim() || undefined;

  return (
    <div ref={scroller} className={`ins-table-wrap ${virtual ? "is-virtual" : ""}`} onScroll={virtual ? onScroll : undefined}
      style={virtual ? { maxHeight } : undefined} tabIndex={virtual ? 0 : undefined} role={virtual ? "region" : undefined}
      aria-label={virtual ? (label || caption || "Table") : undefined}>
      <table className={`data-table ins-table ${virtual ? "is-fixed" : ""}`} aria-rowcount={virtual ? sorted.length + 1 : undefined}
        style={minWidth ? { minWidth } : undefined}>
        {caption && <caption className="sr-only">{caption}</caption>}
        {virtual && <colgroup>{columns.map((column) => <col key={column.key} style={column.width ? { width: column.width } : undefined} />)}</colgroup>}
        <thead>
          <tr aria-rowindex={virtual ? 1 : undefined}>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              const sortable = column.sort !== false;
              return (
                <th key={column.key} scope="col" className={cellClass(column)} title={column.title}
                  style={!virtual && column.width ? { width: column.width } : undefined}
                  aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}>
                  {sortable ? (
                    <button type="button" className="ins-sort" onClick={() => setSort(active
                      ? { key: column.key, direction: sort.direction === "asc" ? "desc" : "asc" }
                      : { key: column.key, direction: column.num ? "desc" : "asc" })}>
                      <span>{column.label}</span>
                      {active ? (sort.direction === "asc" ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />)
                        : <ArrowUpDown size={12} className="sort-idle" aria-hidden="true" />}
                    </button>
                  ) : column.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {virtual && start > 0 && <tr aria-hidden="true" className="ins-spacer"><td colSpan={columns.length} style={{ height: start * rowHeight }} /></tr>}
          {visible.map((row, index) => (
            <tr key={rowKey(row)} className={rowClassName?.(row)} aria-rowindex={virtual ? start + index + 2 : undefined}
              style={virtual ? { height: rowHeight } : undefined}>
              {columns.map((column) => {
                const content = column.render ? column.render(row) : row[column.key];
                return column.header
                  ? <th key={column.key} scope="row" className={cellClass(column)}>{content}</th>
                  : <td key={column.key} className={cellClass(column)}>{content}</td>;
              })}
            </tr>
          ))}
          {virtual && end < sorted.length && <tr aria-hidden="true" className="ins-spacer"><td colSpan={columns.length} style={{ height: (sorted.length - end) * rowHeight }} /></tr>}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}
