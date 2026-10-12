import { memo, useMemo, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, CircleDollarSign, Layers, Plus, ReceiptText, TrendingDown, Truck, Weight } from "lucide-react";
import { sendJson } from "../../api";
import { Badge, EmptyState, ErrorBlock, FilterChips, Loading, PageHeader, SearchInput, StatusBadge } from "../Common";
import { SHIPMENT_PURPOSES, date, money } from "../../lib/format";
import { href } from "../../lib/route";
import useResource, { useDebounced } from "../../lib/useResource";
import { useApp } from "../../shell/AppShell";
import OrderPicker from "./OrderPicker";
import { FLAG_LABELS, PURPOSE_TONE, percent, recordFlags, today } from "./model";
import "../../styles/shipments.css";

const PAGE = 150;
const FLAG_ICONS = { weight_over: Weight, pallets_changed: Layers, cost_over: CircleDollarSign, missing_cost: ReceiptText, loss: TrendingDown };

const STATUS_FILTERS = {
  open: (row) => !["closed", "cancelled"].includes(row.status),
  in_transit: (row) => row.status === "in_transit",
  delivered: (row) => row.status === "delivered",
  closed: (row) => row.status === "closed",
  missing_expense: (row) => ["in_transit", "delivered"].includes(row.status) && !row.expense_count,
  cancelled: (row) => row.status === "cancelled",
  all: () => true,
};

const SORTS = {
  created: { label: "Newest", value: (row) => row.created_at, direction: "desc" },
  number: { label: "Shipment", value: (row) => row.number, direction: "desc" },
  status: { label: "Status", value: (row) => ["planning", "booked", "in_transit", "delivered", "closed", "cancelled"].indexOf(row.status), direction: "asc" },
  pickup: { label: "Pickup date", value: (row) => row.pickup_date || row.expected_delivery_date, direction: "desc" },
  cost: { label: "Cost variance", value: (row) => (row.actual_cents != null && row.expected_cost_cents != null ? row.actual_cents - row.expected_cost_cents : null), direction: "desc" },
  margin: { label: "Margin", value: (row) => row.margin_percent, direction: "asc" },
};

function compare(a, b) {
  if (a === b) return 0;
  if (a === null || a === undefined || a === "") return 1;
  if (b === null || b === undefined || b === "") return -1;
  return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true });
}

function matches(row, term) {
  const haystack = [row.number, row.name, row.carrier, row.broker, row.customer, row.rep, row.quote_number, ...(row.orders || [])].join(" ").toLowerCase();
  return haystack.includes(term);
}

