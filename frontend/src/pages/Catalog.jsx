import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Database, Download, FileSpreadsheet, History, Link2, LoaderCircle, Plus, Save, ShieldAlert, Trash2, Upload, Wand2,
} from "lucide-react";
import { getJson, sendJson, uploadFiles } from "../api";
import { Badge, DataTable, Drawer, EmptyState, ErrorBlock, Field, FilterChips, Loading, Modal, PageHeader, SearchInput, Timeline } from "../components/Common";
import { dims, plural, weight } from "../lib/format";
import useResource from "../lib/useResource";
import { useApp } from "../shell/AppShell";
import { CATALOG_LABEL, SIMPLE_CATALOG } from "../edition";

const ROTATIONS = ["LWH", "WLH", "LHW", "HLW", "WHL", "HWL"];
const KINDS = { cabinet: "Cabinet", cabinet_component: "Cabinet component (door / face frame)", accessory: "Accessory", other: "Other" };
const ROLES = { standard: "Standard", base_panel: "Base panel", top_accessory: "Top accessory" };

const blankItem = () => ({
  id: null, sku: "", manufacturer_sku: "", description: "", manufacturer_dimensions: "",
  flatpack_length: "", flatpack_width: "", flatpack_height: "", assembled_length: "", assembled_width: "", assembled_height: "",
  weight: "", category: "Miscellaneous", item_kind: "other", stackable: true, fragile: false, must_stay_upright: false,
  no_upside_down: true, allowed_rotations: ["LWH", "WLH"], max_stack_layers: "", max_weight_on_top: 1000, load_bearing_strength: 1000,
  preferred_pallet_type: "either", freight_class: "", nmfc: "", color: "", rules_reviewed: false, packing_role: "standard",
  bundle_max_units: 1, bundle_approved: false, allow_edge_standing: false, overhang_approved: false, max_overhang_per_side: 0, active: true,
});

const HANDLING_FIELDS = ["stackable", "fragile", "must_stay_upright", "no_upside_down", "bundle_approved", "allow_edge_standing",
  "overhang_approved", "allowed_rotations", "packing_role", "preferred_pallet_type", "max_stack_layers", "max_weight_on_top",
  "load_bearing_strength", "bundle_max_units", "max_overhang_per_side"];

const NUMERIC = ["flatpack_length", "flatpack_width", "flatpack_height", "assembled_length", "assembled_width", "assembled_height", "weight",
  "max_stack_layers", "max_weight_on_top", "load_bearing_strength", "bundle_max_units", "max_overhang_per_side"];

function toPayload(draft) {
  const item = { ...draft };
  delete item.issues;
  for (const key of NUMERIC) {
    const value = item[key];
    item[key] = value === "" || value === null || value === undefined ? null : Number(value);
  }
  if (item.max_weight_on_top === null) item.max_weight_on_top = 1000;
  if (item.load_bearing_strength === null) item.load_bearing_strength = 1000;
  if (item.bundle_max_units === null) item.bundle_max_units = 1;
  if (item.max_overhang_per_side === null) item.max_overhang_per_side = 0;
  for (const key of ["manufacturer_sku", "manufacturer_dimensions", "freight_class", "nmfc", "color"]) item[key] = item[key] ? String(item[key]).trim() : null;
  return item;
}

function validateItem(item) {
  const problems = {};
  if (!String(item.sku || "").trim()) problems.sku = "SKU is required";
  if (!(Number(item.weight) > 0)) problems.weight = "Weight per unit must be above 0";
  for (const state of ["flatpack", "assembled"]) {
    const values = ["length", "width", "height"].map((axis) => item[`${state}_${axis}`]);
    const filled = values.filter((value) => value !== "" && value !== null && value !== undefined);
    if (filled.length && filled.length < 3) problems[state] = "Enter all three dimensions or leave all blank";
    if (filled.some((value) => !(Number(value) > 0))) problems[state] = "Dimensions must be above 0";
  }
  const hasSet = ["flatpack", "assembled"].some((state) => ["length", "width", "height"].every((axis) => Number(item[`${state}_${axis}`]) > 0));
  if (!hasSet) problems.dimensions = "At least one complete carton size (flatpack or assembled) is required";
  if (!item.allowed_rotations?.length) problems.rotations = "Allow at least one orientation";
  return problems;
}

