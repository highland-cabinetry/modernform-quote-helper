import { Lock } from "lucide-react";
import { Field } from "../Common";
import { CHARGE_SOURCES, centsToInput, money } from "../../lib/format";
import { href } from "../../lib/route";
import { chargeErrors, resolvedCharge } from "./model";
import "../../styles/shipments.css";

// What the customer pays for this freight and where the amount comes from (WORKFLOW §3).
// `form` is the shipment form (charge_source, customer_charge, rep_charge, backcharge); onChange(patch).
export default function ChargeEditor({ form, shipment, onChange, disabled = false }) {
  const errors = chargeErrors(form);
  const resolved = resolvedCharge(form, shipment);
  const quote = shipment.combined_quote;
  const chooseSource = (source) => {
    const patch = { charge_source: source };
    // Start a typed amount from what the customer is charged now, so switching never blanks it.
    if (source === "manual" && !form.customer_charge && resolved.cents != null && !Number.isNaN(resolved.cents)) patch.customer_charge = centsToInput(resolved.cents);
    if (source === "rep_assigned" && !form.rep_charge && shipment.customer_charge_cents != null) patch.rep_charge = centsToInput(shipment.customer_charge_cents);
    onChange(patch);
  };

  return (
    <div className="charge-editor">
      <fieldset className="charge-sources" disabled={disabled}>
        <legend className="sr-only">Where the customer charge comes from</legend>
        {Object.entries(CHARGE_SOURCES).map(([key, entry]) => (
          <label key={key} className={`charge-option ${form.charge_source === key ? "is-active" : ""}`}>
            <input type="radio" name="charge_source" value={key} checked={form.charge_source === key} onChange={() => chooseSource(key)} />
            <span><b>{entry.label}</b><small>{entry.help}</small></span>
          </label>
        ))}
      </fieldset>

      <div className="charge-amount">
        {form.charge_source === "quote" && (
          <div className="charge-readonly" aria-live="polite">
            <span className="charge-readonly-label"><Lock size={13} aria-hidden="true" />Customer charge</span>
            <strong data-testid="charge-amount">{money(resolved.cents)}</strong>
            <small className="muted">
              {quote ? <>From <a className="inline-link" href={href("/quotes", { quote: quote.id })}>{quote.number}</a>{quote.cost?.customer_price_cents != null ? "'s customer price. Change the price on the quote." : " — it has no customer price yet, so the saved charge is kept."}</>
                : resolved.from}
            </small>
          </div>
        )}
        {form.charge_source === "rep_assigned" && (
          <Field label="Rep-assigned charge ($)" error={errors.rep_charge} hint="The customer is charged this amount.">
            <input inputMode="decimal" value={form.rep_charge} disabled={disabled} placeholder="0.00" onChange={(event) => onChange({ rep_charge: event.target.value })} />
          </Field>
        )}
        {form.charge_source === "backcharge" && (
          <Field label="Backcharge ($)" error={errors.backcharge} hint="Billed back to the customer after shipping.">
            <input inputMode="decimal" value={form.backcharge} disabled={disabled} placeholder="0.00" onChange={(event) => onChange({ backcharge: event.target.value })} />
          </Field>
        )}
        {form.charge_source === "manual" && (
          <Field label="Customer charge ($)" error={errors.customer_charge} hint="Leave empty if not known yet.">
            <input inputMode="decimal" value={form.customer_charge} disabled={disabled} placeholder="0.00" onChange={(event) => onChange({ customer_charge: event.target.value })} />
          </Field>
        )}
        {form.charge_source === "none" && (
          <div className="charge-readonly"><span className="charge-readonly-label">Customer charge</span><strong data-testid="charge-amount">{money(0)}</strong><small className="muted">The customer pays nothing for this freight.</small></div>
        )}
      </div>
    </div>
  );
}
