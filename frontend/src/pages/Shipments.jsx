import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Boxes, Calculator, Link2, Lock, Mail, Plus, RotateCcw, Ruler, Save, Trash2, Weight, X, XCircle,
} from "lucide-react";
import { getJson, sendJson } from "../api";
import { Badge, DataTable, Drawer, EmptyState, ErrorBlock, Field, Kpi, Loading, PageHeader, StatusBadge, Timeline } from "../components/Common";
import EmailDraft from "../components/EmailDraft";
import ActualsEditor from "../components/shipment/ActualsEditor";
import ComparisonCard from "../components/shipment/ComparisonCard";
import MoneyPanel from "../components/shipment/MoneyPanel";
import OrderPicker from "../components/shipment/OrderPicker";
import ReasonDialog from "../components/shipment/ReasonDialog";
import ShipmentEmails from "../components/shipment/ShipmentEmails";
import ShipmentList from "../components/shipment/ShipmentList";
import StatusDialog from "../components/shipment/StatusDialog";
import StatusStepper from "../components/shipment/StatusStepper";
import {
  NEXT_ACTION, PURPOSE_TONE, SUGGESTED_EMAIL, chargeErrors, formDirty, formFromShipment, percent, shipmentPayload, today,
} from "../components/shipment/model";
import { SHIPMENT_PURPOSES, SHIPMENT_STATUS, date, money, plural, weight } from "../lib/format";
import { href } from "../lib/route";
import useResource from "../lib/useResource";
import { useApp } from "../shell/AppShell";
import "../styles/shipments.css";

export default function Shipments() {
  const { route } = useApp();
  return route.parts[1] ? <ShipmentDetail shipmentId={route.parts[1]} /> : <ShipmentList />;
}

const TABS = [["shipment", "Shipment"], ["compare", "Quoted vs actual"], ["money", "Money"], ["quotes", "Quotes"], ["history", "History"]];
const TAB_ALIASES = { overview: "shipment", costs: "money", actual: "compare" };
const tabFromQuery = (value) => (TABS.some(([key]) => key === value) ? value : TAB_ALIASES[value] || null);

const SUGGESTION_TEXT = {
  ready: "Booked. Let the customer know the order is ready to ship?",
  shipped: "On its way. Email the customer the PRO number?",
  delivered: "Delivered. Send the delivered email, then record the carrier bill and the actual measurements.",
};

