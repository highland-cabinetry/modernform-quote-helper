export function freightReady(result) {
  if (!result || result.plan_status !== "ready" || result.unpacked?.length || result.freight_ready === false || result.accounting_errors?.length) return false;
  const placed = result.pallets.flatMap(p => p.items);
  const contained = result.contained_items || [];
  const ids = [...placed, ...contained].map(i => i.unit_id);
  return ids.length === new Set(ids).size && ids.length === result.summary.total_items
    && ids.length === result.summary.packed_items && result.summary.unpacked_items === 0
    && !result.warnings.some(w => w.severity === "error");
}
