import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, LoaderCircle, Mail, RotateCcw, Save } from "lucide-react";
import { EMAIL_KINDS } from "../../lib/format";
import useResource from "../../lib/useResource";
import { ErrorBlock, Field, Loading } from "../Common";
import { saveCompanyConfig } from "./shared";

// Same rules as services/emails.render: known {names} are replaced, unknown ones stay as typed,
// a [[ … ]] segment is dropped when a placeholder inside it is empty, and stray spacing is tidied.
export function renderTemplate(text, values) {
  const known = (name) => Object.prototype.hasOwnProperty.call(values, name);
  let result = String(text || "").replace(/\[\[([\s\S]*?)\]\]/g, (_, inner) => {
    const names = [...inner.matchAll(/\{([a-z_]+)\}/g)].map((match) => match[1]).filter(known);
    return names.some((name) => !values[name]) ? "" : inner;
  });
  result = result.replace(/\{([a-z_]+)\}/g, (match, name) => (known(name) ? values[name] : match));
  result = result.replace(/\(\s*\)/g, "").replace(/[ \t]+([,.;:!?])/g, "$1").replace(/[ \t]{2,}/g, " ");
  return result.replace(/\n{3,}/g, "\n\n").trim();
}

export function sampleValues(signature, reps = []) {
  return {
    orders: "SO10421, SO10422", invoices: "INV20417", customer: "Aspen Kitchen Co", contact: "Jordan Buyer",
    price: "$970.00", terms: "Residential, Liftgate", zip: "80022", city: "Commerce City, CO", pallets: "2", weight: "1,240",
    carrier: "Estes", pro: "1234567890", pickup_date: "Tue Oct 13, 2026", delivery_date: "Fri Oct 16, 2026",
    expected_delivery: "Fri Oct 16, 2026", expires: "Mon Oct 19, 2026",
    rep: reps.find((rep) => rep.active)?.name || "Alex Rep", signature: signature || "", quote_number: "Q-000123", shipment_number: "S-000045",
  };
}

const unknownPlaceholders = (text, placeholders) => [...new Set([...String(text || "").matchAll(/\{([a-z_]+)\}/g)]
  .map((match) => match[1]).filter((name) => !placeholders[name]))];

