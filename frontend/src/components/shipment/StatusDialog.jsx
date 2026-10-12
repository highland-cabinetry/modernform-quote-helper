import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { Field, Modal } from "../Common";
import { SHIPMENT_STATUS } from "../../lib/format";
import { today } from "./model";

const TITLES = {
  planning: "Move back to planning",
  booked: "Mark booked",
  in_transit: "Picked up — in transit",
  delivered: "Mark delivered",
  closed: "Close shipment",
  cancelled: "Cancel shipment",
};

const DESCRIPTIONS = {
  booked: "Record who is carrying it and when it will be picked up.",
  in_transit: "Record the PRO number so the customer can track it.",
  delivered: "Record when it arrived.",
  closed: "Closing locks the shipment: orders, costs and measurements can only change after reopening it with a reason.",
  cancelled: "The orders become free to ship on another shipment. You can reopen it later with a reason.",
};

// Confirm a status change and capture the fields that belong to that moment.
export default function StatusDialog({ target, form, shipment, onClose, onConfirm, busy, dirty = false }) {
  const [values, setValues] = useState({});
  useEffect(() => {
    if (!target) return;
    setValues({
      carrier: form.carrier, broker: form.broker, bol_number: form.bol_number, pro_number: form.pro_number,
      pickup_date: form.pickup_date || (target === "in_transit" ? today() : ""),
      expected_delivery_date: form.expected_delivery_date,
      delivery_date: form.delivery_date || (target === "delivered" ? today() : ""),
    });
    // After the modal's own focus handling (child effects run first): start in the first field.
    document.querySelector("#status-form input")?.focus();
  }, [target]);
  if (!target) return null;
  const set = (key) => (event) => setValues({ ...values, [key]: event.target.value });
  const fields = {
    booked: ["carrier", "broker", "bol_number", "pickup_date", "expected_delivery_date"],
    in_transit: ["carrier", "pro_number", "pickup_date", "expected_delivery_date"],
    delivered: ["delivery_date"],
  }[target] || [];
  const labels = {
    carrier: ["Carrier", "text"], broker: ["Broker", "text"], bol_number: ["BOL number", "text"], pro_number: ["PRO / tracking", "text"],
    pickup_date: ["Pickup date", "date"], expected_delivery_date: ["Expected delivery", "date"], delivery_date: ["Delivered on", "date"],
  };
  const checks = target === "closed" ? [
    [shipment.expenses.some((entry) => !entry.voided), "Carrier bill recorded", "No carrier bill yet — profit is only an estimate."],
    [Boolean(shipment.actual), "Actual measurements recorded", "No actual measurements — weight can't be compared to the quote."],
    [Boolean(form.delivery_date), "Delivery date recorded", "No delivery date."],
  ] : [];
  const submit = (event) => {
    event.preventDefault();
    const overrides = { status: target };
    for (const key of fields) overrides[key] = String(values[key] || "").trim();
    onConfirm(overrides);
  };
  const danger = target === "cancelled";
  return (
    <Modal open title={TITLES[target] || `Set to ${SHIPMENT_STATUS[target]?.label}`} description={DESCRIPTIONS[target]} onClose={onClose} tone={danger ? "danger" : undefined}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Back</button>
        <button type="submit" form="status-form" className={`btn ${danger ? "btn-danger-solid" : "btn-primary"}`} disabled={busy}>{TITLES[target] || "Save"}</button>
      </>}>
      <form id="status-form" className="form-grid" onSubmit={submit}>
        {fields.map((key) => (
          <Field key={key} label={labels[key][0]} wide={fields.length === 1}>
            <input type={labels[key][1]} value={values[key] || ""} onChange={set(key)} {...(key === "pro_number" ? { autoComplete: "off" } : {})} />
          </Field>
        ))}
        {target === "in_transit" && !values.pro_number?.trim() && <p className="field-wide small-line warn-text">Without a PRO the shipped email has no tracking number.</p>}
        {checks.length > 0 && <ul className="close-checks field-wide">
          {checks.map(([ok, good, bad]) => <li key={good} className={ok ? "ok-text" : "warn-text"}>{ok ? <CheckCircle2 size={15} aria-hidden="true" /> : <AlertTriangle size={15} aria-hidden="true" />}{ok ? good : bad}</li>)}
        </ul>}
        {dirty && <p className="field-wide small-line muted">Your other unsaved changes to this shipment are saved too.</p>}
      </form>
    </Modal>
  );
}
