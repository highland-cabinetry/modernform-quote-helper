import { Badge } from "./Common";
import { CUSTOMER_STATUS, LOCATION_TYPES, QUOTE_SOURCES, money } from "../lib/format";

// Small pieces shared by the quote drawer, the order's quote history and Quote History.

export const QUOTE_KINDS = { packed: "Pallet plan", measured: "Measured pallets", external: "External quote" };

// Brokers in the order the Build2Quote tabs open; the add-price row suggests the next one not entered yet.
export const BROKER_ORDER = ["echo", "myunishippers", "dsv", "tql", "odfl"];

export function nextSource(options = []) {
  const entered = new Set(options.map((option) => option.source));
  return BROKER_ORDER.find((source) => !entered.has(source)) || "other";
}

export function sourceName(option) {
  if (!option) return "";
  if (option.source === "other" || !QUOTE_SOURCES[option.source]) {
    const label = option.source_label && option.source_label !== "Other" ? option.source_label : "";
    return label || (option.source === "other" ? "Other" : String(option.source || "").toUpperCase());
  }
  return QUOTE_SOURCES[option.source];
}

// Same labels as DeliveryTerms.labels() on the server (used where the list API has no terms_labels).
export function termsLabels(terms) {
  if (!terms) return [];
  return [LOCATION_TYPES[terms.location_type] || "Commercial",
    ...[["liftgate", "Liftgate"], ["appointment", "Appointment"], ["call_ahead", "Call ahead"], ["inside_delivery", "Inside delivery"]]
      .filter(([key]) => terms[key]).map(([, label]) => label)];
}

export function selectedOption(quote) {
  return quote?.selected_option || quote?.options?.find((option) => option.id === quote.selected_option_id) || null;
}

export function CustomerBadge({ status }) {
  const entry = CUSTOMER_STATUS[status] || CUSTOMER_STATUS.not_sent;
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

export function TermsChips({ labels, zip, empty = "No delivery terms" }) {
  if (!labels?.length && !zip) return <span className="muted">{empty}</span>;
  return (
    <span className="qd-chips">
      {labels?.map((label) => <span key={label} className="qd-chip">{label}</span>)}
      {zip && <span className="qd-chip qd-chip-zip">ZIP {zip}</span>}
    </span>
  );
}

const dollars = (cents) => (cents % 100 === 0 ? `$${(cents / 100).toLocaleString()}` : money(cents));

// "15% + round up to $5" from a PricingRule.
export function ruleLabel(rule) {
  if (!rule) return "";
  const parts = [];
  if (rule.markup_percent) parts.push(`${Number(rule.markup_percent)}%`);
  if (rule.markup_fixed_cents) parts.push(`${dollars(rule.markup_fixed_cents)}`);
  if (!parts.length) parts.push("no markup");
  if (rule.round_up_to_cents) parts.push(`round up to ${dollars(rule.round_up_to_cents)}`);
  const text = parts.join(" + ");
  return rule.minimum_cents ? `${text}, at least ${dollars(rule.minimum_cents)}` : text;
}

export function marginOf(quote) {
  const customer = quote?.cost?.customer_price_cents;
  const carrier = selectedOption(quote)?.price_cents ?? quote?.cost?.amount_cents;
  if (customer === null || customer === undefined || carrier === null || carrier === undefined) return null;
  const cents = customer - carrier;
  return { cents, percent: customer ? (cents / customer) * 100 : null };
}
