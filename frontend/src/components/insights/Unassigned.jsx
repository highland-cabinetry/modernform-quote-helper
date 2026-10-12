import { useMemo, useState } from "react";
import { Ban, FileQuestion, Link2, Plus, Receipt, Wallet } from "lucide-react";
import { sendJson } from "../../api";
import { EmptyState, ErrorBlock, Field, Loading, Modal } from "../Common";
import { date, EXPENSE_KINDS, money, parseMoney, plural, REVENUE_KINDS } from "../../lib/format";
import { href } from "../../lib/route";
import useResource from "../../lib/useResource";
import NumTable from "./NumTable";
import ShipmentPicker from "./ShipmentPicker";
import { companyQuery, isoDay } from "./shared";

const today = () => isoDay(new Date());
const blankBill = () => ({ kind: "linehaul", category: "", vendor: "", invoice_reference: "", incurred_on: today(), amount: "", notes: "" });
const blankRevenue = () => ({ kind: "claim_recovery", payer: "", reference: "", received_on: today(), amount: "", notes: "" });

// Money that is not on a shipment yet: carrier bills (e.g. 3PL invoices) and revenue such as
// unmatched claim recoveries. Add, assign to a shipment, or void with a reason.
export default function Unassigned({ company, multiCompany, notify, onChanged }) {
  const [showVoided, setShowVoided] = useState(false);
  const bills = useResource(`/api/unassigned-expenses${companyQuery(company, multiCompany, { include_voided: showVoided ? "true" : "" })}`);
  const revenue = useResource(`/api/revenue${companyQuery(company, multiCompany, { unassigned: "true", include_voided: showVoided ? "true" : "" })}`);
  const [adding, setAdding] = useState(null); // "bill" | "revenue"
  const [assigning, setAssigning] = useState(null); // { type, entry }
  const [voiding, setVoiding] = useState(null);

  const openBills = (bills.data || []).filter((entry) => !entry.voided);
  const openRevenue = (revenue.data || []).filter((entry) => !entry.voided);
  const billTotal = openBills.reduce((sum, entry) => sum + entry.amount_cents, 0);
  const revenueTotal = openRevenue.reduce((sum, entry) => sum + entry.amount_cents, 0);
  const categories = useMemo(() => [...new Set((bills.data || []).map((entry) => entry.category).filter(Boolean))].sort(), [bills.data]);

  const refresh = () => { bills.reload({ quiet: true }); revenue.reload({ quiet: true }); onChanged?.(); };

  const actions = (type) => ({ key: "actions", label: <span className="sr-only">Actions</span>, sort: false, width: 190, render: (entry) => entry.voided
    ? <span className="muted ins-void-note" title={entry.void_reason}>Void: {entry.void_reason || "no reason"}</span>
    : (
      <span className="ins-row-actions">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAssigning({ type, entry })}><Link2 size={14} />Assign</button>
        <button type="button" className="btn btn-ghost btn-ghost-danger btn-sm" onClick={() => setVoiding({ type, entry })}><Ban size={14} />Void</button>
      </span>
    ) });

  const billColumns = [
    { key: "day", label: "Date", width: 110, sort: (entry) => entry.incurred_on || entry.created_at, render: (entry) => date(entry.incurred_on || entry.created_at) },
    { key: "vendor", label: "Vendor", render: (entry) => entry.vendor || <span className="muted">—</span> },
    { key: "kind", label: "Kind", sort: (entry) => entry.category || entry.kind, render: (entry) => (
      <span className="cell-stack"><span>{entry.category || EXPENSE_KINDS[entry.kind] || entry.kind}</span>{entry.category && !(EXPENSE_KINDS[entry.kind] || entry.kind).toLowerCase().startsWith(entry.category.toLowerCase()) && <small>{EXPENSE_KINDS[entry.kind] || entry.kind}</small>}</span>) },
    { key: "invoice_reference", label: "Invoice", render: (entry) => entry.invoice_reference || <span className="muted">—</span> },
    { key: "amount_cents", label: "Amount", num: true, width: 110, render: (entry) => money(entry.amount_cents) },
    { key: "notes", label: "Notes", sort: false, render: (entry) => <span className="ins-notes" title={entry.notes}>{entry.notes || ""}</span> },
    actions("bill"),
  ];
  const revenueColumns = [
    { key: "day", label: "Date", width: 110, sort: (entry) => entry.received_on || entry.created_at, render: (entry) => date(entry.received_on || entry.created_at) },
    { key: "payer", label: "From", render: (entry) => entry.payer || <span className="muted">—</span> },
    { key: "kind", label: "Kind", render: (entry) => REVENUE_KINDS[entry.kind] || entry.kind },
    { key: "reference", label: "Reference", render: (entry) => entry.reference || <span className="muted">—</span> },
    { key: "amount_cents", label: "Amount", num: true, width: 110, render: (entry) => money(entry.amount_cents) },
    { key: "notes", label: "Notes", sort: false, render: (entry) => <span className="ins-notes" title={entry.notes}>{entry.notes || ""}</span> },
    actions("revenue"),
  ];

  return (
    <div className="ins-tab-body">
      <div className="list-toolbar ins-toolbar">
        <p className="muted ins-toolbar-text">Bills and revenue that are not on a shipment yet. They count in the report totals; assign them when you know the shipment.</p>
        <div className="ins-toolbar-group">
          <label className="ins-toggle"><input type="checkbox" checked={showVoided} onChange={(event) => setShowVoided(event.target.checked)} /><span>Show voided</span></label>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => setAdding("revenue")}><Plus size={14} />Add revenue</button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setAdding("bill")}><Plus size={14} />Add bill</button>
        </div>
      </div>

      <section className="card card-flush" aria-labelledby="unassigned-bills">
        <header className="card-head padded">
          <div className="card-head-text">
            <h2 className="card-title" id="unassigned-bills"><Receipt size={16} aria-hidden="true" /> Bills without a shipment</h2>
            <p className="card-description">{bills.data ? `${plural(openBills.length, "bill")} · ${money(billTotal)}` : "Loading…"}</p>
          </div>
        </header>
        {bills.error && <div className="padded"><ErrorBlock message={bills.error} onRetry={bills.reload} /></div>}
        {bills.loading && !bills.data && <Loading />}
        {bills.data && <NumTable columns={billColumns} rows={bills.data} initialSort={{ key: "day", direction: "desc" }} caption="Bills without a shipment"
          rowClassName={(entry) => (entry.voided ? "is-voided" : undefined)} minWidth={860}
          empty={<EmptyState icon={FileQuestion} title="Every bill is on a shipment">Add a bill here when an invoice arrives before you know its shipment (a 3PL invoice for a period, a late accessorial).</EmptyState>} />}
      </section>

      <section className="card card-flush" aria-labelledby="unassigned-revenue">
        <header className="card-head padded">
          <div className="card-head-text">
            <h2 className="card-title" id="unassigned-revenue"><Wallet size={16} aria-hidden="true" /> Revenue without a shipment</h2>
            <p className="card-description">{revenue.data ? `${plural(openRevenue.length, "entry", "entries")} · ${money(revenueTotal)}` : "Loading…"}</p>
          </div>
        </header>
        {revenue.error && <div className="padded"><ErrorBlock message={revenue.error} onRetry={revenue.reload} /></div>}
        {revenue.loading && !revenue.data && <Loading />}
        {revenue.data && <NumTable columns={revenueColumns} rows={revenue.data} initialSort={{ key: "day", direction: "desc" }} caption="Revenue without a shipment"
          rowClassName={(entry) => (entry.voided ? "is-voided" : undefined)} minWidth={860}
          empty={<EmptyState icon={Wallet} title="No unmatched revenue">Claim recoveries and re-bills that are not matched to a shipment show here.</EmptyState>} />}
      </section>

      {adding && <AddMoneyModal type={adding} company={company} categories={categories} notify={notify}
        onClose={() => setAdding(null)} onSaved={() => { setAdding(null); refresh(); }} />}
      {assigning && <AssignModal {...assigning} company={company} multiCompany={multiCompany} notify={notify}
        onClose={() => setAssigning(null)} onSaved={() => { setAssigning(null); refresh(); }} />}
      {voiding && <VoidModal {...voiding} notify={notify} onClose={() => setVoiding(null)} onSaved={() => { setVoiding(null); refresh(); }} />}
    </div>
  );
}

