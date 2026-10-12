import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, FileText, History, Plus, Save, Trash2, Truck } from "lucide-react";
import { getJson, sendJson } from "../api";
import { href } from "../lib/route";
import { date, money, plural, weight } from "../lib/format";
import { Badge, Drawer, ErrorBlock, Field, Loading, StatusBadge, Timeline } from "./Common";
import OrderQuoteHistory from "./OrderQuoteHistory";
import QuoteDetail from "./QuoteDetail";
import { CustomerBadge } from "./QuoteBits";

const blankLine = () => ({ id: crypto.randomUUID(), original_sku: "", description: "", quantity: 1, packing_state: "flatpack", source_ref: "entered" });

function toEditable(order) {
  return {
    order_number: order.order_number,
    references: order.references || [],
    customer_name: order.customer_name || "",
    po_number: order.po_number || "",
    order_date: order.order_date || "",
    ready_date: order.ready_date || "",
    sales_rep: order.sales_rep || "",
    ship_to: { name: "", street: "", city: "", state: "", postal_code: "", phone: "", email: "", ...(order.ship_to || {}) },
    residential: order.residential ?? null,
    liftgate: order.liftgate ?? null,
    customer_contact: order.customer_contact || "",
    // Typed as text ("a@x.com; b@y.com") and split when saving, so separators can be typed freely.
    customer_emails: (order.customer_emails || []).join("; "),
    customer_phone: order.customer_phone || "",
    packing_state: order.packing_state || "flatpack",
    notes: order.notes || "",
    lines: (order.lines || []).map((line) => ({ ...line })),
  };
}

const splitEmails = (text) => String(text || "").split(/[;,\s]+/).map((value) => value.trim()).filter(Boolean);
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function validate(draft) {
  const problems = [];
  if (!draft.order_number.trim()) problems.push("Order number is required.");
  const emails = splitEmails(draft.customer_emails);
  const bad = emails.filter((value) => !EMAIL.test(value));
  if (bad.length) problems.push(`Check the customer email${bad.length === 1 ? "" : "s"}: ${bad.join(", ")}.`);
  if (emails.length > 10) problems.push("Enter no more than 10 customer emails.");
  draft.lines.forEach((line, index) => {
    if (!String(line.original_sku || "").trim()) problems.push(`Line ${index + 1}: SKU is required.`);
    const quantity = Number(line.quantity);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) problems.push(`Line ${index + 1}: quantity must be a whole number from 1 to 10,000.`);
  });
  return problems;
}