// Prepared-email settings: signature, CC the rep, and subject + body per kind with placeholders.
export default function EmailTemplates({ company, notify, onSaved }) {
  const catalog = useResource(company ? `/api/emails/templates?company_id=${encodeURIComponent(company.id)}` : null);
  const [draft, setDraft] = useState(null);
  const [kind, setKind] = useState("quote");
  const [busy, setBusy] = useState(false);
  const fields = { subject: useRef(null), body: useRef(null) };
  const lastField = useRef("body");
  const caret = useRef(null);

  const fromCatalog = (data) => ({
    signature: data.signature || "", cc_rep: Boolean(data.cc_rep),
    templates: Object.fromEntries(data.kinds.map((name) => [name, { subject: data.templates[name].subject, body: data.templates[name].body }])),
  });
  useEffect(() => { if (catalog.data) setDraft(fromCatalog(catalog.data)); }, [catalog.data]);
  useEffect(() => {
    if (!caret.current) return;
    const { field, position } = caret.current;
    caret.current = null;
    const element = fields[field].current;
    if (!element) return;
    // Put the cursor right after the inserted placeholder; again on the next frame in case the
    // browser moved it to the end while applying the new value.
    const place = () => { element.focus(); element.setSelectionRange(position, position); };
    place();
    const frame = requestAnimationFrame(place);
    return () => cancelAnimationFrame(frame);
  });

  const data = catalog.data;
  const placeholders = data?.placeholders || {};
  const current = draft?.templates[kind];
  const defaults = data?.templates[kind]?.default;
  const isDefault = (name) => draft && data && draft.templates[name].subject === data.templates[name].default.subject
    && draft.templates[name].body === data.templates[name].default.body;
  const dirty = useMemo(() => draft && data && JSON.stringify(draft) !== JSON.stringify(fromCatalog(data)), [draft, data]);
  const values = useMemo(() => sampleValues(draft?.signature, company?.config?.reps), [draft?.signature, company]);
  const unknown = current ? unknownPlaceholders(`${current.subject}\n${current.body}`, placeholders) : [];

  if (catalog.error) return <ErrorBlock message={catalog.error} onRetry={catalog.reload} />;
  if (!draft || !data) return <Loading />;

  const setTemplate = (field, value) => setDraft((state) => ({ ...state, templates: { ...state.templates, [kind]: { ...state.templates[kind], [field]: value } } }));
  const insert = (name) => {
    const field = lastField.current;
    const element = fields[field].current;
    const text = current[field];
    const token = `{${name}}`;
    const start = element?.selectionStart ?? text.length;
    const end = element?.selectionEnd ?? text.length;
    caret.current = { field, position: start + token.length };
    setTemplate(field, text.slice(0, start) + token + text.slice(end));
  };
  const reset = () => setDraft((state) => ({ ...state, templates: { ...state.templates, [kind]: { subject: defaults.subject, body: defaults.body } } }));

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    const templates = Object.fromEntries(data.kinds.filter((name) => !isDefault(name)).map((name) => [name, draft.templates[name]]));
    try {
      await saveCompanyConfig(company.id, "email", (email = {}) => ({ ...email, signature: draft.signature, cc_rep: draft.cc_rep, templates }));
      notify("Customer email settings saved.");
      await onSaved?.();
      await catalog.reload({ quiet: true });
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };

  return (
    <form className="ins-email" onSubmit={save} aria-labelledby="email-title">
      <section className="card">
        <header className="card-head">
          <div className="card-head-text">
            <h2 className="card-title" id="email-title">Customer emails</h2>
            <p className="card-description">Quote Helper prepares each email for Outlook; nothing is sent from the app. These settings apply to {company.code}.</p>
          </div>
        </header>
        <div className="form-grid">
          <Field label="Signature" hint="Inserted where a template has {signature}">
            <textarea rows={4} value={draft.signature} maxLength={2000} onChange={(event) => setDraft((state) => ({ ...state, signature: event.target.value }))} />
          </Field>
          <div className="field">
            <span>Copy the sales rep</span>
            <label className="check-field ins-check">
              <input type="checkbox" checked={draft.cc_rep} onChange={(event) => setDraft((state) => ({ ...state, cc_rep: event.target.checked }))} />
              <span><b>CC the order's sales rep</b><small>Uses the active reps with an email under Sales reps, matched by the name on the order.</small></span>
            </label>
          </div>
        </div>
      </section>

      <section className="card" aria-labelledby="templates-title">
        <header className="card-head">
          <div className="card-head-text">
            <h2 className="card-title" id="templates-title">Templates</h2>
            <p className="card-description">Click a placeholder to insert it where the cursor is. Text inside [[ … ]] is left out when a placeholder in it has no value.</p>
          </div>
        </header>
        <div className="ins-kind-tabs" role="tablist" aria-label="Email kind">
          {data.kinds.map((name) => (
            <button key={name} type="button" role="tab" aria-selected={kind === name} className={kind === name ? "active" : ""} onClick={() => setKind(name)}>
              {EMAIL_KINDS[name] || name}{!isDefault(name) && <span className="ins-custom-dot" title="Customized"><span className="sr-only"> (customized)</span></span>}
            </button>
          ))}
        </div>
        <div className="ins-template-grid" role="tabpanel" aria-label={EMAIL_KINDS[kind] || kind}>
          <div className="ins-template-edit">
            <Field label="Subject">
              <input ref={fields.subject} value={current.subject} maxLength={300} onFocus={() => { lastField.current = "subject"; }}
                onChange={(event) => setTemplate("subject", event.target.value)} />
            </Field>
            <Field label="Body">
              <textarea ref={fields.body} rows={11} value={current.body} maxLength={10000} onFocus={() => { lastField.current = "body"; }}
                onChange={(event) => setTemplate("body", event.target.value)} />
            </Field>
            {unknown.length > 0 && <p className="warn-text small-line">Not a placeholder: {unknown.map((name) => `{${name}}`).join(", ")}. It will be left exactly as typed.</p>}
            <div className="ins-placeholders" role="group" aria-label="Insert a placeholder">
              {Object.entries(placeholders).map(([name, description]) => (
                <button key={name} type="button" className="ins-ph" title={description} onMouseDown={(event) => event.preventDefault()} onClick={() => insert(name)}>
                  {`{${name}}`}<span className="sr-only">: {description}</span>
                </button>
              ))}
            </div>
            <div className="card-actions-row">
              <button type="button" className="btn btn-ghost btn-sm" disabled={isDefault(kind)} onClick={reset}><RotateCcw size={14} />Reset to default</button>
              <span className="muted small-line">{isDefault(kind) ? "Using the built-in text." : "Customized."}</span>
            </div>
          </div>
          <div className="ins-template-preview" aria-live="polite">
            <h3><Eye size={15} aria-hidden="true" /> Preview with sample values</h3>
            <div className="ins-mail">
              <div className="ins-mail-head"><Mail size={14} aria-hidden="true" /><b>{renderTemplate(current.subject, values) || <span className="muted">(no subject: the default is used)</span>}</b></div>
              <pre className="ins-mail-body">{renderTemplate(current.body, values) || "(no body: the default is used)"}</pre>
            </div>
            <p className="hint">Sample orders, prices and dates; the real email uses the quote's or shipment's details.</p>
          </div>
        </div>
      </section>
      <div className={`ins-sticky-actions ${dirty ? "is-dirty" : ""}`}>
        <button type="submit" className="btn btn-primary" disabled={busy || !dirty}>{busy ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}Save email settings</button>
        {dirty && <button type="button" className="btn btn-ghost" onClick={() => setDraft(fromCatalog(data))}><RotateCcw size={15} />Discard changes</button>}
        {!dirty && <span className="muted ins-saved">Saved</span>}
      </div>
    </form>
  );
}