function AddMoneyModal({ type, company, categories, notify, onClose, onSaved }) {
  const bill = type === "bill";
  const [form, setForm] = useState(bill ? blankBill : blankRevenue);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const set = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const cents = parseMoney(form.amount);
  let amountError = "";
  if (cents === null) amountError = "Enter the amount.";
  else if (Number.isNaN(cents)) amountError = "Use a number like 185.00.";
  else if (cents === 0) amountError = "Enter a non-zero amount.";
  else if (cents < 0 && !(bill && form.kind === "adjustment")) amountError = bill ? "Only an adjustment or credit can be negative." : "Revenue must be more than zero.";

  const save = async (event) => {
    event.preventDefault();
    setTouched(true);
    if (amountError) return;
    setBusy(true);
    try {
      if (bill) {
        await sendJson("/api/unassigned-expenses", "POST", {
          company_id: company?.id, amount_cents: cents, kind: form.kind, category: form.category.trim(), vendor: form.vendor.trim(),
          invoice_reference: form.invoice_reference.trim(), incurred_on: form.incurred_on || null, notes: form.notes.trim(),
        });
        notify(`Bill of ${money(cents)} added.`);
      } else {
        await sendJson("/api/revenue", "POST", {
          company_id: company?.id, amount_cents: cents, kind: form.kind, payer: form.payer.trim(), reference: form.reference.trim(),
          received_on: form.received_on || null, notes: form.notes.trim(),
        });
        notify(`Revenue of ${money(cents)} added.`);
      }
      onSaved();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };

  return (
    <Modal open onClose={onClose} title={bill ? "Add a bill without a shipment" : "Add revenue without a shipment"}
      description={bill ? "For invoices you cannot tie to one shipment yet. Assign it later from this list." : "Claim recoveries or re-bills that are not matched to a shipment yet."}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="submit" form="ins-add-money" className="btn btn-primary" disabled={busy}><Plus size={15} />{bill ? "Add bill" : "Add revenue"}</button>
      </>}>
      <form id="ins-add-money" className="form-grid ins-modal-form" onSubmit={save} noValidate>
        <Field label="Kind">
          <select value={form.kind} onChange={set("kind")}>
            {Object.entries(bill ? EXPENSE_KINDS : REVENUE_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </Field>
        <Field label="Amount" error={touched ? amountError : ""} hint={bill ? "A credit is a negative adjustment" : undefined}>
          <input inputMode="decimal" value={form.amount} placeholder="0.00" onChange={set("amount")} aria-invalid={Boolean(touched && amountError)} autoFocus />
        </Field>
        {bill ? <>
          <Field label="Category" hint="Free label, e.g. Storage, Lift gate">
            <input value={form.category} list="ins-bill-categories" maxLength={80} onChange={set("category")} />
            <datalist id="ins-bill-categories">{categories.map((value) => <option key={value} value={value} />)}</datalist>
          </Field>
          <Field label="Vendor"><input value={form.vendor} maxLength={120} onChange={set("vendor")} /></Field>
          <Field label="Invoice number"><input value={form.invoice_reference} maxLength={120} onChange={set("invoice_reference")} /></Field>
          <Field label="Invoice date"><input type="date" value={form.incurred_on} onChange={set("incurred_on")} /></Field>
        </> : <>
          <Field label="From (payer)"><input value={form.payer} maxLength={120} onChange={set("payer")} /></Field>
          <Field label="Reference"><input value={form.reference} maxLength={120} onChange={set("reference")} /></Field>
          <Field label="Received on"><input type="date" value={form.received_on} onChange={set("received_on")} /></Field>
        </>}
        <Field label="Notes" wide><textarea value={form.notes} maxLength={1000} onChange={set("notes")} rows={2} /></Field>
      </form>
    </Modal>
  );
}

function AssignModal({ type, entry, company, multiCompany, notify, onClose, onSaved }) {
  const [shipment, setShipment] = useState(null);
  const [busy, setBusy] = useState(false);
  const what = type === "bill" ? `${money(entry.amount_cents)} bill${entry.vendor ? ` from ${entry.vendor}` : ""}` : `${money(entry.amount_cents)} ${REVENUE_KINDS[entry.kind]?.toLowerCase() || "revenue"}`;
  const assign = async () => {
    if (!shipment) return;
    setBusy(true);
    try {
      await sendJson(type === "bill" ? `/api/unassigned-expenses/${entry.id}/assign` : `/api/revenue/${entry.id}/assign`, "POST", { shipment_id: shipment.id });
      notify(`Assigned to ${shipment.number}.`);
      onSaved();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open onClose={onClose} title={`Assign the ${what}`} description="Pick the shipment it belongs to. It moves onto that shipment's costs and revenue."
      footer={<>
        {shipment && <a className="inline-link ins-modal-link" href={href(`/shipments/${shipment.id}`)}>Open {shipment.number}</a>}
        <span className="top-spacer" />
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={!shipment || busy} onClick={assign}><Link2 size={15} />{shipment ? `Assign to ${shipment.number}` : "Assign"}</button>
      </>}>
      <ShipmentPicker company={company} multiCompany={multiCompany} value={shipment} onChange={setShipment} />
    </Modal>
  );
}

function VoidModal({ type, entry, notify, onClose, onSaved }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (event) => {
    event.preventDefault();
    if (!reason.trim()) return;
    setBusy(true);
    try {
      await sendJson(type === "bill" ? `/api/unassigned-expenses/${entry.id}/void` : `/api/revenue/${entry.id}/void`, "POST", { reason: reason.trim() });
      notify(`${money(entry.amount_cents)} voided.`);
      onSaved();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  return (
    <Modal open tone="danger" onClose={onClose} title={`Void this ${money(entry.amount_cents)} ${type === "bill" ? "bill" : "revenue"}?`}
      description="It stays in the history but no longer counts in totals."
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="submit" form="ins-void" className="btn btn-danger-solid" disabled={!reason.trim() || busy}><Ban size={15} />Void</button>
      </>}>
      <form id="ins-void" onSubmit={save}>
        <Field label="Reason" hint="Required, e.g. duplicate of invoice 4471">
          <input value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} autoFocus />
        </Field>
      </form>
    </Modal>
  );
}
