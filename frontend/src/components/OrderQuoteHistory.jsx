import { useState } from "react";
import { Boxes } from "lucide-react";
import { CustomerBadge, TermsChips, selectedOption, sourceName } from "./QuoteBits";
import { date, money, plural, weight } from "../lib/format";
import { href } from "../lib/route";
import "../styles/quotes.css";

export const ORDER_STEPS = ["Imported", "Quoted", "Sent", "Approved", "Booked", "In transit", "Delivered", "Closed"];
const SHIPMENT_STAGE = { planning: 3, booked: 4, in_transit: 5, delivered: 6, closed: 7 };

// Where the order is: Imported → Quoted → Sent → Approved → Booked → In transit → Delivered → Closed,
// from its quote history and the status of the shipment it is on.
export function orderStage(history, shipment) {
  const quotes = (history?.quotes || []).filter((quote) => quote.counted);
  let stage = 0;
  if (quotes.length) stage = 1;
  if (quotes.some((quote) => quote.customer_status !== "not_sent" || quote.sent_at)) stage = 2;
  const approved = Boolean(history?.approved_quote_id);
  if (approved) stage = 3;
  const status = shipment && shipment.status !== "cancelled" ? shipment.status : null;
  if (status && (status !== "planning" || approved)) stage = Math.max(stage, SHIPMENT_STAGE[status] ?? 0);
  const latest = quotes[0];
  const note = !approved && stage === 2 && latest && ["declined", "expired"].includes(latest.customer_status)
    ? { label: latest.customer_status === "declined" ? "Declined" : "Expired", tone: latest.customer_status === "declined" ? "danger" : "warn" }
    : null;
  return { stage, note };
}

export function OrderTrack({ history, shipment }) {
  const { stage, note } = orderStage(history, shipment);
  return (<>
    <ol className="oq-track" aria-label={`Order progress: ${ORDER_STEPS[stage]}${note ? ` (${note.label.toLowerCase()})` : ""}`}>
      {ORDER_STEPS.map((label, index) => {
        const state = index < stage ? "is-done" : index === stage ? "is-current" : "";
        const tone = index === stage && note ? `tone-${note.tone}` : "";
        return (
          <li key={label} className={`${state} ${tone}`} aria-current={index === stage ? "step" : undefined}>
            <span className="oq-dot" aria-hidden="true" />
            <span className="oq-label">{label}{index === stage && note && <small>{note.label}</small>}</span>
          </li>
        );
      })}
    </ol>
    {/* Phones show the dots only, with the current step spelled out here. */}
    <p className="oq-now" aria-hidden="true">Step {stage + 1} of {ORDER_STEPS.length}: <b>{ORDER_STEPS[stage]}</b>{note && <span className={`${note.tone}-text`}> · {note.label}</span>}</p>
  </>);
}

function QuoteCard({ quote, orderId, onOpen }) {
  const others = quote.orders.filter((order) => order.id !== orderId).map((order) => order.order_number);
  const option = selectedOption(quote);
  const zip = quote.terms?.postal_code;
  return (
    <button type="button" className={`oq-card ${quote.counted ? "" : "is-draft"} ${quote.customer_status === "approved" ? "is-approved" : ""}`}
      onClick={() => onOpen(quote)} aria-label={`Open quote ${quote.number}`}>
      <span className="oq-card-top">
        {quote.time_quoted ? <span className="oq-time" title={`Time quoted #${quote.time_quoted}`}>#{quote.time_quoted}</span> : <span className="oq-time" title="Draft: not priced or sent">—</span>}
        <b>{quote.number}</b>
        {quote.counted ? <CustomerBadge status={quote.customer_status} /> : <span className="badge">Draft</span>}
      </span>
      <span className="oq-card-line">
        <span>{date(quote.created_at)}</span>
        <span>{plural(quote.summary.pallets, "pallet")} · {weight(quote.summary.total_weight)}</span>
        {others.length > 0 && <span className="muted">with {others.slice(0, 3).join(", ")}{others.length > 3 ? ` +${others.length - 3}` : ""}</span>}
      </span>
      <TermsChips labels={quote.terms_labels} zip={zip} />
      <span className="oq-card-price">
        <span>{option ? <>{sourceName(option)}{option.carrier ? ` · ${option.carrier}` : ""} <b>{money(option.price_cents)}</b></>
          : <span className="muted">{quote.options?.length ? `${plural(quote.options.length, "price")} found, none used` : "No prices yet"}</span>}</span>
        <span>Customer <b>{money(quote.cost.customer_price_cents)}</b></span>
      </span>
    </button>
  );
}

// Top of the order drawer: progress, "Quoted N times" and one card per quote, newest first.
export default function OrderQuoteHistory({ orderId, history, shipment, error, onOpen }) {
  const [all, setAll] = useState(false);
  const quotes = history?.quotes || [];
  const shown = all ? quotes : quotes.slice(0, 3);
  return (
    <section className="oq-panel" aria-labelledby={`oq-head-${orderId}`}>
      <div className="oq-head">
        <h3 id={`oq-head-${orderId}`}>{history ? (history.quoted_count ? `Quoted ${plural(history.quoted_count, "time")}` : "Not quoted yet") : "Quote history"}
          {history && quotes.length > history.quoted_count && <small>{plural(quotes.length - history.quoted_count, "draft")} not priced</small>}</h3>
        {history && <a className="btn btn-ghost btn-sm" href={href("/quote/new", { orders: orderId })}><Boxes size={14} />Quote again</a>}
      </div>
      <OrderTrack history={history} shipment={shipment} />
      {error && <p className="field-error" role="alert">Quote history unavailable: {error}</p>}
      {quotes.length > 0 && <ul className="oq-cards">
        {shown.map((quote) => <li key={quote.id}><QuoteCard quote={quote} orderId={orderId} onOpen={onOpen} /></li>)}
      </ul>}
      {quotes.length > 3 && <button type="button" className="link-btn oq-more" aria-expanded={all} onClick={() => setAll((value) => !value)}>
        {all ? "Show the latest 3" : `Show all ${quotes.length} quotes`}</button>}
    </section>
  );
}