// Shipment list from GET /api/freight-records (one request; every money and weight figure is precomputed).
export default function ShipmentList() {
  const { route, go, company, multiCompany, notify } = useApp();
  const [search, setSearch] = useState(route.query.q || "");
  const [status, setStatus] = useState(STATUS_FILTERS[route.query.status] ? route.query.status : "open");
  const [purpose, setPurpose] = useState(route.query.purpose || "");
  const [sort, setSort] = useState({ key: "created", direction: "desc" });
  const [limit, setLimit] = useState(PAGE);
  const [creating, setCreating] = useState(false);
  const term = useDebounced(search.trim().toLowerCase());
  const companyQuery = multiCompany ? `company_id=${encodeURIComponent(company.id)}` : "";
  const { data, error, loading, reload } = useResource(`/api/freight-records${companyQuery ? `?${companyQuery}` : ""}`);
  // The server search also looks inside PRO/BOL numbers and notes; one request per typed search, not per row.
  const serverSearch = useResource(term.length >= 2 ? `/api/freight-shipments?${new URLSearchParams({ search: term, ...(multiCompany ? { company_id: company.id } : {}) })}` : null);
  const serverIds = useMemo(() => new Set((term.length >= 2 ? serverSearch.data || [] : []).map((row) => row.id)), [serverSearch.data, term]);

  const records = useMemo(() => (data || []).map((row) => ({ ...row, flags: recordFlags(row) })), [data]);
  const searched = useMemo(() => (term ? records.filter((row) => matches(row, term) || serverIds.has(row.id)) : records), [records, term, serverIds]);
  const scoped = useMemo(() => (purpose ? searched.filter((row) => row.purpose === purpose) : searched), [searched, purpose]);
  const counts = useMemo(() => Object.fromEntries(Object.entries(STATUS_FILTERS).map(([key, test]) => [key, scoped.filter(test).length])), [scoped]);
  const rows = useMemo(() => {
    const filtered = scoped.filter(STATUS_FILTERS[status]);
    const value = SORTS[sort.key].value;
    filtered.sort((a, b) => {
      const order = compare(value(a), value(b));
      const missing = value(a) == null || value(a) === "" || value(b) == null || value(b) === "";
      return missing || sort.direction === "asc" ? order : -order;
    });
    return filtered;
  }, [scoped, status, sort]);
  const shown = rows.slice(0, limit);
  const sortBy = (key) => setSort((current) => (current.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: SORTS[key].direction }));
  const header = (key, label, className = "") => {
    const active = sort.key === key;
    return (
      <th scope="col" className={className} aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}>
        <button type="button" className="sort-btn" onClick={() => sortBy(key)}>{label}
          {active ? (sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ArrowUpDown size={12} className="sort-idle" />}</button>
      </th>
    );
  };

  return (
    <div className="page shipments-page">
      <PageHeader title="Shipments" lede="Each physical load: its orders, status, what it was quoted at and what it really cost."
        actions={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={16} />New shipment</button>} />
      <section className="card card-flush">
        <div className="list-toolbar padded shipments-toolbar">
          <SearchInput value={search} onChange={(value) => { setSearch(value); setLimit(PAGE); }} placeholder="Search shipment, order, customer, carrier or PRO" label="Search shipments" />
          <FilterChips label="Shipment status" value={status} onChange={(value) => { setStatus(value); setLimit(PAGE); }} options={[
            ["open", "Open", counts.open], ["in_transit", "In transit", counts.in_transit], ["delivered", "Delivered", counts.delivered],
            ["closed", "Closed", counts.closed], ["missing_expense", "Needs cost", counts.missing_expense], ["all", "All", counts.all],
          ]} />
          <label className="toolbar-select">
            <span className="sr-only">Purpose</span>
            <select value={purpose} onChange={(event) => { setPurpose(event.target.value); setLimit(PAGE); }} aria-label="Purpose">
              <option value="">All purposes</option>
              {Object.entries(SHIPMENT_PURPOSES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <label className="toolbar-select mobile-sort">
            <span className="sr-only">Sort by</span>
            <select value={`${sort.key}:${sort.direction}`} aria-label="Sort by" onChange={(event) => { const [key, direction] = event.target.value.split(":"); setSort({ key, direction }); }}>
              {Object.entries(SORTS).flatMap(([key, entry]) => [["desc", "↓"], ["asc", "↑"]].map(([direction, arrow]) => <option key={`${key}:${direction}`} value={`${key}:${direction}`}>{entry.label} {arrow}</option>))}
            </select>
          </label>
        </div>
        {loading && !data && <Loading />}
        {error && <div className="padded"><ErrorBlock message={error} onRetry={reload} /></div>}
        {data && (rows.length ? (
          <div className="table-wrap shipments-table-wrap">
            <table className="data-table grid-table shipments-table">
              <caption className="sr-only">Shipments, {rows.length} shown</caption>
              <thead>
                <tr>
                  {header("number", "Shipment")}
                  {header("status", "Status")}
                  <th scope="col">Purpose</th>
                  <th scope="col">Orders</th>
                  <th scope="col">Carrier</th>
                  {header("pickup", "Pickup · delivery")}
                  {header("cost", "Expected → actual cost", "num")}
                  {header("margin", "Margin", "num")}
                  <th scope="col"><span className="sr-only">Flags</span></th>
                </tr>
              </thead>
              <tbody>{shown.map((row) => <ShipmentRow key={row.id} row={row} onOpen={() => go(`/shipments/${row.id}`)} />)}</tbody>
            </table>
          </div>
        ) : <EmptyState icon={Truck} title={records.length ? "No shipments match" : "No shipments yet"}
          action={records.length ? null : <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={16} />New shipment</button>}>
          {records.length ? "Try another status, purpose or search." : "A shipment is created when a customer approves a quote, or choose orders that travel together."}</EmptyState>)}
        {rows.length > limit && <div className="list-more padded">
          <span className="muted">Showing {limit.toLocaleString()} of {rows.length.toLocaleString()}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setLimit(limit + PAGE * 2)}>Show more</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLimit(rows.length)}>Show all</button>
        </div>}
      </section>
      <OrderPicker open={creating} title="New shipment" actionLabel="Create shipment" onClose={() => setCreating(false)}
        onPick={async (orderIds) => {
          try {
            const shipment = await sendJson("/api/freight-shipments", "POST", { company_id: company.id, order_ids: orderIds });
            notify(`Shipment ${shipment.number} created.`);
            go(`/shipments/${shipment.id}`);
          } catch (requestError) { notify(requestError.message, "danger"); }
        }} />
    </div>
  );
}