function ShipmentDetail({ shipmentId }) {
  const { route, go, notify } = useApp();
  const { data: shipment, error, loading, reload, setData } = useResource(`/api/freight-shipments/${shipmentId}`);
  const emails = useResource(`/api/emails?entity_type=shipment&entity_id=${encodeURIComponent(shipmentId)}`);
  const [tab, setTabState] = useState(() => tabFromQuery(route.query.tab));
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [statusTarget, setStatusTarget] = useState(null);
  const [reopening, setReopening] = useState(false);
  const [reopenTo, setReopenTo] = useState("delivered");
  const [emailKind, setEmailKind] = useState(null);
  const [suggestion, setSuggestion] = useState(null);
  const [adding, setAdding] = useState(false);
  const [linking, setLinking] = useState(false);

  // Reset the form only when the shipment itself was saved, not on every reload.
  const version = shipment ? `${shipment.id}:${shipment.updated_at}` : null;
  useEffect(() => { if (shipment) setForm(formFromShipment(shipment)); }, [version]);
  useEffect(() => {
    if (shipment && !tab) setTabState(["delivered", "closed"].includes(shipment.status) ? "compare" : "shipment");
  }, [shipment, tab]);

  // Links such as Dashboard's "#/shipments/<id>?tab=costs" may arrive while this shipment is already open.
  useEffect(() => { const fromRoute = tabFromQuery(route.query.tab); if (fromRoute) setTabState(fromRoute); }, [route.query.tab]);
  const setTab = (key) => { setTabState(key); go(`/shipments/${shipmentId}`, { tab: key }, { replace: true }); };
  const dirty = useMemo(() => formDirty(form, shipment), [form, shipment]);
  const emailRecords = emails.data;
  const prepared = useMemo(() => new Set((emailRecords || []).map((record) => record.kind)), [emailRecords]);

  const run = async (action, success) => {
    setBusy(true);
    try {
      const value = await action();
      if (value && value.id === shipmentId && value.comparison) setData(value); else await reload({ quiet: true });
      if (success) notify(success);
      return value || true;
    } catch (requestError) { notify(requestError.message, "danger"); return null; }
    finally { setBusy(false); }
  };

  if (loading && !shipment) return <div className="page"><Loading /></div>;
  if (error && !shipment) return <div className="page"><ErrorBlock message={error} onRetry={reload} /></div>;
  if (!shipment || !form || !tab) return null;

  const locked = shipment.locked;
  const orderIds = shipment.orders.map((order) => order.id);
  const comparison = shipment.comparison;
  const combined = shipment.combined_quote;
  const next = NEXT_ACTION[shipment.status];

  const save = async (overrides = {}, { reason = "", success } = {}) => {
    const merged = { ...form, ...overrides };
    const problems = chargeErrors(merged);
    if (Object.keys(problems).length) { notify(`Customer charge: ${Object.values(problems)[0]}.`, "danger"); setTab("money"); return null; }
    const payload = shipmentPayload(form, shipment, overrides);
    // Closed books keep their charge: a locked shipment only changes status (reopen) or notes.
    if (locked) payload.customer_charge_cents = shipment.customer_charge_cents;
    const query = reason ? `?reason=${encodeURIComponent(reason)}` : "";
    return run(() => sendJson(`/api/freight-shipments/${shipment.id}${query}`, "PUT", payload), success || `${shipment.number} saved.`);
  };

  const changeStatus = async (overrides) => {
    const target = overrides.status;
    const label = SHIPMENT_STATUS[target]?.label || target;
    const value = await save(overrides, { success: `${shipment.number}: ${label.toLowerCase()}.` });
    setStatusTarget(null);
    if (!value) return;
    const kind = SUGGESTED_EMAIL[target];
    setSuggestion(kind && !prepared.has(kind) ? kind : null);
    if (target === "delivered" || target === "closed") setTabState("compare");
  };
  const reopen = async (reason) => {
    const value = await save({ status: reopenTo }, { reason, success: `${shipment.number} reopened.` });
    if (value) setReopening(false);
  };
  const onStep = (key) => {
    if (locked) return;
    setStatusTarget(key);
  };

  const quotedLine = comparison.quoted
    ? `${comparison.quoted.number} · ${plural(comparison.quoted.pallets, "pallet")} · ${weight(comparison.quoted.total_weight)}`
    : "no quote linked";
  const destination = [shipment.destination?.city, shipment.destination?.state, shipment.destination?.postal_code].filter(Boolean).join(" ");

  return (
    <div className="page shipment-detail">
      <a className="back-link" href={href("/shipments")}><ArrowLeft size={15} />Shipments</a>
      <PageHeader eyebrow="Shipment"
        title={<span className="ship-title">{shipment.number}<span className="ship-title-name">{shipment.name}</span></span>}
        actions={<div className="ship-actions">
          {next && <button type="button" className="btn btn-primary" disabled={busy} onClick={() => setStatusTarget(next.to)}><ArrowRight size={15} />{next.label}</button>}
          {locked && <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => { setReopenTo(shipment.status === "closed" ? "delivered" : "planning"); setReopening(true); }}><RotateCcw size={15} />Reopen…</button>}
          {!locked && <button type="button" className="btn btn-ghost btn-ghost-danger" disabled={busy} onClick={() => setStatusTarget("cancelled")}><XCircle size={15} />Cancel shipment</button>}
        </div>}>
        <div className="ship-meta">
          <StatusBadge kind="shipment" status={shipment.status} />
          <Badge tone={PURPOSE_TONE[shipment.purpose] || "neutral"}>{SHIPMENT_PURPOSES[shipment.purpose] || shipment.purpose}</Badge>
          {locked && <Badge tone="neutral"><Lock size={11} aria-hidden="true" />Locked</Badge>}
          {shipment.status === "in_transit" && shipment.expected_delivery_date && shipment.expected_delivery_date < today() && <Badge tone="danger">Delivery overdue</Badge>}
          <span className="ship-orders">{plural(shipment.orders.length, "order")}:{" "}
            {shipment.orders.map((order, index) => <span key={order.id}>{index ? ", " : ""}<a className="inline-link" href={href("/orders", { open: order.id })}>{order.order_number}</a></span>)}
          </span>
          {shipment.orders[0]?.customer_name && <span className="muted">{shipment.orders[0].customer_name}</span>}
          {destination && <span className="muted">→ {destination}</span>}
        </div>
      </PageHeader>

      <div className="ship-progress">
        <StatusStepper status={shipment.status} disabled={locked || busy} onStep={onStep} />
        {shipment.status === "cancelled" && <p className="small-line danger-text">Cancelled. Reopen it with a reason to use it again.</p>}
      </div>

      {locked && <div className="callout callout-info compact-callout"><Lock size={16} aria-hidden="true" />
        <span>{shipment.status === "closed" ? "Closed" : "Cancelled"} shipments are locked: orders, costs, revenue and measurements change only after reopening with a reason. Notes can still be edited.</span></div>}
      {suggestion && <div className="callout callout-info suggestion" role="status">
        <Mail size={16} aria-hidden="true" />
        <span>{SUGGESTION_TEXT[suggestion]}</span>
        <span className="suggestion-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setEmailKind(suggestion)}>Prepare email</button>
          {suggestion === "delivered" && <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setSuggestion(null); setTab("compare"); }}><Ruler size={14} />Record measurements</button>}
          <button type="button" className="icon-btn" aria-label="Dismiss suggestion" onClick={() => setSuggestion(null)}><X size={16} /></button>
        </span>
      </div>}
      {shipment.warnings.length > 0 && <div className="callout callout-warn"><AlertTriangle size={18} /><ul className="bare-list">{shipment.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}

      <section className="kpi-row ship-kpis" aria-label="Shipment money at a glance">
        <Kpi label="Quoted" value={money(comparison.cost?.expected_cents)} hint={quotedLine} />
        <Kpi label="Actual cost" value={comparison.cost?.actual_cents != null ? money(comparison.cost.actual_cents) : "No bill yet"}
          tone={comparison.flags?.cost_over ? "warn" : comparison.cost?.actual_cents != null ? "default" : ["delivered", "closed"].includes(shipment.status) ? "warn" : "default"}
          hint={comparison.cost?.variance_cents != null ? `${money(comparison.cost.variance_cents, { signed: true })} vs quote` : "carrier bills and other costs"} />
        <Kpi label="Revenue" value={money(comparison.revenue?.total_cents)} hint={comparison.revenue?.extra_cents ? `incl. ${money(comparison.revenue.extra_cents)} extra` : "customer charge"} />
        <Kpi label={comparison.profit_estimated ? "Profit (estimated)" : "Profit"} value={money(comparison.profit_cents)}
          tone={comparison.profit_cents != null && comparison.profit_cents < 0 ? "warn" : comparison.profit_cents != null && !comparison.profit_estimated ? "ok" : "default"}
          hint={comparison.margin_percent != null ? `${percent(comparison.margin_percent)} margin` : "margin unknown"} />
      </section>

      <div className="tabs" role="tablist" aria-label="Shipment sections">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" id={`tab-${key}`} aria-selected={tab === key} aria-controls="shipment-tabpanel" className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}{key === "shipment" && dirty ? <span className="dirty-dot" title="Unsaved changes"><span className="sr-only"> (unsaved changes)</span></span> : null}
            {key === "compare" && Object.values(comparison.flags || {}).some(Boolean) ? <AlertTriangle size={13} className="warn-text" aria-label="has flags" /> : null}
          </button>
        ))}
      </div>

      <div id="shipment-tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "shipment" && <div className="detail-grid">
          <div className="detail-main stack">
            <DetailsForm form={form} setForm={setForm} locked={locked} busy={busy} dirty={dirty}
              onSave={() => save()} onDiscard={() => setForm(formFromShipment(shipment))} />
            <section className="card card-flush">
              <header className="card-head padded"><div className="card-head-text"><h2 className="card-title">Orders on this shipment</h2><p className="card-description">An order can be on only one active shipment.</p></div>
                {!locked && <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAdding(true)}><Plus size={14} />Add orders</button>}</header>
              <DataTable rows={shipment.orders} dense caption="Orders on this shipment" columns={[
                { key: "order_number", label: "Order", render: (order) => <a className="inline-link" href={href("/orders", { open: order.id })}><b>{order.order_number}</b></a>, plain: true },
                { key: "customer_name", label: "Customer" },
                { key: "zip", label: "ZIP", render: (order) => order.ship_to?.postal_code || "—" },
                { key: "unit_count", label: "Units", align: "right" },
                { key: "quotes", label: "Own quotes", sortable: false, render: (order) => (shipment.individual_quotes[order.id] || []).filter((q) => q.status !== "void").map((q) => q.number).join(", ") || <span className="muted">none</span> },
                { key: "remove", label: <span className="sr-only">Remove</span>, sortable: false, render: (order) => !locked && shipment.orders.length > 1 && (
                  <button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove ${order.order_number}`} disabled={busy}
                    onClick={() => { if (window.confirm(`Remove ${order.order_number} from ${shipment.number}?`)) run(() => sendJson(`/api/freight-shipments/${shipment.id}/orders`, "POST", { remove: [order.id] }), `${order.order_number} removed.`); }}><Trash2 size={15} /></button>) },
              ]} />
            </section>
          </div>
          <div className="detail-rail">
            <ShipmentEmails status={shipment.status} records={emailRecords} loading={emails.loading} onCompose={setEmailKind} />
          </div>
        </div>}

        {tab === "compare" && <div className="stack">
          <ComparisonCard comparison={comparison} onRecordActuals={locked ? undefined : () => document.getElementById("actuals-title")?.scrollIntoView({ behavior: "smooth", block: "start" })}
            onRecordCost={locked ? undefined : () => setTab("money")} />
          <ActualsEditor actual={shipment.actual} quoted={comparison.quoted} locked={locked} busy={busy}
            version={`${shipment.updated_at}:${JSON.stringify(shipment.actual)}`}
            onSave={(payload) => run(() => sendJson(`/api/freight-shipments/${shipment.id}/actual`, "PUT", payload), "Actual measurements saved.")}
            onClear={() => run(() => sendJson(`/api/freight-shipments/${shipment.id}/actual`, "DELETE"), "Actual measurements cleared.")} />
        </div>}

        {tab === "money" && <MoneyPanel shipment={shipment} form={form} onFormChange={(patch) => setForm({ ...form, ...patch })}
          onSaveShipment={() => save()} dirty={dirty} run={run} busy={busy} notify={notify} />}

        {tab === "quotes" && <div className="stack">
          <section className="card">
            <header className="card-head"><div className="card-head-text"><h2 className="card-title">Combined quote</h2><p className="card-description">The estimate for all orders travelling together. Building a new combined plan replaces it; the old one stays in history as superseded. The quote the customer approved stays the reference for quoted vs actual.</p></div>
              {!locked && <div className="card-actions">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLinking(true)}><Link2 size={14} />Use an existing quote</button>
                <a className="btn btn-secondary btn-sm" href={href("/quote/new", { orders: orderIds.join(","), shipment: shipment.id })}><Boxes size={14} />Build combined plan</a>
                <a className="btn btn-secondary btn-sm" href={href("/quote/new", { mode: "measured", orders: orderIds.join(","), shipment: shipment.id })}><Weight size={14} />Measured combined quote</a></div>}</header>
            {combined ? <QuoteRow quote={combined} /> : <EmptyState icon={Calculator} title="No combined quote yet">Build a pallet plan for all orders together, enter measured pallets, or link a quote that already covers exactly these orders.</EmptyState>}
          </section>
          <section className="card">
            <h2 className="card-title">Original quotes for each order</h2>
            <p className="card-description">Kept unchanged, so you can see what each order was quoted at before they were combined.</p>
            {shipment.orders.map((order) => (
              <div key={order.id} className="quote-group">
                <h3>{order.order_number}</h3>
                {(shipment.individual_quotes[order.id] || []).length
                  ? (shipment.individual_quotes[order.id] || []).map((quote) => <QuoteRow key={quote.id} quote={quote} />)
                  : <p className="muted">Not quoted on its own.</p>}
              </div>
            ))}
          </section>
        </div>}

        {tab === "history" && <section className="card"><Timeline entries={shipment.history} /></section>}
      </div>

      <StatusDialog target={statusTarget} form={form} shipment={shipment} busy={busy} dirty={dirty} onClose={() => setStatusTarget(null)} onConfirm={changeStatus} />
      <ReasonDialog open={reopening} title={`Reopen ${shipment.number}`} description="Reopening unlocks orders, costs and measurements. The reason is kept in the history."
        label="Why is it being reopened?" confirmLabel="Reopen" busy={busy} onClose={() => setReopening(false)} onConfirm={reopen}>
        <Field label="Return it to" wide><select value={reopenTo} onChange={(event) => setReopenTo(event.target.value)}>
          {["planning", "booked", "in_transit", "delivered"].map((key) => <option key={key} value={key}>{SHIPMENT_STATUS[key].label}</option>)}</select></Field>
      </ReasonDialog>
      <EmailDraft open={Boolean(emailKind)} kind={emailKind || "ready"} shipmentId={shipment.id} notify={notify} onClose={() => setEmailKind(null)}
        onPrepared={() => { if (suggestion === emailKind) setSuggestion(null); setEmailKind(null); emails.reload({ quiet: true }); }} />
      <OrderPicker open={adding} title={`Add orders to ${shipment.number}`} actionLabel="Add to shipment" exclude={orderIds} onClose={() => setAdding(false)}
        onPick={async (ids) => { setAdding(false); await run(() => sendJson(`/api/freight-shipments/${shipment.id}/orders`, "POST", { add: ids }), `${plural(ids.length, "order")} added.`); }} />
      <LinkQuote open={linking} shipment={shipment} onClose={() => setLinking(false)}
        onLink={async (quoteId) => { setLinking(false); await run(() => sendJson(`/api/freight-shipments/${shipment.id}/combined-quote`, "POST", { quote_id: quoteId }), "Combined quote linked."); }} />
    </div>
  );
}

function DetailsForm({ form, setForm, locked, busy, dirty, onSave, onDiscard }) {
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });
  const setDestination = (key) => (event) => setForm({ ...form, destination: { ...form.destination, [key]: event.target.value } });
  const dateOrderProblem = form.pickup_date && form.delivery_date && form.delivery_date < form.pickup_date ? "Delivery is before pickup" : "";
  return (
    <form className="card" aria-labelledby="details-title" onSubmit={(event) => { event.preventDefault(); if (dirty) onSave(); }}>
      <header className="card-head">
        <div className="card-head-text"><h2 className="card-title" id="details-title">Shipment details</h2>
          <p className="card-description">Carrier, tracking and dates. Status changes are made with the steps above.</p></div>
        {dirty && <Badge tone="warn">Unsaved changes</Badge>}
      </header>
      <div className="form-grid form-grid-3 ship-form">
        <Field label="Name" wide><input value={form.name} maxLength={200} disabled={locked} onChange={set("name")} /></Field>
        <Field label="Purpose"><select value={form.purpose} disabled={locked} onChange={set("purpose")}>
          {Object.entries(SHIPMENT_PURPOSES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        <Field label="Carrier"><input value={form.carrier} maxLength={120} disabled={locked} onChange={set("carrier")} /></Field>
        <Field label="Broker"><input value={form.broker} maxLength={120} disabled={locked} onChange={set("broker")} /></Field>
        <Field label="PRO / tracking"><input value={form.pro_number} maxLength={60} disabled={locked} autoComplete="off" onChange={set("pro_number")} /></Field>
        <Field label="BOL number"><input value={form.bol_number} maxLength={60} disabled={locked} autoComplete="off" onChange={set("bol_number")} /></Field>
        <Field label="Pickup date"><input type="date" value={form.pickup_date} disabled={locked} onChange={set("pickup_date")} /></Field>
        <Field label="Expected delivery"><input type="date" value={form.expected_delivery_date} disabled={locked} onChange={set("expected_delivery_date")} /></Field>
        <Field label="Delivered on" error={dateOrderProblem}><input type="date" value={form.delivery_date} disabled={locked} onChange={set("delivery_date")} /></Field>
        <Field label="Destination city"><input value={form.destination.city || ""} disabled={locked} onChange={setDestination("city")} /></Field>
        <Field label="State"><input value={form.destination.state || ""} maxLength={20} disabled={locked} onChange={setDestination("state")} /></Field>
        <Field label="ZIP"><input inputMode="numeric" value={form.destination.postal_code || ""} maxLength={10} disabled={locked} onChange={setDestination("postal_code")} /></Field>
        <Field label="Notes" wide><textarea rows={3} value={form.notes} onChange={set("notes")} /></Field>
      </div>
      <div className="card-actions-row">
        <button type="submit" className="btn btn-primary" disabled={busy || !dirty}><Save size={15} />Save details</button>
        {dirty && <button type="button" className="btn btn-ghost" onClick={onDiscard}>Discard changes</button>}
      </div>
    </form>
  );
}

function QuoteRow({ quote }) {
  return (
    <a className="quote-row" href={href("/quotes", { quote: quote.id })}>
      <span className={`kind-icon kind-${quote.kind}`} aria-hidden="true">{quote.kind === "packed" ? <Boxes size={16} /> : <Weight size={16} />}</span>
      <span className="item-main"><b>{quote.number} · {quote.kind === "packed" ? "Pallet plan" : quote.kind === "measured" ? "Measured pallets" : "Priced without a plan"}</b>
        <small>{(quote.orders || []).map((order) => order.order_number).join(", ")} · {plural(quote.summary.pallets, "pallet")} · {weight(quote.summary.total_weight)} · {date(quote.created_at)}</small></span>
      <span className="item-side">{quote.cost.amount_cents != null ? <b>{money(quote.cost.amount_cents)}</b> : <small className="muted">no price</small>}
        {quote.customer_status && quote.customer_status !== "not_sent" && <Badge tone={quote.customer_status === "approved" ? "ok" : "info"}>{quote.customer_status === "approved" ? "Approved" : quote.customer_status.replace("_", " ")}</Badge>}
        <StatusBadge kind="quote" status={quote.status} /></span>
    </a>
  );
}

function LinkQuote({ open, shipment, onClose, onLink }) {
  const [quotes, setQuotes] = useState(null);
  const orderIds = useMemo(() => new Set(shipment.orders.map((order) => order.id)), [shipment]);
  useEffect(() => {
    if (!open) return;
    setQuotes(null);
    Promise.all(shipment.orders.map((order) => getJson(`/api/quotes?order_id=${order.id}`)))
      .then((lists) => {
        const seen = new Map();
        lists.flat().forEach((quote) => { if (quote.status !== "void" && quote.orders.every((order) => orderIds.has(order.id))) seen.set(quote.id, quote); });
        setQuotes([...seen.values()].sort((a, b) => b.orders.length - a.orders.length || String(b.created_at).localeCompare(String(a.created_at))));
      }).catch(() => setQuotes([]));
  }, [open, shipment, orderIds]);
  return (
    <Drawer open={open} title="Use an existing quote" subtitle="Quotes whose orders are all on this shipment." onClose={onClose} labelId="link-quote-title">
      {!quotes ? <Loading /> : quotes.length ? <ul className="item-list compact">{quotes.map((quote) => (
        <li key={quote.id}><button type="button" className="item-link" onClick={() => onLink(quote.id)}>
          <span className="item-main"><b>{quote.number}</b><small>{quote.orders.map((order) => order.order_number).join(", ")} · {plural(quote.summary.pallets, "pallet")} · {money(quote.cost.amount_cents)}</small></span>
          <span className="item-side"><StatusBadge kind="quote" status={quote.status} /></span></button></li>))}</ul>
        : <EmptyState icon={Calculator} title="No suitable quotes">Build a combined plan instead.</EmptyState>}
    </Drawer>
  );
}
