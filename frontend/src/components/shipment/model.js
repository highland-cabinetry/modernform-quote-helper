// Shipment screen helpers: status steps, the editable form and its PUT payload, list flags.
import { centsToInput, parseMoney } from "../../lib/format";

export const STEPS = [
  { key: "planning", label: "Planning" },
  { key: "booked", label: "Booked" },
  { key: "in_transit", label: "In transit" },
  { key: "delivered", label: "Delivered" },
  { key: "closed", label: "Closed" },
];

export const stepIndex = (status) => STEPS.findIndex((step) => step.key === status);

// The forward action offered for each status (the stepper also allows moving back).
export const NEXT_ACTION = {
  planning: { to: "booked", label: "Mark booked" },
  booked: { to: "in_transit", label: "Picked up" },
  in_transit: { to: "delivered", label: "Mark delivered" },
  delivered: { to: "closed", label: "Close shipment" },
};

// Email that fits each moment of the shipment.
export const SUGGESTED_EMAIL = { booked: "ready", in_transit: "shipped", delivered: "delivered" };

export const ACTUAL_SOURCES = {
  warehouse: "Warehouse measured",
  carrier_bill: "Carrier bill",
  bol: "Bill of lading",
  other: "Other",
};

export const PURPOSE_TONE = {
  standard: "neutral", back_order: "info", warranty: "warn", damage_warranty: "danger", correction: "warn",
  transfer: "neutral", sister: "info",
};

export const isLocked = (status) => status === "closed" || status === "cancelled";

export const today = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
};

export function formFromShipment(shipment) {
  return {
    name: shipment.name || "",
    status: shipment.status,
    carrier: shipment.carrier || "",
    broker: shipment.broker || "",
    pro_number: shipment.pro_number || "",
    bol_number: shipment.bol_number || "",
    pickup_date: shipment.pickup_date || "",
    expected_delivery_date: shipment.expected_delivery_date || "",
    delivery_date: shipment.delivery_date || "",
    destination: { ...(shipment.destination || {}) },
    purpose: shipment.purpose || "standard",
    notes: shipment.notes || "",
    charge_source: shipment.charge_source || "quote",
    customer_charge: centsToInput(shipment.customer_charge_cents),
    rep_charge: centsToInput(shipment.rep_charge_cents),
    backcharge: centsToInput(shipment.backcharge_cents),
  };
}

// Problems with the customer-charge part of the form, keyed by field.
export function chargeErrors(form) {
  const errors = {};
  const check = (key, required, label) => {
    const cents = parseMoney(form[key]);
    if (Number.isNaN(cents)) errors[key] = "Use dollars and cents, e.g. 450.00";
    else if (cents !== null && cents < 0) errors[key] = "Cannot be negative";
    else if (required && cents === null) errors[key] = `Enter the ${label}`;
  };
  check("rep_charge", form.charge_source === "rep_assigned", "charge the rep assigned");
  check("backcharge", form.charge_source === "backcharge", "backcharge amount");
  check("customer_charge", false, "customer charge");
  return errors;
}

// The customer charge that saving will produce (WORKFLOW §3), and where it comes from.
export function resolvedCharge(form, shipment) {
  const quote = shipment.combined_quote;
  const quotePrice = quote && quote.status !== "void" ? quote.cost?.customer_price_cents ?? null : null;
  switch (form.charge_source) {
    case "quote":
      return quotePrice !== null
        ? { cents: quotePrice, from: `${quote.number} customer price` }
        : { cents: shipment.customer_charge_cents ?? null,
          from: quote ? `${quote.number} has no customer price yet; the saved charge is kept` : "No combined quote yet; the saved charge is kept" };
    case "rep_assigned": return { cents: parseMoney(form.rep_charge), from: "Rep-assigned charge" };
    case "backcharge": return { cents: parseMoney(form.backcharge), from: "Backcharge" };
    case "none": return { cents: 0, from: "No charge" };
    default: return { cents: parseMoney(form.customer_charge), from: "Entered by hand" };
  }
}

const money = (text) => {
  const cents = parseMoney(text);
  return cents === null || Number.isNaN(cents) ? null : cents;
};

// PUT /freight-shipments/{id} body (FreightShipmentInput forbids unknown keys; missing keys reset to defaults).
export function shipmentPayload(form, shipment, overrides = {}) {
  const value = { ...form, ...overrides };
  const charge = resolvedCharge(value, shipment);
  return {
    name: value.name.trim(),
    status: value.status,
    carrier: value.carrier.trim(),
    broker: value.broker.trim(),
    pro_number: value.pro_number.trim(),
    bol_number: value.bol_number.trim(),
    pickup_date: value.pickup_date || null,
    delivery_date: value.delivery_date || null,
    expected_delivery_date: value.expected_delivery_date || null,
    destination: value.destination,
    purpose: value.purpose,
    notes: value.notes,
    charge_source: value.charge_source,
    customer_charge_cents: value.charge_source === "manual" ? money(value.customer_charge)
      : (charge.cents === null || Number.isNaN(charge.cents) ? shipment.customer_charge_cents ?? null : charge.cents),
    rep_charge_cents: money(value.rep_charge),
    backcharge_cents: money(value.backcharge),
  };
}

export function formDirty(form, shipment) {
  if (!form || !shipment) return false;
  const base = formFromShipment(shipment);
  return Object.keys(base).some((key) => key !== "status" && JSON.stringify(base[key]) !== JSON.stringify(form[key]));
}

// Flags for a Freight Data row (GET /api/freight-records), mirroring the shipment comparison flags.
export function recordFlags(row) {
  const flags = [];
  if (row.weight_over) flags.push("weight_over");
  if (row.actual_pallets != null && row.pallets != null && row.actual_pallets !== row.pallets) flags.push("pallets_changed");
  if (row.actual_cents != null && row.expected_cost_cents != null && row.actual_cents > row.expected_cost_cents) flags.push("cost_over");
  if (["delivered", "closed"].includes(row.status) && !row.expense_count) flags.push("missing_cost");
  if (row.net_cents != null && row.net_cents < 0) flags.push("loss");
  return flags;
}

export const FLAG_LABELS = {
  weight_over: { label: "Heavier than quoted", tone: "warn" },
  pallets_changed: { label: "Pallet count changed", tone: "warn" },
  cost_over: { label: "Cost over quote", tone: "danger" },
  missing_cost: { label: "No carrier bill yet", tone: "warn" },
  loss: { label: "Loss", tone: "danger" },
};

export function percent(value, { signed = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = `${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (value < 0) return `−${text}`;
  return signed && value > 0 ? `+${text}` : text;
}

export function signedWeight(value) {
  if (value === null || value === undefined) return "—";
  const text = `${Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} lb`;
  if (value < 0) return `−${text}`;
  return value > 0 ? `+${text}` : text;
}
