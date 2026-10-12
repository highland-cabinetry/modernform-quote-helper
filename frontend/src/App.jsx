import { freightReady } from "./freightReady";
import release from "../../release.json";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Box,
  Boxes,
  Check,
  ChevronRight,
  CircleHelp,
  Copy,
  Download,
  FileSpreadsheet,
  FileUp,
  Layers3,
  Link2,
  LoaderCircle,
  PackageCheck,
  PencilLine,
  Plus,
  Rotate3d,
  Ruler,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Truck,
  Unlink,
  Upload,
  Weight,
  X,
} from "lucide-react";
import { useDraft } from "./drafts";
import DraftBar from "./DraftBar";
import LayerView from "./LayerView";
import PlanIssues from "./PlanIssues";
import PackingDiagnostics from "./PackingDiagnostics";
import useModalFocus from "./useModalFocus";
import usePackingJobs, { packingGeometry } from "./usePackingJobs";
import QuickQuote, { BrokerProgress } from "./QuoteWorkflows";
import { exportUrl, getJson, sendJson, uploadFiles } from "./api";
import { MINIMUM_BUILD2QUOTE_VERSION, openPreparedBrokerTabs, pingBuild2QuoteBridge } from "./build2quote";
import BrokerPanel from "./BrokerPanel";
import { AliasDialog } from "./Dialogs";
import { Card, NumberField, Stat, StatusPill, formatDimension, formatWeight } from "./ui";
import { money } from "./lib/format";
import { APP_NAME } from "./edition";

const PalletViewer = lazy(() => import("./PalletViewer"));
// The quote drawer opens from the recorded-quote card ("Add broker prices"); loaded on first use.
const QuoteDetail = lazy(() => import("./components/QuoteDetail"));
const CheckCircle2Icon = () => <ShieldCheck size={18} aria-hidden="true" className="ok-text" />;

function currentLoadFillPercent(pallet) {
  if (pallet?.load_fill_percent !== null && pallet?.load_fill_percent !== undefined && Number.isFinite(Number(pallet.load_fill_percent))) {
    return Math.max(0, Math.min(100, Number(pallet.load_fill_percent)));
  }
  const items = Array.isArray(pallet?.items) ? pallet.items : [];
  const length = Number(pallet?.load_length ?? pallet?.length);
  const width = Number(pallet?.load_width ?? pallet?.width);
  const stackHeight = Number(pallet?.stack_height) || Math.max(0, ...items.map(item => Number(item.z || 0) + Number(item.height || 0)));
  const cartonVolume = items.reduce((sum, item) => sum + Number(item.length || 0) * Number(item.width || 0) * Number(item.height || 0), 0);
  const envelopeVolume = length * width * stackHeight;
  if (Number.isFinite(envelopeVolume) && envelopeVolume > 0 && Number.isFinite(cartonVolume)) {
    return Math.max(0, Math.min(100, (cartonVolume / envelopeVolume) * 100));
  }
  return 0;
}

const blankLine = () => ({
  id: crypto.randomUUID(),
  source_ref: "Manual entry",
  original_sku: "",
  quantity: 1,
  packing_state: "flatpack",
  description: "",
  length: "",
  width: "",
  height: "",
  weight: "",
  category: "",
});

const defaultSettings = {
  standard_pallet: { name: "standard", display_name: "Standard 48 × 40", length: 48, width: 40, deck_height: 6, max_total_height: 48, max_total_weight: 1200, tare_weight: 45 },
  long_pallet: { name: "long", display_name: "Long 96 × 40", length: 96, width: 40, deck_height: 6, max_total_height: 48, max_total_weight: 1600, tare_weight: 80 },
  flatpack_max_total_height: 66,
  assembled_max_total_height: 80,
  general_overhang_per_side: 2,
  flatpack_cartons_on_edge: true,
  truckload_standard_max_weight: 2400,
  truckload_long_max_weight: 1600,
  long_items_position: "auto",
  load_board_weight: 35,
  load_board_thickness: 0.5,
  max_load_boards_per_pallet: 2,
  minimum_support_ratio: 0.75,
  fuzzy_match_threshold: 88,
  fragile_heavy_threshold: 25,
  max_pallets: 50,
  accessory_only_min_freight_height: 24,
  support_height_tolerance: 0.45,
  require_dunnage_for_height_gap: true,
  require_accessory_dunnage: false,
  approved_accessory_overhang_per_side: 1,
  same_sku_bundle_limit: 5,
  quote_origin: { name: "", street: "", city: "", state: "", postal_code: "", phone: "", email: "" },
  default_commodity_description: "Kitchen cabinets and accessories",
  default_nmfc: "",
  sku_prefixes_to_ignore: ["MB", "WHS", "ESS", "GRS", "MSL", "MTS", "ONB", "SBS", "SDW", "SSG", "RTA", "HC"],
};

const emptyAddress = () => ({ name: "", street: "", city: "", state: "", postal_code: "", phone: "", email: "" });
const emptyShippingInfo = () => ({
  confirmed: false,
  customer_references: [],
  origin: emptyAddress(),
  destination: emptyAddress(),
  accessorials: { residential: false, liftgate: false, appointment_delivery: true, delivery_call_ahead: false, limited_access: false, inside_delivery: false },
  commodity_description: "Kitchen cabinets and accessories",
  freight_class: "",
  nmfc: "",
  pickup_date: "",
  source_provenance: [],
});

function versionAtLeast(version, minimum) {
  const current = String(version || "").split(".").map((part) => Number(part) || 0);
  const required = minimum.split(".").map((part) => Number(part) || 0);
  for (let index = 0; index < Math.max(current.length, required.length); index += 1) {
    if ((current[index] || 0) > (required[index] || 0)) return true;
    if ((current[index] || 0) < (required[index] || 0)) return false;
  }
  return true;
}

function bridgeConnectionState(bridge) {
  if (!bridge.connected) return "not-installed";
  return versionAtLeast(bridge.version, MINIMUM_BUILD2QUOTE_VERSION) ? "connected" : "update-required";
}

function optionalPositiveNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  return Number(value);
}

function validateLines(sourceLines) {
  const issues = [];
  sourceLines.filter((line) => line.original_sku.trim()).forEach((line, index) => {
    const quantity = Number(line.quantity);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) {
      issues.push(`Line ${index + 1} quantity must be a whole number between 1 and 10,000.`);
    }
    ["length", "width", "height", "weight"].forEach((field) => {
      const value = optionalPositiveNumber(line[field]);
      if (value !== null && (!Number.isFinite(value) || value <= 0)) {
        issues.push(`Line ${index + 1} ${field} must be greater than zero when supplied.`);
      }
    });
  });
  return issues;
}

function suggestedHandlingProfile(item) {
  const text = `${item.sku} ${item.description}`.toLowerCase();
  const longest = Math.max(item.flatpack_length || 0, item.flatpack_width || 0, item.flatpack_height || 0);
  const normalizedSku = String(item.sku || "").toUpperCase().replaceAll(" ", "");
  if (normalizedSku === "PNL48X96X1/4") {
    return { item_kind: "accessory", packing_role: "base_panel", bundle_max_units: 1, bundle_approved: false, allow_edge_standing: true, max_overhang_per_side: 0 };
  }
  if (/\b(?:pnl|panel)\b/.test(text) && longest >= 72) {
    return { item_kind: "accessory", packing_role: "base_panel", bundle_max_units: 1, bundle_approved: false, allow_edge_standing: false, max_overhang_per_side: 1 };
  }
  return { item_kind: "accessory", packing_role: "top_accessory", bundle_max_units: 5, bundle_approved: true, allow_edge_standing: false, max_overhang_per_side: longest > 48 ? 1 : 0 };
}


