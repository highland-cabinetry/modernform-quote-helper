import { useMemo, useState } from "react";
import { Download, Truck, X } from "lucide-react";
import { Badge, EmptyState, ErrorBlock, FilterChips, Loading, SearchInput, StatusBadge } from "../Common";
import { CUSTOMER_STATUS, date, money, plural, SHIPMENT_PURPOSES } from "../../lib/format";
import { href } from "../../lib/route";
import { useDebounced } from "../../lib/useResource";
import NumTable from "./NumTable";
import RangeControl from "./RangeControl";
import { Margin, NetValue } from "./Reports";
import { companyQuery, count, GROUPS, groupLabel, inRange, percent, recordDay, recordGroupKey } from "./shared";

const ATTENTION_STATUSES = ["in_transit", "delivered", "closed"];
const isLoss = (row) => row.net_cents !== null && row.net_cents !== undefined && row.net_cents < 0;
const isOverQuote = (row) => row.cost_cents != null && row.expected_cost_cents != null && row.cost_cents > row.expected_cost_cents;
const isNoBill = (row) => !row.expense_count && ATTENTION_STATUSES.includes(row.status);

export const LEDGER_FILTERS = {
  all: { label: "All", test: (row) => row.status !== "cancelled" },
  loss: { label: "Losses", test: (row) => row.status !== "cancelled" && isLoss(row) },
  over: { label: "Cost over quote", test: (row) => row.status !== "cancelled" && isOverQuote(row) },
  nobill: { label: "No bill", test: (row) => isNoBill(row) },
  weight: { label: "Weight over", test: (row) => row.status !== "cancelled" && row.weight_over },
  cancelled: { label: "Cancelled", test: (row) => row.status === "cancelled" },
};

// Footer totals in whole dollars so seven-figure sums fit their column (exact value in the tooltip).
function wholeDollars(cents) {
  const value = Math.round(cents / 100);
  const text = `$${Math.abs(value).toLocaleString()}`;
  return value < 0 ? `−${text}` : text;
}

