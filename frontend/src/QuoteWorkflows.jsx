import release from "../../release.json";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy as CopyIcon, FilePlus2, Plus, Save, Trash2, X } from "lucide-react";
import { getJson, sendJson } from "./api";
import { useDraft } from "./drafts";
import DraftBar from "./DraftBar";
import BrokerPanel from "./BrokerPanel";
import { brokers } from "./brokers";
import { Card } from "./ui";
import { APP_NAME } from "./edition";

export { brokers };
const QuoteDetail = lazy(() => import("./components/QuoteDetail"));
const terminal = new Set(["cancelled", "expired", "error", "completed"]);
const statusText = s => s.state === "filled" || (s.state === "completed" && !s.fields?.some(f => !f.ok))
  ? "Filled" : ["error", "expired", "user_action", "login_required"].includes(s.state) || s.fields?.some(f => !f.ok)
    ? "Needs attention" : s.state === "cancelled" ? "Cancelled" : "Filling";

export function BrokerProgress({ sessions, setSessions }) {
  const current = useRef(sessions);
  current.current = sessions;
  useEffect(() => {
    let stopped = false;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      await Promise.all(Object.entries(current.current).map(async ([broker, value]) => {
        if (terminal.has(value.state) || value.monitoring === false) return;
        try {
          const next = await getJson(`/api/quote-handoffs/${value.request_id}/status`);
          if (!stopped) setSessions(old => old[broker]?.request_id === value.request_id ? { ...old, [broker]: { ...value, ...next } } : old);
        } catch (error) {
          if (!stopped) setSessions(old => old[broker]?.request_id === value.request_id
            ? { ...old, [broker]: { ...old[broker], monitoring: false,
              state: value.state === "filled" ? "filled" : "error",
              message: value.state === "filled" ? "Autofill completed. Monitoring ended." : error.message } } : old);
        }
      }));
      polling = false;
    };
    const timer = setInterval(poll, 1500);
    return () => { stopped = true; clearInterval(timer); };
  }, [setSessions]);
  const entries = Object.entries(sessions);
  if (!entries.length) return null;
  const dismiss = broker => setSessions(old => { const next = { ...old }; delete next[broker]; return next; });
  return <aside className="autofill-dock" aria-label="Broker autofill progress" aria-live="polite">
    <h2 className="autofill-title">Broker autofill</h2>
    {entries.map(([broker, s]) => {
      const label = statusText(s);
      const tone = label === "Filled" ? "ok" : label === "Needs attention" ? "warn" : label === "Cancelled" ? "neutral" : "info";
      const finished = terminal.has(s.state) || s.monitoring === false;
      return <details key={broker} className={`broker-chip tone-${tone}`}>
        <summary><span className={`dot dot-${tone}`} aria-hidden="true" /><strong>{brokers[broker]}</strong><span className="broker-chip-state">{label}</span></summary>
        <div className="broker-chip-body">
          <small>{s.shipment_name}</small>
          {s.message && <small>{s.message}</small>}
          {s.fields?.filter(v => !v.ok).slice(0, 3).map(v => <small key={v.field} className="broker-chip-field">{v.field}: {v.message}</small>)}
          <div className="broker-chip-actions">
            {!finished && <button type="button" className="btn btn-ghost btn-sm" onClick={async () => {
              try {
                await sendJson(`/api/quote-handoffs/${s.request_id}/cancel`, "POST");
                setSessions(old => old[broker]?.request_id === s.request_id ? ({ ...old, [broker]: { ...old[broker], state: "cancelled", message: "Cancelled" } }) : old);
              } catch (e) {
                setSessions(old => old[broker]?.request_id === s.request_id ? ({ ...old, [broker]: { ...old[broker], state: "error", message: e.message } }) : old);
              }
            }}>Cancel autofill</button>}
            {finished && <button type="button" className="btn btn-ghost btn-sm" onClick={() => dismiss(broker)}><X size={14} />Dismiss</button>}
          </div>
        </div>
      </details>;
    })}
  </aside>;
}

