import { useMemo, useState } from "react";
import {
  AlarmClock, ArrowRight, CalendarClock, CheckCircle2, CircleDollarSign, FileQuestion, Hourglass, MailQuestion,
  Receipt, Ruler, Truck,
} from "lucide-react";
import { href } from "../../lib/route";

// Needs-attention kinds (WORKFLOW §6) in the order a freight desk works them.
export const ATTENTION_KINDS = {
  overdue_delivery: { label: "Overdue deliveries", action: "Track", icon: AlarmClock },
  approved_not_booked: { label: "Approved, not booked", action: "Book carrier", icon: Truck },
  pickup_today: { label: "Pickups today or tomorrow", action: "Open", icon: CalendarClock },
  quote_unanswered: { label: "Quotes waiting for an answer", action: "Follow up", icon: MailQuestion },
  quote_expiring: { label: "Quote prices expiring", action: "Review", icon: Hourglass },
  cost_over_quote: { label: "Cost over quote", action: "Compare", icon: CircleDollarSign },
  missing_cost: { label: "Missing carrier bill", action: "Add bill", icon: Receipt },
  unassigned_expense: { label: "Bills without a shipment", action: "Assign", icon: FileQuestion },
  missing_actuals: { label: "Missing actual measurements", action: "Record", icon: Ruler },
};

const SEVERITY = {
  danger: { rank: 0, label: "Urgent" },
  warn: { rank: 1, label: "Needs action" },
  info: { rank: 2, label: "For info" },
};

// Where one click on an item should land.
export function attentionHref(item) {
  if (item.entity_type === "quote") return href("/quotes", { quote: item.entity_id });
  if (item.entity_type === "shipment") {
    const costs = item.kind === "missing_cost" || item.kind === "cost_over_quote";
    return href(`/shipments/${item.entity_id}`, costs ? { tab: "costs" } : {});
  }
  if (item.entity_type === "unassigned_expense") return href("/freight", { tab: "unassigned" });
  return href("/dashboard");
}

export function ageText(days) {
  if (days === null || days === undefined) return "";
  if (days <= 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

// Items grouped by kind; groups with the most severe item first, then the desk order above.
export function groupAttention(items) {
  const order = Object.keys(ATTENTION_KINDS);
  const groups = new Map();
  for (const item of items || []) {
    const group = groups.get(item.kind) || { kind: item.kind, items: [], worst: 9 };
    group.items.push(item);
    group.worst = Math.min(group.worst, SEVERITY[item.severity]?.rank ?? 9);
    groups.set(item.kind, group);
  }
  return [...groups.values()].sort((a, b) => a.worst - b.worst || order.indexOf(a.kind) - order.indexOf(b.kind));
}

const PREVIEW = 4;

export function AttentionList({ items, loading }) {
  const groups = useMemo(() => groupAttention(items), [items]);
  const [expanded, setExpanded] = useState(() => new Set());
  if (!loading && !groups.length) {
    return (
      <div className="ins-all-clear">
        <CheckCircle2 size={22} aria-hidden="true" />
        <div><b>Nothing needs attention</b><span>Quotes are answered, shipments are on time and every bill is assigned.</span></div>
      </div>
    );
  }
  return (
    <div className="ins-attention">
      {groups.map((group) => {
        const meta = ATTENTION_KINDS[group.kind] || { label: group.kind.replaceAll("_", " "), action: "Open", icon: ArrowRight };
        const Icon = meta.icon;
        const open = expanded.has(group.kind);
        const shown = open ? group.items : group.items.slice(0, PREVIEW);
        const headingId = `attention-${group.kind}`;
        return (
          <section key={group.kind} className={`ins-att-group sev-${["danger", "warn", "info"][group.worst] || "info"}`} aria-labelledby={headingId}>
            <h3 id={headingId} className="ins-att-head">
              <span className="ins-att-icon" aria-hidden="true"><Icon size={15} /></span>
              <span>{meta.label}</span>
              <span className="ins-att-count">{group.items.length}</span>
            </h3>
            <ul className="ins-att-items">
              {shown.map((item, index) => {
                const severity = SEVERITY[item.severity] || SEVERITY.info;
                return (
                  <li key={`${item.kind}-${item.entity_id || index}`}>
                    <a className={`ins-att-item sev-${item.severity}`} href={attentionHref(item)}>
                      <span className="ins-sev" title={severity.label}><span className="sr-only">{severity.label}: </span></span>
                      <span className="ins-att-main">
                        <b>{item.title}</b>
                        <small>{item.detail}</small>
                      </span>
                      <span className="ins-att-side">
                        {ageText(item.age_days) && <small className="ins-age">{ageText(item.age_days)}</small>}
                        <span className="ins-att-action">{meta.action}<ArrowRight size={13} aria-hidden="true" /></span>
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
            {group.items.length > PREVIEW && (
              <button type="button" className="link-btn ins-att-more" aria-expanded={open}
                onClick={() => setExpanded((current) => {
                  const next = new Set(current);
                  if (next.has(group.kind)) next.delete(group.kind); else next.add(group.kind);
                  return next;
                })}>
                {open ? "Show fewer" : `Show all ${group.items.length}`}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
