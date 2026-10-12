import { useEffect, useRef, useState } from "react";
import { Building2, DollarSign, FlaskConical, LoaderCircle, Mail, Plus, Save, Trash2, Users } from "lucide-react";
import { getJson, sendJson, uploadFiles } from "../api";
import { Badge, DataTable, Drawer, ErrorBlock, Field, Loading, PageHeader } from "../components/Common";
import EmailTemplates from "../components/insights/EmailTemplates";
import PricingSettings from "../components/insights/PricingSettings";
import RepsEditor from "../components/insights/RepsEditor";
import { CATALOG_LABEL, FEATURES } from "../edition";
import useResource from "../lib/useResource";
import { useApp } from "../shell/AppShell";
import "../styles/insights.css";

const MAPPING_FIELDS = [
  ["sku", "SKU / item number"], ["quantity", "Quantity"], ["description", "Description"],
  ["order_number", "Order / invoice number"], ["packing_state", "Flatpack / assembled"],
];
const blankLayout = () => ({ name: "", detect_pattern: "", order_pattern: "", line_pattern: "", po_pattern: "", date_pattern: "", customer_pattern: "", ship_to_zip_pattern: "", assembled_pattern: "" });
const newCompany = () => ({
  id: null, code: "", name: "", active: true,
  config: { catalog: "", rule_set: "none", adapters: ["table"], document_markers: [], column_mapping: { sku: [], description: [], quantity: [], order_number: [], packing_state: [] },
    text_layouts: [], bare_number_prefix: "", default_packing_state: "flatpack", origin: { name: "", street: "", city: "", state: "", postal_code: "", phone: "", email: "" } },
});

const SECTIONS = [
  { key: "companies", label: "Companies & import", icon: Building2 },
  { key: "pricing", label: "Pricing", icon: DollarSign },
  { key: "reps", label: "Sales reps", icon: Users, workflow: true },
  { key: "emails", label: "Customer emails", icon: Mail, workflow: true },
].filter((section) => FEATURES.freightWorkflow || !section.workflow);