export default function Catalog() {
  const { company, notify } = useApp();
  const [catalog, setCatalog] = useState(company.config.catalog);
  const { data: catalogs } = useResource("/api/catalogs");
  const [tab, setTab] = useState("items");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [editing, setEditing] = useState(null);
  const items = useResource(`/api/catalog/${catalog}/items?include_inactive=true`);
  const report = useResource(tab === "quality" ? `/api/catalog/${catalog}/report` : null);
  const aliases = useResource(tab === "aliases" ? `/api/catalog/${catalog}/aliases` : null);
  useEffect(() => { setCatalog(company.config.catalog); }, [company.config.catalog]);

  const all = items.data || [];
  const active = all.filter((item) => item.active);
  const hasIssue = (item, codes) => item.issues.some((issue) => codes.includes(issue.code));
  const counts = {
    all: active.length,
    missing: active.filter((item) => hasIssue(item, ["NO_FLATPACK_DIMENSIONS", "NO_ASSEMBLED_DIMENSIONS"])).length,
    warnings: active.filter((item) => item.issues.some((issue) => issue.severity !== "info" && !["NO_FLATPACK_DIMENSIONS", "NO_ASSEMBLED_DIMENSIONS"].includes(issue.code))).length,
    unreviewed: active.filter((item) => !item.rules_reviewed).length,
    inactive: all.length - active.length,
  };
  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return all.filter((item) => {
      if (filter === "inactive" ? item.active : !item.active) return false;
      if (filter === "missing" && !hasIssue(item, ["NO_FLATPACK_DIMENSIONS", "NO_ASSEMBLED_DIMENSIONS"])) return false;
      if (filter === "warnings" && !item.issues.some((issue) => issue.severity !== "info" && !["NO_FLATPACK_DIMENSIONS", "NO_ASSEMBLED_DIMENSIONS"].includes(issue.code))) return false;
      if (filter === "unreviewed" && item.rules_reviewed) return false;
      return !term || [item.sku, item.manufacturer_sku, item.description, item.category].some((value) => String(value || "").toLowerCase().includes(term));
    });
  }, [all, filter, search]);

  const number = (value) => (value === null || value === undefined || value === "" ? <span className="muted">—</span> : Number(value).toFixed(2).replace(/\.00$/, ""));
  const checks = { key: "flags", label: "Checks", sort: (item) => item.issues.filter((issue) => issue.severity !== "info").length, render: (item) => {
    const serious = item.issues.filter((issue) => issue.severity !== "info");
    return serious.length ? <Badge tone="warn" title={serious.map((issue) => issue.message).join("\n")}>{plural(serious.length, "issue")}</Badge> : <Badge tone="ok">OK</Badge>;
  } };
  // The same columns as the catalog workbook (Export Excel / Import & export).
  const simpleColumns = [
    { key: "sku", label: "In House Item SKU", render: (item) => <b>{item.sku}</b> },
    { key: "manufacturer_sku", label: "Manufacturer Item SKU", render: (item) => item.manufacturer_sku || <span className="muted">—</span> },
    { key: "manufacturer_dimensions", label: "Manufacturer Dimensions", render: (item) => item.manufacturer_dimensions || <span className="muted">—</span> },
    { key: "weight", label: "Unit Weight (lb)", align: "right", render: (item) => number(item.weight) },
    ...[["flatpack_length", "Flatpack L"], ["flatpack_width", "Flatpack W"], ["flatpack_height", "Flatpack H"],
      ["assembled_length", "Assembled L"], ["assembled_width", "Assembled W"], ["assembled_height", "Assembled H"]]
      .map(([key, label]) => ({ key, label, align: "right", render: (item) => number(item[key]) })),
    { key: "category", label: "Category" },
    { key: "description", label: "Description", className: "desc-col" },
    checks,
  ];
  const fullColumns = [
    { key: "sku", label: "SKU", render: (item) => <span className="cell-stack"><b>{item.sku}</b>{item.manufacturer_sku && item.manufacturer_sku !== item.sku && <small>{item.manufacturer_sku}</small>}</span> },
    { key: "description", label: "Description", className: "desc-col" },
    { key: "category", label: "Category" },
    { key: "flatpack", label: "Flatpack carton (in)", sort: (item) => item.flatpack_length, render: (item) => item.flatpack_length ? dims(item.flatpack_length, item.flatpack_width, item.flatpack_height) : <span className="warn-text">missing</span> },
    { key: "assembled", label: "Assembled carton (in)", sort: (item) => item.assembled_length, render: (item) => item.assembled_length ? dims(item.assembled_length, item.assembled_width, item.assembled_height) : <span className="warn-text">missing</span> },
    { key: "weight", label: "Weight", align: "right", render: (item) => weight(item.weight) },
    checks,
  ];
  const columns = SIMPLE_CATALOG ? simpleColumns : fullColumns;
  const catalogLabel = catalog === "default" ? CATALOG_LABEL : catalog;

  return (
    <div className="page">
      <PageHeader title="Product Catalog" lede="Carton sizes, weights and handling rules the pallet planner uses. Changes apply to new plans; saved quotes keep the numbers they were built with."
        actions={<>
          {catalogs && catalogs.length > 1 && <label className="inline-select"><span>Catalog</span>
            <select value={catalog} onChange={(event) => setCatalog(event.target.value)}>
              {catalogs.map((entry) => <option key={entry.name} value={entry.name}>{entry.name === "default" ? CATALOG_LABEL : entry.name} ({entry.item_count})</option>)}
            </select></label>}
          <a className="btn btn-secondary" href={`/api/catalog/${catalog}/export/xlsx`}><Download size={16} />Export Excel</a>
          <button type="button" className="btn btn-primary" onClick={() => setEditing(blankItem())}><Plus size={16} />Add product</button>
        </>} />

      <div className="tabs" role="tablist" aria-label="Catalog sections">
        {[["items", "Products", counts.all], ...(SIMPLE_CATALOG ? [["colors", "Cabinet colors"]] : []), ["quality", "Data quality", counts.missing + counts.warnings || undefined], ["import", "Import & export"], ["aliases", "Alternate SKUs"]].map(([key, label, count]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label}{count !== undefined && <span className={`tab-count ${key === "quality" && count ? "is-warn" : ""}`}>{count}</span>}
          </button>
        ))}
      </div>

      {tab === "items" && <section className="card card-flush">
        <div className="list-toolbar padded">
          <SearchInput value={search} onChange={setSearch} placeholder="Search SKU, manufacturer SKU, description" />
          <FilterChips label="Product filter" value={filter} onChange={setFilter} options={[
            ["all", "Active", counts.all], ["missing", "Missing sizes", counts.missing], ["warnings", "Other issues", counts.warnings],
            ["unreviewed", "Rules not reviewed", counts.unreviewed], ["inactive", "Inactive", counts.inactive],
          ]} />
        </div>
        {items.loading && !items.data && <Loading />}
        {items.error && <ErrorBlock message={items.error} onRetry={items.reload} />}
        {items.data && <DataTable columns={columns} rows={rows} onRowClick={(item) => setEditing(item)} initialSort={{ key: "sku", direction: "asc" }} caption="Products"
          empty={<EmptyState icon={Database} title="No products match">Change the search or filter, or add a product.</EmptyState>} />}
        <p className="hint padded">{SIMPLE_CATALOG
          ? "Enter each product once, without a color prefix. Order lines such as NS-3DB30 match 3DB30 through the cabinet colors; codes printed on documents are never changed."
          : "Matching ignores the company's configured SKU prefixes (for example WHS- or MB-) during lookup only; SKUs printed on documents are never changed."}</p>
      </section>}

      {tab === "colors" && <CabinetColors company={company} notify={notify} />}
      {tab === "quality" && <QualityReport report={report} onOpen={(row) => setEditing(all.find((item) => item.id === row.id) || null)} />}
      {tab === "import" && <ImportExport catalog={catalog} catalogLabel={catalogLabel} onApplied={() => items.reload({ quiet: true })} notify={notify} />}
      {tab === "aliases" && <AliasList catalog={catalog} aliases={aliases} notify={notify} />}

      <ItemDrawer item={editing} catalog={catalog} onClose={() => setEditing(null)} notify={notify}
        onSaved={async (saved) => { await items.reload({ quiet: true }); setEditing(saved); }} />
    </div>
  );
}

