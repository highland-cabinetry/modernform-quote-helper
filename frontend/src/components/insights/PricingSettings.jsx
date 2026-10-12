import { useEffect, useMemo, useRef, useState } from "react";
import { Calculator, LoaderCircle, RotateCcw, Save } from "lucide-react";
import { sendJson } from "../../api";
import { centsToInput, money } from "../../lib/format";
import { useDebounced } from "../../lib/useResource";
import { Field } from "../Common";
import { dollarsToCents, saveCompanyConfig } from "./shared";

const STEPS = [[0, "None"], [100, "$1"], [500, "$5"], [1000, "$10"], [2500, "$25"]];
const EXAMPLES = [15000, 40000, 150000];

function toForm(pricing = {}) {
  const step = pricing.round_up_to_cents || 0;
  return {
    markup_percent: String(pricing.markup_percent ?? 0),
    markup_fixed: centsToInput(pricing.markup_fixed_cents || 0),
    step: STEPS.some(([value]) => value === step) ? String(step) : "custom",
    custom_step: STEPS.some(([value]) => value === step) ? "" : centsToInput(step),
    minimum: centsToInput(pricing.minimum_cents || 0),
  };
}

// Form → PricingRule, or { errors } when a value is out of range.
export function toRule(form) {
  const errors = {};
  const percent = Number(form.markup_percent === "" ? 0 : form.markup_percent);
  if (!Number.isFinite(percent) || percent < 0 || percent > 500) errors.markup_percent = "0 to 500 %";
  const fixed = dollarsToCents(form.markup_fixed);
  if (Number.isNaN(fixed) || fixed > 10_000_000) errors.markup_fixed = "A dollar amount, e.g. 25.00";
  const step = form.step === "custom" ? dollarsToCents(form.custom_step, { allowEmpty: false }) : Number(form.step);
  if (Number.isNaN(step) || step > 100_000 || (form.step === "custom" && step <= 0)) errors.custom_step = "$0.01 to $1,000";
  const minimum = dollarsToCents(form.minimum);
  if (Number.isNaN(minimum) || minimum > 10_000_000) errors.minimum = "A dollar amount, e.g. 150.00";
  if (Object.keys(errors).length) return { errors };
  return { rule: { markup_percent: percent, markup_fixed_cents: fixed, round_up_to_cents: step, minimum_cents: minimum } };
}

function describe(rule) {
  const parts = [`carrier price + ${rule.markup_percent}%`];
  if (rule.markup_fixed_cents) parts.push(`+ ${money(rule.markup_fixed_cents)}`);
  let text = parts.join(" ");
  text += rule.round_up_to_cents ? `, rounded up to the next ${money(rule.round_up_to_cents).replace(/\.00$/, "")}` : ", rounded up to the cent";
  if (rule.minimum_cents) text += `, at least ${money(rule.minimum_cents)}`;
  return `Customer price = ${text}.`;
}