export default function Settings() {
  const { companies, company, reloadCompanies, notify, route, go, multiCompany } = useApp();
  const section = SECTIONS.some((entry) => entry.key === route.query.tab) ? route.query.tab : "companies";
  const select = (key) => go(route.path, { ...route.query, tab: key === "companies" ? "" : key }, { replace: true });
  const onKeyDown = (event) => {
    const index = SECTIONS.findIndex((entry) => entry.key === section);
    const next = { ArrowRight: SECTIONS[(index + 1) % SECTIONS.length], ArrowLeft: SECTIONS[(index + SECTIONS.length - 1) % SECTIONS.length],
      Home: SECTIONS[0], End: SECTIONS[SECTIONS.length - 1] }[event.key];
    if (!next) return;
    event.preventDefault();
    select(next.key);
    requestAnimationFrame(() => document.getElementById(`settings-tab-${next.key}`)?.focus());
  };
  const reload = () => reloadCompanies();
  return (
    <div className="page ins-page">
      <PageHeader title="Company & Import Settings"
        lede={FEATURES.freightWorkflow
          ? "Document formats and pallet limits per company, plus how customer prices are set, who is copied on customer emails, and the email texts."
          : "Document formats and pallet limits per company, and how customer prices are set."} />
      <div className="tabs ins-tabs" role="tablist" aria-label="Settings sections" onKeyDown={onKeyDown}>
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button key={key} id={`settings-tab-${key}`} type="button" role="tab" aria-selected={section === key} aria-controls="settings-panel"
            tabIndex={section === key ? 0 : -1} className={section === key ? "active" : ""} onClick={() => select(key)}>
            <Icon size={15} aria-hidden="true" />{label}
          </button>
        ))}
      </div>
      <div id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${section}`}>
        {section !== "companies" && multiCompany && company && <p className="ins-scope"><Building2 size={14} aria-hidden="true" />Settings for <b>{company.code} · {company.name}</b>. Switch company at the top of the window.</p>}
        {section === "companies" && <CompaniesSection companies={companies} reloadCompanies={reloadCompanies} notify={notify} />}
        {section === "pricing" && company && <PricingSettings company={company} notify={notify} onSaved={reload} />}
        {section === "reps" && company && <RepsEditor company={company} notify={notify} onSaved={reload} />}
        {section === "emails" && company && <EmailTemplates company={company} notify={notify} onSaved={reload} />}
      </div>
    </div>
  );
}

function CompaniesSection({ companies, reloadCompanies, notify }) {
  const { data: formats } = useResource("/api/import-formats");
  const [editing, setEditing] = useState(null);
  const [settingsFor, setSettingsFor] = useState(null);
  return (
    <>
      <div className="list-toolbar">
        <p className="muted ins-toolbar-text">Each company has its own document formats, SKU conventions, product catalog, rule set and pallet limits. Onboarding a new company is configuration, not code.</p>
        <button type="button" className="btn btn-primary" onClick={() => setEditing(newCompany())}><Plus size={16} />Add company</button>
      </div>
      <section className="card card-flush">
        <DataTable rows={companies} onRowClick={(company) => setEditing(structuredClone(company))} columns={[
          { key: "code", label: "Company", render: (company) => <span className="cell-stack"><b>{company.code} · {company.name}</b>{company.is_default && <small>Default company</small>}</span> },
          { key: "catalog", label: "Catalog", sort: (company) => company.config.catalog, render: (company) => company.config.catalog === "default" ? CATALOG_LABEL : company.config.catalog },
          { key: "rules", label: "Product rules", sort: (company) => company.config.rule_set, render: (company) => formats?.rule_sets.find((set) => set.name === company.config.rule_set)?.label || company.config.rule_set },
          { key: "adapters", label: "Document formats", sortable: false, render: (company) => company.config.adapters.map((name) => formats?.adapters.find((a) => a.name === name)?.label || name).join(", ") },
          { key: "active", label: "Status", render: (company) => company.active ? <Badge tone="ok">Active</Badge> : <Badge>Inactive</Badge> },
          { key: "limits", label: <span className="sr-only">Pallet limits</span>, sortable: false, render: (company) => <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSettingsFor(company)}>Pallet limits & defaults</button> },
        ]} />
      </section>
      {formats && <section className="card">
        <h2 className="card-title">Supported document formats</h2>
        <ul className="format-list">{formats.adapters.map((adapter) => <li key={adapter.name}><b>{adapter.label}</b><span>{adapter.description}</span></li>)}</ul>
        <p className="hint">Scanned PDFs without a text layer are reported, not guessed: OCR isn't installed. A company whose PDFs have no tables can be read with a configured text layout.</p>
      </section>}
      <CompanyDrawer company={editing} formats={formats} onClose={() => setEditing(null)} notify={notify}
        onSaved={async () => { await reloadCompanies(); setEditing(null); }} />
      <PackingSettingsDrawer company={settingsFor} onClose={() => setSettingsFor(null)} notify={notify} />
    </>
  );
}

function listText(values) { return (values || []).join(", "); }
function textList(value) { return value.split(",").map((entry) => entry.trim()).filter(Boolean); }

function CompanyDrawer({ company, formats, onClose, onSaved, notify }) {
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);
  const testInput = useRef(null);
  useEffect(() => { setDraft(company ? structuredClone(company) : null); setTest(null); }, [company]);
  if (!company || !draft) return null;
  const config = draft.config;
  const setConfig = (field, value) => setDraft((current) => ({ ...current, config: { ...current.config, [field]: value } }));
  const ruleSet = formats?.rule_sets.find((set) => set.name === config.rule_set);
  const payload = () => ({ code: draft.code, name: draft.name, active: draft.active,
    config: { ...config, catalog: config.catalog || draft.code.toLowerCase(), text_layouts: config.text_layouts.map((layout) => Object.fromEntries(Object.entries(layout).map(([key, value]) => [key, value || null]))) } });
  const save = async () => {
    setBusy(true);
    try {
      if (draft.id) await sendJson(`/api/companies/${draft.id}`, "PUT", payload());
      else await sendJson("/api/companies", "POST", payload());
      notify(`${draft.code} saved.`);
      await onSaved();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const runTest = async (files) => {
    if (!files.length || !draft.id) return;
    setBusy(true);
    try { setTest(await uploadFiles("/api/imports/test", files, "files", { company_id: draft.id, config_json: JSON.stringify(payload().config) })); }
    catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const toggleAdapter = (name, on) => setConfig("adapters", on ? [...config.adapters, name] : config.adapters.filter((value) => value !== name));
  return (
    <Drawer open wide onClose={onClose} labelId="company-title" title={draft.id ? `${draft.code} · ${draft.name}` : "Add company"}
      subtitle="Import, matching and product-rule settings for this company's documents."
      footer={<><span className="top-spacer" /><button type="button" className="btn btn-primary" disabled={busy || !draft.code || !draft.name} onClick={save}><Save size={15} />Save company</button></>}>
      <fieldset className="form-section"><legend>Company</legend><div className="form-grid form-grid-3">
        <Field label="Code" hint="Short, e.g. HCI"><input value={draft.code} maxLength={12} onChange={(event) => setDraft({ ...draft, code: event.target.value.toUpperCase() })} /></Field>
        <Field label="Name"><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></Field>
        <Field label="Status"><select value={draft.active ? "1" : "0"} disabled={draft.is_default} onChange={(event) => setDraft({ ...draft, active: event.target.value === "1" })}><option value="1">Active</option><option value="0">Inactive</option></select></Field>
      </div></fieldset>
      <fieldset className="form-section"><legend>Catalog and product rules</legend><div className="form-grid form-grid-3">
        <Field label="Product catalog" hint={`Companies sharing a name share a catalog; 'default' is the ${CATALOG_LABEL.toLowerCase()}`}><input value={config.catalog} placeholder={draft.code.toLowerCase() || "catalog name"} onChange={(event) => setConfig("catalog", event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} /></Field>
        <Field label="Product rule set"><select value={config.rule_set} onChange={(event) => setConfig("rule_set", event.target.value)}>{formats?.rule_sets.map((set) => <option key={set.name} value={set.name}>{set.label}</option>)}</select></Field>
        <Field label="Bare order numbers become" hint="e.g. INV turns 68606 into INV68606"><input value={config.bare_number_prefix} maxLength={10} onChange={(event) => setConfig("bare_number_prefix", event.target.value.toUpperCase())} /></Field>
      </div>
      {ruleSet && <ul className="rule-summary">{ruleSet.rules.map((rule) => <li key={rule.code}><b>{rule.title}</b><span>{rule.detail}</span></li>)}</ul>}
      </fieldset>
      <fieldset className="form-section"><legend>Document formats</legend>
        <div className="check-grid">{formats?.adapters.map((adapter) => <label key={adapter.name} className="check-field"><input type="checkbox" checked={config.adapters.includes(adapter.name)} onChange={(event) => toggleAdapter(adapter.name, event.target.checked)} /><span><b>{adapter.label}</b><small>{adapter.description}</small></span></label>)}</div>
        <div className="form-grid">
          <Field label="Text that identifies this company's documents" hint="Comma separated"><input value={listText(config.document_markers)} onChange={(event) => setConfig("document_markers", textList(event.target.value))} /></Field>
          <Field label="Default packing state"><select value={config.default_packing_state} onChange={(event) => setConfig("default_packing_state", event.target.value)}><option value="flatpack">Flatpack</option><option value="assembled">Assembled</option></select></Field>
        </div>
      </fieldset>
      <fieldset className="form-section"><legend>Column names in spreadsheets and PDF tables</legend>
        <p className="hint">Common names (SKU, Item Number, Qty, Quantity, Description…) are already recognised. Add this company's own headings here.</p>
        <div className="form-grid">{MAPPING_FIELDS.map(([field, label]) => <Field key={field} label={label}><input value={listText(config.column_mapping[field])} placeholder="e.g. Part #, Units" onChange={(event) => setConfig("column_mapping", { ...config.column_mapping, [field]: textList(event.target.value) })} /></Field>)}</div>
      </fieldset>
      <fieldset className="form-section"><legend>Text layouts (PDFs without tables)</legend>
        {config.text_layouts.map((layout, index) => (
          <div key={index} className="layout-card">
            <div className="form-grid">
              <Field label="Layout name"><input value={layout.name} onChange={(event) => setConfig("text_layouts", config.text_layouts.map((entry, i) => i === index ? { ...entry, name: event.target.value } : entry))} /></Field>
              {[["detect_pattern", "Detect (regex on page 1)"], ["order_pattern", "Order number (?P<order>…)"], ["line_pattern", "Item line (?P<quantity>…) (?P<sku>…) (?P<description>…)"],
                ["po_pattern", "P.O. (optional)"], ["date_pattern", "Date (optional)"], ["customer_pattern", "Customer (optional)"], ["ship_to_zip_pattern", "Ship-to ZIP (optional)"], ["assembled_pattern", "Assembled marker (optional)"]].map(([key, label]) => (
                <Field key={key} label={label}><input className="mono" value={layout[key] || ""} onChange={(event) => setConfig("text_layouts", config.text_layouts.map((entry, i) => i === index ? { ...entry, [key]: event.target.value } : entry))} /></Field>
              ))}
            </div>
            <button type="button" className="btn btn-ghost btn-ghost-danger btn-sm" onClick={() => setConfig("text_layouts", config.text_layouts.filter((_, i) => i !== index))}><Trash2 size={14} />Remove layout</button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setConfig("text_layouts", [...config.text_layouts, blankLayout()]); if (!config.adapters.includes("text_layout")) toggleAdapter("text_layout", true); }}><Plus size={14} />Add text layout</button>
      </fieldset>
      {draft.id && <fieldset className="form-section"><legend>Test a document</legend>
        <p className="hint">Reads a sample file with the settings above (saved or not) and shows what would be imported. Nothing is stored.</p>
        <input ref={testInput} hidden multiple type="file" accept=".pdf,.csv,.tsv,.xlsx,.xlsm,.zip" onChange={(event) => { runTest([...event.target.files]); event.target.value = ""; }} />
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => testInput.current?.click()}>{busy ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}Choose sample file</button>
        {test && <div className="test-results">{test.files.map((file) => (
          <div key={file.file} className="test-file">
            <b>{file.file}</b> <Badge tone={file.status === "ok" ? "ok" : "warn"}>{file.status}</Badge> {file.adapter && <small className="muted">{file.adapter}</small>}
            {file.messages.map((message) => <p key={message} className="small-line">{message}</p>)}
            {file.orders.map((order) => <p key={`${order.order_number}-${order.pages}`} className="small-line">Order <b>{order.order_number || "(none)"}</b>: {order.line_count} lines, {order.unit_count} units{order.customer_name ? `, ${order.customer_name}` : ""}{order.zip ? `, ZIP ${order.zip}` : ""}{order.ignored ? `, ${order.ignored} rows skipped` : ""}{order.warnings.length ? ` — ${order.warnings.join(" ")}` : ""}</p>)}
          </div>))}</div>}
      </fieldset>}
    </Drawer>
  );
}

function PackingSettingsDrawer({ company, onClose, notify }) {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setSettings(null); setError("");
    if (company) getJson(`/api/companies/${company.id}/settings`).then(setSettings).catch((requestError) => setError(requestError.message));
  }, [company]);
  if (!company) return null;
  const set = (field, value) => setSettings((current) => ({ ...current, [field]: value }));
  const setPallet = (which, field, value) => setSettings((current) => ({ ...current, [which]: { ...current[which], [field]: value } }));
  const save = async () => {
    setBusy(true);
    try { setSettings(await sendJson(`/api/companies/${company.id}/settings`, "PUT", settings)); notify(`${company.code} pallet limits saved.`); onClose(); }
    catch (requestError) { notify(requestError.message, "danger"); }
    finally { setBusy(false); }
  };
  const num = (value) => (value === "" ? "" : Number(value));
  return (
    <Drawer open wide onClose={onClose} labelId="limits-title" title={`${company.code} pallet limits & defaults`} subtitle="Starting values for new plans. Each plan can still adjust them; changes there are saved back here."
      footer={settings && <><span className="top-spacer" /><button type="button" className="btn btn-primary" disabled={busy} onClick={save}><Save size={15} />Save</button></>}>
      {error && <ErrorBlock message={error} />}
      {!settings && !error && <Loading />}
      {settings && <>
        {[["standard_pallet", "Standard pallet"], ["long_pallet", "Long pallet"]].map(([which, label]) => (
          <fieldset key={which} className="form-section"><legend>{label}</legend><div className="form-grid form-grid-3">
            <Field label="Name"><input value={settings[which].display_name} onChange={(event) => setPallet(which, "display_name", event.target.value)} /></Field>
            <Field label="Deck length (in)"><input type="number" value={settings[which].length} onChange={(event) => setPallet(which, "length", num(event.target.value))} /></Field>
            <Field label="Deck width (in)"><input type="number" value={settings[which].width} onChange={(event) => setPallet(which, "width", num(event.target.value))} /></Field>
            <Field label="Deck height (in)"><input type="number" value={settings[which].deck_height} onChange={(event) => setPallet(which, "deck_height", num(event.target.value))} /></Field>
            <Field label="Tare weight (lb)"><input type="number" value={settings[which].tare_weight} onChange={(event) => setPallet(which, "tare_weight", num(event.target.value))} /></Field>
            <Field label="Max total weight (lb)"><input type="number" value={settings[which].max_total_weight} onChange={(event) => setPallet(which, "max_total_weight", num(event.target.value))} /></Field>
          </div></fieldset>
        ))}
        <fieldset className="form-section"><legend>Heights, overhang and boards</legend><div className="form-grid form-grid-3">
          <Field label="Flatpack max freight height (in)"><input type="number" value={settings.flatpack_max_total_height} onChange={(event) => set("flatpack_max_total_height", num(event.target.value))} /></Field>
          <Field label="Assembled max freight height (in)"><input type="number" value={settings.assembled_max_total_height} onChange={(event) => set("assembled_max_total_height", num(event.target.value))} /></Field>
          <Field label="Overhang per side (in)"><input type="number" step="0.5" value={settings.general_overhang_per_side} onChange={(event) => set("general_overhang_per_side", num(event.target.value))} /></Field>
          <Field label="Load board weight (lb)"><input type="number" value={settings.load_board_weight} onChange={(event) => set("load_board_weight", num(event.target.value))} /></Field>
          <Field label="Dedicated truck: 48×40 max (lb)"><input type="number" value={settings.truckload_standard_max_weight} onChange={(event) => set("truckload_standard_max_weight", num(event.target.value))} /></Field>
          <Field label="Dedicated truck: 96×40 max (lb)"><input type="number" value={settings.truckload_long_max_weight} onChange={(event) => set("truckload_long_max_weight", num(event.target.value))} /></Field>
        </div></fieldset>
        <fieldset className="form-section"><legend>Matching and quoting defaults</legend><div className="form-grid">
          <Field label="SKU prefixes ignored while matching" hint="Comma separated, e.g. MB, WHS"><input value={listText(settings.sku_prefixes_to_ignore)} onChange={(event) => set("sku_prefixes_to_ignore", textList(event.target.value))} /></Field>
          <Field label="Fuzzy match threshold (%)"><input type="number" value={settings.fuzzy_match_threshold} onChange={(event) => set("fuzzy_match_threshold", num(event.target.value))} /></Field>
          <Field label="Origin ZIP"><input value={settings.quote_origin.postal_code} onChange={(event) => set("quote_origin", { ...settings.quote_origin, postal_code: event.target.value })} /></Field>
          <Field label="Commodity description"><input value={settings.default_commodity_description} onChange={(event) => set("default_commodity_description", event.target.value)} /></Field>
          <Field label="Default NMFC"><input value={settings.default_nmfc} onChange={(event) => set("default_nmfc", event.target.value)} /></Field>
        </div>
        {company.config.rule_set === "highland" && <p className="hint">The Highland rule set always keeps its required color prefixes (WHS, ESS, GRS, MSL, MTS, ONB, SBS, SDW, SSG).</p>}
        </fieldset>
      </>}
    </Drawer>
  );
}
