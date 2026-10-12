// Helpers shared by the Dashboard, Freight Data and Settings (3.2 insights screens).
import { getJson, sendJson } from "../../api";
import { SHIPMENT_PURPOSES } from "../../lib/format";

// "?company_id=…" only when several companies are active (the pages' existing convention: one company sees all).
export function companyQuery(company, multiCompany, extra = {}) {
  const params = new URLSearchParams();
  if (multiCompany && company?.id) params.set("company_id", company.id);
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null && value !== "" && value !== false) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function percent(value, { blank = "—", digits = 1 } = {}) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return blank;
  return `${Number(value).toFixed(digits)}%`;
}

export function count(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString();
}

const pad = (value) => String(value).padStart(2, "0");
export const isoDay = (day) => `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;

export const RANGE_PRESETS = [
  ["this_month", "This month"],
  ["last_month", "Last month"],
  ["this_quarter", "This quarter"],
  ["ytd", "Year to date"],
  ["last_12", "Last 12 months"],
  ["last_year", "Last year"],
  ["all", "All time"],
  ["custom", "Custom dates"],
];

// { from, to } (ISO days, inclusive) for a preset; null bounds mean open-ended. Presets that end
// today are left open at the end: the API dates records by their UTC day, which is already
// tomorrow during the evening in US time zones.
export function rangeFor(preset, custom = {}, today = new Date()) {
  const y = today.getFullYear();
  const m = today.getMonth();
  const day = (year, month, date) => isoDay(new Date(year, month, date));
  switch (preset) {
    case "this_month": return { from: day(y, m, 1), to: null };
    case "last_month": return { from: day(y, m - 1, 1), to: day(y, m, 0) };
    case "this_quarter": return { from: day(y, m - (m % 3), 1), to: null };
    case "ytd": return { from: day(y, 0, 1), to: null };
    case "last_12": return { from: day(y - 1, m, today.getDate() + 1), to: null };
    case "last_year": return { from: day(y - 1, 0, 1), to: day(y - 1, 11, 31) };
    case "custom": return { from: custom.from || null, to: custom.to || null };
    default: return { from: null, to: null };
  }
}

export function inRange(day, range) {
  if (!day) return !range.from && !range.to;
  if (range.from && day < range.from) return false;
  if (range.to && day > range.to) return false;
  return true;
}

export const GROUPS = [
  ["month", "Month"],
  ["rep", "Sales rep"],
  ["carrier", "Carrier"],
  ["purpose", "Purpose"],
  ["customer", "Customer"],
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function groupLabel(group, key) {
  if (key === "(none)") return group === "purpose" ? "Standard" : "Not recorded";
  if (group === "month" && /^\d{4}-\d{2}$/.test(key)) return `${MONTHS[Number(key.slice(5)) - 1]} ${key.slice(0, 4)}`;
  if (group === "purpose") return SHIPMENT_PURPOSES[key] || key;
  return key;
}

// The day a shipment counts on in reports: pickup, else delivery, else creation (WORKFLOW §7).
export const recordDay = (row) => row.pickup_date || row.delivery_date || (row.created_at || "").slice(0, 10);

// Same group keys as the report API, so a report row can drill into the ledger.
export function recordGroupKey(group, row) {
  switch (group) {
    case "month": return recordDay(row).slice(0, 7);
    case "rep": return row.rep || "(none)";
    case "customer": return row.customer || "(none)";
    case "carrier": return row.carrier || "(none)";
    case "purpose": return row.purpose || "standard";
    default: return "";
  }
}

// Replace one section of company.config, re-reading the company first so other sections
// (and edits made elsewhere since the page loaded) are kept exactly as stored.
export async function saveCompanyConfig(companyId, section, value) {
  const companies = await getJson("/api/companies");
  const current = companies.find((entry) => entry.id === companyId);
  if (!current) throw new Error("This company no longer exists. Reload the page.");
  const next = typeof value === "function" ? value(current.config[section], current.config) : value;
  return sendJson(`/api/companies/${current.id}`, "PUT", {
    code: current.code, name: current.name, active: current.active,
    config: { ...current.config, [section]: next },
  });
}

// Money text → cents for settings forms; "" → 0 when allowed.
export function dollarsToCents(text, { allowEmpty = true } = {}) {
  const cleaned = String(text ?? "").replace(/[$,\s]/g, "");
  if (!cleaned) return allowEmpty ? 0 : Number.NaN;
  if (!/^\d+(\.\d{0,2})?$/.test(cleaned)) return Number.NaN;
  return Math.round(Number(cleaned) * 100);
}