export default function PricingSettings({ company, notify, onSaved }) {
  const saved = company?.config?.pricing;
  const [form, setForm] = useState(() => toForm(saved));
  const [sample, setSample] = useState("842.50");
  const [preview, setPreview] = useState({ loading: false, main: null, examples: [], error: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { setForm(toForm(saved)); }, [saved]);

  const { rule, errors = {} } = useMemo(() => toRule(form), [form]);
  const sampleCents = dollarsToCents(sample, { allowEmpty: false });
  const request = useDebounced(JSON.stringify({ rule, sampleCents }), 250);
  const generation = useRef(0);
  const dirty = Boolean(rule) && JSON.stringify(rule) !== JSON.stringify({
    markup_percent: saved?.markup_percent ?? 0, markup_fixed_cents: saved?.markup_fixed_cents ?? 0,
    round_up_to_cents: saved?.round_up_to_cents ?? 0, minimum_cents: saved?.minimum_cents ?? 0,
  });
  const invalid = !rule;

  useEffect(() => {
    const { rule: pending, sampleCents: carrier } = JSON.parse(request);
    if (!pending || !company) return;
    const token = ++generation.current;
    setPreview((current) => ({ ...current, loading: true, error: "" }));
    const ask = (cents) => sendJson("/api/quotes/pricing-preview", "POST", { company_id: company.id, carrier_cents: cents, pricing: pending });
    const amounts = Number.isFinite(carrier) && carrier >= 0 ? [carrier, ...EXAMPLES] : EXAMPLES;
    Promise.all(amounts.map(ask)).then((results) => {
      if (token !== generation.current) return;
      const main = Number.isFinite(carrier) && carrier >= 0 ? results[0] : null;
      setPreview({ loading: false, main, examples: main ? results.slice(1) : results, error: "" });
    }).catch((error) => { if (token === generation.current) setPreview({ loading: false, main: null, examples: [], error: error.message }); });
  }, [request, company]);

  const set = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const save = async (event) => {
    event.preventDefault();
    if (!rule) return;
    setBusy(true);
    try {
      await saveCompanyConfig(company.id, "pricing", rule);
      notify("Pricing rule saved. New price selections use it; existing quotes keep their price.");
      await onSaved?.();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };

  return (
    <form className="ins-settings-grid" onSubmit={save} aria-labelledby="pricing-title">
      <section className="card">
        <header className="card-head">
          <div className="card-head-text">
            <h2 className="card-title" id="pricing-title">Customer price rule</h2>
            <p className="card-description">How a carrier price becomes the price you quote the customer. Applied when a price option is selected on a quote; a price typed by hand on a quote is kept.</p>
          </div>
        </header>
        <div className="form-grid ins-pricing-form">
          <Field label="Markup" error={errors.markup_percent} hint="Percent of the carrier price">
            <span className="ins-unit"><input type="number" min="0" max="500" step="0.5" value={form.markup_percent} onChange={set("markup_percent")} aria-label="Markup percent" /><span aria-hidden="true">%</span></span>
          </Field>
          <Field label="Fixed markup" error={errors.markup_fixed} hint="Added after the percent">
            <span className="ins-unit ins-unit-pre"><span aria-hidden="true">$</span><input inputMode="decimal" value={form.markup_fixed} onChange={set("markup_fixed")} aria-label="Fixed markup in dollars" /></span>
          </Field>
          <div className="field field-wide">
            <span id="round-label">Round up to</span>
            <div className="segmented ins-steps" role="radiogroup" aria-labelledby="round-label">
              {[...STEPS.map(([value, label]) => [String(value), label]), ["custom", "Custom"]].map(([value, label]) => (
                <button key={value} type="button" role="radio" aria-checked={form.step === value} className={form.step === value ? "active" : ""}
                  onClick={() => setForm((current) => ({ ...current, step: value }))}>{label}</button>
              ))}
            </div>
            {form.step === "custom" && (
              <span className="ins-custom-step">
                <span className="ins-unit ins-unit-pre"><span aria-hidden="true">$</span>
                  <input inputMode="decimal" value={form.custom_step} onChange={set("custom_step")} aria-label="Custom rounding step in dollars" placeholder="e.g. 2.50" autoFocus /></span>
                {errors.custom_step && <small className="field-error">{errors.custom_step}</small>}
              </span>
            )}
            <small className="field-hint">Prices always round up, never down.</small>
          </div>
          <Field label="Minimum price" error={errors.minimum} hint="The customer price is never lower">
            <span className="ins-unit ins-unit-pre"><span aria-hidden="true">$</span><input inputMode="decimal" value={form.minimum} onChange={set("minimum")} aria-label="Minimum price in dollars" /></span>
          </Field>
        </div>
        {rule && <p className="hint ins-rule-text">{describe(rule)}</p>}
        <div className="card-actions-row">
          <button type="submit" className="btn btn-primary" disabled={busy || invalid || !dirty}>{busy ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}Save pricing</button>
          {dirty && <button type="button" className="btn btn-ghost" onClick={() => setForm(toForm(saved))}><RotateCcw size={15} />Discard changes</button>}
          {!dirty && !invalid && <span className="muted ins-saved">Saved</span>}
        </div>
      </section>

      <section className="card ins-preview-card" aria-labelledby="pricing-preview-title" aria-live="polite">
        <header className="card-head">
          <div className="card-head-text">
            <h2 className="card-title" id="pricing-preview-title"><Calculator size={16} aria-hidden="true" /> Try it</h2>
            <p className="card-description">{dirty ? "Preview of the unsaved rule." : "Preview of the saved rule."}</p>
          </div>
          {preview.loading && <LoaderCircle size={16} className="spin muted" aria-label="Calculating" />}
        </header>
        <Field label="Carrier price">
          <span className="ins-unit ins-unit-pre"><span aria-hidden="true">$</span><input inputMode="decimal" value={sample} onChange={(event) => setSample(event.target.value)} aria-label="Carrier price in dollars" /></span>
        </Field>
        {preview.error && <p className="danger-text small-line">{preview.error}</p>}
        {invalid && <p className="warn-text small-line">Fix the highlighted values to see a preview.</p>}
        {!invalid && preview.main && (
          <p className="ins-preview-line">
            <span>A <b>{money(preview.main.carrier_cents)}</b> carrier price becomes</span> <strong className="ins-preview-price">{money(preview.main.customer_price_cents)}</strong>
            <small>{money(preview.main.markup_cents, { signed: true })} markup</small>
          </p>
        )}
        {!invalid && preview.examples.length > 0 && (
          <table className="data-table ins-examples">
            <caption className="sr-only">More examples</caption>
            <thead><tr><th scope="col" className="num">Carrier</th><th scope="col" className="num">Customer</th><th scope="col" className="num">Markup</th></tr></thead>
            <tbody>{preview.examples.map((entry) => (
              <tr key={entry.carrier_cents}><td className="num">{money(entry.carrier_cents)}</td><td className="num"><b>{money(entry.customer_price_cents)}</b></td><td className="num muted">{money(entry.markup_cents)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </form>
  );
}