const ShipmentRow = memo(function ShipmentRow({ row, onOpen }) {
  const overdue = row.status === "in_transit" && row.expected_delivery_date && row.expected_delivery_date < today();
  const variance = row.actual_cents != null && row.expected_cost_cents != null ? row.actual_cents - row.expected_cost_cents : null;
  const link = href(`/shipments/${row.id}`);
  return (
    <tr className="is-clickable" onClick={(event) => { if (!event.target.closest("a, button")) onOpen(); }}>
      <td data-label="Shipment" className="ship-cell-main"><a className="row-link cell-stack" href={link}><b>{row.number}</b><small>{row.name}</small></a></td>
      <td data-label="Status"><StatusBadge kind="shipment" status={row.status} /></td>
      <td data-label="Purpose">{row.purpose && row.purpose !== "standard" ? <Badge tone={PURPOSE_TONE[row.purpose]}>{SHIPMENT_PURPOSES[row.purpose] || row.purpose}</Badge> : <span className="muted">Standard</span>}</td>
      <td data-label="Orders"><span className="cell-stack"><span>{row.orders.slice(0, 3).join(", ")}{row.orders.length > 3 ? ` +${row.orders.length - 3}` : ""}</span>{row.customer && <small>{row.customer}</small>}</span></td>
      <td data-label="Carrier"><span className="cell-stack"><span>{row.carrier || <span className="muted">—</span>}</span>{row.broker && <small>{row.broker}</small>}</span></td>
      <td data-label="Pickup · delivery"><span className="cell-stack"><span>{row.pickup_date ? date(row.pickup_date) : <span className="muted">not picked up</span>}</span>
        <small className={overdue ? "danger-text" : ""}>{row.delivery_date ? `delivered ${date(row.delivery_date)}` : row.expected_delivery_date ? `${overdue ? "overdue · " : ""}exp. ${date(row.expected_delivery_date)}` : ""}</small></span></td>
      <td data-label="Expected → actual" className="num"><span className="cell-stack cell-stack-end"><span>{money(row.expected_cost_cents)} → {row.actual_cents != null ? money(row.actual_cents) : <span className="muted">no bill</span>}</span>
        {variance != null && variance !== 0 && <small className={variance > 0 ? "danger-text" : "ok-text"}>{money(variance, { signed: true })}</small>}</span></td>
      <td data-label="Margin" className="num"><span className="cell-stack cell-stack-end"><span className={row.net_cents != null && row.net_cents < 0 ? "danger-text" : ""}>{percent(row.margin_percent)}{row.net_estimated && row.margin_percent != null ? <small className="muted"> est.</small> : null}</span>
        {row.net_cents != null && <small>{money(row.net_cents)} net</small>}</span></td>
      <td data-label="Flags" className="flag-cell">
        {row.flags.length > 0 && <span className="flag-icons">{row.flags.map((flag) => {
          const Icon = FLAG_ICONS[flag] || AlertTriangle;
          return <span key={flag} className={`flag-icon flag-${FLAG_LABELS[flag].tone}`} title={FLAG_LABELS[flag].label}><Icon size={14} aria-hidden="true" /><span className="sr-only">{FLAG_LABELS[flag].label}</span></span>;
        })}</span>}
      </td>
    </tr>
  );
});
