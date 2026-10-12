// Display and parsing helpers shared by every page.

export function money(cents, { blank = "—", signed = false } = {}) {
  if (cents === null || cents === undefined || cents === "") return blank;
  const value = Number(cents) / 100;
  const text = Math.abs(value).toLocaleString(undefined, { style: "currency", currency: "USD" });
  if (value < 0) return `−${text}`;
  return signed && value > 0 ? `+${text}` : text;
}

// "1,234.5" / "$1234.50" -> 123450 cents; "" -> null; invalid -> NaN
export function parseMoney(text) {
  const cleaned = String(text ?? "").replace(/[$,\s]/g, "");
  if (!cleaned) return null;
  if (!/^-?\d+(\.\d{0,2})?$/.test(cleaned)) return Number.NaN;
  return Math.round(Number(cleaned) * 100);
}

export function centsToInput(cents) {
  return cents === null || cents === undefined ? "" : (Number(cents) / 100).toFixed(2);
}

export function weight(value) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} lb`;
}

export function dims(l, w, h) {
  if (!l || !w || !h) return "—";
  const f = (v) => Number(Number(v).toFixed(2));
  return `${f(l)} × ${f(w)} × ${f(h)}`;
}

export function date(value, { time = false } = {}) {
  if (!value) return "—";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(parsed.getTime())) return value;
  return time
    ? parsed.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function relative(value) {
  if (!value) return "";
  const seconds = (Date.now() - new Date(value).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} d ago`;
  return date(value);
}

export const plural = (count, word, many = `${word}s`) => `${count} ${count === 1 ? word : many}`;

export const ORDER_STATUS = {
  needs_review: { label: "Needs review", tone: "warn" },
  ready: { label: "Ready", tone: "ok" },
  reference_only: { label: "Reference only", tone: "neutral" },
  shipped: { label: "Shipped", tone: "info" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const QUOTE_STATUS = {
  draft: { label: "Draft", tone: "warn" },
  final: { label: "Current", tone: "ok" },
  superseded: { label: "Superseded", tone: "neutral" },
  void: { label: "Void", tone: "danger" },
};

export const SHIPMENT_STATUS = {
  planning: { label: "Planning", tone: "info" },
  booked: { label: "Booked", tone: "info" },
  in_transit: { label: "In transit", tone: "warn" },
  delivered: { label: "Delivered", tone: "ok" },
  closed: { label: "Closed", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "danger" },
};

export const EXPENSE_KINDS = {
  linehaul: "Linehaul / carrier invoice",
  fuel: "Fuel surcharge",
  accessorial: "Accessorial",
  redelivery: "Redelivery",
  reweigh: "Reweigh / reclass",
  storage: "Storage",
  adjustment: "Adjustment or credit",
  other: "Other",
};

// 3.2 workflow labels (shared by Orders, Quotes, Shipments, Freight Data, Settings).
export const CUSTOMER_STATUS = {
  not_sent: { label: "Not sent", tone: "neutral" },
  sent: { label: "Sent to customer", tone: "info" },
  approved: { label: "Approved", tone: "ok" },
  declined: { label: "Declined", tone: "danger" },
  expired: { label: "Expired", tone: "warn" },
};

export const LOCATION_TYPES = {
  commercial: "Commercial",
  residential: "Residential",
  limited_access: "Limited access",
};

export const QUOTE_SOURCES = {
  echo: "EchoShip",
  myunishippers: "MyUnishippers",
  dsv: "DSV",
  tql: "TQL",
  odfl: "Old Dominion",
  other: "Other / phone / email",
};

export const SHIPMENT_PURPOSES = {
  standard: "Standard",
  back_order: "Back order",
  warranty: "Warranty",
  damage_warranty: "Damage warranty",
  correction: "Order correction",
  transfer: "Transfer",
  sister: "Sister company",
};

export const CHARGE_SOURCES = {
  quote: { label: "From the approved quote", help: "Follows the quote's customer price." },
  rep_assigned: { label: "Rep-assigned charge", help: "What the sales rep set for the customer." },
  backcharge: { label: "Backcharge", help: "Billed back to the customer after shipping." },
  manual: { label: "Entered by hand", help: "Type the amount the customer pays." },
  none: { label: "No charge", help: "The customer pays nothing for this freight." },
};

export const REVENUE_KINDS = {
  accessorial_rebill: "Accessorial re-billed to customer",
  claim_recovery: "Claim recovery",
  other: "Other revenue",
};

export const EMAIL_KINDS = {
  quote: "Quote to customer",
  follow_up: "Quote follow-up",
  ready: "Order ready to ship",
  shipped: "Shipped (PRO / tracking)",
  delivered: "Delivered",
};

export const ALLOCATION_METHODS = {
  equal: { label: "Equal", help: "Same share for every order." },
  weight: { label: "By weight", help: "Each order's packed item weight." },
  pallets: { label: "By pallets", help: "Each order's share of the pallets it rides on." },
  manual: { label: "Manual", help: "Enter each order's amount; they must add up to the total." },
};
