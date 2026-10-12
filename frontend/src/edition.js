// Which build this is (release.json "edition"). Modernform Quote Helper is the downsized edition:
// import, pallet plans, measured pallets, broker autofill, quote history and the product catalog,
// without Highland's freight workflow (shipments, customer answers, emails, freight reports).
import release from "../../release.json";

export const EDITION = release.edition || "highland";
const MODERNFORM = EDITION === "modernform";

export const APP_NAME = MODERNFORM ? "Modernform Quote Helper" : "Quote Helper";
export const SETUP_PREFIX = MODERNFORM ? "ModernformQuoteHelperSetup-" : "QuoteHelperSetup-";
// The product catalog in the simple "item list" layout with a Cabinet Colors list (color name → item prefix).
export const SIMPLE_CATALOG = MODERNFORM;
export const CATALOG_LABEL = MODERNFORM ? "Item master" : "Highland item master";
export const FEATURES = {
  // Shipments, customer answers (approve → shipment), prepared emails, sales reps, the dashboard and
  // Freight Data reports.
  freightWorkflow: !MODERNFORM,
};
