import { AlertTriangle, ChevronRight, LoaderCircle, Search, ShieldCheck, X } from "lucide-react";
import { StatusPill } from "./ui";

export function AliasDialog({ resolution, options, busy, onClose, onSearch, onChoose }) {
  const { match } = resolution;
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="alias-title">
        <header className="modal-head">
          <div>
            <h2 id="alias-title">Choose the physical item</h2>
            <p>Which item-master product is <b>{match.line.original_sku}</b>?</p>
          </div>
          <button type="button" className="icon-btn" aria-label="Close match selection" disabled={busy} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="modal-body">
          <div className="source-sku">
            <span>From the packing slip</span>
            <b>{match.line.original_sku}</b>
            <small>{match.line.description || "No description supplied"}</small>
          </div>
          {match.matched_item && (
            <button type="button" className="suggested-match" disabled={busy} onClick={() => onChoose(match.matched_item)}>
              <StatusPill severity="warn">Suggested</StatusPill>
              <span><b>{match.matched_item.sku}</b><small>{match.matched_item.description}</small></span>
              <span className="confidence">{match.confidence}% match</span>
            </button>
          )}
          <label className="search-field search-field-lg">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search all item-master records</span>
            <input autoFocus value={resolution.search} placeholder="Search by SKU or description" onChange={(event) => onSearch(event.target.value)} />
          </label>
          <div className="option-list">
            {options.slice(0, 50).map((item) => (
              <button type="button" key={item.id} disabled={busy} onClick={() => onChoose(item)}>
                <span><b>{item.sku}</b><small>{item.manufacturer_sku || "No manufacturer SKU"}</small></span>
                <span className="option-desc">{item.description}</span>
                <ChevronRight size={15} aria-hidden="true" />
              </button>
            ))}
            {!options.length && <p className="option-empty">No item-master records match that search.</p>}
          </div>
        </div>
        <footer className="modal-foot">
          <p className="hint"><ShieldCheck size={15} />Your choice is reused on future imports. The original packing-slip SKU stays unchanged in results and reports.</p>
        </footer>
      </section>
    </div>
  );
}

export function ReplaceDialog({ replacement, busy, onClose, onConfirmText, onReplace }) {
  const { preview } = replacement;
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal modal-danger" role="dialog" aria-modal="true" aria-labelledby="replace-title">
        <header className="modal-head">
          <span className="modal-icon" aria-hidden="true"><AlertTriangle size={20} /></span>
          <div>
            <h2 id="replace-title">Replace all production data?</h2>
            <p>Workbook: <b>{preview.filename}</b></p>
          </div>
          <button type="button" className="icon-btn" aria-label="Close replacement dialog" disabled={busy} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="modal-body">
          <dl className="impact-grid">
            <div><dt>Incoming items</dt><dd>{preview.incoming_rows}</dd></div>
            <div><dt>Current items removed</dt><dd>{preview.current_items_removed}</dd></div>
            <div><dt>Shipments deleted</dt><dd>{preview.shipments_deleted}</dd></div>
            <div><dt>Settings reset</dt><dd>{preview.settings_reset}</dd></div>
          </dl>
          <p>Learned mappings: <b>{preview.aliases_preserved} kept</b> and <b>{preview.aliases_removed} removed</b> because their target SKU is not in the new workbook.</p>
          {preview.skipped_count > 0 ? (
            <div className="callout callout-danger"><AlertTriangle size={17} /><div><b>Replacement blocked</b><p>{preview.skipped_count} invalid or skipped row{preview.skipped_count === 1 ? "" : "s"} must be corrected in the workbook first.</p></div></div>
          ) : (
            <div className="callout callout-ok"><ShieldCheck size={17} /><span>All {preview.incoming_rows} rows passed import validation. A full database backup is created first.</span></div>
          )}
          <label className="field">
            <span>Type <b>REPLACE</b> to confirm</span>
            <input autoComplete="off" value={replacement.confirmation} onChange={(event) => onConfirmText(event.target.value)} />
          </label>
        </div>
        <footer className="modal-foot modal-actions">
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-danger-solid" disabled={busy || !preview.can_replace || replacement.confirmation !== "REPLACE"} onClick={onReplace}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <AlertTriangle size={16} />}Back up and replace everything
          </button>
        </footer>
      </section>
    </div>
  );
}