function weightText(value) {
  return !value ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// Shipments ledger: one row per shipment with quote vs actual cost, revenue, net and margin.
export default function Ledger({ records, error, loading, reload, company, multiCompany, range, excludeSister, filter, drill, onChange }) {
  const [search, setSearch] = useState("");
  const term = useDebounced(search.trim().toLowerCase(), 150);

  const scoped = useMemo(() => (records || []).filter((row) => inRange(recordDay(row), range)
    && (!excludeSister || row.purpose !== "sister")
    && (!drill.group || recordGroupKey(drill.group, row) === drill.key)), [records, range, excludeSister, drill.group, drill.key]);

  const counts = useMemo(() => Object.fromEntries(Object.entries(LEDGER_FILTERS).map(([key, entry]) => [key, scoped.filter(entry.test).length])), [scoped]);

  const rows = useMemo(() => {
    const test = (LEDGER_FILTERS[filter] || LEDGER_FILTERS.all).test;
    return scoped.filter(test).filter((row) => !term || [row.number, row.name, row.customer, row.rep, row.carrier, row.broker, row.quote_number, SHIPMENT_PURPOSES[row.purpose], ...(row.orders || [])]
      .some((value) => String(value || "").toLowerCase().includes(term)));
  }, [scoped, filter, term]);

  const totals = useMemo(() => {
    const sum = (key) => rows.reduce((total, row) => total + (row[key] || 0), 0);
    const revenue = sum("revenue_cents");
    const net = sum("net_cents");
    return {
      shipments: rows.length, expected: sum("expected_cost_cents"), actual: sum("cost_cents"), revenue, net,
      margin: revenue ? Math.round((net / revenue) * 1000) / 10 : null,
      estimated: rows.filter((row) => row.net_estimated).length,
    };
  }, [rows]);

  // Money first so the figures are on screen without scrolling sideways; descriptive columns follow.
  const columns = useMemo(() => [
    { key: "number", label: "Shipment", width: 148, sort: (row) => row.number, render: (row) => (
      <span className="cell-stack ins-cell">
        <a className="ins-row-link" href={href(`/shipments/${row.id}`)}>{row.number}</a>
        <small title={(row.orders || []).join(", ")}>{(row.orders || []).join(", ") || row.name}</small>
      </span>) },
    { key: "day", label: "Date", width: 104, sort: recordDay, render: (row) => date(recordDay(row)) },
    { key: "customer", label: "Customer / rep", width: 176, render: (row) => (
      <span className="cell-stack ins-cell">
        <span title={row.customer}>{row.customer || <span className="muted">—</span>}</span>
        {row.rep && <small>{row.rep}</small>}
      </span>) },
    { key: "expected_cost_cents", label: "Quoted cost", num: true, width: 114, render: (row) => money(row.expected_cost_cents) },
    { key: "cost_cents", label: "Actual cost", num: true, width: 114, render: (row) => row.cost_cents != null ? money(row.cost_cents)
      : (ATTENTION_STATUSES.includes(row.status) ? <span className="warn-text">no bill</span> : <span className="muted">—</span>) },
    { key: "variance", label: "Variance", num: true, width: 98, sort: (row) => (row.cost_cents != null && row.expected_cost_cents != null ? row.cost_cents - row.expected_cost_cents : null),
      render: (row) => {
        if (row.cost_cents == null || row.expected_cost_cents == null) return <span className="muted">—</span>;
        const variance = row.cost_cents - row.expected_cost_cents;
        return <span className={variance > 0 ? "danger-text" : variance < 0 ? "ok-text" : ""}>{money(variance, { signed: true })}</span>;
      } },
    { key: "revenue_cents", label: "Revenue", num: true, width: 112, render: (row) => money(row.revenue_cents) },
    { key: "net_cents", label: "Net", num: true, width: 112, render: (row) => (
      <span className="cell-stack ins-cell ins-cell-num"><NetValue cents={row.net_cents} />{row.net_estimated && <small title="No carrier bill yet: uses the quoted cost">estimated</small>}</span>) },
    { key: "margin_percent", label: "Margin", num: true, width: 78, render: (row) => <Margin value={row.margin_percent} /> },
    { key: "weight", label: "Weight (lb)", num: true, width: 138, sort: (row) => row.actual_weight ?? row.weight, title: "Quoted → actual",
      render: (row) => (
        <span className="ins-weight">
          {row.weight_over && <Badge tone="danger" title="Actual weight is more than 5% or 100 lb over the quote">over</Badge>}
          <span>{weightText(row.weight)}{row.actual_weight != null && <> → <b className={row.weight_over ? "danger-text" : ""}>{weightText(row.actual_weight)}</b></>}</span>
        </span>) },
    { key: "status", label: "Status", width: 132, render: (row) => (
      <span className="cell-stack ins-cell">
        <StatusBadge kind="shipment" status={row.status} />
        {row.purpose && row.purpose !== "standard" && <small>{SHIPMENT_PURPOSES[row.purpose] || row.purpose}</small>}
      </span>) },
    { key: "carrier", label: "Carrier", width: 150, sort: (row) => row.carrier || row.broker, render: (row) => (
      <span className="cell-stack ins-cell"><span>{row.carrier || <span className="muted">—</span>}</span>{row.broker && <small>via {row.broker}</small>}</span>) },
    { key: "quote_number", label: "Quote", width: 124, render: (row) => row.quote_number ? (
      <span className="cell-stack ins-cell"><span>{row.quote_number}</span>
        {row.quote_status && <small className={`tone-text-${CUSTOMER_STATUS[row.quote_status]?.tone || "neutral"}`}>{CUSTOMER_STATUS[row.quote_status]?.label || row.quote_status}</small>}
      </span>) : <span className="muted">—</span> },
  ], []);

  const footer = rows.length > 0 && (
    <tr className="ins-total">
      <th scope="row" colSpan={3}>{plural(totals.shipments, "shipment")}{totals.estimated ? ` · ${totals.estimated} with estimated net` : ""}</th>
      <td className="num" title={money(totals.expected)}>{wholeDollars(totals.expected)}</td>
      <td className="num" title={money(totals.actual)}>{wholeDollars(totals.actual)}</td>
      <td />
      <td className="num" title={money(totals.revenue)}>{wholeDollars(totals.revenue)}</td>
      <td className="num" title={money(totals.net)}><span className={totals.net < 0 ? "danger-text" : ""}>{wholeDollars(totals.net)}</span></td>
      <td className="num">{percent(totals.margin)}</td>
      <td colSpan={4} />
    </tr>
  );

  const drillLabel = drill.group ? `${GROUPS.find(([key]) => key === drill.group)?.[1] || drill.group}: ${groupLabel(drill.group, drill.key)}` : "";
  const csv = `/api/freight-records/export.csv${companyQuery(company, multiCompany)}`;

  return (
    <div className="ins-tab-body">
      <div className="list-toolbar ins-toolbar">
        <div className="ins-toolbar-group">
          <SearchInput value={search} onChange={setSearch} placeholder="Shipment, order, customer, carrier" label="Search shipments" />
          <RangeControl value={range} onChange={onChange} idPrefix="ledger-range" />
          <label className="ins-toggle">
            <input type="checkbox" checked={excludeSister} onChange={(event) => onChange({ sister: event.target.checked ? "exclude" : "" })} />
            <span>Exclude sister company</span>
          </label>
        </div>
        <a className="btn btn-secondary btn-sm" href={csv} download><Download size={14} />CSV (all shipments)</a>
      </div>
      <div className="ins-filter-row">
        <FilterChips label="Show" value={filter} onChange={(value) => onChange({ filter: value === "all" ? "" : value })}
          options={Object.entries(LEDGER_FILTERS).map(([key, entry]) => [key, entry.label, counts[key]])} />
        {drill.group && (
          <span className="ins-chip">
            {drillLabel}
            <button type="button" className="icon-btn" aria-label={`Remove filter ${drillLabel}`} onClick={() => onChange({ dg: "", dk: "" })}><X size={14} /></button>
          </span>
        )}
      </div>
      {loading && !records && <Loading label="Loading shipments…" />}
      {error && <ErrorBlock message={error} onRetry={reload} />}
      {records && (
        <section className="card card-flush">
          <NumTable columns={columns} rows={rows} footer={footer} initialSort={{ key: "day", direction: "desc" }} rowHeight={52}
            caption="Shipments ledger" label="Shipments ledger" minWidth={1600} virtualizeAbove={80} maxHeight="calc(100vh - 300px)"
            rowClassName={(row) => (isLoss(row) ? "is-loss" : undefined)}
            empty={<EmptyState icon={Truck} title={records.length ? "No shipments match" : "No shipments yet"}>
              {records.length ? "Change the period, filter or search." : "Shipments appear here once a quote is approved or orders are grouped."}</EmptyState>} />
        </section>
      )}
      {records && <p className="hint ins-footnote">{count(rows.length)} of {count(records.length)} shipments. Net uses the quoted carrier cost until a bill is recorded. The CSV has every shipment.</p>}
    </div>
  );
}
