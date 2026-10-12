import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban, Check, CheckCircle2, ChevronDown, ExternalLink, Hourglass, Mail, MailCheck, Pencil, Plus, Receipt, RotateCcw,
  Send, Trash2, Truck, Undo2, X,
} from "lucide-react";
import { getJson, sendJson } from "../api";
import { FEATURES } from "../edition";
import { ErrorBlock, Field, Loading, StatusBadge, Timeline } from "./Common";
import { EmailDraftLayer, LayerDrawer, LayerModal } from "./QuoteLayers";
import {
  CustomerBadge, QUOTE_KINDS, TermsChips, marginOf, nextSource, ruleLabel, selectedOption, sourceName,
} from "./QuoteBits";
import { LOCATION_TYPES, QUOTE_SOURCES, SHIPMENT_STATUS, centsToInput, date, money, parseMoney, plural, relative, weight } from "../lib/format";
import { href, parseHash } from "../lib/route";
import "../styles/quotes.css";

const nextFrame = (callback) => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(callback) : setTimeout(callback, 0));

// One quote as a "time quoted": delivery terms, the prices found per broker page and which one was
// used, the customer price from the pricing rule (or typed), the customer's answer, emails and history.
//   <QuoteDetail quoteId onClose onChanged={(quote) => …} notify position={{ n, of }} focusPrices />
export default function QuoteDetail({ quoteId, onClose, onChanged, notify, position = null, focusPrices = false, onNavigate }) {
  const [quote, setQuote] = useState(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [live, setLive] = useState("");
  const [place, setPlace] = useState(null);
  const [modal, setModal] = useState(null);
  const [email, setEmail] = useState(null);
  const [askSent, setAskSent] = useState(false);
  const [approval, setApproval] = useState(null);
  const [away, setAway] = useState(false);
  const generation = useRef(0);
  const selecting = useRef(Promise.resolve());
  const focused = useRef(false);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const navigated = useRef(onNavigate);
  navigated.current = onNavigate;

  const load = useCallback(async () => {
    const token = ++generation.current;
    setLoadError("");
    try {
      const value = await getJson(`/api/quotes/${quoteId}/detail`);
      if (token === generation.current) setQuote(value);
    } catch (reason) {
      if (token === generation.current) setLoadError(reason.message);
    }
  }, [quoteId]);

  useEffect(() => {
    if (!quoteId) return;
    setQuote(null); setError(""); setLive(""); setModal(null); setEmail(null); setAskSent(false); setApproval(null);
    setAway(false); setPlace(null); focused.current = false;
    load();
  }, [quoteId]);

  // A link inside the panel that leaves this page (a shipment, another page) closes it.
  useEffect(() => {
    if (!quoteId) return undefined;
    const start = parseHash().path;
    const check = () => { if (parseHash().path !== start) { setAway(true); navigated.current?.(); } };
    window.addEventListener("hashchange", check);
    return () => window.removeEventListener("hashchange", check);
  }, [quoteId]);

  // "Quoted N of M" for the first order, when the caller didn't pass it.
  const firstOrder = quote?.orders?.[0]?.id;
  useEffect(() => {
    if (position || !firstOrder || !quote?.counted) return undefined;
    let stale = false;
    getJson(`/api/orders/${firstOrder}/quote-history`).then((history) => {
      const entry = history.quotes.find((entry) => entry.id === quote.id);
      if (!stale && entry?.time_quoted) setPlace({ n: entry.time_quoted, of: history.quoted_count, order: history.order.order_number });
    }).catch(() => {});
    return () => { stale = true; };
  }, [firstOrder, quote?.id, quote?.counted, position]);

  const editable = quote ? quote.status !== "void" : false;
  useEffect(() => {
    if (!quote || !focusPrices || focused.current || !editable) return;
    focused.current = true;
    nextFrame(() => document.getElementById(`qd-${quote.id}-add-carrier`)?.focus());
  }, [quote, focusPrices, editable]);

  const apply = (updated, message) => {
    setQuote(updated);
    if (message) { setLive(message); notify?.(message); }
    changed.current?.(updated);
  };

  const run = async (key, action) => {
    setBusy(key); setError("");
    try { return await action(); }
    catch (reason) { setError(reason.message); return null; }
    finally { setBusy(""); }
  };

  const path = (suffix = "") => `/api/quotes/${quote.id}${suffix}`;

  const select = (optionId) => {
    if (!editable || optionId === quote.selected_option_id) return;
    const option = quote.options.find((entry) => entry.id === optionId) || null;
    setQuote((current) => ({ ...current, selected_option_id: optionId, selected_option: option, pending_price: true }));
    setError("");
    // Requests run one after another so the last choice wins even when arrow keys move fast.
    selecting.current = selecting.current.then(async () => {
      try {
        const updated = await sendJson(path("/select"), "POST", { option_id: optionId });
        apply(updated, option
          ? `Using ${sourceName(option)}${option.carrier ? ` · ${option.carrier}` : ""} ${money(option.price_cents)}. Customer price ${money(updated.cost.customer_price_cents)}.`
          : "No price is marked as used.");
      } catch (reason) {
        setError(reason.message);
        await load();
      }
    });
  };

  const answer = (kind, body = {}) => run(kind, async () => {
    const updated = await sendJson(path(`/${kind}`), "POST", body);
    const words = { sent: "marked as sent", decline: "marked declined", expire: "marked expired", reopen: "reopened" };
    apply(updated, `${updated.number} ${words[kind]}.`);
    setAskSent(false);
    return updated;
  });

  if (!quoteId) return null;
  const cs = quote?.customer_status;
  const title = quote
    ? <>Quote {quote.number} <StatusBadge kind="quote" status={quote.status} /> <CustomerBadge status={cs} /></>
    : "Quote";
  const where = position || place;
  const subtitle = quote && <>
    <span>{where && <b>Quoted {where.n} of {where.of}{where.order && quote.orders.length > 1 ? ` for ${where.order}` : ""} · </b>}{QUOTE_KINDS[quote.kind] || quote.kind} · {date(quote.created_at)}</span>
    <span className="qd-links">
      {quote.orders.length ? quote.orders.map((order) => <a key={order.id} className="inline-link" href={href("/orders", { open: order.id })}>{order.order_number}</a>)
        : <span className="muted">No orders linked</span>}
      {quote.shipment && <a className="inline-link qd-shipment-link" href={href(`/shipments/${quote.shipment.id}`)}><Truck size={13} aria-hidden="true" />{quote.shipment.number}</a>}
    </span>
  </>;

  return (
    <>
      <LayerDrawer open={!away} onClose={onClose} title={title} subtitle={subtitle} labelId={`qd-title-${quoteId}`}
        footer={<>
          {quote && <a className="btn btn-ghost btn-sm" href={href(`/quotes/${quote.id}`)}><ExternalLink size={14} />Full quote record</a>}
          {/* The page's toasts sit under this panel, so the last change is confirmed here (and announced). */}
          <span className="qd-status" role="status" aria-live="polite">{live && <><Check size={14} aria-hidden="true" /><span>{live}</span></>}</span>
          <button type="button" className="btn btn-secondary" onClick={onClose}>Close</button>
        </>}>
        {loadError && <ErrorBlock message={loadError} onRetry={load} />}
        {!quote && !loadError && <Loading label="Loading the quote…" />}
        {quote && <>
          {error && <ErrorBlock message={error} />}
          {!editable && <div className="callout compact-callout"><Ban size={16} /><span>This quote is void. It stays in the history, but it can't be priced or answered; record a new quote instead.</span></div>}
          <SummaryStrip quote={quote} />
          {FEATURES.freightWorkflow && <AnswerSection quote={quote} busy={busy} editable={editable} approval={approval} askSent={askSent}
            onAnswer={answer} onModal={setModal} onEmail={setEmail} onDismissAsk={() => setAskSent(false)} />}
          <PricesSection quote={quote} editable={editable} busy={busy} onSelect={select}
            onAdd={(payload) => run("add", async () => {
              const updated = await sendJson(path("/options"), "POST", payload);
              apply(updated, `${sourceName(payload)} ${money(payload.price_cents)} added${payload.select ? " and used for the quote" : ""}.`);
              return updated;
            })}
            onEdit={(optionId, payload) => run(`edit-${optionId}`, async () => {
              const updated = await sendJson(path(`/options/${optionId}`), "PUT", payload);
              apply(updated, "Price updated.");
              return updated;
            })}
            onRemove={(optionId) => run(`remove-${optionId}`, async () => {
              const updated = await sendJson(path(`/options/${optionId}`), "DELETE");
              apply(updated, "Price removed.");
              return updated;
            })} />
          <CustomerPriceSection quote={quote} editable={editable} busy={busy}
            onPrice={(cents) => run("price", async () => {
              const updated = await sendJson(path("/customer-price"), "PUT", { cents });
              apply(updated, cents === null ? `Customer price back to the pricing rule: ${money(updated.cost.customer_price_cents)}.`
                : `Customer price set to ${money(cents)}.`);
              return updated;
            })} />
          <TermsSection quote={quote} editable={editable} busy={busy}
            onSave={(terms) => run("terms", async () => {
              const updated = await sendJson(path("/terms"), "PUT", terms);
              apply(updated, "Delivery terms saved.");
              return updated;
            })} />
          <PalletsSection quote={quote} />
          <HistorySection quoteId={quote.id} version={quote.updated_at} />
        </>}
      </LayerDrawer>

      {quote && <ApproveModal open={modal === "approve"} quote={quote} onClose={() => setModal(null)}
        onApproved={(result) => {
          setModal(null);
          setApproval({ shipment: result.shipment, created: result.shipment_created });
          apply(result.quote, `${result.quote.number} approved. Shipment ${result.shipment.number} ${result.shipment_created ? "created" : "linked"}.`);
        }} />}
      {quote && <NoteModal open={modal === "decline" || modal === "expire"} kind={modal} quote={quote} busy={Boolean(busy)}
        onClose={() => setModal(null)}
        onSubmit={async (note) => { const done = await answer(modal, { note }); if (done) setModal(null); }} />}
      {quote && <EmailDraftLayer open={Boolean(email)} kind={email} quoteId={quote.id} notify={notify}
        onClose={() => setEmail(null)}
        onPrepared={() => {
          const kind = email;
          setEmail(null);
          if (kind === "quote" && quote.customer_status === "not_sent") setAskSent(true);
        }} />}
    </>
  );
}

// ------------------------------------------------------------------------------------------ summary

function SummaryStrip({ quote }) {
  const option = selectedOption(quote);
  const margin = marginOf(quote);
  const pending = quote.pending_price;
  return (
    <dl className="qd-strip">
      <div className="qd-tile qd-tile-main">
        <dt>Customer price</dt>
        <dd aria-busy={pending ? "true" : undefined}>{pending ? <span className="muted">Updating…</span> : money(quote.cost.customer_price_cents)}</dd>
        <small>{quote.cost.customer_price_cents == null ? "Not priced yet" : quote.price_manual ? "Typed by hand" : "Pricing rule"}</small>
      </div>
      <div className="qd-tile">
        <dt>Price used</dt>
        <dd>{option ? money(option.price_cents) : money(quote.cost.amount_cents)}</dd>
        <small>{option ? [sourceName(option), option.carrier].filter(Boolean).join(" · ") : quote.cost.amount_cents != null ? [quote.cost.broker, quote.cost.carrier].filter(Boolean).join(" · ") || "Entered by hand" : "Not chosen yet"}</small>
      </div>
      <div className={`qd-tile ${margin && margin.cents < 0 ? "is-loss" : ""}`}>
        <dt>Expected margin</dt>
        <dd>{pending || !margin ? "—" : money(margin.cents)}</dd>
        <small>{!pending && margin?.percent != null ? `${margin.percent.toFixed(1)}% margin` : "Price − cost"}</small>
      </div>
      <div className="qd-tile">
        <dt>Freight</dt>
        <dd>{plural(quote.summary.pallets, "pallet")}</dd>
        <small>{weight(quote.summary.total_weight)}</small>
      </div>
    </dl>
  );
}

// ------------------------------------------------------------------------------------------ customer answer

const ANSWER_TEXT = {
  not_sent: () => "Not sent to the customer yet.",
  sent: (quote) => `Sent ${date(quote.sent_at)} (${relative(quote.sent_at)}). Waiting for the customer's answer.`,
  approved: (quote) => `Approved ${date(quote.responded_at)}.`,
  declined: (quote) => `Declined ${date(quote.responded_at)}.`,
  expired: (quote) => `Marked expired ${date(quote.responded_at)}.`,
};

function AnswerSection({ quote, busy, editable, approval, askSent, onAnswer, onModal, onEmail, onDismissAsk }) {
  const cs = quote.customer_status;
  const shipment = quote.shipment;
  const shipmentActive = Boolean(shipment && shipment.is_combined_quote && shipment.status !== "cancelled");
  const hasPrice = quote.cost.customer_price_cents != null || Boolean(selectedOption(quote));
  const hasOrders = quote.orders.length > 0;
  const canApprove = editable && hasPrice && hasOrders;
  const canDecline = editable && (["not_sent", "sent", "expired"].includes(cs) || (cs === "approved" && !shipmentActive));
  const canExpire = editable && ["not_sent", "sent"].includes(cs);
  const canReopen = editable && (["declined", "expired"].includes(cs) || (cs === "approved" && !shipmentActive));
  const why = !editable ? "" : !hasPrice ? "Approving needs a price: choose the price that was used below, or type the customer price."
    : !hasOrders ? "Link this quote to its order before approving it."
      : cs === "approved" && shipmentActive ? `This is the approved quote of shipment ${shipment.number}. To change the answer, cancel that shipment first.` : "";
  const approveButton = (primary) => (
    <button type="button" className={`btn ${primary ? "btn-primary" : "btn-secondary"}`} disabled={!canApprove || Boolean(busy)}
      aria-describedby={why ? `qd-why-${quote.id}` : undefined} onClick={() => onModal("approve")}>
      {cs === "approved" ? <><Receipt size={15} />Add invoice numbers</> : <><CheckCircle2 size={15} />Approved…</>}
    </button>
  );
  return (
    <section className="qd-section qd-answer" aria-labelledby={`qd-answer-${quote.id}`}>
      <div className="qd-section-head">
        <h3 id={`qd-answer-${quote.id}`}>Customer answer</h3>
        <div className="qd-email-actions">
          <button type="button" className="btn btn-ghost btn-sm" disabled={!editable || quote.cost.customer_price_cents == null}
            title={quote.cost.customer_price_cents == null ? "Price the quote first" : "Prepare the quote email in Outlook"} onClick={() => onEmail("quote")}><Mail size={14} />Email quote</button>
          {cs === "sent" && <button type="button" className="btn btn-ghost btn-sm" disabled={!editable} onClick={() => onEmail("follow_up")}><MailCheck size={14} />Follow up</button>}
        </div>
      </div>
      <p className={`qd-answer-line tone-${cs}`}>{ANSWER_TEXT[cs]?.(quote)}{quote.response_note ? <span className="muted"> “{quote.response_note}”</span> : null}</p>

      {askSent && cs === "not_sent" && <div className="qd-ask" role="group" aria-label="Mark as sent">
        <Send size={16} aria-hidden="true" /><span>Email opened in Outlook. Mark this quote as sent?</span>
        <button type="button" className="btn btn-primary btn-sm" disabled={Boolean(busy)} onClick={() => onAnswer("sent")}>Mark as sent</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDismissAsk}>Not now</button>
      </div>}

      {editable && <div className="qd-answer-actions">
        {cs === "not_sent" && <button type="button" className="btn btn-primary" disabled={Boolean(busy)} onClick={() => onAnswer("sent")}><Send size={15} />Mark sent</button>}
        {["not_sent", "sent", "declined", "expired", "approved"].includes(cs) && approveButton(cs === "sent")}
        {canReopen && <button type="button" className={`btn ${cs === "approved" ? "btn-secondary" : "btn-primary"}`} disabled={Boolean(busy)} onClick={() => onAnswer("reopen")}><RotateCcw size={15} />Reopen</button>}
        {canDecline && <button type="button" className="btn btn-secondary" disabled={Boolean(busy)} onClick={() => onModal("decline")}><X size={15} />Declined…</button>}
        {canExpire && <button type="button" className="btn btn-secondary" disabled={Boolean(busy)} onClick={() => onModal("expire")}><Hourglass size={15} />Expired…</button>}
        {cs === "sent" && <button type="button" className="btn btn-ghost btn-sm" disabled={Boolean(busy)} onClick={() => onAnswer("sent")}><Undo2 size={14} />Sent again today</button>}
      </div>}
      {why && <p id={`qd-why-${quote.id}`} className={`qd-why ${hasPrice && hasOrders ? "is-info" : ""}`}>{why}</p>}

      {shipment && <div className={`qd-shipment ${approval ? "is-new" : ""}`}>
        <Truck size={16} aria-hidden="true" />
        <span>{approval ? (approval.created ? "Shipment created: " : "Linked to shipment ") : "Shipment "}
          <a className="inline-link" href={href(`/shipments/${shipment.id}`)}>{shipment.number}</a>
          {" "}<span className="muted">· {SHIPMENT_STATUS[shipment.status]?.label || shipment.status}{shipment.is_combined_quote ? "" : " · uses another quote"}</span></span>
        {approval && <a className="btn btn-secondary btn-sm" href={href(`/shipments/${shipment.id}`)}>Open shipment</a>}
      </div>}
    </section>
  );
}

// ------------------------------------------------------------------------------------------ prices found

const blankOption = (source) => ({ source, carrier: "", service: "", transit: "", price: "", reference: "", expires_on: "", notes: "", select: false });

function toForm(option) {
  return { source: option.source, source_label: option.source_label, carrier: option.carrier || "", service: option.service || "",
    transit: option.transit_days ?? "", price: centsToInput(option.price_cents), reference: option.reference || "",
    expires_on: option.expires_on || "", notes: option.notes || "" };
}

function PricesSection({ quote, editable, busy, onSelect, onAdd, onEdit, onRemove }) {
  const [editing, setEditing] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [addKey, setAddKey] = useState(0);
  const options = quote.options || [];
  const lowest = options.length > 1 ? Math.min(...options.map((option) => option.price_cents)) : null;
  const suggested = useMemo(() => nextSource(options), [options]);
  const name = `qd-used-${quote.id}`;
  return (
    <section className="qd-section" aria-labelledby={`qd-prices-${quote.id}`}>
      <div className="qd-section-head">
        <h3 id={`qd-prices-${quote.id}`}>Prices found <span className="qd-count">{options.length}</span></h3>
        {editable && quote.selected_option_id && <button type="button" className="link-btn" disabled={Boolean(busy)} onClick={() => onSelect(null)}>Clear the used price</button>}
      </div>
      {options.length > 0 ? (
        <div className="qd-table-wrap">
          <table className="qd-options">
            <caption className="sr-only">Prices found on the broker pages; choose the one used for the quote</caption>
            <thead><tr>
              <th scope="col" className="qd-col-use">Used</th><th scope="col">Source</th><th scope="col">Carrier</th>
              <th scope="col" className="num">Transit</th><th scope="col" className="num">Price</th><th scope="col">Reference</th>
              <th scope="col">Notes</th><th scope="col"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>
              {options.map((option) => {
                const used = option.id === quote.selected_option_id;
                const label = `${sourceName(option)}${option.carrier ? ` ${option.carrier}` : ""} ${money(option.price_cents)}`;
                if (editing === option.id) return (
                  <tr key={option.id} className="qd-editing"><td colSpan={8}>
                    <OptionForm id={`qd-${quote.id}-edit`} initial={toForm(option)} busy={busy === `edit-${option.id}`} submitLabel="Save price" autoFocus
                      onCancel={() => setEditing(null)}
                      onSubmit={async (payload) => { if (await onEdit(option.id, payload)) setEditing(null); }} />
                  </td></tr>
                );
                return (
                  <tr key={option.id} className={`${used ? "is-used" : ""} ${editable ? "is-selectable" : ""}`}
                    onClick={(event) => { if (editable && !event.target.closest("button, input, a")) onSelect(option.id); }}>
                    <td className="qd-col-use"><input type="radio" name={name} checked={used} disabled={!editable} aria-label={`Use ${label} for the quote`} onChange={() => onSelect(option.id)} /></td>
                    <td className="qd-col-source"><b>{sourceName(option)}</b></td>
                    <td className="qd-col-carrier"><span className="cell-stack"><span>{option.carrier || <span className="muted">—</span>}</span>{option.service && <small>{option.service}</small>}</span></td>
                    <td className="num qd-col-transit">{option.transit_days != null ? plural(option.transit_days, "day") : <span className="muted">—</span>}</td>
                    <td className="num qd-col-price"><b>{money(option.price_cents)}</b>{option.price_cents === lowest && <span className="qd-lowest">Lowest</span>}</td>
                    <td className={`qd-col-ref ${option.reference || option.expires_on ? "" : "is-empty"}`}><span className="cell-stack"><span>{option.reference || <span className="muted">—</span>}</span>{option.expires_on && <small>Expires {date(option.expires_on)}</small>}</span></td>
                    <td className={`qd-col-notes ${option.notes ? "" : "is-empty"}`}>{option.notes || <span className="muted">—</span>}</td>
                    <td className="qd-col-actions">
                      {editable && (confirming === option.id ? <span className="qd-confirm">
                        <button type="button" className="btn btn-danger btn-sm" autoFocus disabled={busy === `remove-${option.id}`} onClick={async () => { await onRemove(option.id); setConfirming(null); }}>Remove</button>
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setConfirming(null)}>Keep</button>
                      </span> : <>
                        <button type="button" className="icon-btn" aria-label={`Edit ${label}`} onClick={() => { setConfirming(null); setEditing(option.id); }}><Pencil size={15} /></button>
                        <button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove ${label}`} onClick={() => { setEditing(null); setConfirming(option.id); }}><Trash2 size={15} /></button>
                      </>)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="qd-empty">No prices yet. Type each broker's price as you find it — <kbd>Enter</kbd> adds the row.</p>}
      {editable && options.length < 30 && <div className="qd-add">
        <h4 className="sr-only">Add a price</h4>
        <OptionForm key={`${addKey}-${suggested}`} id={`qd-${quote.id}-add`} initial={blankOption(suggested)} busy={busy === "add"} submitLabel="Add price"
          showUse={!quote.selected_option_id}
          onSubmit={async (payload) => {
            if (await onAdd(payload)) {
              setAddKey((value) => value + 1);
              nextFrame(() => document.getElementById(`qd-${quote.id}-add-carrier`)?.focus());
            }
          }} />
      </div>}
    </section>
  );
}

function OptionForm({ id, initial, busy, submitLabel, onSubmit, onCancel, autoFocus = false, showUse = false }) {
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState("");
  const set = (field) => (event) => { setProblem(""); setForm((current) => ({ ...current, [field]: event.target.type === "checkbox" ? event.target.checked : event.target.value })); };
  const submit = async (event) => {
    event?.preventDefault();
    if (busy) return;
    const price = parseMoney(form.price);
    if (price === null || Number.isNaN(price) || price < 0) { setProblem("Enter the price in dollars, for example 968.88."); document.getElementById(`${id}-price`)?.focus(); return; }
    const transit = form.transit === "" ? null : Number(form.transit);
    if (transit !== null && (!Number.isInteger(transit) || transit < 0 || transit > 60)) { setProblem("Transit days must be a whole number from 0 to 60."); return; }
    const payload = { source: form.source, carrier: form.carrier.trim(), service: form.service.trim(), transit_days: transit, price_cents: price,
      reference: form.reference.trim(), expires_on: form.expires_on || null, notes: form.notes.trim() };
    if (form.source_label && form.source === initial.source) payload.source_label = form.source_label;
    await onSubmit(onCancel ? payload : { ...payload, select: Boolean(form.select) });
  };
  // Enter in any field (the source list too) saves the row; Escape cancels an edit.
  const keys = (event) => {
    if (event.key === "Escape" && onCancel) { event.preventDefault(); onCancel(); return; }
    if (event.key === "Enter" && !event.shiftKey && ["INPUT", "SELECT"].includes(event.target.tagName) && event.target.type !== "checkbox") {
      event.preventDefault();
      submit();
    }
  };
  return (
    <form className="qd-option-form" onSubmit={submit} onKeyDown={keys} data-own-escape={onCancel ? "" : undefined} noValidate>
      <label className="field field-sm qd-f-source"><span>Source</span>
        <select id={`${id}-source`} value={form.source} onChange={set("source")}>
          {Object.entries(QUOTE_SOURCES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
      <label className="field field-sm qd-f-carrier"><span>Carrier</span><input id={`${id}-carrier`} value={form.carrier} autoFocus={autoFocus} maxLength={120} placeholder="e.g. Saia" onChange={set("carrier")} /></label>
      <label className={`field field-sm qd-f-price ${problem.startsWith("Enter the price") ? "has-error" : ""}`}><span>Price ($)</span>
        <input id={`${id}-price`} inputMode="decimal" value={form.price} placeholder="0.00" aria-invalid={problem.startsWith("Enter the price") || undefined} onChange={set("price")} /></label>
      <label className="field field-sm qd-f-transit"><span>Transit days</span><input type="number" min="0" max="60" step="1" value={form.transit} onChange={set("transit")} /></label>
      <label className="field field-sm qd-f-service"><span>Service</span><input value={form.service} maxLength={80} placeholder="Standard" onChange={set("service")} /></label>
      <label className="field field-sm qd-f-ref"><span>Broker reference</span><input value={form.reference} maxLength={120} onChange={set("reference")} /></label>
      <label className="field field-sm qd-f-expires"><span>Expires</span><input type="date" value={form.expires_on} onChange={set("expires_on")} /></label>
      <label className="field field-sm qd-f-notes"><span>Notes</span><input value={form.notes} maxLength={500} onChange={set("notes")} /></label>
      <div className="qd-f-actions">
        {showUse && <label className="chip-check qd-use-check"><input type="checkbox" checked={Boolean(form.select)} onChange={set("select")} /><span>Use for the quote</span></label>}
        {onCancel && <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>}
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>{onCancel ? <Check size={14} /> : <Plus size={14} />}{submitLabel}</button>
      </div>
      {problem && <p className="field-error qd-f-problem" role="alert">{problem}</p>}
    </form>
  );
}

// ------------------------------------------------------------------------------------------ customer price

function CustomerPriceSection({ quote, editable, busy, onPrice }) {
  const option = selectedOption(quote);
  const [preview, setPreview] = useState(null);
  const [typing, setTyping] = useState(null);
  const cache = useRef(new Map());
  const carrier = option?.price_cents;
  useEffect(() => {
    if (carrier === undefined || carrier === null) { setPreview(null); return undefined; }
    const key = `${quote.company_id}:${carrier}`;
    if (cache.current.has(key)) { setPreview(cache.current.get(key)); return undefined; }
    let stale = false;
    sendJson("/api/quotes/pricing-preview", "POST", { company_id: quote.company_id, carrier_cents: carrier })
      .then((value) => { cache.current.set(key, value); if (!stale) setPreview(value); })
      .catch(() => { if (!stale) setPreview(null); });
    return () => { stale = true; };
  }, [carrier, quote.company_id]);

  const price = quote.cost.customer_price_cents;
  const margin = marginOf(quote);
  const typed = typing === null ? null : parseMoney(typing);
  const invalid = typing !== null && (typed === null || Number.isNaN(typed) || typed < 0);
  const save = async (event) => {
    event.preventDefault();
    if (invalid) return;
    if (await onPrice(typed)) setTyping(null);
  };
  const ruleText = preview && option ? <>
    <span className="qd-calc">{money(option.price_cents)} <span className="muted">({[sourceName(option), option.carrier].filter(Boolean).join(" · ")})</span> → <b>{money(preview.customer_price_cents)}</b></span>
    <span className="muted"> ({ruleLabel(preview.rule)})</span>
  </> : null;

  return (
    <section className="qd-section" aria-labelledby={`qd-price-${quote.id}`}>
      <div className="qd-section-head"><h3 id={`qd-price-${quote.id}`}>Customer price</h3></div>
      <div className="qd-price">
        <div className="qd-price-value">
          <strong aria-busy={quote.pending_price ? "true" : undefined}>{quote.pending_price ? "…" : money(price)}</strong>
          {quote.price_manual && <span className="badge badge-warn">Typed by hand</span>}
        </div>
        <div className="qd-price-explain">
          {!option && price == null && <p className="muted">Choose the price that was used above, or type the customer price.</p>}
          {!option && price != null && <p className="muted">No broker price is marked as used{quote.cost.amount_cents != null ? `; carrier price ${money(quote.cost.amount_cents)} was entered by hand` : ""}.</p>}
          {option && !quote.price_manual && <p>{ruleText || <span className="muted">Pricing rule…</span>}</p>}
          {option && quote.price_manual && <p><span className="muted">The pricing rule would give </span>{preview ? <b>{money(preview.customer_price_cents)}</b> : "…"}{preview && <span className="muted"> ({ruleLabel(preview.rule)})</span>}</p>}
          {margin && !quote.pending_price && <p className={`qd-margin ${margin.cents < 0 ? "is-loss" : ""}`}>Expected margin <b>{money(margin.cents)}</b>{margin.percent != null && <span className="muted"> · {margin.percent.toFixed(1)}%</span>}</p>}
        </div>
      </div>
      {editable && (typing === null ? (
        <div className="qd-price-actions">
          <button type="button" className="btn btn-secondary btn-sm" disabled={Boolean(busy)} onClick={() => setTyping(centsToInput(price))}><Pencil size={14} />Type a different price</button>
          {quote.price_manual && <button type="button" className="btn btn-ghost btn-sm" disabled={Boolean(busy)} onClick={() => onPrice(null)}><RotateCcw size={14} />Use pricing rule</button>}
        </div>
      ) : (
        <form className="qd-price-form" onSubmit={save} data-own-escape="" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setTyping(null); } }}>
          <Field label="Customer price ($)" error={invalid && typing !== "" ? "Use dollars and cents, e.g. 970.00" : ""}>
            <input inputMode="decimal" value={typing} autoFocus onChange={(event) => setTyping(event.target.value)} />
          </Field>
          <button type="submit" className="btn btn-primary btn-sm" disabled={invalid || busy === "price"}><Check size={14} />Save price</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTyping(null)}>Cancel</button>
        </form>
      ))}
    </section>
  );
}

// ------------------------------------------------------------------------------------------ delivery terms

const BLANK_TERMS = { postal_code: "", city: "", state: "", location_type: "commercial", liftgate: false, appointment: false, call_ahead: false, inside_delivery: false, notes: "" };
const TOGGLES = [["liftgate", "Liftgate"], ["appointment", "Appointment"], ["call_ahead", "Call ahead"], ["inside_delivery", "Inside delivery"]];

function TermsSection({ quote, editable, busy, onSave }) {
  // Keyed by content: other changes to the quote (a price selected) keep terms being edited.
  const termsKey = JSON.stringify(quote.terms || null);
  const initial = useMemo(() => ({ ...BLANK_TERMS, ...(quote.terms || {}) }), [termsKey]);
  const [form, setForm] = useState(initial);
  useEffect(() => { setForm(initial); }, [initial]);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const set = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const save = async (event) => {
    event.preventDefault();
    if (!dirty && quote.terms_source !== "source") return;
    await onSave({ ...form, state: form.state.toUpperCase() });
  };
  const hint = quote.terms_source === "source" ? "Shown from the pallet plan's shipping info (this quote was saved before terms were kept). Save to keep them on the quote."
    : quote.terms_source === "recorded" ? (quote.customer_status === "approved" ? "As the customer approved them. Changes are kept in the quote's history."
      : "Copied from the plan's shipping info. Until approval, re-saving the plan updates them; notes typed here are kept.")
      : "No delivery terms were recorded for this quote.";
  const group = `qd-loc-${quote.id}`;
  return (
    <section className="qd-section" aria-labelledby={`qd-terms-${quote.id}`}>
      <div className="qd-section-head">
        <h3 id={`qd-terms-${quote.id}`}>Delivery terms</h3>
        <TermsChips labels={quote.terms_labels} />
      </div>
      <p className="qd-hint">{hint}</p>
      <form className="qd-terms" onSubmit={save}>
        <fieldset disabled={!editable} className="qd-terms-fields">
          <legend className="sr-only">Delivery terms</legend>
          <div className="qd-terms-place">
            <label className="field field-sm"><span>ZIP</span><input inputMode="numeric" maxLength={12} value={form.postal_code} onChange={(event) => set("postal_code", event.target.value)} /></label>
            <label className="field field-sm"><span>City</span><input maxLength={80} value={form.city} onChange={(event) => set("city", event.target.value)} /></label>
            <label className="field field-sm qd-state"><span>State</span><input maxLength={2} value={form.state} onChange={(event) => set("state", event.target.value.toUpperCase())} /></label>
          </div>
          <div className="qd-loc" role="radiogroup" aria-label="Location type">
            {Object.entries(LOCATION_TYPES).map(([key, label]) => (
              <label key={key} className={`qd-loc-option ${form.location_type === key ? "is-on" : ""}`}>
                <input type="radio" name={group} value={key} checked={form.location_type === key} onChange={() => set("location_type", key)} />{label}
              </label>
            ))}
          </div>
          <div className="qd-toggles">
            {TOGGLES.map(([key, label]) => <label key={key} className="chip-check"><input type="checkbox" checked={Boolean(form[key])} onChange={(event) => set(key, event.target.checked)} /><span>{label}</span></label>)}
          </div>
          <label className="field field-sm qd-terms-notes"><span>Notes</span><input maxLength={500} value={form.notes} placeholder="Dock hours, contact on site…" onChange={(event) => set("notes", event.target.value)} /></label>
        </fieldset>
        {editable && <div className="qd-terms-actions">
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy === "terms" || (!dirty && quote.terms_source !== "source")}><Check size={14} />Save terms</button>
          {dirty && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setForm(initial)}>Undo changes</button>}
        </div>}
      </form>
    </section>
  );
}

// ------------------------------------------------------------------------------------------ pallets, history

function PalletsSection({ quote }) {
  const summary = quote.summary;
  return (
    <section className="qd-section" aria-labelledby={`qd-pallets-${quote.id}`}>
      <div className="qd-section-head"><h3 id={`qd-pallets-${quote.id}`}>Pallets &amp; weight</h3></div>
      <p className="qd-pallet-line">
        <b>{plural(summary.pallets, "pallet")}</b>
        <span className="muted">{summary.standard_pallets} standard · {summary.long_pallets} long</span>
        <b>{weight(summary.total_weight)}</b>
        <span className="muted">{quote.kind === "packed" ? `${weight(summary.item_weight)} of product` : quote.kind === "measured" ? "measured gross" : ""}</span>
      </p>
      {summary.rows?.length > 0 ? (
        <div className="qd-table-wrap">
          <table className="qd-pallets">
            <thead><tr><th scope="col">Pallet</th><th scope="col">Dimensions</th><th scope="col" className="num">Weight</th><th scope="col">Class</th></tr></thead>
            <tbody>{summary.rows.map((row, index) => (
              <tr key={`${row.pallet}-${index}`}><th scope="row">P{row.pallet}</th><td>{row.dimensions}</td><td className="num">{weight(row.weight)}</td>
                <td>{row.freight_class || "—"}{row.density_pcf ? <small className="muted"> · {row.density_pcf} pcf</small> : null}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p className="muted">No pallet rows were recorded.</p>}
    </section>
  );
}

function HistorySection({ quoteId, version }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { setEntries(null); }, [version]);
  useEffect(() => {
    if (!open || entries) return undefined;
    let stale = false;
    getJson(`/api/audit?entity_type=quote&entity_id=${encodeURIComponent(quoteId)}`)
      .then((value) => { if (!stale) setEntries(value); })
      .catch((reason) => { if (!stale) setError(reason.message); });
    return () => { stale = true; };
  }, [open, entries, quoteId]);
  return (
    <section className="qd-section qd-history">
      <button type="button" className="qd-disclosure" aria-expanded={open} aria-controls={`qd-history-${quoteId}`} onClick={() => setOpen((value) => !value)}>
        <ChevronDown size={16} aria-hidden="true" />History
      </button>
      {open && <div id={`qd-history-${quoteId}`} className="qd-history-body">
        {error ? <ErrorBlock message={error} /> : entries ? <Timeline entries={entries} /> : <Loading label="Loading history…" />}
      </div>}
    </section>
  );
}

// ------------------------------------------------------------------------------------------ modals

const splitInvoices = (text) => String(text || "").split(/[,;\s]+/).map((value) => value.trim()).filter(Boolean);

function ApproveModal({ open, quote, onClose, onApproved }) {
  const [shared, setShared] = useState("");
  const [perOrder, setPerOrder] = useState({});
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setShared(""); setPerOrder({}); setNote(""); setError(""); } }, [open]);
  const several = quote.orders.length > 1;
  const again = quote.customer_status === "approved";
  const submit = async (event) => {
    event?.preventDefault();
    setBusy(true); setError("");
    try {
      const body = several
        ? { invoice_numbers: [], order_invoices: Object.fromEntries(Object.entries(perOrder).map(([id, text]) => [id, splitInvoices(text)]).filter(([, list]) => list.length)), note }
        : { invoice_numbers: splitInvoices(shared), order_invoices: {}, note };
      onApproved(await sendJson(`/api/quotes/${quote.id}/approve`, "POST", body));
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  const description = again ? "Add invoice numbers that came later. The shipment stays as it is."
    : `The customer approved ${money(quote.cost.customer_price_cents)}. ${quote.shipment ? `This quote becomes the quote of ${quote.shipment.number}.`
      : "A shipment is created for these orders, or this quote is linked to the shipment they are already on."}`;
  return (
    <LayerModal open={open} onClose={onClose} title={again ? `Invoice numbers for ${quote.number}` : `Approve ${quote.number}`} description={description}
      labelId={`qd-approve-${quote.id}`}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}><CheckCircle2 size={15} />{again ? "Save invoice numbers" : "Approve"}</button>
      </>}>
      <form id={`qd-approve-form-${quote.id}`} className="form-grid qd-approve-form" onSubmit={submit}>
        {error && <div className="field-wide"><ErrorBlock message={error} /></div>}
        {several ? quote.orders.map((order) => (
          <Field key={order.id} label={`Invoice for ${order.order_number}`} hint="Leave empty if it hasn't come yet" wide>
            <input value={perOrder[order.id] || ""} onChange={(event) => setPerOrder((current) => ({ ...current, [order.id]: event.target.value }))} />
          </Field>
        )) : (
          <Field label={`Invoice number${quote.orders[0] ? ` for ${quote.orders[0].order_number}` : ""}`} hint="Separate several with commas. Leave empty if it hasn't come yet." wide>
            <input value={shared} onChange={(event) => setShared(event.target.value)} />
          </Field>
        )}
        <Field label="Note" wide><textarea rows={2} value={note} maxLength={1000} placeholder="Optional, e.g. approved by phone" onChange={(event) => setNote(event.target.value)} /></Field>
      </form>
    </LayerModal>
  );
}

function NoteModal({ open, kind, quote, busy, onClose, onSubmit }) {
  const [note, setNote] = useState("");
  useEffect(() => { if (open) setNote(""); }, [open]);
  const declined = kind === "decline";
  return (
    <LayerModal open={open} onClose={onClose} title={declined ? `Customer declined ${quote.number}` : `Mark ${quote.number} expired`}
      description={declined ? "Keep a note of why, for the next quote." : "The price is no longer valid. You can reopen the quote later."}
      labelId={`qd-note-${quote.id}`}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onSubmit(note.trim())}>{declined ? <X size={15} /> : <Hourglass size={15} />}{declined ? "Mark declined" : "Mark expired"}</button>
      </>}>
      <form id={`qd-note-form-${quote.id}`} onSubmit={(event) => { event.preventDefault(); onSubmit(note.trim()); }}>
        <Field label="Note" hint="Optional"><textarea rows={3} value={note} maxLength={1000} placeholder={declined ? "e.g. went with their own carrier" : ""} onChange={(event) => setNote(event.target.value)} /></Field>
      </form>
    </LayerModal>
  );
}
