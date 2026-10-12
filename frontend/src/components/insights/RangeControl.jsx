import { CalendarRange } from "lucide-react";
import { RANGE_PRESETS } from "./shared";

// Period picker: presets plus custom from/to days. value = { range, from, to }.
export default function RangeControl({ value, onChange, idPrefix = "range" }) {
  return (
    <div className="ins-range">
      <label className="ins-inline-field" htmlFor={`${idPrefix}-preset`}>
        <CalendarRange size={15} aria-hidden="true" />
        <span className="sr-only">Period</span>
        <select id={`${idPrefix}-preset`} value={value.range} onChange={(event) => onChange({ range: event.target.value })}>
          {RANGE_PRESETS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      {value.range === "custom" && (
        <span className="ins-range-days">
          <label><span className="sr-only">From</span>
            <input type="date" aria-label="From" value={value.from || ""} max={value.to || undefined} onChange={(event) => onChange({ range: "custom", from: event.target.value, to: value.to })} />
          </label>
          <span aria-hidden="true">–</span>
          <label><span className="sr-only">To</span>
            <input type="date" aria-label="To" value={value.to || ""} min={value.from || undefined} onChange={(event) => onChange({ range: "custom", from: value.from, to: event.target.value })} />
          </label>
        </span>
      )}
    </div>
  );
}
