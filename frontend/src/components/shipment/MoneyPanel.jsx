import { useMemo, useState } from "react";
import { AlertTriangle, Calculator, Link2, Plus, Receipt, Save, Wallet } from "lucide-react";
import { sendJson } from "../../api";
import { Badge, DataTable, Drawer, EmptyState, Field, Loading } from "../Common";
import { ALLOCATION_METHODS, EXPENSE_KINDS, REVENUE_KINDS, centsToInput, date, money, parseMoney } from "../../lib/format";
import useResource from "../../lib/useResource";
import ChargeEditor from "./ChargeEditor";
import ReasonDialog from "./ReasonDialog";
import { chargeErrors, percent } from "./model";
import "../../styles/shipments.css";

const emptyExpense = () => ({ amount: "", kind: "linehaul", category: "", vendor: "", invoice_reference: "", incurred_on: "", notes: "" });
const emptyRevenue = () => ({ amount: "", kind: "accessorial_rebill", payer: "", reference: "", received_on: "", notes: "" });

export function expenseError(entry) {
  const cents = parseMoney(entry.amount);
  if (!String(entry.amount).trim()) return "";
  if (Number.isNaN(cents)) return "Use dollars and cents";
  if (cents === 0) return "Enter a non-zero amount";
  if (cents < 0 && entry.kind !== "adjustment") return "Only an adjustment or credit can be negative";
  return "";
}

function revenueError(entry) {
  const cents = parseMoney(entry.amount);
  if (!String(entry.amount).trim()) return "";
  if (Number.isNaN(cents)) return "Use dollars and cents";
  if (cents <= 0) return "Enter an amount above 0";
  return "";
}

