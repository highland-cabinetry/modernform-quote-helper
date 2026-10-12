import { AlertOctagon, ClipboardCheck, NotebookPen } from "lucide-react";

export default function PlanIssues({ result, onInspect }) {
  const unpackedIds = new Set(result.unpacked.map(item => item.unit_id));
  const errors = result.warnings.filter(w => w.severity === "error" && !(w.code === "CABINET_PACKING_BLOCKED" && w.unit_ids?.every(id => unpackedIds.has(id))));
  const actions = result.warnings.filter(w => w.severity !== "error" && w.code === "OFF_CENTER_LOAD");
  const notes = result.warnings.filter(w => w.severity !== "error" && w.code !== "OFF_CENTER_LOAD");
  const palletActions = result.pallets.flatMap(p => (p.required_actions || []).map((text, index) => ({ pallet: p.pallet_number, text, index })));
  const blockerCount = errors.length + result.unpacked.length;
  const warning = (entry, index) => <li key={`${entry.code}-${index}`}>
    <div><b>{entry.code.replaceAll("_", " ").toLowerCase()}</b><p>{entry.message}</p></div>
    {(entry.pallet_number || entry.unit_ids?.some(id => result.pallets.some(p => p.items.some(i => i.unit_id === id)))) &&
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onInspect(entry)}>Inspect affected freight</button>}
  </li>;
  return <section className="card issues-card">
    <header className="card-head"><div className="card-head-text"><h3 className="card-title">Review exceptions</h3><p className="card-description">Blockers stop quoting. Warehouse actions must be done when the load is built.</p></div></header>
    <div className={`issue-group ${blockerCount ? "is-danger" : "is-clear"}`}>
      <h4><AlertOctagon size={16} aria-hidden="true" />Blockers ({blockerCount})</h4>
      {!blockerCount ? <p className="muted">No blocking validation errors.</p> : <ul className="issue-list">{errors.map(warning)}
        {result.unpacked.map(item => <li key={item.unit_id}><div><b>{item.original_sku}</b><p>{item.reason}</p><p className="muted">{item.reason_code === "OVERSIZE_FOOTPRINT" ? "Physical restriction: review dimensions and approved pallet handling." : item.reason_code === "NO_SAFE_POSITION" ? "Search incomplete: revise the arrangement or request refinement." : "Input correction required: review this item and its packing state."}</p></div></li>)}</ul>}
    </div>
    <div className={`issue-group ${actions.length || palletActions.length ? "is-warn" : ""}`}>
      <h4><ClipboardCheck size={16} aria-hidden="true" />Required warehouse actions</h4>
      {actions.length > 0 || palletActions.length > 0 ? <ul className="issue-list">{actions.map(warning)}{palletActions.map(({ pallet, text, index }) =>
        <li key={`${pallet}-${index}`}><div><p>{text}</p></div><button type="button" className="btn btn-ghost btn-sm" onClick={() => onInspect({ pallet_number: pallet })}>Inspect pallet {pallet}</button></li>)}</ul>
        : <p className="muted">No additional handling actions recorded. Review the physical load before release.</p>}
    </div>
    <details className="issue-group disclosure">
      <summary><NotebookPen size={16} aria-hidden="true" />Review notes ({notes.length})</summary>
      {notes.length ? <ul className="issue-list">{notes.map(warning)}</ul> : <p className="muted">No review notes.</p>}
    </details>
  </section>;
}
