import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, BarChart3, Boxes, Building2, Check, ClipboardList, Database, FileStack, Gauge, History, Layers3,
  LoaderCircle, Menu, Truck, X,
} from "lucide-react";
import release from "../../../release.json";
import { getJson } from "../api";
import { ensureBridgeCurrent } from "../build2quote";
import { APP_NAME, FEATURES } from "../edition";
import UpdateBanner from "../components/UpdateBanner";
import { href, useRoute } from "../lib/route";

const Dashboard = lazy(() => import("../pages/Dashboard"));
const NewQuote = lazy(() => import("../pages/NewQuote"));
const Orders = lazy(() => import("../pages/Orders"));
const QuoteHistory = lazy(() => import("../pages/QuoteHistory"));
const Shipments = lazy(() => import("../pages/Shipments"));
const Catalog = lazy(() => import("../pages/Catalog"));
const Settings = lazy(() => import("../pages/Settings"));
const FreightData = lazy(() => import("../pages/FreightData"));

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

// Pages that belong to the freight workflow (FEATURES.freightWorkflow): hidden in Modernform Quote Helper.
const WORKFLOW_PAGES = new Set(["dashboard", "shipments", "freight"]);
const shown = (item) => FEATURES.freightWorkflow || !WORKFLOW_PAGES.has(item.key);

const NAV = [
  { section: "Work", items: [
    { key: "dashboard", label: "Dashboard", icon: Gauge, path: "/dashboard" },
    { key: "quote", label: "New Quote", icon: Boxes, path: "/quote/new" },
    { key: "orders", label: "Orders", icon: ClipboardList, path: "/orders" },
    { key: "quotes", label: "Quote History", icon: History, path: "/quotes" },
    { key: "shipments", label: "Shipments", icon: Truck, path: "/shipments" },
  ].filter(shown) },
  { section: "Data", items: [
    { key: "catalog", label: "Product Catalog", icon: Database, path: "/catalog" },
    { key: "freight", label: "Freight Data", icon: BarChart3, path: "/freight" },
    { key: "settings", label: "Company & Import", icon: Building2, path: "/settings" },
  ].filter(shown) },
];

const PAGES = Object.fromEntries(Object.entries({
  dashboard: Dashboard, quote: NewQuote, orders: Orders, quotes: QuoteHistory, shipments: Shipments,
  catalog: Catalog, settings: Settings, freight: FreightData,
}).filter(([key]) => shown({ key })));
const HOME = FEATURES.freightWorkflow ? "dashboard" : "quote";

function versionAtLeast(version, minimum) {
  const current = String(version || "").split(".").map((part) => Number(part) || 0);
  const required = String(minimum).split(".").map((part) => Number(part) || 0);
  for (let index = 0; index < Math.max(current.length, required.length); index += 1) {
    if ((current[index] || 0) !== (required[index] || 0)) return (current[index] || 0) > (required[index] || 0);
  }
  return true;
}

