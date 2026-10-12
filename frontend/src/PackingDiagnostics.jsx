const titles = {
  input_blocked: "Resolve shipment inputs",
  physically_blocked: "Review carton constraints",
  timed_out: "Search time limit reached",
  software_error: "Packing could not finish",
};

export default function PackingDiagnostics({ job, onInspect }) {
  const blockers = job.blockers || [];
  const title = titles[job.outcome_category];
  if (!title && !blockers.length) return null;
  return <section className="diagnostics" aria-label="Packing diagnostics">
    {title && <h4>{title}</h4>}
    {job.outcome_category === "timed_out" && <p>No complete result was found in this search. Your reviewed plan is unchanged; you can try a longer refinement.</p>}
    {job.outcome_category === "software_error" && <p>The search failed. Your inputs are kept. Retry the search; if it fails again, keep the diagnostics for review.</p>}
    {job.termination_reason === "preflight" && <p>Fix these items before searching again — more search time can’t resolve input or size restrictions.</p>}
    {blockers.length > 0 && <ul className="blocker-list">{blockers.map((block, index) => <li key={`${block.reason_code}-${index}`}>
      <div>
        <strong>{block.sku}{block.quantity > 1 ? ` × ${block.quantity}` : ""}</strong>
        <p>{block.message}</p>
        {block.suggested_correction && <p className="muted">{block.suggested_correction}</p>}
      </div>
      {onInspect && <button type="button" className="btn btn-secondary btn-sm" onClick={() => onInspect(block)}>Review {block.sku || "item"}</button>}
    </li>)}</ul>}
    {!!job.search_progress?.rejected_placements?.length && <details className="disclosure"><summary>Search diagnostics</summary>
      <p className="muted">Rejected trial positions are search evidence, not proof that the shipment can’t be packed.</p>
      <ul className="plain-list">{job.search_progress.rejected_placements.map(row => <li key={row.reason}><span>{row.reason}</span><span>{row.count} trials</span></li>)}</ul>
    </details>}
  </section>;
}
