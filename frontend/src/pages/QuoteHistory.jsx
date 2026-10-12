import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Boxes, Columns3, ExternalLink, History, Save, Truck, Weight, XCircle } from "lucide-react";
import { getJson, sendJson } from "../api";
import { FEATURES } from "../edition";
import { Badge, DataTable, EmptyState, ErrorBlock, Field, FilterChips, Loading, Modal, PageHeader, SearchInput, StatusBadge, Timeline } from "../components/Common";
import QuoteDetail from "../components/QuoteDetail";
import { CustomerBadge, QUOTE_KINDS, TermsChips, selectedOption, sourceName, termsLabels } from "../components/QuoteBits";
import { CUSTOMER_STATUS, QUOTE_STATUS, centsToInput, date, money, parseMoney, plural, relative, weight } from "../lib/format";
import { href } from "../lib/route";
import useResource, { useDebounced } from "../lib/useResource";
import { useApp } from "../shell/AppShell";

const orderLabel = (quote) => quote.orders.map((order) => order.order_number).join(", ") || quote.name;

export default function QuoteHistory() {
  const { route } = useApp();
  return route.parts[1] ? <QuoteRecord quoteId={route.parts[1]} /> : <QuoteList />;
}

// Customer-answer filter: All, Not sent, Sent, Approved, Declined/Expired.
const ANSWER_FILTERS = [
  ["all", "All", () => true],
  ["not_sent", "Not sent", (quote) => quote.customer_status === "not_sent"],
  ["sent", "Sent", (quote) => quote.customer_status === "sent"],
  ["approved", "Approved", (quote) => quote.customer_status === "approved"],
  ["closed", "Declined/Expired", (quote) => ["declined", "expired"].includes(quote.customer_status)],
];
const RECORD_FILTERS = [["current", "Current records"], ["superseded", "Superseded"], ["void", "Void"], ["all", "All records"]];

