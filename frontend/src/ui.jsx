import { Check } from "lucide-react";

export const formatDimension = (value) => Number(Number(value).toFixed(2));

export function formatWeight(value) {
  return `${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} lb`;
}

export function StatusPill({ severity = "neutral", children }) {
  const label = typeof children === "string" && children ? children.charAt(0).toUpperCase() + children.slice(1) : children;
  return <span className={`badge badge-${severity}`}>{label}</span>;
}

export function Stat({ icon: Icon, label, value, tone = "default", hint }) {
  return (
    <div className={`stat stat-${tone}`}>
      {Icon && <span className="stat-icon" aria-hidden="true"><Icon size={18} /></span>}
      <div>
        <span className="stat-label">{label}</span>
        <strong className="stat-value">{value}</strong>
        {hint && <span className="stat-hint">{hint}</span>}
      </div>
    </div>
  );
}

export function Card({ title, description, actions, children, className = "", headingLevel = 2, id }) {
  const Heading = `h${headingLevel}`;
  return (
    <section className={`card ${className}`} id={id}>
      {(title || actions) && (
        <header className="card-head">
          <div className="card-head-text">
            {title && <Heading className="card-title">{title}</Heading>}
            {description && <p className="card-description">{description}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Checklist({ items }) {
  return (
    <ol className="checklist">
      {items.map((item, index) => (
        <li key={item.label} className={item.done ? "is-done" : ""}>
          <span className="checklist-mark" aria-hidden="true">{item.done ? <Check size={13} strokeWidth={3} /> : index + 1}</span>
          <span>
            <b>{item.label}</b>
            {item.detail && <small>{item.detail}</small>}
          </span>
          <span className="sr-only">{item.done ? "complete" : "not complete"}</span>
        </li>
      ))}
    </ol>
  );
}

export function NumberField({ label, hint, unit, value, onChange, min = 0, step = 1, ariaLabel }) {
  return (
    <label className="limit-field">
      <span className="limit-field-text">
        <b>{label}</b>
        {hint && <small>{hint}</small>}
      </span>
      <span className="input-unit">
        <input aria-label={ariaLabel} type="number" min={min} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
        {unit && <em>{unit}</em>}
      </span>
    </label>
  );
}