function QualityReport({ report, onOpen }) {
  if (report.loading && !report.data) return <Loading />;
  if (report.error) return <ErrorBlock message={report.error} onRetry={report.reload} />;
  if (!report.data) return null;
  const rows = report.data.rows.filter((row) => row.issues.some((issue) => issue.severity !== "info"));
  const labels = {
    NO_FLATPACK_DIMENSIONS: "No flatpack size", NO_ASSEMBLED_DIMENSIONS: "No assembled size", AMBIGUOUS_SKU: "Ambiguous SKU",
    OVERSIZE: "Oversize dimension", TINY_DIMENSION: "Tiny dimension", HEAVY: "Unusually heavy", RULES_NOT_REVIEWED: "Rules not reviewed",
    FLATPACK_TALLER: "Flatpack taller than assembled", NO_DESCRIPTION: "No description",
  };
  return (
    <div className="stack">
      <section className="kpi-row">
        {Object.entries(report.data.counts).sort((a, b) => b[1] - a[1]).map(([code, count]) => (
          <div key={code} className={`kpi ${["AMBIGUOUS_SKU", "OVERSIZE", "HEAVY"].includes(code) ? "kpi-warn" : ""}`}><span className="kpi-label">{labels[code] || code}</span><strong className="kpi-value">{count}</strong></div>
        ))}
      </section>
      <section className="card card-flush">
        <header className="card-head padded"><div className="card-head-text"><h2 className="card-title">Products that need attention</h2>
          <p className="card-description">A missing carton size only matters for the packing state that uses it; an assembled line for a product with no assembled size cannot be planned and is reported, never guessed.</p></div></header>
        <DataTable rows={rows} onRowClick={onOpen} dense
          empty={<EmptyState icon={CheckCircle2} title="No data issues">Every active product has complete, plausible data.</EmptyState>}
          columns={[
            { key: "sku", label: "SKU", render: (row) => <b>{row.sku}</b> },
            { key: "description", label: "Description" },
            { key: "issues", label: "Issues", sortable: false, render: (row) => <ul className="bare-list">{row.issues.filter((issue) => issue.severity !== "info").map((issue) => <li key={issue.code}><span className={issue.severity === "error" ? "danger-text" : "warn-text"}>{issue.message}</span></li>)}</ul> },
          ]} />
      </section>
    </div>
  );
}