function QuoteList() {
  const { route, go, company, multiCompany, notify } = useApp();
  const [search, setSearch] = useState(route.query.q || "");
  const [status, setStatus] = useState(RECORD_FILTERS.some(([key]) => key === route.query.status) ? route.query.status : "current");
  // ?answer=<filter>, or ?customer_status=<status> (links from the Dashboard).
  const linkedAnswer = route.query.answer || ({ declined: "closed", expired: "closed" }[route.query.customer_status] || route.query.customer_status);
  const [answer, setAnswer] = useState(ANSWER_FILTERS.some(([key]) => key === linkedAnswer) ? linkedAnswer : "all");
  const [kind, setKind] = useState("all");
  const openQuote = route.query.quote || null;
  const showQuote = (id) => { const { quote: _quote, ...rest } = route.query; go("/quotes", id ? { ...rest, quote: id } : rest); };
  const [selected, setSelected] = useState(new Set());
  const [comparing, setComparing] = useState(false);
  const term = useDebounced(search.trim());
  // Selections are of visible rows only: a new search starts a new selection.
  useEffect(() => { setSelected(new Set()); }, [term]);
  const params = new URLSearchParams({ limit: "2000", ...(multiCompany ? { company_id: company.id } : {}), ...(term ? { search: term } : {}) });
  const { data, setData, error, loading, reload } = useResource(`/api/quotes?${params}`);
  const quotes = data || [];
  const recordRows = useMemo(() => quotes.filter((quote) => {
    if (status === "current" && !["final", "draft"].includes(quote.status)) return false;
    if (status !== "current" && status !== "all" && quote.status !== status) return false;
    if (kind !== "all" && quote.kind !== kind) return false;
    return true;
  }), [quotes, status, kind]);
  const answerCounts = useMemo(() => Object.fromEntries(ANSWER_FILTERS.map(([key, , test]) => [key, recordRows.filter(test).length])), [recordRows]);
  const rows = useMemo(() => {
    const test = ANSWER_FILTERS.find(([key]) => key === answer)[2];
    return recordRows.filter(test).map((quote) => ({ ...quote, label: quote.number, used: selectedOption(quote) }));
  }, [recordRows, answer]);

  const columns = [
    { key: "number", label: "Quote", className: "qh-nowrap", render: (quote) => <span className="cell-stack"><b>{quote.number}</b><small>{QUOTE_KINDS[quote.kind] || quote.kind}{quote.role === "combined" ? " · combined" : ""}{quote.legacy ? " · from history" : ""}{quote.status !== "final" ? ` · ${QUOTE_STATUS[quote.status]?.label || quote.status}` : ""}</small></span> },
    { key: "orders", label: "Orders", sort: orderLabel, render: (quote) => <span className="cell-stack"><span>{orderLabel(quote)}</span>{quote.shipment_number && <small><a className="inline-link" href={href(`/shipments/${quote.freight_shipment_id}`)}>{quote.shipment_number}</a></small>}</span> },
    { key: "customer_status", label: "Customer", sort: (quote) => CUSTOMER_STATUS[quote.customer_status]?.label, render: (quote) => <span className="cell-stack"><CustomerBadge status={quote.customer_status} />{quote.sent_at && quote.customer_status === "sent" && <small>sent {relative(quote.sent_at)}</small>}</span> },
    { key: "terms", label: "Delivery terms", sortable: false, render: (quote) => <span className="qh-terms"><TermsChips labels={termsLabels(quote.terms)} empty="—" /></span> },
    { key: "zip", label: "ZIP", sort: (quote) => quote.terms?.postal_code, render: (quote) => quote.terms?.postal_code || <span className="muted">—</span> },
    { key: "used", label: "Used price", align: "right", sort: (quote) => quote.used?.price_cents ?? quote.cost.amount_cents,
      render: (quote) => quote.used ? <span className="qh-used"><b>{money(quote.used.price_cents)}</b><small>{[sourceName(quote.used), quote.used.carrier].filter(Boolean).join(" · ")}</small></span>
        : quote.cost.amount_cents != null ? <span className="qh-used"><b>{money(quote.cost.amount_cents)}</b><small>{quote.cost.carrier || "entered by hand"}</small></span>
          : <span className="muted">{quote.options?.length ? plural(quote.options.length, "price") : "—"}</span> },
    { key: "customer_price", label: "Customer price", align: "right", sort: (quote) => quote.cost.customer_price_cents, render: (quote) => quote.cost.customer_price_cents != null ? <b>{money(quote.cost.customer_price_cents)}</b> : <span className="muted">—</span> },
    { key: "pallets", label: "Freight", align: "right", sort: (quote) => quote.summary.total_weight, render: (quote) => <span className="cell-stack num-stack"><span>{plural(quote.summary.pallets, "pallet")}</span><small>{weight(quote.summary.total_weight)}</small></span> },
    { key: "created_at", label: "Created", defaultDirection: "desc", render: (quote) => <span title={date(quote.created_at, { time: true })}>{relative(quote.created_at)}</span> },
  ];
  const compareQuotes = quotes.filter((quote) => selected.has(quote.id));

  return (
    <div className="page">
      <PageHeader title="Quote History" lede="Every time an order was quoted: the delivery terms, the broker price used, the customer price and the customer's answer."
        actions={<a className="btn btn-primary" href={href("/quote/new")}><Boxes size={16} />New quote</a>} />
      <section className="card card-flush">
        <div className="list-toolbar padded">
          <SearchInput value={search} onChange={setSearch} placeholder="Search quote number, order or name" />
          <div className="toolbar-group qh-filters">
            <FilterChips label="Customer answer" value={answer} onChange={(value) => { setAnswer(value); setSelected(new Set()); }}
              options={ANSWER_FILTERS.map(([key, label]) => [key, label, answerCounts[key]])} />
            <label><span className="sr-only">Quote record status</span>
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                {RECORD_FILTERS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select></label>
            <label><span className="sr-only">Quote type</span>
              <select value={kind} onChange={(event) => setKind(event.target.value)}>
                <option value="all">All types</option><option value="packed">Pallet plans</option><option value="measured">Measured</option><option value="external">External</option>
              </select></label>
          </div>
        </div>
        {selected.size > 0 && <div className="bulk-bar" role="region" aria-label="Selected quotes">
          <span><b>{plural(selected.size, "quote")} selected</b></span>
          <button type="button" className="btn btn-primary btn-sm" disabled={selected.size < 2 || selected.size > 4} onClick={() => setComparing(true)}><Columns3 size={14} />Compare</button>
          {selected.size > 4 && <small className="muted">Compare up to 4 at a time.</small>}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>Clear</button>
        </div>}
        {loading && !data && <Loading />}
        {error && <ErrorBlock message={error} onRetry={reload} />}
        {data && <div className="qh-table"><DataTable columns={columns} rows={rows} selectable selected={selected} onSelectedChange={setSelected}
          onRowClick={(quote) => showQuote(quote.id)} initialSort={{ key: "created_at", direction: "desc" }} caption="Quotes"
          empty={<EmptyState icon={History} title="No quotes match">Change the filters, or create a quote from New Quote.</EmptyState>} /></div>}
      </section>
      <Modal open={comparing} title="Compare quotes" onClose={() => setComparing(false)}>
        <CompareTable quotes={compareQuotes} />
      </Modal>
      {/* Changes in the drawer patch the row in place: no reload of the whole list per price typed. */}
      {openQuote && <QuoteDetail quoteId={openQuote} notify={notify} onClose={() => showQuote(null)}
        onChanged={(quote) => setData((current) => current?.map((row) => (row.id === quote.id
          ? { ...row, ...quote, shipment_number: quote.shipment?.number ?? row.shipment_number } : row)))} />}
    </div>
  );
}

function CompareTable({ quotes }) {
  const rows = [
    ["Type", (q) => QUOTE_KINDS[q.kind] || q.kind],
    ["Orders", orderLabel],
    ["Status", (q) => <StatusBadge kind="quote" status={q.status} />],
    ["Customer answer", (q) => <CustomerBadge status={q.customer_status} />],
    ["Delivery terms", (q) => termsLabels(q.terms).join(", ") || "—"],
    ["Delivery ZIP", (q) => q.terms?.postal_code || "—"],
    ["Price used", (q) => { const used = selectedOption(q); return used ? `${money(used.price_cents)} · ${[sourceName(used), used.carrier].filter(Boolean).join(" · ")}` : "—"; }],
    ["Pallets", (q) => `${q.summary.pallets} (${q.summary.standard_pallets} × 48×40, ${q.summary.long_pallets} long)`],
    ["Freight weight", (q) => weight(q.summary.total_weight)],
    ["Item units", (q) => q.summary.total_units || "—"],
    ["Builder", (q) => q.summary.optimizer],
    ["Carrier price", (q) => money(q.cost.amount_cents)],
    ["Carrier", (q) => q.cost.carrier || "—"],
    ["Customer price", (q) => money(q.cost.customer_price_cents)],
    ["Created", (q) => date(q.created_at, { time: true })],
  ];
  return (
    <div className="table-wrap">
      <table className="data-table compare-table">
        <thead><tr><th scope="col"><span className="sr-only">Field</span></th>{quotes.map((quote) => <th key={quote.id} scope="col">{quote.number}</th>)}</tr></thead>
        <tbody>{rows.map(([label, value]) => <tr key={label}><th scope="row">{label}</th>{quotes.map((quote) => <td key={quote.id}>{value(quote)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

// The full record page (#/quotes/<id>): 3.0 cost editor, make current, void. The 3.2 quote drawer
// (#/quotes?quote=<id>) is where prices, terms and the customer's answer are kept.
function QuoteRecord({ quoteId }) {
  const { go, notify, company } = useApp();
  const { data: quote, error, loading, reload } = useResource(`/api/quotes/${quoteId}`);
  const [cost, setCost] = useState(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!quote) return;
    setCost({ amount: centsToInput(quote.cost.amount_cents), customer: centsToInput(quote.cost.customer_price_cents),
      carrier: quote.cost.carrier, broker: quote.cost.broker, reference: quote.cost.reference, expires_on: quote.cost.expires_on || "" });
    setNotes(quote.notes || "");
  }, [quote]);

  if (loading && !quote) return <div className="page"><Loading /></div>;
  if (error) return <div className="page"><ErrorBlock message={error} onRetry={reload} /></div>;
  if (!quote || !cost) return null;

  const amount = parseMoney(cost.amount);
  const customer = parseMoney(cost.customer);
  const invalid = Number.isNaN(amount) || Number.isNaN(customer) || (amount !== null && amount < 0) || (customer !== null && customer < 0);
  const save = async (extra = {}) => {
    if (invalid) { notify("Enter prices as dollars and cents, e.g. 1250.00.", "danger"); return; }
    setBusy(true);
    try {
      await sendJson(`/api/quotes/${quote.id}`, "PUT", {
        notes,
        cost: { amount_cents: amount, customer_price_cents: customer, carrier: cost.carrier, broker: cost.broker, reference: cost.reference, expires_on: cost.expires_on || null },
        ...extra,
      });
      notify(`${quote.number} saved.`);
      await reload({ quiet: true });
    } catch (requestError) { notify(requestError.message, "danger"); }
    finally { setBusy(false); }
  };
  const createShipment = async () => {
    try {
      const shipment = await sendJson("/api/freight-shipments", "POST", { company_id: quote.company_id, order_ids: quote.orders.map((order) => order.id) });
      notify(`Shipment ${shipment.number} created.`);
      go(`/shipments/${shipment.id}`);
    } catch (requestError) { notify(requestError.message, "danger"); }
  };
  const reopenHref = quote.kind === "packed"
    ? href("/quote/new", { result: quote.packing_result_id })
    : href("/quote/new", { mode: "measured", manual: quote.manual_quote_id });
  const margin = amount !== null && customer !== null && !Number.isNaN(amount) && !Number.isNaN(customer) ? customer - amount : null;
  const locked = quote.status === "void";

  return (
    <div className="page">
      <a className="back-link" href={href("/quotes")}><ArrowLeft size={15} />Quote History</a>
      <PageHeader eyebrow={quote.kind === "packed" ? "Pallet plan quote" : "Measured-pallet quote"}
        title={<>{quote.number} <StatusBadge kind="quote" status={quote.status} />{quote.role === "combined" && <Badge tone="info">Combined shipment quote</Badge>}</>}
        lede={`${quote.name}${quote.legacy ? " · recorded from earlier history" : ""} · ${date(quote.created_at, { time: true })}`}
        actions={<>
          <a className="btn btn-secondary" href={href("/quotes", { quote: quote.id })}><History size={15} />{FEATURES.freightWorkflow ? <>Prices &amp; customer answer</> : "Broker prices"}</a>
          {quote.source_available && <a className="btn btn-secondary" href={reopenHref}><ExternalLink size={15} />Open {quote.kind === "packed" ? "pallet plan" : "measurements"}</a>}
          {!FEATURES.freightWorkflow ? null : quote.shipment
            ? <a className="btn btn-secondary" href={href(`/shipments/${quote.shipment.id}`)}><Truck size={15} />{quote.shipment.number}</a>
            : quote.orders.length > 0 && !locked && <button type="button" className="btn btn-primary" onClick={createShipment}><Truck size={15} />Create shipment</button>}
        </>} />

      <div className="detail-grid">
        <div className="detail-main">
          <section className="card">
            <div className="kpi-row kpi-row-tight">
              <div className="kpi"><span className="kpi-label">Pallets</span><strong className="kpi-value">{quote.summary.pallets}</strong><span className="kpi-hint">{quote.summary.standard_pallets} × 48×40 · {quote.summary.long_pallets} long</span></div>
              <div className="kpi"><span className="kpi-label">Freight weight</span><strong className="kpi-value">{weight(quote.summary.total_weight)}</strong><span className="kpi-hint">{quote.kind === "packed" ? `${weight(quote.summary.item_weight)} of product` : "measured gross"}</span></div>
              <div className="kpi"><span className="kpi-label">Units</span><strong className="kpi-value">{quote.summary.total_units || "—"}</strong><span className="kpi-hint">{quote.summary.optimizer === "truckload" ? "dedicated truck" : quote.summary.optimizer}</span></div>
              <div className={`kpi ${quote.summary.freight_ready ? "kpi-ok" : "kpi-warn"}`}><span className="kpi-label">Plan</span><strong className="kpi-value">{quote.summary.freight_ready ? "Freight-ready" : "Has blockers"}</strong></div>
            </div>
            <h3 className="section-title">Pallets sent to brokers</h3>
            {quote.summary.rows.length ? (
              <div className="table-wrap"><table className="data-table is-dense">
                <thead><tr><th scope="col">Pallet</th><th scope="col">Dimensions</th><th scope="col" className="num">Weight</th><th scope="col">Class</th></tr></thead>
                <tbody>{quote.summary.rows.map((row) => <tr key={row.pallet}><th scope="row">P{row.pallet}</th><td>{row.dimensions}</td><td className="num">{row.weight} lb</td><td>{row.freight_class} <small className="muted">({row.density_pcf} pcf)</small></td></tr>)}</tbody>
              </table></div>
            ) : <p className="muted">No pallet rows were recorded.</p>}
          </section>

          <section className="card">
            <h3 className="section-title">Orders in this quote</h3>
            {quote.orders.length ? <ul className="item-list compact">{quote.orders.map((order) => (
              <li key={order.id}><a className="item-link" href={href("/orders", { open: order.id })}>
                <span className="item-main"><b>{order.order_number}</b>
                  {quote.summary.order_weights?.[order.id] !== undefined && <small>{weight(quote.summary.order_weights[order.id])} of product{quote.summary.order_pallet_shares?.[order.id] !== undefined ? ` · ${quote.summary.order_pallet_shares[order.id].toFixed(2)} pallet-equivalents` : ""}</small>}</span>
              </a></li>))}</ul>
              : <p className="muted">No orders are linked. The quote name didn't contain a recognisable order number.</p>}
          </section>

          <section className="card">
            <h3 className="section-title">History</h3>
            <Timeline entries={quote.history} />
          </section>
        </div>

        <aside className="detail-rail">
          <section className="card">
            <h2 className="card-title">Carrier price</h2>
            <p className="card-description">Record the rate you chose so it can be compared with the actual freight cost later. Quote Helper never reads rates from broker sites.</p>
            <div className="form-grid drawer-form">
              <Field label="Carrier price ($)" error={Number.isNaN(amount) ? "Use dollars and cents" : ""}><input inputMode="decimal" value={cost.amount} disabled={locked} onChange={(event) => setCost({ ...cost, amount: event.target.value })} /></Field>
              <Field label="Customer price ($)" hint="What the customer is charged for freight" error={Number.isNaN(customer) ? "Use dollars and cents" : ""}><input inputMode="decimal" value={cost.customer} disabled={locked} onChange={(event) => setCost({ ...cost, customer: event.target.value })} /></Field>
              <Field label="Carrier"><input value={cost.carrier} disabled={locked} onChange={(event) => setCost({ ...cost, carrier: event.target.value })} /></Field>
              <Field label="Broker"><input value={cost.broker} disabled={locked} onChange={(event) => setCost({ ...cost, broker: event.target.value })} /></Field>
              <Field label="Quote reference"><input value={cost.reference} disabled={locked} onChange={(event) => setCost({ ...cost, reference: event.target.value })} /></Field>
              <Field label="Price expires"><input type="date" value={cost.expires_on} disabled={locked} onChange={(event) => setCost({ ...cost, expires_on: event.target.value })} /></Field>
              <Field label="Notes" wide><textarea rows={3} value={notes} disabled={locked} onChange={(event) => setNotes(event.target.value)} /></Field>
            </div>
            {margin !== null && <p className={`margin-line ${margin < 0 ? "is-loss" : ""}`}>Freight {margin < 0 ? "loss" : "margin"}: <b>{money(margin)}</b></p>}
            <div className="card-actions-row">
              <button type="button" className="btn btn-primary" disabled={busy || locked || invalid} onClick={() => save()}><Save size={15} />Save</button>
              {quote.status === "superseded" && <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => save({ status: "final" })}>Make current</button>}
              {!locked && <button type="button" className="btn btn-ghost btn-ghost-danger" disabled={busy} onClick={() => { if (window.confirm(`Void ${quote.number}? It stays in history but can't be used again.`)) save({ status: "void" }); }}><XCircle size={15} />Void</button>}
            </div>
          </section>
          <section className="card subtle-card">
            <p className="hint">{quote.kind === "packed" ? <Boxes size={14} /> : <Weight size={14} />}
              {quote.kind === "packed" ? "Built from catalog cartons by the pallet planner." : "User-supplied measured pallets; gross weight already includes the pallet."}
              {company && ` Company: ${company.code}.`}</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
