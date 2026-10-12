import { useState } from "react";

export default function LayerView({ pallet, selectedId, onSelect, containedItems = [] }) {
  const [height, setHeight] = useState(null);
  if (!pallet) return null;
  const levels = [...new Set(pallet.items.map(item => item.z))].sort((a, b) => a - b);
  const z = levels.includes(height) ? height : levels[0];
  const items = pallet.items.filter(item => Math.abs(item.z - z) < 0.001);
  const minX = Math.min(0, ...pallet.items.map(i => i.x));
  const minY = Math.min(0, ...pallet.items.map(i => i.y));
  const width = Math.max(pallet.length, ...pallet.items.map(i => i.x + i.length)) - minX;
  const depth = Math.max(pallet.width, ...pallet.items.map(i => i.y + i.width)) - minY;
  return <details className="layer-view disclosure"><summary>Layer view and keyboard carton selection</summary>
    <div className="layer-body">
      <div className="layer-plan">
        <label className="field field-sm">
          <span>Layer above deck</span>
          <select value={z ?? ""} onChange={e => setHeight(Number(e.target.value))}>
            {levels.map(level => <option key={level} value={level}>{level.toFixed(2)} in</option>)}
          </select>
        </label>
        <svg viewBox={`${minX - 2} ${minY - 2} ${width + 4} ${depth + 4}`} role="img" aria-label={`Top view of pallet ${pallet.pallet_number} at ${z} inches`}>
          <rect x="0" y="0" width={pallet.length} height={pallet.width} fill="#efe4d2" stroke="#8a6a43" strokeDasharray="1 1" strokeWidth="0.35" />
          {items.map(item => <g key={item.unit_id}>
            <rect x={item.x} y={item.y} width={item.length} height={item.width} fill={item.unit_id === selectedId ? "#c98a2e" : item.color || "#2b6e68"} stroke="#fff" strokeWidth="0.3" />
            <title>{item.original_sku}: {Math.round(item.support_ratio * 100)}% area support</title>
          </g>)}
        </svg>
        <p className="hint">The dashed outline is the pallet deck. Cartons beyond it need approved overhang.</p>
      </div>
      <ul className="carton-list">{pallet.items.map(item => <li key={item.unit_id}><button type="button" aria-pressed={item.unit_id === selectedId}
        onClick={() => { setHeight(item.z); onSelect?.(item); }}><b>{item.original_sku}</b><span>{item.unit_id} · layer {item.z.toFixed(2)} in · {Math.round(item.support_ratio * 100)}% support</span></button>{containedItems.filter(content => content.host_unit_id === item.unit_id).map(content => <p key={content.unit_id} className="contained">{content.sku} · inside {item.original_sku} · {content.weight} lb included in host weight</p>)}</li>)}</ul>
    </div>
  </details>;
}