export const emptyManualRow = () => ({ id: crypto.randomUUID(), length: "", width: "", height: "", weight: "", quantity: 1 });
export function manualPayload(name, pallets, shipping, extra = {}) {
  if (!name.trim()) throw new Error("Enter a shipment/order reference.");
  if (!pallets.length) throw new Error("Add at least one pallet row.");
  const rows = pallets.map((p, index) => {
    const row = { id: p.id };
    for (const key of ["length", "width", "height", "weight", "quantity"]) {
      row[key] = Number(p[key]);
      if (!Number.isFinite(row[key]) || row[key] <= 0) throw new Error(`Pallet row ${index + 1}: ${key} must be positive.`);
    }
    if (!Number.isInteger(row.quantity)) throw new Error(`Pallet row ${index + 1}: quantity must be a whole number.`);
    return row;
  });
  if (rows.reduce((total, row) => total + row.quantity, 0) > 200) throw new Error("Enter no more than 200 pallets per shipment.");
  return { shipment_name: name.trim(), pallets: rows, shipping_info: { ...shipping, customer_references: [name.trim()] }, ...extra };
}

export default function QuickQuote({ settings, onDefaults, quoteId, openToken, bridgeConnection, checkBridge, onPrepared, launchPrepared, backendReady = true, company = null, context = null, brokerSessions = {} }) {
  const [recorded, setRecorded] = useState(null);
  const [pricesOpen, setPricesOpen] = useState(false);
  const [linkedOrders, setLinkedOrders] = useState([]);
  const extra = () => (company ? {
    company_id: company.id,
    order_ids: linkedOrders.map((order) => order.id),
    freight_shipment_id: context?.shipmentId || null,
  } : {});
  const [id, setId] = useState(null);
  const [name, setName] = useState("");
  const [rows, setRows] = useState([emptyManualRow()]);
  const [shipping, setShipping] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState("");
  const [revision, setRevision] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const restoredGeneration = useRef(0);
  const draft = useDraft("quick-quote", { id, name, rows, shipping, revision }, value => {
    restoredGeneration.current += 1;
    setId(value.id); setName(value.name); setRows(value.rows); setRevision(value.revision);
    setShipping(value.shipping ? { ...value.shipping, confirmed: false } : null);
    setPreview(null); setNotice("Draft restored. Review the transmitted measurements before quoting.");
  });
  const defaults = () => ({ confirmed: false, origin: { ...settings.quote_origin }, destination: { postal_code: "" },
    commodity_description: settings.default_commodity_description || "", nmfc: settings.default_nmfc || "",
    accessorials: { residential: false, liftgate: false, appointment_delivery: true, delivery_call_ahead: false, limited_access: false, inside_delivery: false } });
  useEffect(() => { setShipping(old => !old || (!id && !name && rows.every(r => !r.length && !r.weight)) ? defaults() : old); }, [settings]);
  useEffect(() => {
    let cancelled = false;
    const generation = restoredGeneration.current;
    if (quoteId) getJson(`/api/manual-quotes/${quoteId}`).then(value => {
      if (cancelled || generation !== restoredGeneration.current) return;
      setId(value.id); setName(value.shipment_name); setRows(value.pallets); setShipping(value.shipping_info);
      setRevision(value.revision); setNotice("Reopened user-supplied measurements."); setError("");
      setRecorded(value.freight_quote || null); setLinkedOrders(value.freight_quote?.orders || []);
    }).catch(e => setError(e.message));
    else {
      setId(null); setRevision(null); setRows([emptyManualRow()]); setNotice(""); setRecorded(null);
      const orders = context?.orders || [];
      setLinkedOrders(orders);
      setName(orders.map((order) => order.order_number).join(" "));
      const zips = [...new Set(orders.map((order) => order.ship_to?.postal_code).filter(Boolean))];
      const base = defaults();
      setShipping(zips.length === 1 ? { ...base, destination: { ...base.destination, postal_code: zips[0] } } : base);
    }
    return () => { cancelled = true; };
  }, [quoteId, openToken]);
  useEffect(() => {
    let ignore = false;
    const abort = new AbortController();
    setPreview(null);
    setPreviewError("");
    if (!backendReady) return;
    const timer = setTimeout(async () => {
      let payload;
      try { payload = manualPayload(name, rows, shipping); } catch { return; }
      try {
        const result = await sendJson("/api/manual-quotes/preview", "POST", payload, { signal: abort.signal });
        if (!ignore) setPreview(result);
      } catch (e) { if (!ignore) setPreviewError(`Pallet preview unavailable: ${e.message}`); }
    }, 250);
    return () => { ignore = true; abort.abort(); clearTimeout(timer); };
  }, [name, rows, shipping, backendReady]);
  const changeShipping = (field, value) => setShipping(old => ({ ...old, [field]: value, confirmed: field === "confirmed" ? value : false }));
  const changeAddressZip = (side, postal_code) => setShipping(old => ({
    ...old,
    confirmed: false,
    [side]: {
      ...old[side],
      postal_code,
      ...(old[side].postal_code !== postal_code ? { city: "", state: "" } : {}),
    },
  }));
  const reviseMeasurements = update => {
    setRows(update); setPreview(null); setShipping(old => ({ ...old, confirmed: false }));
  };
  async function save() {
    const result = await sendJson(`/api/manual-quotes${id ? `/${id}` : ""}`, id ? "PUT" : "POST", manualPayload(name, rows, shipping, extra()));
    setId(result.id); setRevision(result.revision);
    if (result.freight_quote) { setRecorded(result.freight_quote); setLinkedOrders(result.freight_quote.orders || []); }
    setNotice(`Saved revision ${result.revision}. Existing broker packets are unchanged.`);
    return result;
  }
  async function action(broker) {
    setBusy(true); setError("");
    try {
      if (!backendReady) throw new Error(`Restart ${APP_NAME} to load the current backend, then refresh this page.`);
      if (broker) {
        const checked = await sendJson("/api/manual-quotes/preview", "POST", manualPayload(name, rows, shipping));
        if (!checked.handoff_validation.ready) throw new Error(checked.handoff_validation.missing_fields.join(" · "));
        if (bridgeConnection.state !== "connected") throw new Error("Reload the current Build2Quote Bridge, then check the connection.");
      }
      const result = await save();
      if (broker) {
        const prepare = async (replace_active = false) => broker === "all"
          ? await sendJson("/api/quote-handoffs/prepare-all", "POST", { shipment_id: result.id, source_kind: "manual", replace_active })
          : { handoffs: [{ ...await sendJson(`/api/quote-handoffs/${broker}/prepare`, "POST", { shipment_id: result.id, source_kind: "manual", replace_active }), broker }] };
        let response;
        try {
          response = await prepare();
        } catch (prepareError) {
          if (prepareError.status !== 409 || !window.confirm(`${prepareError.message}\n\nCancel the active handoff and open a replacement?`)) throw prepareError;
          response = await prepare(true);
        }
        if (launchPrepared) await launchPrepared(response.handoffs, result);
        else response.handoffs.forEach(session => onPrepared?.(session.broker, { ...session, shipment_name: result.shipment_name, source_revision: result.revision }));
        if (response.skipped?.length) setNotice(`Not opened: ${response.skipped.map((entry) => entry.reason).join(" ")}`);
      }
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  if (!shipping) return null;
  const packet = preview?.quote_packet;
  const ready = Boolean(preview?.handoff_validation?.ready);
  const bridgeOk = bridgeConnection?.state === "connected";
  const missing = preview?.handoff_validation?.missing_fields || [];
  const rowsComplete = rows.length > 0 && rows.every(r => ["length", "width", "height", "weight", "quantity"].every(k => Number(r[k]) > 0));
  const zipsEntered = Boolean(String(shipping.origin?.postal_code || "").trim() && String(shipping.destination?.postal_code || "").trim());
  const totalPallets = rows.reduce((sum, r) => sum + (Number(r.quantity) || 0), 0);
  const resetShipment = () => { setId(null); setRevision(null); setName(""); setRows([emptyManualRow()]); setShipping(defaults()); setNotice(""); setError(""); setRecorded(null); setLinkedOrders([]); };
  const services = { residential: "Residential delivery", liftgate: "Liftgate delivery", appointment_delivery: "Appointment delivery", delivery_call_ahead: "Delivery call ahead", limited_access: "Limited access location", inside_delivery: "Inside delivery" };
  const blockedReason = !backendReady ? "" : !ready
    ? (missing.length ? missing.join(" · ") : "Complete the reference, ZIPs and pallet rows, then tick the review box.")
    : !bridgeOk ? "Connect the Build2Quote Bridge to open broker tabs." : "";
  let offset = 0;
  return <main className="page workflow-page">
    <section className="page-header">
      <div className="page-header-main">
        <h1>Quick Quote</h1>
        <p className="page-lede">For pallets that are already built and measured. Enter gross weight with the pallet included — no tare is added and Builder limits don’t apply.</p>
      </div>
      <div className="page-header-actions">
        <DraftBar draft={draft} />
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={resetShipment}><FilePlus2 size={16} />New manual shipment</button>
      </div>
    </section>
    <div className="review-layout">
      <div className="review-main">
        <Card title="Shipment" description="Reference, route and delivery services.">
          <div className="form-grid form-grid-wide">
            <label className="field field-wide"><span>Shipment / order reference</span><input value={name} onChange={e => { setName(e.target.value); setShipping(old => ({ ...old, confirmed: false })); }} />
              {company && <small className="field-hint">{linkedOrders.length ? `Linked to ${linkedOrders.map((order) => order.order_number).join(", ")}.` : "Order numbers typed here (e.g. SO77767 68606) are linked to their orders when you save."}</small>}</label>
            <label className="field"><span>Origin ZIP</span><input inputMode="numeric" value={shipping.origin.postal_code || ""} onChange={e => changeAddressZip("origin", e.target.value)} /></label>
            <label className="field"><span>Delivery ZIP</span><input inputMode="numeric" value={shipping.destination.postal_code || ""} onChange={e => changeAddressZip("destination", e.target.value)} /></label>
            <label className="field field-wide"><span>Commodity</span><input value={shipping.commodity_description} onChange={e => changeShipping("commodity_description", e.target.value)} /></label>
            <label className="field"><span>NMFC</span><input value={shipping.nmfc} onChange={e => changeShipping("nmfc", e.target.value)} /></label>
          </div>
          <fieldset className="chip-group">
            <legend>Delivery services</legend>
            {Object.entries(services).map(([k, label]) => <label key={k} className="chip-check">
              <input type="checkbox" checked={Boolean(shipping.accessorials[k])} onChange={e => changeShipping("accessorials", { ...shipping.accessorials, ...(e.target.checked && k === "residential" ? { limited_access: false } : e.target.checked && k === "limited_access" ? { residential: false } : {}), [k]: e.target.checked })} /><span>{label}</span></label>)}
          </fieldset>
          <div className="card-foot-actions">
            <button type="button" className="btn btn-ghost btn-sm" onClick={async () => { try { await onDefaults(shipping); setNotice("Commodity, NMFC and origin ZIP saved as defaults."); } catch (e) { setError(e.message); } }}><Save size={14} />Save as defaults</button>
          </div>
        </Card>
        <Card title="Measured pallets" description="Size and gross weight are per pallet. Quantity is the number of identical pallets.">
          <div className="table-wrap">
            <table className="data-table pallet-entry-table"><thead><tr><th scope="col">#</th><th scope="col" className="num">Length (in)</th><th scope="col" className="num">Width (in)</th><th scope="col" className="num">Total height (in)</th><th scope="col" className="num">Gross weight (lb)</th><th scope="col" className="num">Qty</th><th scope="col">Sent to brokers</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>{rows.map((row, index) => {
                const sent = packet?.pallets?.[offset]; offset += Number(row.quantity) || 0;
                return <tr key={row.id}><th scope="row">{index + 1}</th>{["length", "width", "height", "weight", "quantity"].map(field => <td key={field} className="num"><input className="cell-input dim-input" aria-label={`Pallet ${index + 1} ${field}`} type="number" step={field === "quantity" ? "1" : "any"} min="0" value={row[field]} onChange={e => reviseMeasurements(old => old.map(p => p.id === row.id ? { ...p, [field]: e.target.value } : p))} /></td>)}
                  <td className="sent-col">{sent ? <><b>{sent.length} × {sent.width} × {sent.height} in, {sent.weight} lb</b><small>Class {sent.freight_class} from density ({sent.density_pcf} pcf)</small></> : <span className="muted">Fill in all fields to preview</span>}</td>
                  <td className="row-actions"><button type="button" className="btn btn-ghost btn-sm" onClick={() => reviseMeasurements(old => [...old, { ...row, id: crypto.randomUUID() }])}><CopyIcon size={14} />Duplicate</button><button type="button" className="btn btn-ghost btn-sm btn-ghost-danger" disabled={rows.length === 1} onClick={() => reviseMeasurements(old => old.filter(p => p.id !== row.id))}><Trash2 size={14} />Remove</button></td></tr>;
              })}</tbody></table>
          </div>
          <div className="table-footer">
            <button type="button" className="btn btn-ghost" onClick={() => reviseMeasurements(old => [...old, emptyManualRow()])}><Plus size={16} />Add pallet row</button>
            {packet ? <p className="total-line"><b>{packet.pallets.length} pallet{packet.pallets.length === 1 ? "" : "s"}, {packet.total_weight} lb</b> will be sent. Broker sizes round up to whole inches; weight rounds to the nearest pound.</p>
              : <p className="hint">{totalPallets} pallet{totalPallets === 1 ? "" : "s"} entered.</p>}
          </div>
          <p className="hint card-foot">Class is derived from density on the versioned scale; it is not an NMFC classification guarantee.</p>
        </Card>
      </div>
      <aside className="review-rail">
        <Card className="quote-card" title="Get quotes" description="Save the shipment, or open the brokers to autofill these pallets.">
          <label className="confirm-check"><input type="checkbox" checked={shipping.confirmed} onChange={e => changeShipping("confirmed", e.target.checked)} /><span>I reviewed the ZIPs, services and transmitted pallet measurements.</span></label>
          <BrokerPanel
            steps={[
              { label: "Shipment reference", done: Boolean(name.trim()) },
              { label: "Origin and delivery ZIP", done: zipsEntered },
              { label: "Pallet measurements", done: rowsComplete, detail: packet ? `${packet.pallets.length} pallet${packet.pallets.length === 1 ? "" : "s"}, ${packet.total_weight} lb` : undefined },
              { label: "Details reviewed", done: Boolean(shipping.confirmed) },
              { label: "Broker Bridge connected", done: bridgeOk, detail: bridgeConnection?.version ? `v${bridgeConnection.version} (needs ${release.minimum_bridge_version}+)` : undefined },
            ]}
            bridge={bridgeConnection || {}}
            minimumVersion={release.minimum_bridge_version}
            onCheckBridge={checkBridge}
            canSend={ready && bridgeOk}
            busy={busy}
            onOpenAll={() => action("all")}
            onOpenBroker={key => action(key)}
            blockedReason={blockedReason}
            sessions={brokerSessions}
            services={shipping.accessorials}
          >
            <button type="button" className="btn btn-secondary btn-block" disabled={busy || !backendReady} onClick={() => action()}><Save size={15} />Save shipment</button>
            {revision && <p className="saved-line"><CheckCircle2 size={14} />Saved revision {revision}{recorded ? <> · quote <a className="inline-link" href={`#/quotes?quote=${recorded.id}`}>{recorded.number}</a></> : null}</p>}
            {recorded && <button type="button" className="btn btn-primary btn-block" onClick={() => setPricesOpen(true)}><Plus size={15} />Add broker prices to {recorded.number}</button>}
          </BrokerPanel>
          {!backendReady && <p className="inline-error" role="alert">Restart {APP_NAME} (Start menu › {APP_NAME} Tools › Stop {APP_NAME}, then open it again), then refresh to enable Quick Quote.</p>}
          {previewError && <p className="inline-error" role="alert">{previewError}</p>}
          {notice && <p className="inline-ok" role="status">{notice}</p>}
          {error && <p className="inline-error" role="alert">{error}</p>}
        </Card>
        {pricesOpen && recorded && <Suspense fallback={null}>
          <QuoteDetail quoteId={recorded.id} focusPrices onClose={() => setPricesOpen(false)} onNavigate={() => setPricesOpen(false)}
            notify={(message, tone) => (tone === "danger" ? setError(message) : setNotice(message))} />
        </Suspense>}
      </aside>
    </div>
  </main>;
}
