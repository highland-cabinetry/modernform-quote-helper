import { CheckCircle2, Mail } from "lucide-react";
import { EMAIL_KINDS, date } from "../../lib/format";
import { SUGGESTED_EMAIL } from "./model";
import "../../styles/shipments.css";

const KINDS = [
  ["ready", "Ready to ship"],
  ["shipped", "Shipped with PRO"],
  ["delivered", "Delivered"],
];

// Prepared customer emails for a shipment: one button per moment plus the log of emails opened.
export default function ShipmentEmails({ status, records, loading, onCompose }) {
  const list = records || [];
  const done = new Set(list.map((record) => record.kind));
  const suggested = SUGGESTED_EMAIL[status];
  return (
    <section className="card emails-card" aria-labelledby="emails-title">
      <h2 className="card-title" id="emails-title"><Mail size={16} aria-hidden="true" /> Customer emails</h2>
      <p className="card-description">Prepared for Outlook; you review and send them. Quote Helper never sends email.</p>
      <div className="email-buttons">
        {KINDS.map(([kind, label]) => {
          const isSuggested = kind === suggested && !done.has(kind);
          return (
            <button key={kind} type="button" className={`btn btn-sm ${isSuggested ? "btn-primary" : "btn-secondary"}`} onClick={() => onCompose(kind)}
              aria-describedby={isSuggested ? "email-suggested" : undefined}>
              {done.has(kind) ? <CheckCircle2 size={14} aria-label="prepared before" /> : <Mail size={14} aria-hidden="true" />}{label}
            </button>
          );
        })}
      </div>
      {suggested && !done.has(suggested) && <p id="email-suggested" className="small-line muted">Suggested now: {EMAIL_KINDS[suggested]}.</p>}
      <h3 className="emails-log-title">Emails prepared</h3>
      {loading && !records ? <p className="muted small-line">Loading…</p> : list.length ? (
        <ul className="emails-log">
          {list.map((record) => (
            <li key={record.id}>
              <b>{EMAIL_KINDS[record.kind] || record.kind}</b>
              <small>{date(record.created_at, { time: true })}{record.to?.length ? ` · to ${record.to.join(", ")}` : " · no recipient"}</small>
            </li>
          ))}
        </ul>
      ) : <p className="muted small-line">None yet.</p>}
    </section>
  );
}