// The quote workspace: build a pallet plan from order lines (or typed SKUs), review it and open
// the brokers; or enter measured pallets (Quick Quote). Inside the app shell it receives the
// active company and the orders/shipment the quote is for; on its own (tests) it still works.
export default function App({ mode = "builder", company = null, context = null, onRecorded, onOrderOpen, orderRefresh = 0 }) {
  const view = mode;
  const setView = () => {};
  const [quickId, setQuickId] = useState(null);
  const [quickOpenToken, setQuickOpenToken] = useState(0);
  const [quoteOrders, setQuoteOrders] = useState([]);
  const [orderWarnings, setOrderWarnings] = useState([]);
  const [importReport, setImportReport] = useState(null);
  const [recordedQuote, setRecordedQuote] = useState(null);
  const [pricesQuoteId, setPricesQuoteId] = useState(null);
  const shipmentId = context?.shipmentId || null;
  const catalogPath = company ? `/api/catalog/${company.config.catalog}` : null;
  const settingsPath = company ? `/api/companies/${company.id}/settings` : "/api/settings";
  const [brokerSessions, setBrokerSessions] = useState({});
  const onPrepared = (broker, session) => setBrokerSessions(old => ({ ...old, [broker]: session }));
  const [lines, setLines] = useState([blankLine()]);
  const [shipmentName, setShipmentName] = useState("New shipment");
  const [settings, setSettings] = useState(defaultSettings);
  const [allowFuzzy, setAllowFuzzy] = useState(false);
  // "standard" = LTL pallet builder; "truckload" = dedicated box truck / dry van (option B).
  const [optimizer, setOptimizer] = useState("standard");
  const [result, setResult] = useState(null);
  const [comparisonEstimate, setComparisonEstimate] = useState(null);
  const [builderStage, setBuilderStage] = useState("items");
  const [reviewCandidate, setReviewCandidate] = useState("validated");
  const [selectedPallet, setSelectedPallet] = useState(0);
  const [selectedItem, setSelectedItem] = useState(null);
  const [colorMode, setColorMode] = useState("category");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [backendVersion, setBackendVersion] = useState(null);
  const [reviewMode, setReviewMode] = useState(false);
  const backendReady = backendVersion !== null && versionAtLeast(backendVersion, release.version);
  const [masterItems, setMasterItems] = useState([]);
  const [ignoredRows, setIgnoredRows] = useState([]);
  const [importDocuments, setImportDocuments] = useState([]);
  const [lineMatches, setLineMatches] = useState([]);
  const [aliasResolution, setAliasResolution] = useState(null);
  const [shippingInfo, setShippingInfo] = useState(emptyShippingInfo);
  const [bridgeConnection, setBridgeConnection] = useState({ state: "checking", version: "" });
  const draftRestored = useRef(false);
  const draft = useDraft("builder", { lines, shipmentName, settings, allowFuzzy, optimizer,
    ignoredRows, importDocuments, lineMatches, shippingInfo, quoteOrders }, value => {
    draftRestored.current = true;
    setQuoteOrders(value.quoteOrders || []);
    setLines(value.lines); setShipmentName(value.shipmentName); setSettings(value.settings);
    setAllowFuzzy(value.allowFuzzy); setOptimizer(value.optimizer || "standard"); setIgnoredRows(value.ignoredRows || []);
    setImportDocuments(value.importDocuments || []); setLineMatches(value.lineMatches || []);
    setShippingInfo({ ...value.shippingInfo, confirmed: false }); setResult(null); setComparisonEstimate(null);
    setNotice("Draft restored. Rebuild and review the plan before quoting.");
  });
  useModalFocus(Boolean(aliasResolution), () => { setAliasResolution(null); });

  const slipInputRef = useRef(null);
  const reviewHeadingRef = useRef(null);
  const [focusLineId, setFocusLineId] = useState(null);
  const [showFallback, setShowFallback] = useState(false);

  useEffect(() => {
    if (builderStage === "review") reviewHeadingRef.current?.focus();
  }, [builderStage, result]);

  useEffect(() => {
    if (builderStage !== "items" || !focusLineId) return;
    document.getElementById(`line-${focusLineId}`)?.querySelector(".sku-input")?.focus();
    setFocusLineId(null);
  }, [builderStage, focusLineId, lines]);

  const focusLine = (lineId) => {
    setFocusLineId(lineId);
    setBuilderStage("items");
  };

  const selectReviewCandidate = (candidate) => {
    const nextPlan = candidate === "estimate" ? comparisonEstimate : result;
    setReviewCandidate(candidate);
    setSelectedPallet(0);
    setSelectedItem(nextPlan?.pallets?.[0]?.items?.[0] || null);
  };

  const handleReviewTabKeyDown = (event) => {
    if (!hasComparisonEstimate || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const candidate = event.key === "ArrowRight"
      ? (estimateActive ? "validated" : "estimate")
      : (estimateActive ? "validated" : "estimate");
    document.getElementById(candidate === "estimate" ? "estimate-plan-tab" : "validated-plan-tab")?.focus();
    selectReviewCandidate(candidate);
  };

  useEffect(() => {
    Promise.all([getJson(settingsPath), getJson("/api/health")])
      .then(([savedSettings, health]) => {
        if (!draftRestored.current) setSettings(savedSettings);
        if (!draftRestored.current) setShippingInfo((current) => ({
          ...current,
          origin: { ...current.origin, ...(savedSettings.quote_origin || {}) },
          commodity_description: savedSettings.default_commodity_description || current.commodity_description,
          nmfc: savedSettings.default_nmfc || current.nmfc,
        }));
        setBackendVersion(health.version || "0");
        setReviewMode(Boolean(health.review_mode));
      })
      .catch((requestError) => setError(`Backend connection: ${requestError.message}`));
    pingBuild2QuoteBridge().then((bridge) => setBridgeConnection({
      state: bridgeConnectionState(bridge),
      version: bridge.version || "",
    }));
  }, []);

  const checkBridgeConnection = async () => {
    setBridgeConnection((current) => ({ ...current, state: "checking" }));
    const bridge = await pingBuild2QuoteBridge();
    setBridgeConnection({ state: bridgeConnectionState(bridge), version: bridge.version || "" });
  };

  const hasComparisonEstimate = comparisonEstimate?.status === "review_only" && Array.isArray(comparisonEstimate.pallets);
  const estimateActive = reviewCandidate === "estimate" && hasComparisonEstimate;
  const reviewResult = estimateActive
    ? {
      ...result,
      pallets: comparisonEstimate.pallets || [],
      summary: comparisonEstimate.summary || result.summary,
      warnings: comparisonEstimate.issues || [],
      unpacked: [],
      contained_items: comparisonEstimate.contained_items || [],
      quote_rows: [],
      human_review_required: true,
      disclaimer: comparisonEstimate.label || "Estimate — review required",
    }
    : result;
  const activePallet = reviewResult?.pallets?.[selectedPallet] || null;
  const totalUnits = lines.reduce((sum, line) => sum + (Number(line.quantity) || 0), 0);
  const matchesByLine = useMemo(
    () => new Map(lineMatches.map((match) => [match.line.id, match])),
    [lineMatches],
  );
  const aliasOptions = useMemo(() => {
    if (!aliasResolution) return [];
    const term = aliasResolution.search.trim().toLowerCase();
    if (!term) return aliasResolution.items;
    return aliasResolution.items.filter((item) => (
      item.sku.toLowerCase().includes(term)
      || (item.manufacturer_sku || "").toLowerCase().includes(term)
      || item.description.toLowerCase().includes(term)
    ));
  }, [aliasResolution]);

  const updateLine = (id, field, value) => {
    setLines((current) => current.map((line) => (line.id === id ? { ...line, [field]: value } : line)));
    if (["original_sku", "quantity", "packing_state"].includes(field)) setLineMatches([]);
  };

  const setAllPackingStates = (packingState) => {
    setLines((current) => current.map((line) => ({ ...line, packing_state: packingState })));
    setLineMatches([]);
  };

  const updateShippingAddress = (side, field, value) => {
    setShippingInfo((current) => ({
      ...current,
      confirmed: false,
      [side]: {
        ...current[side],
        [field]: value,
        ...(field === "postal_code" ? { city: "", state: "" } : {}),
      },
    }));
    if (side === "origin") {
      setSettings((current) => ({ ...current, quote_origin: {
        ...(current.quote_origin || emptyAddress()),
        [field]: value,
        ...(field === "postal_code" ? { city: "", state: "" } : {}),
      } }));
    }
  };

  const updateAccessorial = (field, value) => {
    // Residential and limited access are delivery location types: choosing one clears the other.
    const exclusive = value && field === "residential" ? { limited_access: false } : value && field === "limited_access" ? { residential: false } : {};
    setShippingInfo((current) => ({ ...current, confirmed: false, accessorials: { ...current.accessorials, ...exclusive, [field]: value } }));
  };

  const saveQuoteDefaults = async () => {
    const nextSettings = {
      ...settings,
      default_commodity_description: shippingInfo.commodity_description,
      default_nmfc: shippingInfo.nmfc,
      quote_origin: { ...(settings.quote_origin || emptyAddress()), postal_code: shippingInfo.origin.postal_code },
    };
    setSettings(nextSettings);
    await sendJson(settingsPath, "PUT", nextSettings);
    setNotice("Origin ZIP, commodity, and NMFC defaults saved locally.");
  };

  const requestPayload = (sourceLines = lines, saveResult = true) => ({
    shipment_name: shipmentName.trim() || "New shipment",
    lines: sourceLines
      .filter((line) => line.original_sku.trim())
      .map((line) => ({
        ...line,
        quantity: Number(line.quantity),
        length: optionalPositiveNumber(line.length),
        width: optionalPositiveNumber(line.width),
        height: optionalPositiveNumber(line.height),
        weight: optionalPositiveNumber(line.weight),
        category: line.category || null,
      })),
    settings,
    allow_fuzzy_matches: allowFuzzy,
    optimizer,
    save_result: saveResult,
    shipping_info: shippingInfo,
    company_id: company?.id || null,
    order_ids: quoteOrders.map((order) => order.id),
    freight_shipment_id: shipmentId,
  });

  const packingSearch = usePackingJobs(() => requestPayload(lines, false));

  const geometryKey = packingGeometry({ lines, settings, allowFuzzy, optimizer });
  const reviewedGeometry = useRef(null);
  const reviewedResult = useRef(null);
  const resultStale = Boolean(result && reviewedResult.current === result.id && reviewedGeometry.current !== geometryKey);
  useEffect(() => {
    if (result && reviewedResult.current !== result.id) { reviewedResult.current = result.id; reviewedGeometry.current = geometryKey; }
  }, [result]);

  const refreshMatches = async (sourceLines = lines) => {
    if (!sourceLines.some((line) => line.original_sku.trim())) {
      setLineMatches([]);
      return [];
    }
    const matches = await sendJson(
      "/api/shipments/match",
      "POST",
      requestPayload(sourceLines, false),
    );
    setLineMatches(matches);
    return matches;
  };

  // Builder lines that came from stored orders carry "<order number> · <source>" as their
  // source reference; that is how a packed plan is attributed back to each order.
  const orderLinePrefixes = (orders) => orders.map((order) => `${order.order_number} · `);
  const isOrderLine = (line, orders = quoteOrders) => orderLinePrefixes(orders).some((prefix) => String(line.source_ref || "").startsWith(prefix));

  const loadOrders = async (orderList, { replaceLines = true } = {}) => {
    const unique = [...new Map(orderList.map((order) => [order.id, order])).values()];
    if (!unique.length) {
      setQuoteOrders([]); setOrderWarnings([]);
      setLines((current) => {
        const kept = current.filter((line) => !isOrderLine(line));
        return kept.length ? kept : [blankLine()];
      });
      return;
    }
    const payload = await sendJson("/api/orders/packing-lines", "POST", { order_ids: unique.map((order) => order.id) });
    const previous = quoteOrders;
    const manualLines = lines.filter((line) => line.original_sku.trim() && !isOrderLine(line, previous) && !isOrderLine(line, unique));
    const orderLines = payload.lines.map((line) => ({ ...line, id: crypto.randomUUID(), length: line.length ?? "", width: line.width ?? "", height: line.height ?? "", weight: line.weight ?? "", category: "" }));
    const nextLines = replaceLines ? [...orderLines, ...manualLines] : [...lines, ...orderLines];
    setQuoteOrders(unique.map((order) => ({ id: order.id, order_number: order.order_number, customer_name: order.customer_name || "",
      postal_code: order.ship_to?.postal_code || "", status: order.status, issues: order.issues || [],
      unit_count: order.unit_count ?? (order.lines || []).reduce((sum, line) => sum + line.quantity, 0) })));
    setOrderWarnings(payload.warnings || []);
    setLines(nextLines.length ? nextLines : [blankLine()]);
    setLineMatches([]);
    if (!shipmentName.trim() || shipmentName === "New shipment" || previous.length) setShipmentName(payload.suggested_name);
    setShippingInfo((current) => ({
      ...current,
      confirmed: false,
      customer_references: payload.shipping.customer_references,
      destination: payload.shipping.destination?.postal_code
        ? { ...current.destination, ...payload.shipping.destination }
        : current.destination,
      accessorials: { ...current.accessorials, residential: payload.shipping.residential, liftgate: payload.shipping.liftgate },
    }));
    await refreshMatches(nextLines).catch(() => {});
  };

  const loadOrderIds = async (ids) => {
    const orders = await Promise.all(ids.map((id) => getJson(`/api/orders/${id}`).then((trace) => ({ ...trace.order }))));
    await loadOrders(orders);
  };

  const removeOrder = async (orderId) => {
    setBusy(true);
    try { await loadOrders(quoteOrders.filter((order) => order.id !== orderId)); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };

  const reloadOrderLines = async () => {
    if (!quoteOrders.length) return;
    setBusy(true);
    try { await loadOrderIds(quoteOrders.map((order) => order.id)); setNotice("Order lines refreshed from the corrected orders."); }
    catch (requestError) { setError(requestError.message); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    if (!context) return;
    let cancelled = false;
    (async () => {
      try {
        if (context.resultId) { await openShipment(context.resultId); return; }
        if (context.orderIds?.length) {
          setBusy(true);
          setResult(null); setComparisonEstimate(null); setBuilderStage("items"); setRecordedQuote(null);
          await loadOrderIds(context.orderIds);
        }
      } catch (requestError) { if (!cancelled) setError(requestError.message); }
      finally { if (!cancelled) setBusy(false); }
    })();
    return () => { cancelled = true; };
  }, [context?.token]);

  useEffect(() => { if (orderRefresh) reloadOrderLines(); }, [orderRefresh]);

  const importSlips = async (fileList) => {
    const files = [...fileList];
    if (!files.length) return;
    if (company) {
      setBusy(true);
      setError("");
      try {
        const report = await uploadFiles("/api/imports", files, "files", { company_id: company.id });
        setImportReport(report);
        const usable = report.orders.filter((order) => order.status !== "cancelled" && !quoteOrders.some((entry) => entry.id === order.id));
        if (usable.length) await loadOrders([...quoteOrders, ...usable]);
        const { summary } = report;
        setNotice(`${summary.created} new order${summary.created === 1 ? "" : "s"}${summary.existing ? `, ${summary.existing} already on file` : ""} added to this quote.`
          + (summary.failed ? ` ${summary.failed} file${summary.failed === 1 ? " was" : "s were"} not read — see the import notes.` : ""));
        if (report.conflicts?.length) setError(`${report.conflicts.map((conflict) => conflict.order_number).join(", ")} already exist with different lines. The stored version is used; open Orders to review.`);
      } catch (requestError) {
        setError(requestError.message);
      } finally {
        setBusy(false);
      }
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await uploadFiles("/api/import/packing-slips", files);
      const imported = payload.lines.map((line) => ({ packing_state: "flatpack", ...line }));
      const retained = lines.filter((line) => line.original_sku.trim());
      const nextLines = [...retained, ...imported];
      setLines(nextLines);
      setIgnoredRows((current) => [...current, ...(payload.ignored_rows || [])]);
      setImportDocuments((current) => [...current, ...(payload.documents || [])]);
      if (payload.suggested_shipment_name) setShipmentName(payload.suggested_shipment_name);
      const suggestions = payload.shipping_suggestions || [];
      if (suggestions.length) {
        const references = [...new Set(suggestions.flatMap((suggestion) => suggestion.customer_references || []))];
        const zipCandidates = [...new Set(suggestions.flatMap((suggestion) => (
          suggestion.destination_zip_candidates || (suggestion.destination?.postal_code ? [suggestion.destination.postal_code] : [])
        )))];
        const destinationZip = zipCandidates.length === 1 ? zipCandidates[0] : "";
        setShippingInfo((current) => ({
          ...current,
          confirmed: false,
          customer_references: references.length ? references : current.customer_references,
          destination: {
            ...current.destination,
            ...(current.destination.postal_code !== destinationZip ? { city: "", state: "" } : {}),
            postal_code: destinationZip,
          },
          accessorials: {
            ...current.accessorials,
            residential: suggestions.some((suggestion) => suggestion.accessorials?.residential),
            liftgate: suggestions.some((suggestion) => suggestion.accessorials?.liftgate),
          },
          source_provenance: [
            ...current.source_provenance,
            ...suggestions.flatMap((suggestion) => suggestion.source_provenance || []),
          ],
        }));
      }
      const importWarnings = suggestions.flatMap((suggestion) => suggestion.warnings || []);
      setNotice(
        `${payload.accepted_count} line${payload.accepted_count === 1 ? "" : "s"} imported; `
        + `${payload.ignored_count} non-item row${payload.ignored_count === 1 ? "" : "s"} ignored.`
        + (importWarnings.length ? ` ${importWarnings.join(" ")}` : ""),
      );
      await refreshMatches(nextLines);
      if (payload.errors.length) setError(payload.errors.map((entry) => `${entry.file}: ${entry.error}`).join(" · "));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const runPacking = async () => {
    if (!lines.some((line) => line.original_sku.trim())) {
      setError("Add at least one SKU before building the pallet plan.");
      return;
    }
    const validationIssues = validateLines(lines);
    if (validationIssues.length) {
      setError(validationIssues.slice(0, 4).join(" · "));
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const packed = await packingSearch.search(requestPayload(lines, false));
      if (!packed) return;
      const { comparison_estimate: _comparisonEstimate, ...validatedResult } = packed;
      setResult(validatedResult);
      setComparisonEstimate(packingSearch.getComparisonEstimate());
      setReviewCandidate("validated");
      setSelectedPallet(0);
      setSelectedItem(validatedResult.pallets[0]?.items[0] || null);
      setBuilderStage("review");
      if (company && validatedResult.id) {
        getJson(`/api/quotes/for-source?packing_result_id=${validatedResult.id}`).then((quote) => {
          setRecordedQuote(quote);
          if (quote) onRecorded?.(quote);
        }).catch(() => setRecordedQuote(null));
      }
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const handleSelectItem = useCallback((item) => {
    setSelectedItem(item);
  }, []);

  const openShipment = async (id) => {
    setBusy(true);
    try {
      const [saved, input] = await Promise.all([
        getJson(`/api/shipments/${id}`), getJson(`/api/shipments/${id}/request`),
      ]);
      reviewedResult.current = null;
      draftRestored.current = true;
      setLines(input.lines);
      setShipmentName(input.shipment_name);
      setSettings(input.settings);
      setAllowFuzzy(Boolean(input.allow_fuzzy_matches));
      setOptimizer(input.optimizer || "standard");
      setLineMatches(saved.match_review || []);
      setImportDocuments([]);
      setIgnoredRows([]);
      const { comparison_estimate: _comparisonEstimate, ...savedResult } = saved;
      setResult(savedResult);
      setComparisonEstimate(null);
      setShippingInfo(saved.shipping_info || emptyShippingInfo());
      setSelectedPallet(0);
      const firstItem = savedResult.pallets[0]?.items[0] || null;
      setSelectedItem(firstItem);
      setReviewCandidate("validated");
      setBuilderStage("review");
      setQuoteOrders([]);
      setOrderWarnings([]);
      if (company) getJson(`/api/quotes/for-source?packing_result_id=${id}`).then(async (quote) => {
        setRecordedQuote(quote);
        if (quote?.orders?.length) {
          const traces = await Promise.all(quote.orders.map((order) => getJson(`/api/orders/${order.id}`)));
          setQuoteOrders(traces.map(({ order }) => ({ id: order.id, order_number: order.order_number, customer_name: order.customer_name,
            postal_code: order.ship_to?.postal_code || "", status: order.status, issues: order.issues || [], unit_count: order.unit_count })));
        }
      }).catch(() => setRecordedQuote(null));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const launchPrepared = async (handoffs, shipment) => {
    let ambiguousError = null;
    try {
      await openPreparedBrokerTabs(handoffs);
    } catch (launchError) {
      if (launchError.ambiguous) ambiguousError = launchError;
      else {
        await Promise.allSettled(handoffs.map(handoff => sendJson(`/api/quote-handoffs/${handoff.request_id}/cancel`, "POST")));
        throw launchError;
      }
    }
    handoffs.forEach(handoff => onPrepared(handoff.broker, {
      ...handoff, shipment_name: shipment.shipment_name, source_revision: shipment.revision,
    }));
    if (ambiguousError) throw ambiguousError;
  };

  const prepareBroker = async (broker) => {
    if (estimateActive) { setError("Broker handoff is disabled for review-only estimates."); return; }
    if (!result || resultStale) { setError("Rebuild the changed shipment before quoting."); return; }
    if (!backendReady) { setError(`Restart ${APP_NAME} to load the current backend before autofill.`); return; }
    setBusy(true);
    setError("");
    try {
      const saved = await sendJson(`/api/shipments/${result.id}/shipping-info`, "PUT", shippingInfo);
      setResult(saved);
      const prepare = async (replace_active = false) => broker === "all"
        ? await sendJson("/api/quote-handoffs/prepare-all", "POST", { shipment_id: result.id, replace_active })
        : { handoffs: [{ ...await sendJson(`/api/quote-handoffs/${broker}/prepare`, "POST", { shipment_id: result.id, replace_active }), broker }] };
      let response;
      try {
        response = await prepare();
      } catch (prepareError) {
        if (prepareError.status !== 409 || !window.confirm(`${prepareError.message}\n\nCancel the active handoff and open a replacement?`)) throw prepareError;
        response = await prepare(true);
      }
      await launchPrepared(response.handoffs, saved);
      setNotice(`${broker === "all" ? "All broker tabs" : broker.toUpperCase()} opened beside ${APP_NAME}. Sign in where needed; autofill will resume automatically.`);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const copyQuoteSummary = async () => {
    if (!result) return;
    if (estimateActive) { setError("Broker measurements are unavailable for review-only estimates."); return; }
    if (resultStale) { setError("Rebuild before copying broker measurements."); return; }
    const rows = (result.quote_rows || []).map(row => `P${row.pallet}: ${row.dimensions}, ${row.weight} lb`);
    const services = Object.entries(accessorialLabels).filter(([field]) => shippingInfo.accessorials?.[field]).map(([, label]) => label);
    await navigator.clipboard.writeText([
      shippingInfo.customer_references.join(", "),
      `Origin ZIP: ${shippingInfo.origin.postal_code}`,
      `Destination ZIP: ${shippingInfo.destination.postal_code}`,
      `Services: ${services.length ? services.join(", ") : "none"}`,
      ...rows,
    ].join("\n"));
    setNotice("Broker-ready pallet summary copied.");
  };

  const openAliasResolution = async (match) => {
    setBusy(true);
    setError("");
    try {
      const items = masterItems.length ? masterItems : await getJson(catalogPath ? `${catalogPath}/items` : "/api/item-master");
      setMasterItems(items);
      setAliasResolution({ match, items, search: "" });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  const saveAliasResolution = async (targetItem) => {
    if (!aliasResolution) return;
    setBusy(true);
    setError("");
    try {
      await sendJson(catalogPath ? `${catalogPath}/aliases` : "/api/sku-aliases", "POST", {
        alias_sku: aliasResolution.match.line.original_sku,
        target_item_id: targetItem.id,
      });
      setAliasResolution(null);
      await refreshMatches(lines);
      setNotice(
        `${aliasResolution.match.line.original_sku} will now match ${targetItem.sku} on future imports.`,
      );
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(""), 8000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!context || mode !== "quick") return;
    setQuickId(context.manualId || null);
    setQuickOpenToken((value) => value + 1);
  }, [context?.token, mode]);

  const filledLines = lines.filter((line) => line.original_sku.trim());
  const hasItems = filledLines.length > 0;
  const matchCounts = lineMatches.reduce((counts, match) => {
    if (match.match_status === "unmatched" || match.match_status === "fuzzy_review") counts.review += 1;
    else counts.matched += 1;
    return counts;
  }, { matched: 0, review: 0 });
  const fallbackForced = matchCounts.review > 0 || lines.some((line) => ["length", "width", "height", "weight"].some((field) => line[field] !== "" && line[field] !== null && line[field] !== undefined));
  const fallbackVisible = showFallback || fallbackForced;
  const planReady = Boolean(result) && freightReady(result);
  const zipsEntered = Boolean(String(shippingInfo.origin.postal_code || "").trim() && String(shippingInfo.destination.postal_code || "").trim());
  const bridgeOk = bridgeConnection.state === "connected";
  const canSendBrokers = !estimateActive && !busy && !resultStale && planReady && shippingInfo.confirmed && bridgeOk;
  const brokerBlockedReason = estimateActive
    ? "Broker actions are off for review-only estimates. Switch to the validated plan."
    : resultStale ? "The shipment changed since this plan was built. Rebuild first."
      : !planReady ? "Resolve every blocker in the plan before quoting."
        : !shippingInfo.confirmed ? "Tick the review box above to unlock broker autofill."
          : !bridgeOk ? "Connect the Build2Quote Bridge to open broker tabs." : "";
  const showJobCard = Boolean(packingSearch.job) && (packingSearch.running || !packingSearch.job.candidate
    || (packingSearch.job.blockers || []).length > 0
    || ["input_blocked", "physically_blocked", "timed_out", "software_error"].includes(packingSearch.job.outcome_category));
  const accessorialLabels = {
    residential: "Residential delivery",
    liftgate: "Liftgate delivery",
    appointment_delivery: "Appointment delivery",
    delivery_call_ahead: "Delivery call ahead",
    limited_access: "Limited access location",
    inside_delivery: "Inside delivery",
  };
  const exportsEnabled = !estimateActive && planReady && !resultStale;
  const updateSetting = (field, value) => setSettings((current) => ({ ...current, [field]: value }));

  return (
    <div className="quote-workspace">
      {!company && reviewMode && <div className="app-banner app-banner-info" role="status"><CircleHelp size={16} />Review build — changes here do not affect live shipments.</div>}
      {!company && backendVersion !== null && !backendReady && <div className="app-banner app-banner-danger" role="alert">
        <AlertTriangle size={16} /><span><b>Restart required.</b> Version {backendVersion} is still running in the background. Use Start menu › {APP_NAME} Tools › Stop {APP_NAME}, open {APP_NAME} again, then refresh this page.</span>
      </div>}

      <div className="toast-region" aria-live="polite">
        {error && <div className="toast toast-danger" role="alert">
          <AlertTriangle size={18} /><span>{error}</span>
          <button type="button" className="icon-btn" onClick={() => setError("")} aria-label="Dismiss"><X size={16} /></button>
        </div>}
        {notice && !error && <div className="toast toast-ok">
          <Check size={18} /><span>{notice}</span>
          <button type="button" className="icon-btn" onClick={() => setNotice("")} aria-label="Dismiss"><X size={16} /></button>
        </div>}
      </div>

      <div hidden={view !== "quick"}><QuickQuote settings={settings} quoteId={quickId} openToken={quickOpenToken}
        company={company} context={context}
        backendReady={backendReady}
        bridgeConnection={bridgeConnection} checkBridge={checkBridgeConnection} onPrepared={onPrepared} launchPrepared={launchPrepared}
        brokerSessions={brokerSessions}
        onDefaults={async info => {
          const latest = await getJson(settingsPath);
          const next = { ...latest, quote_origin: { ...latest.quote_origin, city: info.origin.city || "", state: info.origin.state || "", postal_code: info.origin.postal_code }, default_commodity_description: info.commodity_description, default_nmfc: info.nmfc };
          await sendJson(settingsPath, "PUT", next);
          setSettings(old => ({ ...old, quote_origin: next.quote_origin, default_commodity_description: next.default_commodity_description, default_nmfc: next.default_nmfc }));
        }} /></div>
      <BrokerProgress sessions={brokerSessions} setSessions={setBrokerSessions} />

      {view === "builder" && (
        <main className="page builder-page">
          <section className="page-header">
            <div className="page-header-main">
              <label className="title-field">
                <span className="sr-only">Shipment name</span>
                <input className="shipment-title" value={shipmentName} onChange={(event) => setShipmentName(event.target.value)} aria-label="Shipment name" />
                <PencilLine size={16} aria-hidden="true" />
              </label>
              <ol className="stepper" aria-label="Workflow steps">
                <li className={builderStage === "items" ? "is-current" : "is-done"}><span>1</span>Add items</li>
                <li className={builderStage === "review" ? "is-done" : busy ? "is-current" : ""}><span>2</span>Build pallets</li>
                <li className={builderStage === "review" ? "is-current" : ""}><span>3</span>Review &amp; quote</li>
              </ol>
            </div>
            <div className="page-header-actions">
              <DraftBar draft={draft} />
              {builderStage === "items" && result && <button type="button" className="btn btn-secondary" onClick={() => { setReviewCandidate("validated"); setBuilderStage("review"); }}>
                {resultStale ? "View previous plan" : "Review pallet plan"}
              </button>}
              {builderStage === "items" ? <button type="button" className="btn btn-primary btn-lg" disabled={busy || !hasItems} onClick={runPacking}>
                {busy ? <LoaderCircle className="spin" size={18} /> : <Rotate3d size={18} />}{busy ? "Building pallet plan…" : "Build pallet plan"}
              </button> : <button type="button" className="btn btn-secondary" onClick={() => setBuilderStage("items")}>
                <PencilLine size={16} />Edit shipment
              </button>}
            </div>
          </section>

          {showJobCard && <section className={`job-card ${packingSearch.running ? "is-running" : "is-stopped"}`} aria-live="polite">
            <div className="job-card-head">
              {packingSearch.running ? <LoaderCircle className="spin" size={20} /> : <AlertTriangle size={20} />}
              <div>
                <h3>{packingSearch.running ? "Building the pallet plan" : "No plan was produced"}</h3>
                <p>{packingSearch.job.message}{packingSearch.job.elapsed_seconds !== undefined ? ` — ${packingSearch.job.elapsed_seconds}s elapsed` : ""}</p>
                {packingSearch.job.candidate && <p>Best so far: {packingSearch.job.candidate.pallets.length} pallet{packingSearch.job.candidate.pallets.length === 1 ? "" : "s"}.</p>}
              </div>
              {packingSearch.running && <button type="button" className="btn btn-secondary btn-sm" onClick={() => packingSearch.cancel().catch(e => setError(e.message))}>Stop search</button>}
            </div>
            <PackingDiagnostics job={packingSearch.job} onInspect={block => {
              const line = lines.find(line => block.line_ids?.includes(line.id) || line.original_sku === block.sku);
              if (line) focusLine(line.id);
            }} />
          </section>}

          {shipmentId && <div className="callout callout-info" role="status">
            <Truck size={18} /><span><b>Combined plan for a shipment.</b> When this plan is accepted it becomes the shipment's combined quote; each order's own earlier quotes stay unchanged.</span>
          </div>}

          {builderStage === "items" && result && resultStale && <div className="callout callout-warn" role="alert">
            <AlertTriangle size={18} /><span>The last pallet plan is out of date. Rebuild before exporting or opening brokers.</span>
          </div>}

          {builderStage === "items" && <div className="items-layout">
            <Card
              className="items-card"
              title="Shipment items"
              description={hasItems ? `${filledLines.length} line${filledLines.length === 1 ? "" : "s"}, ${totalUnits} unit${totalUnits === 1 ? "" : "s"}` : "Import packing slips or type SKUs to start."}
              actions={<>
                <input ref={slipInputRef} hidden multiple type="file" accept=".csv,.tsv,.xlsx,.xlsm,.pdf,.zip" onChange={(event) => { importSlips(event.target.files); event.target.value = ""; }} />
                <button type="button" className="btn btn-secondary" onClick={() => slipInputRef.current?.click()}><Upload size={16} />Import slips</button>
                <button type="button" className="btn btn-secondary" disabled={busy || !hasItems} onClick={() => refreshMatches().catch((e) => setError(e.message))}><PackageCheck size={16} />Check matches</button>
              </>}
            >
              <div
                className={`dropzone ${dragging ? "is-dragging" : ""} ${hasItems ? "is-compact" : ""}`}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); slipInputRef.current?.click(); } }}
                onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => { event.preventDefault(); setDragging(false); importSlips(event.dataTransfer.files); }}
                onClick={() => slipInputRef.current?.click()}
              >
                <FileUp size={hasItems ? 18 : 28} aria-hidden="true" />
                <span className="dropzone-text">
                  <b>{hasItems ? "Drop more packing slips to add their orders" : "Drop packing slips or invoices here"}</b>
                  <small>{company ? "PDF, CSV, Excel or ZIP — several at once. Each order is saved; the same file is never imported twice." : "CSV, Excel or PDF — or click to browse"}</small>
                </span>
              </div>

              {quoteOrders.length > 0 && <div className="quote-orders" aria-label="Orders in this quote">
                <div className="quote-orders-head">
                  <b>{quoteOrders.length} order{quoteOrders.length === 1 ? "" : "s"} in this quote</b>
                  <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={reloadOrderLines} title="Reload lines after correcting an order">Refresh lines</button>
                </div>
                <ul className="order-chips">
                  {quoteOrders.map((order) => (
                    <li key={order.id} className={`order-chip ${order.status === "needs_review" ? "is-warn" : ""}`}>
                      <button type="button" className="order-chip-main" onClick={() => onOrderOpen?.(order.id)} title="Review or correct this order">
                        <b>{order.order_number}</b>
                        <small>{[order.customer_name, order.postal_code && `ZIP ${order.postal_code}`, `${order.unit_count} units`].filter(Boolean).join(" · ")}</small>
                        {order.status === "needs_review" && <small className="warn-text">{order.issues[0] || "Needs review"}</small>}
                        {order.status === "reference_only" && <small className="muted">No lines imported</small>}
                      </button>
                      <button type="button" className="icon-btn" aria-label={`Remove ${order.order_number} from this quote`} disabled={busy} onClick={() => removeOrder(order.id)}><Unlink size={14} /></button>
                    </li>
                  ))}
                </ul>
                {orderWarnings.length > 0 && <ul className="order-warnings">{orderWarnings.map((warning) => <li key={warning}><AlertTriangle size={13} />{warning}</li>)}</ul>}
              </div>}

              {importReport && importReport.files.some((file) => !["imported", "duplicate"].includes(file.status) || (file.ignored_rows || []).length) && <details className="disclosure import-report">
                <summary><FileSpreadsheet size={15} /><span><b>Import notes</b> — {importReport.files.length} file{importReport.files.length === 1 ? "" : "s"} read</span></summary>
                <ul className="plain-list">
                  {importReport.files.map((file) => <li key={`${file.file}-${file.sha256 || ""}`}>
                    <span><b>{file.file}</b><small>{file.status}{file.adapter ? ` · ${file.adapter.replaceAll("_", " ")}` : ""}</small></span>
                    <span>{[...(file.messages || []), ...((file.ignored_rows || []).map((row) => `${row.original_sku || "Blank"}: ${row.reason}`))].join(" · ") || "OK"}</span>
                  </li>)}
                </ul>
              </details>}

              {(importDocuments.length > 0 || ignoredRows.length > 0) && <div className="import-notes">
                {importDocuments.length > 0 && (
                  <details className="disclosure">
                    <summary><FileSpreadsheet size={15} /><span><b>{importDocuments.length} document{importDocuments.length === 1 ? "" : "s"} recognized</b> in the imported PDFs</span></summary>
                    <ul className="plain-list">
                      {importDocuments.map((document, index) => <li key={`${document.primary_reference || "document"}-${index}`}>
                        <span><b>{document.primary_reference || "Unnumbered document"}</b><small>{document.document_type.replaceAll("_", " ")}, page{document.pages.length === 1 ? "" : "s"} {document.pages.join(", ")}</small></span>
                        <span>{document.packing_state} — ZIP {(document.destination_zips || []).join(", ") || "needs review"}</span>
                      </li>)}
                    </ul>
                  </details>
                )}
                {ignoredRows.length > 0 && (
                  <details className="disclosure">
                    <summary><AlertTriangle size={15} /><span><b>{ignoredRows.length} non-item row{ignoredRows.length === 1 ? "" : "s"} skipped</b> during import</span></summary>
                    <ul className="plain-list">
                      {ignoredRows.map((row, index) => <li key={`${row.source_ref}-${row.row}-${index}`}>
                        <span><b>{row.original_sku || "Blank item"}</b><small>{row.source_ref}, row {row.row}</small></span>
                        <span>{row.reason}</span>
                      </li>)}
                    </ul>
                  </details>
                )}
              </div>}

              <div className="table-toolbar">
                <div className="segmented" role="group" aria-label="Set packing state for all shipment lines">
                  <span className="segmented-label">Set all lines to</span>
                  <button type="button" onClick={() => setAllPackingStates("flatpack")}>Flatpack</button>
                  <button type="button" onClick={() => setAllPackingStates("assembled")}>Assembled</button>
                </div>
                {lineMatches.length > 0 && <div className="match-summary">
                  <StatusPill severity="ok">{matchCounts.matched} matched</StatusPill>
                  {matchCounts.review > 0 && <StatusPill severity="warn">{matchCounts.review} need a choice</StatusPill>}
                </div>}
              </div>

              <div className="table-wrap">
                <table className="data-table item-table">
                  <thead>
                    <tr>
                      <th scope="col">Order / source</th>
                      <th scope="col">SKU</th>
                      <th scope="col" className="num">Qty</th>
                      <th scope="col">Packing</th>
                      <th scope="col">Description</th>
                      <th scope="col">Item master match</th>
                      {fallbackVisible && <th scope="col" colSpan={4} className="th-group" title="Only used when a SKU has no item-master match">Fallback L × W × H (in), weight (lb)</th>}
                      <th scope="col"><span className="sr-only">Remove</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => {
                      const match = matchesByLine.get(line.id);
                      const name = line.original_sku || "new row";
                      return (
                        <tr key={line.id} id={`line-${line.id}`}>
                          <td><input className="cell-input" aria-label={`Item ${name} source ref`} value={line.source_ref} onChange={(event) => updateLine(line.id, "source_ref", event.target.value)} /></td>
                          <td><input className="cell-input sku-input" aria-label={`Item ${name} original sku`} value={line.original_sku} placeholder="e.g. WHS-B30" onChange={(event) => updateLine(line.id, "original_sku", event.target.value)} /></td>
                          <td className="num"><input className="cell-input qty-input" type="number" min="1" aria-label={`Item ${name} quantity`} value={line.quantity} onChange={(event) => updateLine(line.id, "quantity", event.target.value)} /></td>
                          <td><select className="cell-input" aria-label={`Item ${name} packing state`} value={line.packing_state || "flatpack"} onChange={(event) => updateLine(line.id, "packing_state", event.target.value)}><option value="flatpack">Flatpack</option><option value="assembled">Assembled</option></select></td>
                          <td><input className="cell-input" aria-label={`Item ${name} description`} value={line.description} placeholder="Optional" onChange={(event) => updateLine(line.id, "description", event.target.value)} /></td>
                          <td className="match-cell">
                            {match ? (
                              <div className="match-stack">
                                <StatusPill severity={match.match_status === "unmatched" ? "danger" : match.match_status === "fuzzy_review" ? "warn" : match.match_status === "confirmed_alias" ? "info" : "ok"}>
                                  {match.match_status.replaceAll("_", " ")}
                                </StatusPill>
                                {match.matched_item && <small>{match.matched_item.sku}</small>}
                                {["fuzzy_review", "unmatched"].includes(match.match_status) && (
                                  <button type="button" className="link-btn" onClick={() => openAliasResolution(match)}>Choose item</button>
                                )}
                              </div>
                            ) : <span className="muted">{line.original_sku.trim() ? "Not checked" : "—"}</span>}
                          </td>
                          {fallbackVisible && ["length", "width", "height", "weight"].map((field) => <td key={field} className="num"><input aria-label={`Item ${name} ${field}`} className="cell-input dim-input" type="number" min="0" step="0.1" value={line[field] ?? ""} placeholder="—" onChange={(event) => updateLine(line.id, field, event.target.value)} /></td>)}
                          <td><button type="button" className="icon-btn icon-btn-danger" title="Remove row" aria-label={`Remove ${name}`} onClick={() => { setLines((current) => current.filter((entry) => entry.id !== line.id)); setLineMatches((current) => current.filter((entry) => entry.line.id !== line.id)); }}><Trash2 size={15} /></button></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="table-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setLines((current) => [...current, blankLine()])}><Plus size={16} />Add row</button>
                <div className="table-footer-right">
                  <p className="hint"><CircleHelp size={14} />Fallback sizes are only used when a SKU has no item-master match; those lines are always flagged for review.</p>
                  {!fallbackForced && <button type="button" className="btn btn-ghost btn-sm" aria-pressed={showFallback} onClick={() => setShowFallback((value) => !value)}><Ruler size={14} />{showFallback ? "Hide fallback sizes" : "Enter fallback sizes"}</button>}
                </div>
              </div>
            </Card>

            <aside className="items-aside">
              <Card className="limits-card" title="Warehouse limits" description="Used for this build. Freight totals include the pallet.">
                <div className="builder-choice">
                  <div className="segmented" role="group" aria-label="Pallet builder">
                    <button type="button" className={optimizer === "standard" ? "active" : ""} aria-pressed={optimizer === "standard"} onClick={() => setOptimizer("standard")}>Standard LTL</button>
                    <button type="button" className={optimizer === "truckload" ? "active" : ""} aria-pressed={optimizer === "truckload"} onClick={() => setOptimizer("truckload")}><Truck size={14} aria-hidden="true" />Dedicated truck</button>
                  </div>
                  <p className="hint">{optimizer === "truckload"
                    ? "Bulk order on a box truck or dry van: least truck floor, pallets two across, heavier 48 × 40 pallets."
                    : "LTL freight: fewest pallets, then the smallest decks."}</p>
                </div>
                <dl className="limit-summary">
                  <div><dt>Flatpack max height</dt><dd>{settings.flatpack_max_total_height ?? 66} in</dd></div>
                  <div><dt>Assembled max height</dt><dd>{settings.assembled_max_total_height ?? 80} in</dd></div>
                  <div><dt>Overhang per side</dt><dd>{settings.general_overhang_per_side ?? 2} in</dd></div>
                  <div><dt>Minimum support</dt><dd>{Math.round(settings.minimum_support_ratio * 100)}%</dd></div>
                  <div><dt>48 × 40 max weight</dt><dd>{formatWeight(optimizer === "truckload" ? settings.truckload_standard_max_weight ?? 2400 : settings.standard_pallet.max_total_weight)}</dd></div>
                  <div><dt>96 × 40 max weight</dt><dd>{formatWeight(optimizer === "truckload" ? settings.truckload_long_max_weight ?? 1600 : settings.long_pallet.max_total_weight)}</dd></div>
                  <div><dt>Fuzzy matches</dt><dd>{allowFuzzy ? "Allowed" : "Off"}</dd></div>
                </dl>
                <details className="limits-editor">
                  <summary className="btn btn-secondary btn-block"><SlidersHorizontal size={16} />Adjust limits</summary>
                  <div className="limits-group">
                    <h3>Physical limits</h3>
                    <NumberField label="Flatpack max height" hint="Total freight height, deck included" unit="in" value={settings.flatpack_max_total_height ?? 66} min={12} onChange={(value) => updateSetting("flatpack_max_total_height", value)} ariaLabel="Flatpack maximum total height" />
                    <NumberField label="Assembled max height" hint="When a pallet carries any assembled carton" unit="in" value={settings.assembled_max_total_height ?? 80} min={12} onChange={(value) => updateSetting("assembled_max_total_height", value)} ariaLabel="Assembled maximum total height" />
                    <NumberField label="Overhang allowed" hint="Per side, only when a carton needs it" unit="in" value={settings.general_overhang_per_side ?? 2} min={0} step={0.5} onChange={(value) => updateSetting("general_overhang_per_side", value)} ariaLabel="Overhang allowed per side" />
                    <NumberField label="48 × 40 max weight" hint={`${settings.standard_pallet.tare_weight} lb pallet tare`} unit="lb" value={settings.standard_pallet.max_total_weight} min={100} onChange={(value) => setSettings((current) => ({ ...current, standard_pallet: { ...current.standard_pallet, max_total_weight: value } }))} ariaLabel="Standard pallet max weight" />
                    <NumberField label="96 × 40 max weight" hint={`${settings.long_pallet.tare_weight} lb pallet tare`} unit="lb" value={settings.long_pallet.max_total_weight} min={100} onChange={(value) => setSettings((current) => ({ ...current, long_pallet: { ...current.long_pallet, max_total_weight: value } }))} ariaLabel="Long pallet max weight" />
                    <NumberField label="Dedicated truck: 48 × 40 max" hint="Weight limit for the dedicated-truck builder" unit="lb" value={settings.truckload_standard_max_weight ?? 2400} min={100} onChange={(value) => updateSetting("truckload_standard_max_weight", Math.min(5000, value))} ariaLabel="Dedicated truck standard pallet max weight" />
                    <NumberField label="Dedicated truck: 96 × 40 max" hint="Weight limit for the dedicated-truck builder" unit="lb" value={settings.truckload_long_max_weight ?? 1600} min={100} onChange={(value) => updateSetting("truckload_long_max_weight", Math.min(5000, value))} ariaLabel="Dedicated truck long pallet max weight" />
                    <NumberField label="Minimum support" hint="Prevents floating or bridging boxes" unit="%" value={Math.round(settings.minimum_support_ratio * 100)} min={50} step={5} onChange={(value) => updateSetting("minimum_support_ratio", Math.min(100, value) / 100)} ariaLabel="Minimum support ratio" />
                  </div>
                  <div className="limits-group">
                    <h3>Optimizer preferences</h3>
                    <p className="hint">These guide ranking; they never waive the physical limits.</p>
                    <label className="limit-field">
                      <span className="limit-field-text"><b>Long items on 96 × 40</b><small>Panels, pantry boxes and trim</small></span>
                      <select aria-label="Long item position" value={settings.long_items_position || "auto"} onChange={(event) => updateSetting("long_items_position", event.target.value)}>
                        <option value="auto">Best of bottom or top</option><option value="bottom">Bottom platform</option><option value="top">On top</option>
                      </select>
                    </label>
                    <NumberField label="Load board weight" hint="96 × 40 × ½ in board, added to tare when used" unit="lb" value={settings.load_board_weight ?? 35} min={0} onChange={(value) => updateSetting("load_board_weight", value)} ariaLabel="Load board weight" />
                    <NumberField label="Accessory-only threshold" hint="Height before preferring a dedicated long pallet" unit="in" value={settings.accessory_only_min_freight_height} min={0} onChange={(value) => updateSetting("accessory_only_min_freight_height", value)} ariaLabel="Accessory only freight height threshold" />
                    <NumberField label="Near-level tolerance" hint="Height gap allowed only with marked dunnage" unit="in" value={settings.support_height_tolerance} min={0} step={0.05} onChange={(value) => updateSetting("support_height_tolerance", value)} ariaLabel="Support height tolerance" />
                    <NumberField label="Approved accessory overhang" hint="Needs a reviewed item rule" unit="in" value={settings.approved_accessory_overhang_per_side} min={0} step={0.25} onChange={(value) => updateSetting("approved_accessory_overhang_per_side", value)} ariaLabel="Approved accessory overhang" />
                    <label className="check-field"><input type="checkbox" checked={settings.flatpack_cartons_on_edge ?? true} onChange={(event) => updateSetting("flatpack_cartons_on_edge", event.target.checked)} /><span><b>Stand flatpack cabinet cartons on edge to fill gaps</b><small>Only in deck strips too narrow for a flat pile. Door cartons and accessories always lie flat.</small></span></label>
                    <label className="check-field"><input type="checkbox" checked={settings.require_dunnage_for_height_gap} onChange={(event) => updateSetting("require_dunnage_for_height_gap", event.target.checked)} /><span><b>Require dunnage for near-level bridges</b><small>Every support gap is marked for the warehouse.</small></span></label>
                    <label className="check-field"><input type="checkbox" checked={settings.require_accessory_dunnage || false} onChange={(event) => updateSetting("require_accessory_dunnage", event.target.checked)} /><span><b>Require accessory dunnage</b><small>Accessory support and height tolerance are always enforced.</small></span></label>
                    <label className="check-field"><input type="checkbox" checked={allowFuzzy} onChange={(event) => setAllowFuzzy(event.target.checked)} /><span><b>Use reviewable fuzzy matches</b><small>Off by default. Rough matches stay unpacked until enabled.</small></span></label>
                  </div>
                </details>
              </Card>
              <div className="assurance">
                <ShieldCheck size={18} aria-hidden="true" />
                <p><b>Checked on every plan.</b> Upright rules, support, collisions, pallet bounds, weight limits and top-load limits are validated independently before a plan can be quoted.</p>
              </div>
            </aside>
          </div>}

          {builderStage === "review" && result && <>
            {hasComparisonEstimate && <div className="tabs" role="tablist" aria-label="Pallet plan version">
              <button id="validated-plan-tab" type="button" role="tab" tabIndex={estimateActive ? -1 : 0} aria-selected={!estimateActive} aria-controls="plan-review-panel" className={!estimateActive ? "active" : ""} onKeyDown={handleReviewTabKeyDown} onClick={() => selectReviewCandidate("validated")}>Validated plan</button>
              <button id="estimate-plan-tab" type="button" role="tab" tabIndex={estimateActive ? 0 : -1} aria-selected={estimateActive} aria-controls="plan-review-panel" className={estimateActive ? "active" : ""} onKeyDown={handleReviewTabKeyDown} onClick={() => selectReviewCandidate("estimate")}>
                Estimate <span className="badge badge-warn">Review required</span>
              </button>
            </div>}
            <section className="review" id="plan-review-panel" role={hasComparisonEstimate ? "tabpanel" : undefined} aria-labelledby={hasComparisonEstimate ? (estimateActive ? "estimate-plan-tab" : "validated-plan-tab") : undefined}>
              {resultStale && <div className="callout callout-warn" role="alert"><AlertTriangle size={18} /><span>Shipment items or warehouse limits changed. Rebuild before exporting or opening brokers.</span></div>}
              {estimateActive && <div className="callout callout-warn" role="alert">
                <AlertTriangle size={18} /><div><b>Estimate — review required</b><p>This arrangement has not passed the production validation gate. Do not ship it or send these measurements to a broker.</p></div>
              </div>}
              {!estimateActive && !planReady && <div className="callout callout-danger" role="alert"><AlertTriangle size={18} /><span>Incomplete layout — resolve every unaccounted item and validation error below before exporting or opening brokers.</span></div>}

              <div className="review-head">
                <div>
                  <h2 ref={reviewHeadingRef} tabIndex={-1}>Plan review</h2>
                  <p>{estimateActive ? comparisonEstimate.label : result.engine ? `Built with ${result.engine} ${result.engine_version || ""}`.trim() : "Review the pallet arrangement and freight measurements."}</p>
                </div>
                <div className="export-group" role="group" aria-label="Download report">
                  <span className="export-label"><Download size={15} aria-hidden="true" />Download</span>
                  {["pdf", "xlsx", "csv", "json"].map((format) => exportsEnabled
                    ? <a key={format} className="btn btn-secondary btn-sm" href={exportUrl(result.id, format)}>{format.toUpperCase()}</a>
                    : <button key={format} type="button" className="btn btn-secondary btn-sm" disabled title={estimateActive ? "Review-only estimates cannot be exported" : "Resolve every unpacked item before exporting a freight-ready report"}>{format.toUpperCase()}</button>)}
                </div>
              </div>

              <div className="stat-row">
                <Stat icon={Layers3} label="Pallets" value={reviewResult.summary.total_pallets ?? reviewResult.pallets.length} />
                <Stat icon={Box} label="Item units" value={reviewResult.summary.total_items} />
                <Stat icon={Weight} label="Freight weight" value={formatWeight(reviewResult.summary.total_freight_weight)} />
                <Stat
                  icon={estimateActive || reviewResult.human_review_required ? AlertTriangle : ShieldCheck}
                  label="Manual review"
                  value={estimateActive || reviewResult.human_review_required ? "Required" : "Clear"}
                  tone={estimateActive || reviewResult.human_review_required ? "warn" : "ok"}
                />
              </div>

              {reviewResult.truck_load && (
                <div className="truck-summary">
                  <Truck size={18} aria-hidden="true" />
                  <p><b>Dedicated truck: {reviewResult.truck_load.linear_feet} ft of floor</b>
                    {reviewResult.truck_load.standard_pallets} × 48 × 40 and {reviewResult.truck_load.long_pallets} × 96 × 40, two across, not stacked. {String(reviewResult.truck_load.fits).startsWith("more") ? "Needs" : "Fits a"} {reviewResult.truck_load.fits}.</p>
                </div>
              )}

              {(reviewResult.warnings.length > 0 || reviewResult.unpacked.length > 0) && (
                <a className="notes-banner" href="#plan-issues">
                  <AlertTriangle size={18} />
                  <span><b>{reviewResult.unpacked.length ? `${reviewResult.unpacked.length} item unit${reviewResult.unpacked.length === 1 ? "" : "s"} unpacked` : "This plan has review notes"}</b>{reviewResult.warnings[0]?.message || reviewResult.unpacked[0]?.reason}</span>
                  <span className="notes-banner-count">{reviewResult.warnings.length} note{reviewResult.warnings.length === 1 ? "" : "s"}<ChevronRight size={15} /></span>
                </a>
              )}

              <div className="review-layout">
                <div className="review-main">
                  <section className="card plan-card" aria-label="Pallet plan">
                    <div className="pallet-strip" role="list" aria-label="Pallets">
                      {reviewResult.pallets.map((pallet, index) => {
                        const fillPercent = currentLoadFillPercent(pallet);
                        const warnings = pallet.warnings || [];
                        return <div role="listitem" key={pallet.pallet_number}>
                          <button type="button" aria-pressed={index === selectedPallet} className={`pallet-tab ${index === selectedPallet ? "is-selected" : ""}`} onClick={() => { setSelectedPallet(index); setSelectedItem(pallet.items[0] || null); }}>
                            <span className="pallet-tab-top">
                              <b>Pallet {pallet.pallet_number}</b>
                              {warnings.length ? <StatusPill severity="warn">{warnings.length} note{warnings.length === 1 ? "" : "s"}</StatusPill> : <StatusPill severity="ok">Valid</StatusPill>}
                            </span>
                            <span className="pallet-tab-dims">{formatDimension(pallet.load_length || pallet.length)} × {formatDimension(pallet.load_width || pallet.width)} × {formatDimension(pallet.freight_height)} in</span>
                            <span className="pallet-tab-meta"><span>{pallet.display_name}</span><span>{formatWeight(pallet.total_weight)}</span></span>
                            <span className="fill">
                              <span className="fill-bar" role="progressbar" aria-label={`Current load fill for pallet ${pallet.pallet_number}`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(fillPercent)}><i style={{ width: `${fillPercent}%` }} /></span>
                              <span className="fill-value">{Math.round(fillPercent)}% full</span>
                            </span>
                          </button>
                        </div>;
                      })}
                      {reviewResult.unpacked.length > 0 && <div role="listitem" className="pallet-tab is-unpacked"><AlertTriangle size={17} /><span><b>{reviewResult.unpacked.length} unpacked</b><small>{reviewResult.unpacked[0].reason_code.replaceAll("_", " ")}</small></span></div>}
                    </div>

                    <div className="viewer-toolbar">
                      <div className="viewer-title"><b>Pallet {activePallet?.pallet_number}</b><span>{activePallet?.display_name}{activePallet ? `, ${activePallet.items.length} units` : ""}</span></div>
                      <div className="segmented" role="group" aria-label="Color boxes by">
                        <span className="segmented-label">Color by</span>
                        {[["category", "Category"], ["fragility", "Fragility"], ["order", "Order"]].map(([mode, label]) => <button key={mode} type="button" aria-pressed={colorMode === mode} className={colorMode === mode ? "active" : ""} onClick={() => setColorMode(mode)}>{label}</button>)}
                      </div>
                    </div>

                    <div className="viewer-layout">
                      <div className="viewer-wrap">
                        <Suspense fallback={<div className="viewer-empty"><LoaderCircle className="spin" size={20} />Loading 3D view…</div>}><PalletViewer pallet={activePallet} colorMode={colorMode} onSelect={handleSelectItem} selectedId={selectedItem?.unit_id} /></Suspense>
                        <p className="viewer-hint">Drag to rotate, scroll to zoom, right-drag to pan. Click a box to inspect it.</p>
                      </div>
                      <aside className="inspector" aria-label="Item inspector">
                        {selectedItem ? (
                          <>
                            <div className="inspector-head">
                              <span className="swatch" style={{ background: selectedItem.color }} aria-hidden="true" />
                              <div><h3>{selectedItem.original_sku}</h3><p>{selectedItem.description}</p></div>
                            </div>
                            <dl className="facts">
                              <div><dt>Master SKU</dt><dd>{selectedItem.sku}</dd></div>
                              <div><dt>Packing</dt><dd>{selectedItem.packing_state}</dd></div>
                              <div><dt>Item kind</dt><dd>{selectedItem.item_kind?.replaceAll("_", " ")}</dd></div>
                              <div><dt>Profile</dt><dd>{selectedItem.packing_role?.replaceAll("_", " ")}</dd></div>
                              <div><dt>Weight</dt><dd>{formatWeight(selectedItem.weight)}</dd></div>
                              <div><dt>Support</dt><dd>{Math.round(selectedItem.support_ratio * 100)}%</dd></div>
                              <div className="wide"><dt>Packed size</dt><dd>{formatDimension(selectedItem.length)} × {formatDimension(selectedItem.width)} × {formatDimension(selectedItem.height)} in</dd></div>
                              {selectedItem.bundle_id && <div className="wide"><dt>Bundle</dt><dd>{selectedItem.bundle_position} of {selectedItem.bundle_size}</dd></div>}
                              {selectedItem.pair_group_id && <div className="wide"><dt>Cabinet pair</dt><dd>{selectedItem.paired_family}</dd></div>}
                              {selectedItem.requires_dunnage && <div className="wide"><dt>Dunnage</dt><dd>Required at marked support gap</dd></div>}
                              <div className="wide"><dt>Source</dt><dd>{(selectedItem.source_refs || []).join(", ")}</dd></div>
                            </dl>
                            <p className="inspector-note"><ShieldCheck size={15} />{selectedItem.must_stay_upright ? "Upright-only orientation is enforced." : `Placed in ${selectedItem.rotation || "the standard"} orientation.`}</p>
                          </>
                        ) : <div className="inspector-empty"><Box size={28} /><p>Click a box in the 3D view, or pick one from the layer list, to see its details.</p></div>}
                      </aside>
                    </div>

                    {activePallet && <dl className="freight-strip">
                      <div><dt><Ruler size={14} />Freight size</dt><dd>{formatDimension(activePallet.load_length || activePallet.length)} × {formatDimension(activePallet.load_width || activePallet.width)} × {formatDimension(activePallet.freight_height)} in</dd></div>
                      <div><dt><Weight size={14} />Total weight</dt><dd>{formatWeight(activePallet.total_weight)}</dd></div>
                      <div><dt><PackageCheck size={14} />Density class</dt><dd>{activePallet.estimated_freight_class} <small>({activePallet.density_pcf} pcf)</small></dd></div>
                      <div><dt><Box size={14} />Layer / volume</dt><dd>{activePallet.layer_coverage_percent}% / {activePallet.used_volume_percent}%</dd></div>
                      <div><dt><Box size={14} />Largest void</dt><dd>{activePallet.largest_void_percent || 0}%</dd></div>
                      <div><dt><PackageCheck size={14} />Top support</dt><dd>{activePallet.top_support_coverage_percent || 0}%</dd></div>
                    </dl>}
                    <LayerView containedItems={reviewResult.contained_items || []} pallet={activePallet} selectedId={selectedItem?.unit_id} onSelect={handleSelectItem} />
                  </section>

                  <Card title="Pallet breakdown" headingLevel={3} description="Measured dimensions show the packed load. Broker values round up to whole inches; weight rounds to the nearest pound.">
                    <div className="table-wrap">
                      <table className="data-table breakdown-table" aria-label="Measured pallet values and broker packet values">
                        <thead><tr><th scope="col">Pallet</th><th scope="col">Type</th><th scope="col">Measured size</th><th scope="col" className="num">Measured weight</th><th scope="col">Sent to brokers</th><th scope="col" className="num">Sent weight</th><th scope="col">Class</th><th scope="col">Items</th></tr></thead>
                        <tbody>
                          {reviewResult.pallets.map((pallet) => {
                            const packet = (reviewResult.quote_rows || []).find(row => Number(row.pallet) === Number(pallet.pallet_number));
                            const itemQuantities = Object.entries(pallet.item_quantities || {}).map(([sku, qty]) => `${sku} × ${qty}`).join(", ");
                            return <tr key={pallet.pallet_number}>
                              <th scope="row">P{String(pallet.pallet_number).padStart(2, "0")}</th>
                              <td>{pallet.pallet_type}</td>
                              <td>{formatDimension(pallet.load_length || pallet.length)} × {formatDimension(pallet.load_width || pallet.width)} × {formatDimension(pallet.freight_height)} in</td>
                              <td className="num">{formatWeight(pallet.total_weight)}</td>
                              <td>{packet?.dimensions || (estimateActive ? "Unavailable for estimate" : "—")}</td>
                              <td className="num">{packet ? `${packet.weight} lb` : "—"}</td>
                              <td>{packet ? `${packet.freight_class} (${packet.density_pcf} pcf)` : "—"}</td>
                              <td className="items-col">{itemQuantities || `${pallet.items.length} units`}</td>
                            </tr>;
                          })}
                        </tbody>
                      </table>
                    </div>
                  </Card>

                  <div id="plan-issues">
                    <PlanIssues result={reviewResult} onInspect={entry => {
                      const index = reviewResult.pallets.findIndex(p => p.pallet_number === entry.pallet_number || p.items.some(i => entry.unit_ids?.includes(i.unit_id)));
                      if (index < 0) return;
                      setSelectedPallet(index);
                      const item = reviewResult.pallets[index].items.find(i => entry.unit_ids?.includes(i.unit_id)) || reviewResult.pallets[index].items[0];
                      handleSelectItem(item);
                    }} />
                  </div>
                  <p className="disclaimer"><CircleHelp size={15} />{reviewResult.disclaimer}</p>
                </div>

                <aside className="review-rail">
                  {company && recordedQuote && !estimateActive && <section className="card recorded-quote" aria-label="Quote record">
                    <div className="recorded-quote-head"><CheckCircle2Icon /><div><b>Saved as quote {recordedQuote.number}</b>
                      <small>{recordedQuote.orders.length ? `For ${recordedQuote.orders.map((order) => order.order_number).join(", ")}` : "No orders linked"}{recordedQuote.role === "combined" ? " · combined shipment quote" : ""}</small></div></div>
                    {recordedQuote.options?.length > 0 && <p className="recorded-quote-prices">{recordedQuote.options.length} price{recordedQuote.options.length === 1 ? "" : "s"} found
                      {recordedQuote.cost?.customer_price_cents != null ? ` · customer price ${money(recordedQuote.cost.customer_price_cents)}` : " · none used yet"}</p>}
                    <div className="recorded-quote-actions">
                      <button type="button" className="btn btn-primary btn-sm" onClick={() => setPricesQuoteId(recordedQuote.id)}><Plus size={14} />Add broker prices</button>
                      <a className="btn btn-ghost btn-sm" href={`#/quotes?quote=${recordedQuote.id}`}><Link2 size={14} />Quote History</a>
                    </div>
                  </section>}
                  <Card className="quote-card" title="Get quotes" description="Confirm the details, then open the brokers." actions={<button type="button" className="btn btn-ghost btn-sm" title="Save origin ZIP, commodity and NMFC as defaults for new shipments" onClick={() => saveQuoteDefaults().catch((e) => setError(e.message))}><Save size={14} />Save defaults</button>}>
                    <div className="form-grid form-grid-3">
                      <label className="field"><span>Origin ZIP</span><input inputMode="numeric" value={shippingInfo.origin.postal_code} onChange={(event) => updateShippingAddress("origin", "postal_code", event.target.value)} /></label>
                      <label className="field"><span>Delivery ZIP</span><input inputMode="numeric" value={shippingInfo.destination.postal_code} onChange={(event) => updateShippingAddress("destination", "postal_code", event.target.value)} /></label>
                      <label className="field"><span>NMFC</span><input value={shippingInfo.nmfc} onChange={(event) => setShippingInfo((current) => ({ ...current, confirmed: false, nmfc: event.target.value }))} /></label>
                      <label className="field field-wide"><span>Commodity</span><input value={shippingInfo.commodity_description} onChange={(event) => setShippingInfo((current) => ({ ...current, confirmed: false, commodity_description: event.target.value }))} /></label>
                    </div>
                    <fieldset className="chip-group chip-grid">
                      <legend>Delivery services</legend>
                      {Object.entries(accessorialLabels).map(([field, label]) => <label key={field} className="chip-check"><input type="checkbox" checked={Boolean(shippingInfo.accessorials[field])} onChange={(event) => updateAccessorial(field, event.target.checked)} /><span>{label}</span></label>)}
                    </fieldset>
                    <label className="confirm-check"><input type="checkbox" checked={shippingInfo.confirmed} onChange={(event) => setShippingInfo((current) => ({ ...current, confirmed: event.target.checked }))} /><span>I reviewed these ZIP codes, delivery services, and pallet dimensions.</span></label>
                    <BrokerPanel
                      steps={[
                        { label: "Validated plan", done: planReady && !resultStale && !estimateActive, detail: estimateActive ? "Estimates can't be quoted" : resultStale ? "Rebuild needed" : planReady ? `${reviewResult.pallets.length} pallet${reviewResult.pallets.length === 1 ? "" : "s"}, ${formatWeight(reviewResult.summary.total_freight_weight)}` : "Resolve blockers first" },
                        { label: "Origin and delivery ZIP", done: zipsEntered },
                        { label: "Details reviewed", done: shippingInfo.confirmed },
                        { label: "Broker Bridge connected", done: bridgeOk },
                      ]}
                      bridge={bridgeConnection}
                      minimumVersion={MINIMUM_BUILD2QUOTE_VERSION}
                      onCheckBridge={checkBridgeConnection}
                      canSend={canSendBrokers}
                      busy={busy}
                      onOpenAll={() => prepareBroker("all")}
                      onOpenBroker={(key) => prepareBroker(key)}
                      blockedReason={brokerBlockedReason}
                      sessions={brokerSessions}
                      services={shippingInfo.accessorials}
                    >
                      <button type="button" className="btn btn-ghost btn-block" disabled={estimateActive} onClick={() => copyQuoteSummary().catch((e) => setError(e.message))}><Copy size={15} />Copy summary</button>
                    </BrokerPanel>
                  </Card>
                </aside>
              </div>
            </section>
          </>}
        </main>
      )}

      {pricesQuoteId && <Suspense fallback={null}>
        <QuoteDetail quoteId={pricesQuoteId} focusPrices notify={(message, tone) => (tone === "danger" ? setError(message) : setNotice(message))}
          onClose={() => setPricesQuoteId(null)} onNavigate={() => setPricesQuoteId(null)}
          onChanged={(quote) => setRecordedQuote((current) => (current?.id === quote.id ? { ...current, ...quote } : current))} />
      </Suspense>}

      {aliasResolution && <AliasDialog
        resolution={aliasResolution}
        options={aliasOptions}
        busy={busy}
        onClose={() => setAliasResolution(null)}
        onSearch={(search) => setAliasResolution((current) => ({ ...current, search }))}
        onChoose={saveAliasResolution}
      />}

    </div>
  );
}