// Money tab: customer charge, extra revenue, costs (bills), totals and the per-order split.
export default function MoneyPanel({ shipment, form, onFormChange, onSaveShipment, dirty, run, busy, notify }) {
  const locked = shipment.locked;
  const comparison = shipment.comparison;
  const [expense, setExpense] = useState(emptyExpense());
  const [revenue, setRevenue] = useState(emptyRevenue());
  const [voiding, setVoiding] = useState(null); // {type: "expense"|"revenue", entry}
  const [picker, setPicker] = useState(null); // "expense" | "revenue"
  const chargeProblems = chargeErrors(form);
  const categories = useMemo(() => [...new Set(shipment.expenses.map((entry) => entry.category).filter(Boolean))], [shipment.expenses]);

  const addExpense = async (event) => {
    event?.preventDefault();
    const cents = parseMoney(expense.amount);
    const problem = expenseError(expense) || (cents === null ? "Enter the amount" : "");
    if (problem) { notify(problem, "danger"); return; }
    const value = await run(() => sendJson(`/api/freight-shipments/${shipment.id}/expenses`, "POST", {
      amount_cents: cents, kind: expense.kind, category: expense.category.trim(), vendor: expense.vendor.trim(),
      invoice_reference: expense.invoice_reference.trim(), incurred_on: expense.incurred_on || null, notes: expense.notes.trim(),
    }), "Cost recorded.");
    if (value) setExpense(emptyExpense());
  };
  const addRevenue = async (event) => {
    event?.preventDefault();
    const cents = parseMoney(revenue.amount);
    const problem = revenueError(revenue) || (cents === null ? "Enter the amount" : "");
    if (problem) { notify(problem, "danger"); return; }
    const value = await run(() => sendJson(`/api/freight-shipments/${shipment.id}/revenue`, "POST", {
      amount_cents: cents, kind: revenue.kind, payer: revenue.payer.trim(), reference: revenue.reference.trim(),
      received_on: revenue.received_on || null, notes: revenue.notes.trim(),
    }), "Revenue recorded.");
    if (value) setRevenue(emptyRevenue());
  };
  const confirmVoid = async (reason) => {
    const { type, entry } = voiding;
    setVoiding(null);
    const path = type === "expense" ? `/api/freight-shipments/${shipment.id}/expenses/${entry.id}/void` : `/api/freight-shipments/${shipment.id}/revenue/${entry.id}/void`;
    await run(() => sendJson(path, "POST", { reason }), "Entry voided.");
  };

  const voidButton = (type, entry) => !entry.voided && !locked && (
    <button type="button" className="btn btn-ghost btn-ghost-danger btn-sm" disabled={busy} onClick={() => setVoiding({ type, entry })}>Void</button>);
  const voidedNote = (entry) => entry.voided && <small className="warn-text">Voided: {entry.void_reason}</small>;
  const expenseProblem = expenseError(expense);
  const revenueProblem = revenueError(revenue);

  return (
    <div className="money-panel">
      <section className="money-totals" aria-label="Shipment totals">
        <div><span>Revenue</span><strong>{money(comparison.revenue?.total_cents)}</strong><small>charge {money(comparison.revenue?.customer_charge_cents)} + extra {money(comparison.revenue?.extra_cents || 0)}</small></div>
        <div><span>Cost</span><strong>{money(comparison.cost?.actual_cents)}</strong><small>{comparison.cost?.actual_cents == null ? `expected ${money(comparison.cost?.expected_cents)}` : `${shipment.expenses.filter((e) => !e.voided).length} bill line(s)`}</small></div>
        <div className={comparison.profit_cents != null && comparison.profit_cents < 0 ? "is-bad" : ""}><span>Net {comparison.profit_estimated && <Badge tone="info">estimated</Badge>}</span><strong>{money(comparison.profit_cents)}</strong><small>{comparison.profit_estimated ? "revenue − expected cost" : "revenue − cost"}</small></div>
        <div><span>Margin</span><strong>{percent(comparison.margin_percent)}</strong><small>net ÷ revenue</small></div>
      </section>

      <div className="money-grid">
        <section className="card" aria-labelledby="charge-title">
          <header className="card-head">
            <div className="card-head-text"><h2 className="card-title" id="charge-title"><Wallet size={16} aria-hidden="true" /> Customer charge</h2>
              <p className="card-description">What the customer pays for this freight. Same order as the Freight Manager workbook: backcharge, rep charge, then the quote.</p></div>
          </header>
          <ChargeEditor form={form} shipment={shipment} disabled={locked} onChange={onFormChange} />
          {!locked && <div className="card-actions-row">
            <button type="button" className="btn btn-primary" disabled={busy || !dirty || Object.keys(chargeProblems).length > 0} onClick={() => onSaveShipment()}><Save size={15} />Save charge</button>
            {dirty && <small className="muted">Saves the other shipment fields you changed too.</small>}
          </div>}
        </section>

        <section className="card card-flush" aria-labelledby="revenue-title">
          <header className="card-head padded">
            <div className="card-head-text"><h2 className="card-title" id="revenue-title">Extra revenue</h2>
              <p className="card-description">Accessorials re-billed to the customer, claim recoveries and other money in.</p></div>
            <div className="money-head-side">
              {!locked && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPicker("revenue")}><Link2 size={14} />Match a payment not yet linked</button>}
              <b className="total-badge">{money(shipment.revenue_total_cents)}</b>
            </div>
          </header>
          <DataTable dense rows={shipment.revenue} rowClassName={(entry) => entry.voided ? "is-voided" : ""} caption="Extra revenue"
            empty={<p className="muted padded small-line">No extra revenue recorded.</p>}
            columns={[
              { key: "kind", label: "Type", render: (entry) => <span className="cell-stack"><span>{REVENUE_KINDS[entry.kind] || entry.kind}</span>{voidedNote(entry)}</span> },
              { key: "payer", label: "From", render: (entry) => entry.payer || <span className="muted">—</span> },
              { key: "reference", label: "Reference", render: (entry) => entry.reference || <span className="muted">—</span> },
              { key: "received_on", label: "Date", render: (entry) => date(entry.received_on || entry.created_at) },
              { key: "amount_cents", label: "Amount", align: "right", render: (entry) => money(entry.amount_cents) },
              { key: "void", label: <span className="sr-only">Void</span>, sortable: false, render: (entry) => voidButton("revenue", entry) },
            ]} />
          {!locked && <form className="money-form padded" onSubmit={addRevenue} aria-label="Add extra revenue">
            <div className="form-grid form-grid-3">
              <Field label="Amount ($)" error={revenueProblem}><input inputMode="decimal" value={revenue.amount} placeholder="0.00" onChange={(event) => setRevenue({ ...revenue, amount: event.target.value })} /></Field>
              <Field label="Type"><select value={revenue.kind} onChange={(event) => setRevenue({ ...revenue, kind: event.target.value })}>{Object.entries(REVENUE_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
              <Field label="From"><input value={revenue.payer} maxLength={120} placeholder="Customer or carrier" onChange={(event) => setRevenue({ ...revenue, payer: event.target.value })} /></Field>
              <Field label="Reference"><input value={revenue.reference} maxLength={120} placeholder="Invoice or claim #" onChange={(event) => setRevenue({ ...revenue, reference: event.target.value })} /></Field>
              <Field label="Received on"><input type="date" value={revenue.received_on} onChange={(event) => setRevenue({ ...revenue, received_on: event.target.value })} /></Field>
              <Field label="Note"><input value={revenue.notes} maxLength={1000} onChange={(event) => setRevenue({ ...revenue, notes: event.target.value })} /></Field>
            </div>
            <div className="card-actions-row"><button type="submit" className="btn btn-secondary" disabled={busy || !revenue.amount || Boolean(revenueProblem)}><Plus size={15} />Add revenue</button></div>
          </form>}
        </section>
      </div>

      <section className="card card-flush" aria-labelledby="costs-title">
        <header className="card-head padded">
          <div className="card-head-text"><h2 className="card-title" id="costs-title">Costs</h2>
            <p className="card-description">Carrier bills and every other freight cost for this shipment. Voided entries stay visible.</p></div>
          <div className="money-head-side">
            {!locked && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPicker("expense")}><Link2 size={14} />Add a bill not yet linked</button>}
            <b className="total-badge">{money(shipment.expense_total_cents)}</b>
          </div>
        </header>
        <DataTable dense rows={shipment.expenses} rowClassName={(entry) => entry.voided ? "is-voided" : ""} caption="Costs"
          empty={<EmptyState icon={Receipt} title="No cost recorded">Add the carrier invoice below once it arrives.</EmptyState>}
          columns={[
            { key: "kind", label: "Type", render: (entry) => <span className="cell-stack"><span>{EXPENSE_KINDS[entry.kind] || entry.kind}</span>{entry.category && <small>{entry.category}</small>}{voidedNote(entry)}</span> },
            { key: "vendor", label: "Paid to", render: (entry) => entry.vendor || <span className="muted">—</span> },
            { key: "invoice_reference", label: "Invoice", render: (entry) => entry.invoice_reference || <span className="muted">—</span> },
            { key: "incurred_on", label: "Date", render: (entry) => date(entry.incurred_on || entry.created_at) },
            { key: "amount_cents", label: "Amount", align: "right", render: (entry) => money(entry.amount_cents) },
            { key: "void", label: <span className="sr-only">Void</span>, sortable: false, render: (entry) => voidButton("expense", entry) },
          ]} />
        {!locked && <form className="money-form padded" onSubmit={addExpense} aria-label="Add a cost">
          <div className="form-grid form-grid-wide">
            <Field label="Amount ($)" error={expenseProblem}><input inputMode="decimal" value={expense.amount} placeholder="0.00" onChange={(event) => setExpense({ ...expense, amount: event.target.value })} /></Field>
            <Field label="Type"><select value={expense.kind} onChange={(event) => setExpense({ ...expense, kind: event.target.value })}>{Object.entries(EXPENSE_KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
            <Field label="Category" hint="Your own label, optional"><input value={expense.category} maxLength={80} list="expense-categories" onChange={(event) => setExpense({ ...expense, category: event.target.value })} /></Field>
            <Field label="Paid to"><input value={expense.vendor} maxLength={120} placeholder="Carrier or broker" onChange={(event) => setExpense({ ...expense, vendor: event.target.value })} /></Field>
            <Field label="Invoice / reference"><input value={expense.invoice_reference} maxLength={120} onChange={(event) => setExpense({ ...expense, invoice_reference: event.target.value })} /></Field>
            <Field label="Date"><input type="date" value={expense.incurred_on} onChange={(event) => setExpense({ ...expense, incurred_on: event.target.value })} /></Field>
            <Field label="Note" wide><input value={expense.notes} maxLength={1000} onChange={(event) => setExpense({ ...expense, notes: event.target.value })} /></Field>
          </div>
          <datalist id="expense-categories">{[...new Set([...categories, "Shipping", "Additional charges", "Claims", "Storage", "Reweigh"])].map((value) => <option key={value} value={value} />)}</datalist>
          <div className="card-actions-row"><button type="submit" className="btn btn-primary" disabled={busy || !expense.amount || Boolean(expenseProblem)}><Plus size={15} />Record cost</button>
            <small className="muted">Credits: choose "Adjustment or credit" and enter a negative amount.</small></div>
        </form>}
      </section>

      <AllocationCard key={shipment.allocation?.created_at || "none"} shipment={shipment} run={run} busy={busy} notify={notify} />

      <ReasonDialog open={Boolean(voiding)} tone="danger" title={voiding?.type === "expense" ? "Void this cost?" : "Void this revenue?"}
        description={voiding ? `${money(voiding.entry.amount_cents)} stays visible, crossed out, and no longer counts.` : ""}
        label="Why is it being voided?" confirmLabel="Void entry" busy={busy} onClose={() => setVoiding(null)} onConfirm={confirmVoid} />
      <UnlinkedPicker kind={picker} shipment={shipment} onClose={() => setPicker(null)} run={run} busy={busy} />
    </div>
  );
}

// Bills (or revenue) recorded without a shipment, to link to this one.
function UnlinkedPicker({ kind, shipment, onClose, run, busy }) {
  const open = Boolean(kind);
  const params = new URLSearchParams({ company_id: shipment.company_id, ...(kind === "revenue" ? { unassigned: "true" } : {}) });
  const path = kind === "expense" ? `/api/unassigned-expenses?${params}` : kind === "revenue" ? `/api/revenue?${params}` : null;
  const { data, loading, error, reload } = useResource(path);
  const rows = (data || []).filter((entry) => !entry.voided && !entry.shipment_id);
  const assign = async (entry) => {
    const url = kind === "expense" ? `/api/unassigned-expenses/${entry.id}/assign` : `/api/revenue/${entry.id}/assign`;
    const value = await run(() => sendJson(url, "POST", { shipment_id: shipment.id }), `${money(entry.amount_cents)} linked to ${shipment.number}.`);
    if (value) { await reload({ quiet: true }); onClose(); }
  };
  return (
    <Drawer open={open} onClose={onClose} labelId="unlinked-title"
      title={kind === "expense" ? "Add a bill not yet linked" : "Match a payment not yet linked"}
      subtitle={kind === "expense" ? "Bills recorded without a shipment. Linking one moves it here and clears the cost split." : "Claim recoveries and other money recorded without a shipment."}>
      {loading && !data && <Loading />}
      {error && <p className="field-error">{error}</p>}
      {data && (rows.length ? <ul className="item-list compact unlinked-list">
        {rows.map((entry) => (
          <li key={entry.id}>
            <div className="item-link">
              <span className="item-main">
                <b>{money(entry.amount_cents)} · {kind === "expense" ? (EXPENSE_KINDS[entry.kind] || entry.kind) : (REVENUE_KINDS[entry.kind] || entry.kind)}</b>
                <small>{[entry.vendor || entry.payer, entry.invoice_reference || entry.reference, entry.category, date(entry.incurred_on || entry.received_on || entry.created_at)].filter(Boolean).join(" · ")}</small>
                {entry.notes && <small>{entry.notes}</small>}
              </span>
              <span className="item-side"><button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => assign(entry)}><Link2 size={14} />Link to {shipment.number}</button></span>
            </div>
          </li>
        ))}
      </ul> : <EmptyState icon={Receipt} title={kind === "expense" ? "No unlinked bills" : "Nothing to match"}>Everything recorded is already on a shipment.</EmptyState>)}
    </Drawer>
  );
}

function AllocationCard({ shipment, run, busy, notify }) {
  const locked = shipment.locked;
  const [method, setMethod] = useState(shipment.allocation?.method || "weight");
  const [manual, setManual] = useState(() => Object.fromEntries((shipment.allocation?.lines || []).map((line) => [line.order_id, centsToInput(line.amount_cents)])));
  const orderIds = shipment.orders.map((order) => order.id);
  if (shipment.orders.length < 2 && !shipment.allocation) return null;
  const manualTotal = orderIds.reduce((sum, id) => sum + (parseMoney(manual[id]) || 0), 0);
  const allocate = () => {
    const payload = { method };
    if (method === "manual") {
      payload.manual_cents = Object.fromEntries(orderIds.map((id) => [id, parseMoney(manual[id])]));
      if (Object.values(payload.manual_cents).some((value) => value === null || Number.isNaN(value))) { notify("Enter an amount for every order.", "danger"); return; }
    }
    run(() => sendJson(`/api/freight-shipments/${shipment.id}/allocation`, "POST", payload), "Freight cost allocated.");
  };
  return (
    <section className="card" aria-labelledby="split-title">
      <header className="card-head"><div className="card-head-text"><h2 className="card-title" id="split-title">Split the cost across orders</h2>
        <p className="card-description">For reporting only: divides this shipment's cost between its orders; it adds no charges. The amounts always add up to the cost to the cent.</p></div></header>
      {shipment.allocation_stale && <div className="callout callout-warn compact-callout"><AlertTriangle size={16} /><span>The cost or orders changed since this split was made. Recalculate it.</span></div>}
      <div className="split-layout">
        <div>
          <div className="method-grid" role="radiogroup" aria-label="Allocation method">
            {Object.entries(ALLOCATION_METHODS).map(([key, entry]) => (
              <label key={key} className={`method-option ${method === key ? "is-active" : ""}`}>
                <input type="radio" name="allocation" value={key} checked={method === key} disabled={locked} onChange={() => setMethod(key)} />
                <span><b>{entry.label}</b><small>{entry.help}</small></span>
              </label>
            ))}
          </div>
          {method === "manual" && <div className="manual-grid">
            {shipment.orders.map((order) => <Field key={order.id} label={order.order_number}><input inputMode="decimal" value={manual[order.id] ?? ""} disabled={locked} onChange={(event) => setManual({ ...manual, [order.id]: event.target.value })} /></Field>)}
            <p className={`small-line ${manualTotal === shipment.expense_total_cents ? "ok-text" : "warn-text"}`}>Entered {money(manualTotal)} of {money(shipment.expense_total_cents)}</p>
          </div>}
          <div className="card-actions-row"><button type="button" className="btn btn-secondary" disabled={busy || locked || !shipment.expense_total_cents} onClick={allocate}><Calculator size={15} />{shipment.allocation ? "Recalculate" : "Allocate cost"}</button></div>
        </div>
        {shipment.allocation && <div className="allocation-result">
          <p className="small-line muted">{ALLOCATION_METHODS[shipment.allocation.method]?.label} · {shipment.allocation.basis_source}</p>
          <table className="data-table is-dense"><tbody>
            {shipment.allocation.lines.map((line) => {
              const order = shipment.orders.find((entry) => entry.id === line.order_id);
              return <tr key={line.order_id}><th scope="row">{order?.order_number || line.order_id}</th><td className="num">{(line.share * 100).toFixed(1)}%</td><td className="num"><b>{money(line.amount_cents)}</b>{line.rounding_adjustment_cents ? <small className="muted" title="Rounding cent"> *</small> : null}</td></tr>;
            })}
            <tr className="total-row"><th scope="row">Total</th><td /><td className="num"><b>{money(shipment.allocation.total_cents)}</b></td></tr>
          </tbody></table>
          {shipment.allocation.lines.some((line) => line.rounding_adjustment_cents) && <p className="hint">* Includes a rounding cent so the split matches the total exactly.</p>}
        </div>}
      </div>
    </section>
  );
}