// Review, correct and trace one order. Every save is a numbered revision with a reason.
// The quote history sits on top; a quote opens in the quote drawer above this one. Pass openQuoteId and
// onOpenQuote to keep the open quote in the address (#/orders?open=…&quote=…); otherwise it is local.
export default function OrderDrawer({ orderId, onClose, onSaved, notify, openQuoteId, onOpenQuote }) {
  const [trace, setTrace] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState("");
  const [localQuote, setLocalQuote] = useState(null);
  const quoteId = onOpenQuote ? openQuoteId : localQuote;
  const openQuote = onOpenQuote || setLocalQuote;
  const [draft, setDraft] = useState(null);
  const [reason, setReason] = useState("");
  const [tab, setTab] = useState("lines");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const current = useRef(orderId);
  current.current = orderId;
  const loadHistory = async (id = orderId) => {
    try {
      const value = await getJson(`/api/orders/${id}/quote-history`);
      if (id === current.current) { setHistory(value); setHistoryError(""); }
    } catch (requestError) { if (id === current.current) setHistoryError(requestError.message); }
  };
  const load = async () => {
    const id = orderId;
    setError("");
    // Order and quote history load together, so the drawer opens once with both.
    const [traceResult] = await Promise.allSettled([getJson(`/api/orders/${id}`), loadHistory(id)]);
    if (id !== current.current) return;
    if (traceResult.status === "fulfilled") {
      setTrace(traceResult.value);
      setDraft(toEditable(traceResult.value.order));
    } else setError(traceResult.reason.message);
  };
  useEffect(() => { if (orderId) { setTrace(null); setHistory(null); setHistoryError(""); setLocalQuote(null); setDraft(null); setTab("lines"); setReason(""); load(); } }, [orderId]);

  const order = trace?.order;
  const locked = order?.status === "shipped" || order?.status === "cancelled";
  const dirty = draft && order && JSON.stringify(draft) !== JSON.stringify(toEditable(order));
  const problems = draft ? validate(draft) : [];
  const update = (field, value) => setDraft((current) => ({ ...current, [field]: value }));
  const updateLine = (id, field, value) => setDraft((current) => ({ ...current, lines: current.lines.map((line) => line.id === id ? { ...line, [field]: value } : line) }));

  const save = async () => {
    if (problems.length) { setError(problems.slice(0, 3).join(" ")); return; }
    setBusy(true); setError("");
    try {
      const payload = { ...draft, customer_emails: splitEmails(draft.customer_emails), lines: draft.lines.map((line) => ({ ...line, quantity: Number(line.quantity),
        length: line.length || null, width: line.width || null, height: line.height || null, weight: line.weight || null })),
        reason: reason.trim() || "Corrected in review" };
      const saved = await sendJson(`/api/orders/${orderId}`, "PUT", payload);
      notify?.(`${saved.order_number} saved as revision ${saved.revision}.`);
      setReason("");
      await load();
      onSaved?.(saved);
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };

  const setStatus = async (status) => {
    const why = window.prompt(status === "cancelled" ? "Why is this order cancelled?" : "Reason for the change?");
    if (!why) return;
    setBusy(true); setError("");
    try {
      await sendJson(`/api/orders/${orderId}/status`, "POST", { status, reason: why });
      notify?.(`Order ${status === "cancelled" ? "cancelled" : "updated"}.`);
      await load(); onSaved?.();
    } catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };

  const units = draft ? draft.lines.reduce((sum, line) => sum + (Number(line.quantity) || 0), 0) : 0;
  return (
    <Drawer open={Boolean(orderId)} wide onClose={onClose} labelId="order-drawer-title"
      title={order ? <>Order {order.order_number} <StatusBadge kind="order" status={order.status} /></> : "Order"}
      subtitle={order ? [order.customer_name, order.ship_to?.postal_code && `ZIP ${order.ship_to.postal_code}`, order.document_type?.replaceAll("_", " ")].filter(Boolean).join(" · ") : ""}
      footer={draft && !locked && tab !== "history" && <>
        <label className="field reason-field"><span className="sr-only">Reason for the change</span>
          <input value={reason} placeholder="Reason for the change (kept in the history)" onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={save}><Save size={15} />Save correction</button>
      </>}>
      {error && <ErrorBlock message={error} />}
      {!trace && !error && <Loading />}
      {trace && draft && <>
        {order.issues?.length > 0 && <div className="callout callout-warn compact-callout"><AlertTriangle size={16} /><ul className="bare-list">{order.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul></div>}
        {locked && <div className="callout compact-callout"><Truck size={16} /><span>This order is {order.status}. Its lines can't be changed; reopen the shipment first if a correction is needed.</span></div>}

        <OrderQuoteHistory orderId={order.id} history={history} shipment={trace.shipment} error={historyError}
          onOpen={(quote) => openQuote(quote.id)} />

        <div className="tabs" role="tablist" aria-label="Order sections">
          {[["lines", `Lines (${draft.lines.length})`], ["details", "Details"], ["trace", "Quotes & shipment"], ["history", "History"]].map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>

        {tab === "lines" && <>
          <p className="muted small-line">{plural(units, "unit")} on {plural(draft.lines.length, "line")}. SKUs are kept exactly as printed; catalog matching ignores configured prefixes.</p>
          <div className="table-wrap">
            <table className="data-table grid-table is-dense">
              <thead><tr><th scope="col">SKU</th><th scope="col" className="num">Qty</th><th scope="col">Packing</th><th scope="col">Description</th><th scope="col">Source</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
              <tbody>
                {draft.lines.map((line, index) => (
                  <tr key={line.id}>
                    <td><input className="cell-input" aria-label={`Line ${index + 1} SKU`} value={line.original_sku} disabled={locked} onChange={(event) => updateLine(line.id, "original_sku", event.target.value)} /></td>
                    <td className="num"><input className="cell-input qty-input" type="number" min="1" aria-label={`Line ${index + 1} quantity`} value={line.quantity} disabled={locked} onChange={(event) => updateLine(line.id, "quantity", event.target.value)} /></td>
                    <td><select className="cell-input" aria-label={`Line ${index + 1} packing`} value={line.packing_state} disabled={locked} onChange={(event) => updateLine(line.id, "packing_state", event.target.value)}><option value="flatpack">Flatpack</option><option value="assembled">Assembled</option></select></td>
                    <td><input className="cell-input" aria-label={`Line ${index + 1} description`} value={line.description} disabled={locked} onChange={(event) => updateLine(line.id, "description", event.target.value)} /></td>
                    <td className="muted source-col" title={line.source_ref}>{line.source_ref || "—"}</td>
                    <td><button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove line ${index + 1}`} disabled={locked} onClick={() => update("lines", draft.lines.filter((entry) => entry.id !== line.id))}><Trash2 size={15} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!locked && <div className="table-footer">
            <button type="button" className="btn btn-ghost" onClick={() => update("lines", [...draft.lines, blankLine()])}><Plus size={15} />Add line</button>
            <div className="segmented" role="group" aria-label="Set packing for all lines">
              <span className="segmented-label">All lines</span>
              <button type="button" onClick={() => setDraft((current) => ({ ...current, packing_state: "flatpack", lines: current.lines.map((line) => ({ ...line, packing_state: "flatpack" })) }))}>Flatpack</button>
              <button type="button" onClick={() => setDraft((current) => ({ ...current, packing_state: "assembled", lines: current.lines.map((line) => ({ ...line, packing_state: "assembled" })) }))}>Assembled</button>
            </div>
          </div>}
        </>}

        {tab === "details" && <div className="form-grid form-grid-3 drawer-form">
          <Field label="Order number"><input value={draft.order_number} disabled={locked} onChange={(event) => update("order_number", event.target.value)} /></Field>
          <Field label="Other references" hint="Invoice, SO or PO numbers that identify this order, comma separated">
            <input value={draft.references.join(", ")} disabled={locked} onChange={(event) => update("references", event.target.value.split(",").map((value) => value.trim()).filter(Boolean))} /></Field>
          <Field label="Customer"><input value={draft.customer_name} onChange={(event) => update("customer_name", event.target.value)} /></Field>
          <Field label="P.O. number"><input value={draft.po_number} onChange={(event) => update("po_number", event.target.value)} /></Field>
          <Field label="Order date"><input value={draft.order_date} onChange={(event) => update("order_date", event.target.value)} /></Field>
          <Field label="Ready / ship date"><input value={draft.ready_date} onChange={(event) => update("ready_date", event.target.value)} /></Field>
          <Field label="Sales rep"><input value={draft.sales_rep} onChange={(event) => update("sales_rep", event.target.value)} /></Field>
          <Field label="Ship-to street"><input value={draft.ship_to.street} onChange={(event) => update("ship_to", { ...draft.ship_to, street: event.target.value })} /></Field>
          <Field label="City"><input value={draft.ship_to.city} onChange={(event) => update("ship_to", { ...draft.ship_to, city: event.target.value })} /></Field>
          <Field label="State"><input value={draft.ship_to.state} maxLength={2} onChange={(event) => update("ship_to", { ...draft.ship_to, state: event.target.value.toUpperCase() })} /></Field>
          <Field label="ZIP"><input inputMode="numeric" value={draft.ship_to.postal_code} onChange={(event) => update("ship_to", { ...draft.ship_to, postal_code: event.target.value })} /></Field>
          <Field label="Delivery">
            <select value={draft.residential === null ? "" : draft.residential ? "res" : "com"} onChange={(event) => update("residential", event.target.value === "" ? null : event.target.value === "res")}>
              <option value="">Not stated</option><option value="com">Commercial</option><option value="res">Residential</option>
            </select></Field>
          <Field label="Liftgate">
            <select value={draft.liftgate === null ? "" : draft.liftgate ? "yes" : "no"} onChange={(event) => update("liftgate", event.target.value === "" ? null : event.target.value === "yes")}>
              <option value="">Not stated</option><option value="no">No liftgate</option><option value="yes">Liftgate</option>
            </select></Field>
          <Field label="Customer contact"><input value={draft.customer_contact} maxLength={120} autoComplete="off" onChange={(event) => update("customer_contact", event.target.value)} /></Field>
          <Field label="Customer emails" hint="Quote emails go here. Separate several with ; or ,">
            <input type="text" inputMode="email" value={draft.customer_emails} autoComplete="off" onChange={(event) => update("customer_emails", event.target.value)} /></Field>
          <Field label="Customer phone"><input type="tel" value={draft.customer_phone} maxLength={40} autoComplete="off" onChange={(event) => update("customer_phone", event.target.value)} /></Field>
          <Field label="Notes" wide><textarea rows={3} value={draft.notes} onChange={(event) => update("notes", event.target.value)} /></Field>
        </div>}

        {tab === "trace" && <div className="trace">
          <section>
            <h3><FileText size={15} />Source document</h3>
            {trace.source_file ? <p>{trace.source_file.filename} · read with {trace.source_file.adapter?.replaceAll("_", " ")} · {date(trace.source_file.created_at)}
              {" "}<a className="btn btn-ghost btn-sm" href={`/api/source-files/${trace.source_file.id}`}><Download size={14} />Original file</a></p>
              : <p className="muted">{order.status === "reference_only" ? "Reference only — created from a typed order number." : "Entered by hand."}</p>}
          </section>
          <section>
            <h3><History size={15} />Quotes</h3>
            {trace.quotes.length ? <ul className="item-list compact">{trace.quotes.map((quote) => (
              <li key={quote.id}><button type="button" className="item-link" onClick={() => openQuote(quote.id)}>
                <span className="item-main"><b>{quote.number} · {quote.kind === "packed" ? "Pallet plan" : quote.kind === "measured" ? "Measured pallets" : "External"}{quote.role === "combined" ? " (combined)" : ""}</b>
                  <small>{quote.orders.map((entry) => entry.order_number).join(", ")} · {plural(quote.summary.pallets, "pallet")} · {weight(quote.summary.total_weight)} · {date(quote.created_at)}</small></span>
                <span className="item-side">{quote.cost.customer_price_cents != null && <b>{money(quote.cost.customer_price_cents)}</b>}<CustomerBadge status={quote.customer_status} /><StatusBadge kind="quote" status={quote.status} /></span>
              </button></li>))}</ul> : <p className="muted">Not quoted yet.</p>}
          </section>
          <section>
            <h3><Truck size={15} />Shipment</h3>
            {trace.shipment ? <p><a className="inline-link" href={href(`/shipments/${trace.shipment.id}`)}>{trace.shipment.number} · {trace.shipment.name}</a>{" "}
              <StatusBadge kind="shipment" status={trace.shipment.status} />{" "}
              {trace.shipment.order_count > 1 && <Badge tone="info">shared by {trace.shipment.order_count} orders</Badge>}
              <br /><small className="muted">Shipment freight cost {money(trace.shipment.expense_total_cents)}{trace.shipment.allocated_cents != null ? ` · this order's allocated share ${money(trace.shipment.allocated_cents)} (${trace.shipment.allocation_method})` : " · not allocated yet"}</small></p>
              : <p className="muted">Not in a shipment yet.</p>}
          </section>
          {!locked && order.status !== "cancelled" && <section>
            <button type="button" className="btn btn-ghost btn-ghost-danger btn-sm" disabled={busy} onClick={() => setStatus("cancelled")}>Cancel this order…</button>
          </section>}
        </div>}

        {tab === "history" && <>
          <p className="muted small-line">Revision {order.revision}. {trace.revisions.length} saved revision{trace.revisions.length === 1 ? "" : "s"}.</p>
          <Timeline entries={trace.history} />
        </>}
      </>}
      {quoteId && <QuoteDetail quoteId={quoteId} notify={notify}
        position={(() => { const entry = history?.quotes.find((quote) => quote.id === quoteId); return entry?.time_quoted ? { n: entry.time_quoted, of: history.quoted_count } : null; })()}
        onClose={() => openQuote(null)} onNavigate={onOpenQuote ? undefined : () => setLocalQuote(null)}
        onChanged={() => {
          // Quote changes can add invoice numbers or a shipment: refresh what the drawer shows, keep edits in progress.
          loadHistory();
          getJson(`/api/orders/${orderId}`).then((value) => { if (orderId === current.current) setTrace(value); }).catch(() => {});
          onSaved?.();
        }} />}
    </Drawer>
  );
}