export default function AppShell() {
  const [route, go] = useRoute();
  const [companies, setCompanies] = useState([]);
  const [companyId, setCompanyId] = useState(() => {
    try { return localStorage.getItem("qh-company") || ""; } catch { return ""; }
  });
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState("");
  const [toasts, setToasts] = useState([]);
  const [bridge, setBridge] = useState({ state: "checking", version: "" });
  const [navOpen, setNavOpen] = useState(false);

  const notify = useCallback((message, tone = "ok") => {
    const id = crypto.randomUUID();
    setToasts((current) => [...current.slice(-1), { id, message, tone }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), tone === "danger" ? 12000 : 4500);
  }, []);

  const loadCompanies = useCallback(async () => {
    const list = await getJson("/api/companies");
    setCompanies(list);
    return list;
  }, []);

  useEffect(() => {
    getJson("/api/health").then(setHealth).catch((error) => setHealthError(error.message));
    loadCompanies().catch((error) => setHealthError(error.message));
    // Reloads an older, already-loaded Bridge from the updated program folder (once per page load).
    ensureBridgeCurrent(release.version).then(setBridge).catch(() => setBridge({ state: "not-installed", version: "" }));
  }, [loadCompanies]);

  const company = companies.find((entry) => entry.id === companyId && entry.active)
    || companies.find((entry) => entry.is_default) || companies[0] || null;

  const chooseCompany = (id) => {
    setCompanyId(id);
    try { localStorage.setItem("qh-company", id); } catch { /* per-viewer convenience only */ }
  };

  useEffect(() => { setNavOpen(false); setToasts((current) => current.filter((toast) => toast.tone === "danger")); }, [route.path]);
  const [quoteOpened, setQuoteOpened] = useState(false);
  useEffect(() => { if (route.parts[0] === "quote" || (!PAGES[route.parts[0]] && HOME === "quote")) setQuoteOpened(true); }, [route.parts]);

  const requested = route.parts[0] === "quote" ? "quote" : (route.parts[0] || HOME);
  const pageKey = PAGES[requested] ? requested : HOME;
  const Page = PAGES[pageKey] || PAGES[HOME];
  const context = useMemo(() => ({
    route, go, company, companies, reloadCompanies: loadCompanies, notify, bridge, health,
    multiCompany: companies.filter((entry) => entry.active).length > 1,
  }), [route, go, company, companies, loadCompanies, notify, bridge, health]);

  const backendStale = health && !versionAtLeast(health.version, release.version);

  return (
    <AppContext.Provider value={context}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <div className={`shell ${navOpen ? "nav-open" : ""}`}>
        <aside className="sidebar" aria-label="Main navigation">
          <div className="sidebar-brand">
            <span className="brand-mark" aria-hidden="true"><Layers3 size={19} /></span>
            <span><b>{APP_NAME}</b><small>{company?.name || "Freight quoting"}</small></span>
            <button type="button" className="icon-btn sidebar-close" aria-label="Close navigation" onClick={() => setNavOpen(false)}><X size={18} /></button>
          </div>
          <nav>
            {NAV.map((group) => (
              <div key={group.section} className="nav-group">
                <span className="nav-section">{group.section}</span>
                {group.items.map(({ key, label, icon: Icon, path }) => (
                  <a key={key} href={href(path)} className={`nav-link ${pageKey === key ? "active" : ""}`} aria-current={pageKey === key ? "page" : undefined}>
                    <Icon size={17} aria-hidden="true" />{label}
                  </a>
                ))}
              </div>
            ))}
          </nav>
          <div className="sidebar-foot">
            <button type="button" className={`bridge-pill ${bridge.state === "connected" ? "is-ok" : ""}`}
              title="Build2Quote Bridge — click to check again"
              onClick={async () => {
                setBridge((current) => ({ ...current, state: "checking" }));
                setBridge(await ensureBridgeCurrent(release.version));
              }}>
              <span className={`dot ${bridge.state === "connected" ? "dot-ok" : bridge.state === "checking" ? "dot-info" : "dot-warn"}`} aria-hidden="true" />
              {bridge.state === "connected" ? `Bridge ${bridge.version}` : bridge.state === "checking" ? "Checking bridge…"
                : bridge.state === "update-required" ? `Bridge ${bridge.version}: reload it once in chrome://extensions` : "Bridge not connected"}
            </button>
            <small className="version-line">App {release.version} · Engine {release.production_engine_version}</small>
          </div>
        </aside>
        <div className="shell-main">
          <header className="shell-top">
            <button type="button" className="icon-btn nav-toggle" aria-label="Open navigation" onClick={() => setNavOpen(true)}><Menu size={20} /></button>
            {companies.filter((entry) => entry.active).length > 1 ? (
              <label className="company-switch">
                <FileStack size={15} aria-hidden="true" />
                <span className="sr-only">Company</span>
                <select value={company?.id || ""} onChange={(event) => chooseCompany(event.target.value)}>
                  {companies.filter((entry) => entry.active).map((entry) => <option key={entry.id} value={entry.id}>{entry.code} · {entry.name}</option>)}
                </select>
              </label>
            ) : <span className="company-name"><FileStack size={15} aria-hidden="true" />{company?.name || "…"}</span>}
            <span className="top-spacer" />
            <a className="btn btn-primary btn-sm" href={href("/quote/new")}><Boxes size={15} />New quote</a>
          </header>
          {health?.review_mode && <div className="app-banner app-banner-info" role="status">Preview build — running against a copy of the database. Changes here do not affect live data.</div>}
          {backendStale && <div className="app-banner app-banner-danger" role="alert"><AlertTriangle size={16} /><span><b>Restart required.</b> Version {health.version} is still running in the background. Use Start menu › {APP_NAME} Tools › Stop {APP_NAME}, open {APP_NAME} again, then refresh.</span></div>}
          {healthError && <div className="app-banner app-banner-danger" role="alert"><AlertTriangle size={16} /><span><b>Cannot reach the {APP_NAME} service.</b> {healthError}. Open {APP_NAME} from the Start menu, then refresh.</span></div>}
          <UpdateBanner />
          <main id="main-content" className="shell-content" tabIndex={-1}>
            <Suspense fallback={<div className="loading-block"><LoaderCircle className="spin" size={18} />Loading…</div>}>
              {!company ? (!healthError && <div className="loading-block"><LoaderCircle className="spin" size={18} />Connecting…</div>) : <>
                {/* The quote workspace stays mounted once opened, so work in progress survives visits to other pages. */}
                {quoteOpened && <div hidden={pageKey !== "quote"}><NewQuote active={pageKey === "quote"} /></div>}
                {pageKey !== "quote" && <Page key={route.path} />}
              </>}
            </Suspense>
          </main>
        </div>
        {navOpen && <button type="button" className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      </div>
      <div className="toast-region toast-region-shell" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.tone === "danger" ? "danger" : "ok"}`} role={toast.tone === "danger" ? "alert" : "status"}>
            {toast.tone === "danger" ? <AlertTriangle size={18} /> : <Check size={18} />}
            <span>{toast.message}</span>
            <button type="button" className="icon-btn" aria-label="Dismiss" onClick={() => setToasts((current) => current.filter((entry) => entry.id !== toast.id))}><X size={16} /></button>
          </div>
        ))}
      </div>
    </AppContext.Provider>
  );
}