function ImportExport({ catalog, catalogLabel, onApplied, notify }) {
  const input = useRef(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState("changes");
  const upload = async (file) => {
    if (!file) return;
    setBusy(true);
    try { setPreview(await uploadFiles(`/api/catalog/${catalog}/import/preview`, [file], "file")); setShow("changes"); }
    catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    setBusy(true);
    try {
      const result = await sendJson(`/api/catalog/${catalog}/import/apply`, "POST", { preview_id: preview.preview_id });
      notify(`${result.inserted} added and ${result.updated} updated from ${result.filename}.`);
      setPreview(null); onApplied();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const statusTone = { new: "ok", changed: "info", unchanged: "neutral", invalid: "danger", conflict: "danger" };
  const visible = preview ? preview.rows.filter((row) => show === "all" || (show === "changes" ? ["new", "changed"].includes(row.status) : ["invalid", "conflict"].includes(row.status))) : [];
  return (
    <div className="stack">
      <section className="card">
        <h2 className="card-title">Update from Excel or CSV</h2>
        <p className="card-description">{SIMPLE_CATALOG
          ? "Export the catalog workbook (sheets Cabinet Colors and Item References: In House Item SKU, Manufacturer Item SKU, Manufacturer Dimensions, Unit Weight (lb), Flatpack L/W/H, Assembled L/W/H, Category, Description), edit or add rows, then upload it here. Only those columns change; each product's handling rules stay. Use 0 or blank for a carton size a product doesn't have."
          : "Upload the exported workbook after editing it, or any sheet with SKU, carton size and weight columns."} You'll see every new, changed, invalid and conflicting row before anything is saved; applying writes all accepted rows together or none at all.</p>
        <input ref={input} hidden type="file" accept=".xlsx,.xlsm,.csv,.tsv" onChange={(event) => { upload(event.target.files[0]); event.target.value = ""; }} />
        <div className="card-actions-row">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}Choose file to preview</button>
          <a className="btn btn-secondary" href={`/api/catalog/${catalog}/export/xlsx`}><FileSpreadsheet size={16} />Export current catalog (.xlsx)</a>
          <a className="btn btn-ghost" href={`/api/catalog/${catalog}/export/csv`}><Download size={16} />CSV</a>
        </div>
      </section>
      {preview && <section className="card card-flush">
        <header className="card-head padded">
          <div className="card-head-text"><h2 className="card-title">Preview: {preview.filename}</h2>
            <p className="card-description">{preview.counts.new} new · {preview.counts.changed} changed · {preview.counts.unchanged} unchanged · {preview.counts.invalid} invalid · {preview.counts.conflict} conflicts
              {preview.colors?.length > 0 && ` · cabinet colors replaced with ${preview.colors.map((color) => `${color.name || "?"} (${color.prefix})`).join(", ")}`}
              {preview.not_in_file.length > 0 && ` · ${preview.not_in_file.length} existing products are not in the file (they are left as they are)`}</p></div>
          <div className="card-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setPreview(null)}>Discard</button>
            <button type="button" className="btn btn-primary" disabled={busy || !preview.can_apply} onClick={apply}><Save size={15} />Apply {preview.counts.new + preview.counts.changed} change{preview.counts.new + preview.counts.changed === 1 ? "" : "s"}</button>
          </div>
        </header>
        {(preview.counts.invalid > 0 || preview.counts.conflict > 0) && <div className="callout callout-warn compact-callout padded-x"><AlertTriangle size={16} /><span>Invalid and conflicting rows are skipped. Fix them in the file and preview again to include them.</span></div>}
        <div className="list-toolbar padded"><FilterChips label="Preview rows" value={show} onChange={setShow} options={[["changes", "New & changed"], ["problems", "Invalid & conflicts"], ["all", "All rows"]]} /></div>
        <DataTable dense maxHeight="60vh" rows={visible.map((row, index) => ({ ...row, id: `${row.sku}-${index}` }))}
          empty={<EmptyState title="Nothing in this view" />}
          columns={[
            { key: "sku", label: "SKU", render: (row) => <b>{row.sku || "—"}</b> },
            { key: "status", label: "Result", render: (row) => <Badge tone={statusTone[row.status]}>{row.status}</Badge> },
            { key: "detail", label: "Detail", sortable: false, render: (row) => row.message || (row.changes ? Object.entries(row.changes).map(([field, [before, after]]) => `${field}: ${before ?? "—"} → ${after ?? "—"}`).join("; ") : row.description) },
            { key: "issues", label: "Warnings", sortable: false, render: (row) => (row.issues || []).map((issue) => issue.message).join(" ") },
          ]} />
      </section>}
      <ReplaceAll catalog={catalog} catalogLabel={catalogLabel} notify={notify} onReplaced={onApplied} />
    </div>
  );
}

function ReplaceAll({ catalog, catalogLabel, notify, onReplaced }) {
  const input = useRef(null);
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  if (catalog !== "default") return null;
  const preview = async (file) => {
    if (!file) return;
    setBusy(true);
    try { setState({ file, preview: await uploadFiles("/api/item-master/replace/preview", [file], "file"), confirmation: "" }); }
    catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const replace = async () => {
    setBusy(true);
    try {
      const result = await uploadFiles("/api/item-master/replace", [state.file], "file", { confirmation: state.confirmation });
      notify(`Catalog replaced with ${result.inserted} products. Backup: ${result.backup_name}.`);
      setState(null); onReplaced();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  return (
    <section className="card danger-zone">
      <h2 className="card-title">Replace the whole {catalogLabel}</h2>
      <p className="card-description">{SIMPLE_CATALOG
        ? "For a complete switch to your own product list: every product is replaced by the workbook's rows (and its Cabinet Colors sheet). Saved pallet plans are reset; settings, orders and quotes are kept. A verified backup is made first."
        : "For a complete cutover to a new workbook. Saved pallet plans and packing settings are reset; orders, quotes, shipments and freight costs are kept. A verified backup is made first."}</p>
      <input ref={input} hidden type="file" accept=".xlsx,.xlsm,.csv,.tsv" onChange={(event) => { preview(event.target.files[0]); event.target.value = ""; }} />
      <button type="button" className="btn btn-danger" disabled={busy} onClick={() => input.current?.click()}><ShieldAlert size={16} />Replace all data…</button>
      <Modal open={Boolean(state)} tone="danger" title="Replace the item master?" description={state?.file?.name} onClose={() => setState(null)}
        footer={<>
          <button type="button" className="btn btn-secondary" onClick={() => setState(null)}>Cancel</button>
          <button type="button" className="btn btn-danger-solid" disabled={busy || state?.confirmation !== "REPLACE" || !state?.preview?.can_replace} onClick={replace}>Replace data</button>
        </>}>
        {state && <>
          <dl className="impact-list">
            <div><dt>Products in the file</dt><dd>{state.preview.incoming_rows}</dd></div>
            <div><dt>Current products removed</dt><dd>{state.preview.current_items_removed}</dd></div>
            <div><dt>Saved pallet plans reset</dt><dd>{state.preview.shipments_deleted}</dd></div>
            <div><dt>Learned SKU mappings kept / removed</dt><dd>{state.preview.aliases_preserved} / {state.preview.aliases_removed}</dd></div>
          </dl>
          {!state.preview.can_replace && <div className="callout callout-danger compact-callout"><AlertTriangle size={16} /><span>{state.preview.skipped_count} rows are invalid. Fix them before replacing.</span></div>}
          <Field label='Type REPLACE to confirm'><input value={state.confirmation} onChange={(event) => setState({ ...state, confirmation: event.target.value })} /></Field>
        </>}
      </Modal>
    </section>
  );
}

function AliasList({ catalog, aliases, notify }) {
  if (aliases.loading && !aliases.data) return <Loading />;
  if (aliases.error) return <ErrorBlock message={aliases.error} onRetry={aliases.reload} />;
  const remove = async (alias) => {
    if (!window.confirm(`Stop matching ${alias.alias_sku} to ${alias.target_sku}?`)) return;
    try { await sendJson(`/api/catalog/${catalog}/aliases/${alias.id}`, "DELETE"); notify("Alternate SKU removed."); aliases.reload({ quiet: true }); }
    catch (error) { notify(error.message, "danger"); }
  };
  return (
    <section className="card card-flush">
      <header className="card-head padded"><div className="card-head-text"><h2 className="card-title">Alternate SKUs</h2>
        <p className="card-description">Other names that resolve to a catalog product. They are created when you choose the matching product for an unknown SKU while building a quote, or from a product's page.</p></div></header>
      <DataTable rows={aliases.data || []} dense
        empty={<EmptyState icon={Link2} title="No alternate SKUs yet">When a document uses a SKU the catalog doesn't know, choose its product once and it will match automatically afterwards.</EmptyState>}
        columns={[
          { key: "alias_sku", label: "Alternate SKU", render: (alias) => <b>{alias.alias_sku}</b> },
          { key: "target_sku", label: "Catalog product" },
          { key: "target_description", label: "Description" },
          { key: "updated_at", label: "Saved", render: (alias) => new Date(alias.updated_at).toLocaleDateString() },
          { key: "remove", label: <span className="sr-only">Remove</span>, sortable: false, render: (alias) => <button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove ${alias.alias_sku}`} onClick={() => remove(alias)}><Trash2 size={15} /></button> },
        ]} />
    </section>
  );
}

function suggestedAccessoryProfile(item) {
  const text = `${item.sku} ${item.description}`.toLowerCase();
  const longest = Math.max(Number(item.flatpack_length) || 0, Number(item.flatpack_width) || 0, Number(item.flatpack_height) || 0);
  if (/\b(?:pnl|panel)\b/.test(text) && longest >= 72) return { item_kind: "accessory", packing_role: "base_panel", bundle_max_units: 1, bundle_approved: false, max_overhang_per_side: 1 };
  return { item_kind: "accessory", packing_role: "top_accessory", bundle_max_units: 5, bundle_approved: true, max_overhang_per_side: longest > 48 ? 1 : 0 };
}

function ItemDrawer({ item, catalog, onClose, onSaved, notify }) {
  const [draft, setDraft] = useState(null);
  const [tab, setTab] = useState("data");
  const [history, setHistory] = useState(null);
  const [alias, setAlias] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!item) { setDraft(null); return; }
    const value = { ...blankItem(), ...item };
    for (const key of NUMERIC) if (value[key] === null || value[key] === undefined) value[key] = "";
    setDraft(value); setTab("data"); setHistory(null); setAlias("");
  }, [item]);
  useEffect(() => {
    if (tab === "history" && item?.id) getJson(`/api/catalog/${catalog}/items/${item.id}/history`).then(setHistory).catch(() => setHistory([]));
  }, [tab, item, catalog]);
  if (!item || !draft) return null;
  const problems = validateItem(draft);
  const set = (field, value) => setDraft((current) => ({ ...current, [field]: value,
    ...(HANDLING_FIELDS.includes(field) && current.rules_reviewed ? { rules_reviewed: false } : {}) }));
  const numberField = (field, label, unit = "in", hint) => (
    <Field label={`${label}${unit ? ` (${unit})` : ""}`} hint={hint}><input type="number" min="0" step="any" value={draft[field]} onChange={(event) => set(field, event.target.value)} /></Field>
  );
  const save = async (extra = {}) => {
    if (Object.keys(problems).length) { notify(Object.values(problems)[0], "danger"); return; }
    setBusy(true);
    try {
      const saved = await sendJson(`/api/catalog/${catalog}/items`, "POST", { item: toPayload({ ...draft, ...extra }), reason: item.id ? "edited in Product Catalog" : "added in Product Catalog" });
      notify(`${saved.sku} saved.`);
      await onSaved(saved);
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const deactivate = async () => {
    if (!window.confirm(`Remove ${item.sku} from the active catalog? Saved quotes keep their data, and you can reactivate it later.`)) return;
    setBusy(true);
    try { await sendJson(`/api/catalog/${catalog}/items/${item.id}`, "DELETE"); notify(`${item.sku} deactivated.`); await onSaved(null); onClose(); }
    catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };
  const saveAlias = async () => {
    try { await sendJson(`/api/catalog/${catalog}/aliases`, "POST", { alias_sku: alias, target_item_id: item.id }); notify(`${alias} now matches ${item.sku}.`); setAlias(""); }
    catch (error) { notify(error.message, "danger"); }
  };
  return (
    <Drawer open wide onClose={onClose} labelId="item-title"
      title={item.id ? <>{item.sku} {!item.active && <Badge tone="neutral">Inactive</Badge>}</> : "Add product"}
      subtitle={item.id ? item.description : "Carton sizes are the shipping cartons, not the cabinet itself."}
      footer={tab === "data" && <>
        {item.id && item.active && <button type="button" className="btn btn-ghost btn-ghost-danger" disabled={busy} onClick={deactivate}><Trash2 size={15} />Deactivate</button>}
        <span className="top-spacer" />
        {!draft.rules_reviewed && item.id && <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => save({ rules_reviewed: true })}><CheckCircle2 size={15} />Save &amp; mark rules reviewed</button>}
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => save(item.active === false ? { active: true } : {})}><Save size={15} />{item.active === false ? "Save & reactivate" : "Save product"}</button>
      </>}>
      {item.id && <div className="tabs" role="tablist" aria-label="Product sections">
        {[["data", "Product data"], ["history", "Change history"]].map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}
      </div>}
      {tab === "history" && (history ? <Timeline entries={history} /> : <Loading />)}
      {tab === "data" && <>
        {item.issues?.filter((issue) => issue.severity !== "info").length > 0 && <div className="callout callout-warn compact-callout"><AlertTriangle size={16} /><ul className="bare-list">{item.issues.filter((issue) => issue.severity !== "info").map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul></div>}
        {problems.dimensions && <p className="field-error">{problems.dimensions}</p>}
        <fieldset className="form-section"><legend>Identity</legend><div className="form-grid form-grid-3">
          <Field label="SKU" error={problems.sku} hint={item.id ? "SKUs can't be renamed; add an alternate SKU instead" : ""}><input value={draft.sku} disabled={Boolean(item.id)} onChange={(event) => set("sku", event.target.value)} /></Field>
          <Field label="Manufacturer SKU"><input value={draft.manufacturer_sku || ""} onChange={(event) => set("manufacturer_sku", event.target.value)} /></Field>
          <Field label="Category"><input value={draft.category} onChange={(event) => set("category", event.target.value)} /></Field>
          <Field label="Description" wide><input value={draft.description} onChange={(event) => set("description", event.target.value)} /></Field>
          <Field label="Cabinet size (reference only)" hint="Manufacturer's cabinet dimensions; never used for packing"><input value={draft.manufacturer_dimensions || ""} onChange={(event) => set("manufacturer_dimensions", event.target.value)} /></Field>
          <Field label="Item kind"><select value={draft.item_kind} onChange={(event) => set("item_kind", event.target.value)}>{Object.entries(KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        </div></fieldset>
        <fieldset className="form-section"><legend>Shipping cartons</legend>
          <div className="form-grid form-grid-3">
            {numberField("flatpack_length", "Flatpack L")}{numberField("flatpack_width", "Flatpack W")}{numberField("flatpack_height", "Flatpack H")}
          </div>
          {problems.flatpack && <p className="field-error">{problems.flatpack}</p>}
          <div className="form-grid form-grid-3">
            {numberField("assembled_length", "Assembled L")}{numberField("assembled_width", "Assembled W")}{numberField("assembled_height", "Assembled H")}
          </div>
          {problems.assembled && <p className="field-error">{problems.assembled}</p>}
          <div className="form-grid form-grid-3">
            <Field label="Weight per unit (lb)" error={problems.weight}><input type="number" min="0" step="any" value={draft.weight} onChange={(event) => set("weight", event.target.value)} /></Field>
            <Field label="Freight class"><input value={draft.freight_class || ""} onChange={(event) => set("freight_class", event.target.value)} /></Field>
            <Field label="NMFC"><input value={draft.nmfc || ""} onChange={(event) => set("nmfc", event.target.value)} /></Field>
          </div>
        </fieldset>
        <fieldset className="form-section"><legend>Handling rules {draft.rules_reviewed ? <Badge tone="ok">Reviewed</Badge> : <Badge tone="warn">Not reviewed</Badge>}</legend>
          <div className="check-grid">
            {[["stackable", "Other cartons may stack on it"], ["fragile", "Fragile (stays on top)"], ["must_stay_upright", "Must stay upright"], ["no_upside_down", "Never upside down"],
              ["bundle_approved", "May be bundled with the same SKU"], ["allow_edge_standing", "May stand on edge"], ["overhang_approved", "Approved for extra overhang"]].map(([field, label]) => (
              <label key={field} className="check-field"><input type="checkbox" checked={Boolean(draft[field])} onChange={(event) => set(field, event.target.checked)} /><span><b>{label}</b></span></label>
            ))}
          </div>
          <fieldset className="chip-group"><legend>Allowed orientations {problems.rotations && <span className="field-error">{problems.rotations}</span>}</legend>
            {ROTATIONS.map((rotation) => <label key={rotation} className="chip-check"><input type="checkbox" checked={draft.allowed_rotations.includes(rotation)}
              onChange={(event) => set("allowed_rotations", event.target.checked ? [...draft.allowed_rotations, rotation] : draft.allowed_rotations.filter((value) => value !== rotation))} /><span>{rotation}</span></label>)}
          </fieldset>
          <div className="form-grid form-grid-3">
            <Field label="Packing role"><select value={draft.packing_role} onChange={(event) => set("packing_role", event.target.value)}>{Object.entries(ROLES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
            <Field label="Preferred pallet"><select value={draft.preferred_pallet_type} onChange={(event) => set("preferred_pallet_type", event.target.value)}><option value="either">Either</option><option value="standard">48 × 40</option><option value="long">96 × 40</option></select></Field>
            {numberField("max_stack_layers", "Max stack layers", "", "Blank = no limit")}
            {numberField("max_weight_on_top", "Max weight on top", "lb")}
            {numberField("load_bearing_strength", "Load bearing", "lb")}
            {numberField("bundle_max_units", "Bundle max units", "")}
            {numberField("max_overhang_per_side", "Approved overhang per side")}
          </div>
          {draft.item_kind === "accessory" && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDraft((current) => ({ ...current, ...suggestedAccessoryProfile(current), rules_reviewed: false }))}><Wand2 size={14} />Suggest accessory handling</button>}
          <p className="hint">Changing handling rules marks them as not reviewed until the warehouse confirms them.</p>
        </fieldset>
        {item.id && <fieldset className="form-section"><legend>Alternate SKU</legend>
          <div className="inline-form"><input aria-label="Alternate SKU" value={alias} placeholder="e.g. a supplier's code for this product" onChange={(event) => setAlias(event.target.value)} />
            <button type="button" className="btn btn-secondary btn-sm" disabled={!alias.trim()} onClick={saveAlias}><Link2 size={14} />Add</button></div>
        </fieldset>}
      </>}
      {tab === "history" && !item.id && <p className="muted"><History size={14} /> No history yet.</p>}
    </Drawer>
  );
}


// Cabinet colors / door styles and the item prefix their SKUs carry (Natural Shaker → NS-). Order lines
// match the catalog without the prefix. Saved in the company's packing settings.
function CabinetColors({ company, notify }) {
  const path = `/api/companies/${company.id}/settings`;
  const [settings, setSettings] = useState(null);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => getJson(path).then((value) => { setSettings(value); setRows((value.cabinet_colors || []).map((row) => ({ ...row, id: crypto.randomUUID() }))); setError(""); })
    .catch((requestError) => setError(requestError.message));
  useEffect(() => { load(); }, [company.id]);
  const update = (id, field, value) => setRows((current) => current.map((row) => (row.id === id ? { ...row, [field]: value } : row)));
  const save = async () => {
    const colors = rows.filter((row) => row.prefix.trim()).map(({ name, prefix }) => ({ name: name.trim(), prefix: prefix.trim() }));
    setBusy(true);
    try {
      const saved = await sendJson(path, "PUT", { ...settings, cabinet_colors: colors });
      setSettings(saved); setRows(saved.cabinet_colors.map((row) => ({ ...row, id: crypto.randomUUID() })));
      notify(`${plural(colors.length, "cabinet color")} saved.`);
    } catch (requestError) { notify(requestError.message, "danger"); }
    finally { setBusy(false); }
  };
  if (error) return <ErrorBlock message={error} onRetry={load} />;
  if (!settings) return <Loading />;
  return (
    <section className="card">
      <h2 className="card-title">Cabinet colors</h2>
      <p className="card-description">Each cabinet color (door style) and the prefix on its item codes, as on the Cabinet Colors sheet of the catalog workbook. An order line such as NS-3DB30 is matched to catalog product 3DB30.</p>
      <table className="data-table grid-table is-dense">
        <thead><tr><th scope="col">Cabinet Color</th><th scope="col">Item Prefix</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id}>
              <td><input className="cell-input" aria-label={`Color ${index + 1} name`} value={row.name} onChange={(event) => update(row.id, "name", event.target.value)} /></td>
              <td><input className="cell-input" aria-label={`Color ${index + 1} prefix`} value={row.prefix} placeholder="NS-" onChange={(event) => update(row.id, "prefix", event.target.value.toUpperCase())} /></td>
              <td><button type="button" className="icon-btn" aria-label={`Remove color ${index + 1}`} onClick={() => setRows((current) => current.filter((entry) => entry.id !== row.id))}><Trash2 size={15} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="card-actions-row">
        <button type="button" className="btn btn-secondary" onClick={() => setRows((current) => [...current, { id: crypto.randomUUID(), name: "", prefix: "" }])}><Plus size={16} />Add color</button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>{busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Save colors</button>
      </div>
    </section>
  );
}
