import { useEffect, useMemo, useRef, useState } from "react";
import { Boxes, ClipboardList, Plus, Truck, Upload, Weight } from "lucide-react";
import { sendJson } from "../api";
import { FEATURES } from "../edition";
import { Badge, DataTable, Drawer, EmptyState, ErrorBlock, Field, FilterChips, Loading, PageHeader, SearchInput, StatusBadge } from "../components/Common";
import { Dropzone, ImportResults, importFiles } from "../components/ImportPanel";
import OrderDrawer from "../components/OrderDrawer";
import QuoteDetail from "../components/QuoteDetail";
import { CustomerBadge } from "../components/QuoteBits";
import { href } from "../lib/route";
import { CUSTOMER_STATUS, date, plural, relative } from "../lib/format";
import useResource, { useDebounced } from "../lib/useResource";
import { useApp } from "../shell/AppShell";

const VIEWS = [
  ["open", "Current"],
  ["needs_review", "Needs review"],
  ["quoted", "Quoted, not shipped"],
  ["all", "All"],
];

export default function Orders() {
  const { route, go, company, multiCompany, notify } = useApp();
  const [search, setSearch] = useState(route.query.q || "");
  const [view, setView] = useState(route.query.status === "needs_review" ? "needs_review"
    : VIEWS.some(([key]) => key === route.query.view) ? route.query.view : "open");
  const [selected, setSelected] = useState(new Set());
  const [importOpen, setImportOpen] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [importing, setImporting] = useState(false);
  const [newOrder, setNewOrder] = useState(null);
  const openId = route.query.open || null;
  const quoteId = route.query.quote || null;
  const withQuote = (id) => { const { quote: _quote, ...rest } = route.query; go("/orders", id ? { ...rest, quote: id } : rest); };
  const term = useDebounced(search.trim());
  // Selections are of visible rows only: a new search starts a new selection.
  useEffect(() => { setSelected(new Set()); }, [term]);
  const query = new URLSearchParams({ limit: "2000", ...(multiCompany ? { company_id: company.id } : {}), ...(term ? { search: term } : {}) });
  const { data, error, loading, reload } = useResource(`/api/orders?${query}`);
  const orders = data || [];
  // Quote edits (each price typed, each answer) refresh the list once they settle, not on every change.
  const refreshTimer = useRef(null);
  const refreshSoon = () => { clearTimeout(refreshTimer.current); refreshTimer.current = setTimeout(() => reload({ quiet: true }), 800); };
  useEffect(() => () => clearTimeout(refreshTimer.current), []);

  const counts = useMemo(() => ({
    open: orders.filter((order) => !order.shipment && order.status !== "cancelled" && order.document_type !== "history").length,
    needs_review: orders.filter((order) => order.status === "needs_review").length,
    quoted: orders.filter((order) => order.quote_ids.length && !order.shipment && order.status !== "cancelled" && order.document_type !== "history").length,
    all: orders.length,
  }), [orders]);
  const rows = useMemo(() => orders.filter((order) => {
    if (view === "open") return !order.shipment && order.status !== "cancelled" && order.document_type !== "history";
    if (view === "needs_review") return order.status === "needs_review";
    if (view === "quoted") return order.quote_ids.length > 0 && !order.shipment && order.status !== "cancelled" && order.document_type !== "history";
    return true;
  }).map((order) => ({ ...order, label: order.order_number })), [orders, view]);

  const selectedOrders = orders.filter((order) => selected.has(order.id));
  const selection = [...selected].join(",");
  const runImport = async (files) => {
    if (!files.length) return;
    setImporting(true);
    try {
      const result = await importFiles(files, company.id);
      setImportResult(result);
      await reload({ quiet: true });
      if (result.summary.created || result.summary.updated) notify(`${plural(result.summary.created + result.summary.updated, "order")} imported.`);
    } catch (requestError) { notify(requestError.message, "danger"); }
    finally { setImporting(false); }
  };

  const createShipment = async () => {
    try {
      const shipment = await sendJson("/api/freight-shipments", "POST", { company_id: company.id, order_ids: [...selected] });
      notify(`Shipment ${shipment.number} created with ${plural(selected.size, "order")}.`);
      go(`/shipments/${shipment.id}`);
    } catch (requestError) { notify(requestError.message, "danger"); }
  };

  const saveNewOrder = async () => {
    try {
      const order = await sendJson("/api/orders", "POST", { company_id: company.id, order_number: newOrder.order_number.trim(),
        customer_name: newOrder.customer_name, ship_to: { postal_code: newOrder.zip }, lines: [] });
      notify(`Order ${order.order_number} added. Add its lines, or import its packing slip later.`);
      setNewOrder(null);
      await reload({ quiet: true });
      go("/orders", { open: order.id });
    } catch (requestError) { notify(requestError.message, "danger"); }
  };

  const columns = [
    { key: "order_number", label: "Order", render: (order) => <span className="cell-stack"><b>{order.order_number}</b>{order.references.length > 0 && <small>{order.references.join(", ")}</small>}</span> },
    { key: "customer_name", label: "Customer", render: (order) => order.customer_name || <span className="muted">—</span> },
    { key: "zip", label: "Ship-to", sort: (order) => order.ship_to?.postal_code, render: (order) => order.ship_to?.postal_code ? `${order.ship_to.city ? `${order.ship_to.city}, ${order.ship_to.state} ` : ""}${order.ship_to.postal_code}` : <span className="muted">—</span> },
    { key: "unit_count", label: "Units", align: "right" },
    { key: "packing_state", label: "Packing", render: (order) => order.packing_state === "assembled" ? "Assembled" : "Flatpack" },
    { key: "status", label: "Status", render: (order) => <span className="cell-stack">{order.document_type === "history" ? <Badge tone="neutral">From history</Badge> : <StatusBadge kind="order" status={order.status} />}{order.issues?.length > 0 && order.status === "needs_review" && <small className="warn-text">{order.issues[0]}</small>}</span> },
    { key: "quoted_count", label: "Quoted", align: "right", defaultDirection: "desc", sort: (order) => order.quoted_count ?? 0,
      render: (order) => order.quoted_count ? <span className="cell-stack num-stack" title={order.quote_numbers.join(", ")}><b>{order.quoted_count}×</b><small>{order.quote_numbers.at(-1)}</small></span>
        : order.quote_numbers.length ? <span className="cell-stack num-stack"><span className="muted">0</span><small>{plural(order.quote_numbers.length, "draft")}</small></span> : <span className="muted">—</span> },
    { key: "latest_customer_status", label: "Customer answer", sort: (order) => CUSTOMER_STATUS[order.latest_customer_status]?.label,
      render: (order) => order.latest_customer_status ? <CustomerBadge status={order.latest_customer_status} /> : <span className="muted">—</span> },
    { key: "shipment", label: "Shipment", sort: (order) => order.shipment?.number, render: (order) => order.shipment ? <a className="inline-link" href={href(`/shipments/${order.shipment.id}`)}>{order.shipment.number}</a> : <span className="muted">—</span> },
    { key: "updated_at", label: "Updated", defaultDirection: "desc", render: (order) => <span title={date(order.updated_at, { time: true })}>{relative(order.updated_at)}</span> },
  ].filter((column) => FEATURES.freightWorkflow || !["latest_customer_status", "shipment"].includes(column.key));

  return (
    <div className="page">
      <PageHeader title="Orders" lede={FEATURES.freightWorkflow ? "Every imported or referenced order. Select orders to quote them together, measure them, or group them into one shipment." : "Every imported or referenced order. Select orders to quote them together or measure them."}
        actions={<>
          <button type="button" className="btn btn-secondary" onClick={() => setNewOrder({ order_number: "", customer_name: "", zip: "" })}><Plus size={16} />Add by number</button>
          <button type="button" className="btn btn-primary" onClick={() => { setImportResult(null); setImportOpen(true); }}><Upload size={16} />Import documents</button>
        </>} />

      <section className="card card-flush">
        <div className="list-toolbar padded">
          <SearchInput value={search} onChange={setSearch} placeholder="Search order, reference, customer or SKU" />
          <FilterChips label="Order view" value={view} onChange={(value) => { setView(value); setSelected(new Set()); }}
            options={VIEWS.map(([key, label]) => [key, label, counts[key]])} />
        </div>
        {selected.size > 0 && <div className="bulk-bar" role="region" aria-label="Selected orders">
          <span><b>{plural(selected.size, "order")} selected</b>{selectedOrders.some((order) => order.shipment) && <small className="warn-text"> · some are already in a shipment</small>}</span>
          <a className="btn btn-primary btn-sm" href={href("/quote/new", { orders: selection })}><Boxes size={14} />Quote together</a>
          <a className="btn btn-secondary btn-sm" href={href("/quote/new", { mode: "measured", orders: selection })}><Weight size={14} />Measured quote</a>
          {FEATURES.freightWorkflow && <button type="button" className="btn btn-secondary btn-sm" disabled={selectedOrders.some((order) => order.shipment || order.status === "cancelled")} onClick={createShipment}><Truck size={14} />Create shipment</button>}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>Clear</button>
        </div>}
        {loading && !data && <Loading />}
        {error && <ErrorBlock message={error} onRetry={reload} />}
        {data && <DataTable columns={columns} rows={rows} selectable selected={selected} onSelectedChange={setSelected}
          onRowClick={(order) => go("/orders", { ...route.query, open: order.id })} caption="Orders"
          initialSort={{ key: "updated_at", direction: "desc" }}
          empty={<EmptyState icon={ClipboardList} title={search ? "No orders match that search" : "No orders here yet"}
            action={<button type="button" className="btn btn-primary" onClick={() => setImportOpen(true)}><Upload size={16} />Import documents</button>}>
            Import packing slips or invoices, or add an order by its number.</EmptyState>} />}
      </section>

      <OrderDrawer orderId={openId} notify={notify} onSaved={refreshSoon}
        openQuoteId={quoteId} onOpenQuote={withQuote}
        onClose={() => { const { open: _open, quote: _quote, ...rest } = route.query; go("/orders", rest); }} />
      {/* #/orders?quote=<id> without an order: the quote drawer on its own. */}
      {!openId && quoteId && <QuoteDetail quoteId={quoteId} notify={notify} onClose={() => withQuote(null)}
        onChanged={refreshSoon} />}

      <Drawer open={importOpen} title="Import documents" subtitle={`Orders are added to ${company.name}. The same file is never imported twice.`}
        onClose={() => setImportOpen(false)} labelId="import-title"
        footer={importResult && <>
          <button type="button" className="btn btn-secondary" onClick={() => setImportResult(null)}>Import more</button>
          {importResult.orders.length > 0 && <a className="btn btn-primary" href={href("/quote/new", { orders: importResult.orders.filter((order) => order.status !== "cancelled").map((order) => order.id).join(",") })}><Boxes size={15} />Quote these orders</a>}
        </>}>
        {!importResult && <Dropzone busy={importing} onFiles={runImport} />}
        <ImportResults result={importResult} notify={notify} onChanged={() => reload({ quiet: true })} />
        {importResult?.orders?.length > 0 && <ul className="item-list compact">
          {importResult.orders.map((order) => <li key={order.id}><a className="item-link" href={href("/orders", { open: order.id })} onClick={() => setImportOpen(false)}>
            <span className="item-main"><b>{order.order_number}</b><small>{[order.customer_name, order.ship_to?.postal_code, plural(order.lines.reduce((sum, line) => sum + line.quantity, 0), "unit")].filter(Boolean).join(" · ")}</small></span>
            <span className="item-side"><StatusBadge kind="order" status={order.status} /></span></a></li>)}
        </ul>}
      </Drawer>

      <Drawer open={Boolean(newOrder)} title="Add an order by number" subtitle="Use this when a quote or shipment needs an order whose documents aren't imported yet." onClose={() => setNewOrder(null)} labelId="new-order-title"
        footer={<button type="button" className="btn btn-primary" disabled={!newOrder?.order_number.trim()} onClick={saveNewOrder}><Plus size={15} />Add order</button>}>
        {newOrder && <div className="form-grid drawer-form">
          <Field label="Order number" hint={`Bare numbers are stored as ${company.config.bare_number_prefix || ""}12345.`}><input autoFocus value={newOrder.order_number} onChange={(event) => setNewOrder({ ...newOrder, order_number: event.target.value })} /></Field>
          <Field label="Customer"><input value={newOrder.customer_name} onChange={(event) => setNewOrder({ ...newOrder, customer_name: event.target.value })} /></Field>
          <Field label="Ship-to ZIP"><input inputMode="numeric" value={newOrder.zip} onChange={(event) => setNewOrder({ ...newOrder, zip: event.target.value })} /></Field>
        </div>}
        {multiCompany && <p className="hint">Company: <Badge>{company.code}</Badge></p>}
      </Drawer>
    </div>
  );
}
