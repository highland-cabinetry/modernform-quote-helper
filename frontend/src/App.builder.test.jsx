import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { getJson, sendJson } from "./api";

vi.mock("./api", () => ({ getJson: vi.fn(), sendJson: vi.fn(), uploadFiles: vi.fn(), exportUrl: (id, format) => `/api/shipments/${id}/export/${format}` }));
vi.mock("./drafts", () => ({ useDraft: () => ({ ready: true }) }));
vi.mock("./DraftBar", () => ({ default: () => null }));
vi.mock("./PalletViewer", () => ({ default: () => <div>Pallet viewer</div> }));
vi.mock("./LayerView", () => ({ default: () => <div>Layer view</div> }));
vi.mock("./QuoteWorkflows", () => ({ default: () => null, BrokerProgress: () => null }));
vi.mock("./useModalFocus", () => ({ default: () => undefined }));
vi.mock("./build2quote", () => ({
  MINIMUM_BUILD2QUOTE_VERSION: "2.8.0",
  openPreparedBrokerTabs: vi.fn(),
  pingBuild2QuoteBridge: vi.fn(async () => ({ connected: true, version: "2.8.0" })),
}));

const settings = {
  standard_pallet: { name: "standard", display_name: "Standard 48 × 40", length: 48, width: 40, deck_height: 6, max_total_height: 48, max_total_weight: 2500, tare_weight: 45 },
  long_pallet: { name: "long", display_name: "Long 96 × 40", length: 96, width: 40, deck_height: 6, max_total_height: 48, max_total_weight: 2500, tare_weight: 80 },
  minimum_support_ratio: 0.75,
  accessory_only_min_freight_height: 24,
  support_height_tolerance: 0.45,
  approved_accessory_overhang_per_side: 1,
  require_dunnage_for_height_gap: true,
  require_accessory_dunnage: false,
  quote_origin: { postal_code: "80022", city: "", state: "" },
  default_commodity_description: "Cabinets",
  default_nmfc: "79300",
};

const makePallet = (fill = 63) => ({
  pallet_number: 1,
  pallet_type: "standard",
  display_name: "Standard 48 × 40",
  length: 48,
  width: 40,
  load_length: 48,
  load_width: 40,
  stack_height: 18,
  freight_height: 24,
  total_weight: 50,
  used_volume_percent: 88,
  load_fill_percent: fill,
  estimated_freight_class: 70,
  density_pcf: 0.2,
  layer_coverage_percent: 50,
  largest_void_percent: 5,
  top_support_coverage_percent: 100,
  item_quantities: { B12: 1 },
  warnings: [],
  required_actions: [],
  items: [{
    unit_id: "b12-1", sku: "B12", original_sku: "WHS-B12", description: "Base cabinet",
    packing_state: "flatpack", item_kind: "cabinet", packing_role: "standard", weight: 50,
    support_ratio: 1, length: 36, width: 24, height: 3.5, x: 0, y: 0, z: 0,
    rotation: "LWH", must_stay_upright: false, color: "#2f6f73", source_refs: ["SO123"],
  }],
});

const makeResult = (id = "saved-result") => ({
  id,
  shipment_name: "SO123",
  plan_status: "ready",
  freight_ready: true,
  engine: "test-engine",
  candidate_scores: [],
  pallets: [makePallet()],
  contained_items: [],
  unpacked: [],
  warnings: [],
  human_review_required: false,
  summary: { total_items: 1, packed_items: 1, unpacked_items: 0, total_pallets: 1, total_freight_weight: 50 },
  quote_rows: [{ pallet: 1, dimensions: "48 × 40 × 24", weight: 50, density_pcf: 0.2, freight_class: 70 }],
  disclaimer: "Review the physical load before release.",
});

const estimate = {
  status: "review_only",
  label: "Estimate — review required",
  pallets: [makePallet(44)],
  summary: { total_items: 1, packed_items: 1, unpacked_items: 0, total_pallets: 1, total_freight_weight: 50 },
  issues: [{ code: "SUPPORT_REVIEW", severity: "warning", message: "Verify estimated layer support." }],
};

beforeEach(() => {
  getJson.mockImplementation(async (path) => {
    if (path === "/api/settings") return settings;
    if (path === "/api/health") return { version: "9.0.0", item_master_count: 100, review_mode: false };
    if (path === "/api/shipments") return [{ id: "history-1", shipment_name: "SAVED SO", source_kind: "packed", total_pallets: 1, total_weight: 50 }];
    if (path === "/api/shipments/history-1") return makeResult("history-1");
    if (path === "/api/shipments/history-1/request") return {
      shipment_name: "SAVED SO",
      lines: [{ id: "saved-line", source_ref: "SO123", original_sku: "B12", quantity: 1, packing_state: "flatpack", description: "", length: "", width: "", height: "", weight: "" }],
      settings,
      allow_fuzzy_matches: false,
      overrides: [{ unit_id: "b12-1", locked: true }],
      rotation_preferences: [{ unit_id: "b12-1", rotation: "WLH" }],
      split_bundle_ids: ["old-bundle"],
    };
    return [];
  });
});

afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe("Builder shipment and plan review", () => {
  it("switches to review after a build, renders a transient estimate, and blocks its broker/export actions", async () => {
    const accepted = makeResult("new-result");
    sendJson.mockImplementation(async (path) => {
      if (path === "/api/packing-jobs") return { id: "job-1", state: "completed", candidate: { id: "candidate-1", pallets: [makePallet()] }, comparison_estimate: estimate };
      if (path === "/api/packing-jobs/job-1/accept") return accepted;
      return {};
    });
    render(<App />);

    const sku = await screen.findByLabelText("Item new row original sku");
    fireEvent.change(sku, { target: { value: "B12" } });
    fireEvent.click(screen.getByRole("button", { name: "Build pallet plan" }));

    const validatedTab = await screen.findByRole("tab", { name: "Validated plan" });
    expect(screen.getByRole("heading", { name: "Plan review" })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Plan review" }));
    expect(screen.queryByLabelText("Item B12 original sku")).toBeNull();
    expect(screen.queryByText("Manual override")).toBeNull();
    expect(screen.queryByText("Advanced exact placement")).toBeNull();
    expect(screen.queryByText("City and state for broker location matching")).toBeNull();
    expect(screen.queryByText("Measurements sent to brokers")).toBeNull();
    expect(screen.getByText("Pallet breakdown")).toBeTruthy();
    expect(screen.getAllByText("48 × 40 × 24", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByRole("progressbar", { name: "Current load fill for pallet 1" }).getAttribute("aria-valuenow")).toBe("63");
    expect(validatedTab).toBeTruthy();

    const jobCall = sendJson.mock.calls.find(([path]) => path === "/api/packing-jobs");
    expect(jobCall[2]).not.toHaveProperty("overrides");
    expect(jobCall[2]).not.toHaveProperty("rotation_preferences");
    expect(jobCall[2]).not.toHaveProperty("split_bundle_ids");
    const acceptCall = sendJson.mock.calls.find(([path]) => path === "/api/packing-jobs/job-1/accept");
    expect(acceptCall[2]).not.toHaveProperty("comparison_estimate");

    fireEvent.click(screen.getByLabelText(/I reviewed these ZIP codes/));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open all brokers" }).disabled).toBe(false));
    fireEvent.click(screen.getByRole("tab", { name: /Estimate/ }));
    expect(screen.getByRole("alert").textContent).toContain("Estimate — review required");
    expect(screen.getAllByText("Verify estimated layer support.").length).toBeGreaterThan(0);
    expect(screen.getByRole("progressbar", { name: "Current load fill for pallet 1" }).getAttribute("aria-valuenow")).toBe("44");
    expect(screen.getByRole("button", { name: "Open all brokers" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Copy summary" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "PDF" }).disabled).toBe(true);
    expect(screen.getByText("Unavailable for estimate")).toBeTruthy();
  });

  it("keeps a failed build on the item view and diagnostics focus the affected SKU", async () => {
    sendJson.mockResolvedValue({ id: "job-failed", state: "completed", candidate: null, message: "No complete plan found.", outcome_category: "input_blocked", termination_reason: "preflight", blockers: [{ sku: "B12", line_ids: [], message: "Review B12 dimensions.", suggested_correction: "Confirm the item master match." }] });
    render(<App />);
    const sku = await screen.findByLabelText("Item new row original sku");
    fireEvent.change(sku, { target: { value: "B12" } });
    fireEvent.click(screen.getByRole("button", { name: "Build pallet plan" }));
    const diagnosticButton = await screen.findByRole("button", { name: "Review B12" });
    expect(screen.getByRole("heading", { name: "Shipment items" })).toBeTruthy();
    fireEvent.click(diagnosticButton);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Item B12 original sku")));
  });

  it("builds a plan in one pass without a separate refinement step", async () => {
    sendJson.mockImplementation(async (path) => {
      if (path === "/api/packing-jobs") return { id: "job-1", state: "completed", candidate: { id: "candidate-1", pallets: [makePallet()] } };
      if (path === "/api/packing-jobs/job-1/accept") return makeResult("new-result");
      return {};
    });
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Item new row original sku"), { target: { value: "B12" } });
    fireEvent.click(screen.getByRole("button", { name: "Build pallet plan" }));
    await screen.findByRole("heading", { name: "Plan review" });
    expect(screen.queryByRole("button", { name: /Refine arrangement/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Estimate/ })).toBeNull();
  });

  it("opens saved shipments in read-only review without carrying historical manual preferences into a new build", async () => {
    sendJson.mockResolvedValue({});
    // Quote History reopens a saved plan by passing its id to the workspace.
    render(<App context={{ token: 1, resultId: "history-1" }} />);
    expect(await screen.findByRole("heading", { name: "Plan review" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit shipment" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Estimate/ })).toBeNull();
    expect(screen.queryByText("Manual override")).toBeNull();
    expect(screen.queryByLabelText("Item B12 original sku")).toBeNull();
  });

  it("shows zero current-load fill for an old empty pallet with legacy maximum-volume data", async () => {
    const oldResult = makeResult("history-1");
    oldResult.pallets = [{
      ...makePallet(),
      load_fill_percent: undefined,
      stack_height: 0,
      used_volume_percent: 88,
      item_quantities: {},
      items: [],
    }];
    getJson.mockImplementation(async (path) => {
      if (path === "/api/settings") return settings;
      if (path === "/api/health") return { version: "9.0.0", item_master_count: 100, review_mode: false };
      if (path === "/api/shipments") return [{ id: "history-1", shipment_name: "SAVED SO", source_kind: "packed", total_pallets: 1, total_weight: 50 }];
      if (path === "/api/shipments/history-1") return oldResult;
      if (path === "/api/shipments/history-1/request") return {
        shipment_name: "SAVED SO",
        lines: [{ id: "saved-line", source_ref: "SO123", original_sku: "B12", quantity: 1, packing_state: "flatpack", description: "", length: "", width: "", height: "", weight: "" }],
        settings,
        allow_fuzzy_matches: false,
      };
      return [];
    });
    // Quote History reopens a saved plan by passing its id to the workspace.
    render(<App context={{ token: 1, resultId: "history-1" }} />);

    expect(await screen.findByRole("heading", { name: "Plan review" })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Current load fill for pallet 1" }).getAttribute("aria-valuenow")).toBe("0");
  });
});
