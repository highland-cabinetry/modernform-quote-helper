import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getJson, sendJson } from "./api";
import usePackingJobs from "./usePackingJobs";

vi.mock("./api", () => ({ getJson: vi.fn(), sendJson: vi.fn() }));

afterEach(() => vi.resetAllMocks());

describe("usePackingJobs transient comparison estimates", () => {
  it("keeps the completed job estimate separate from the accepted PackingResult", async () => {
    const estimate = {
      status: "review_only",
      label: "Estimate — review required",
      pallets: [{ pallet_number: 1, load_fill_percent: 71 }],
      summary: { total_items: 1, total_pallets: 1, total_freight_weight: 50 },
      issues: [{ code: "SUPPORT_REVIEW", severity: "warning", message: "Check layer support." }],
    };
    const accepted = { id: "saved-result", plan_status: "ready", pallets: [], comparison_estimate: undefined };
    const request = { shipment_name: "Fixture", lines: [{ original_sku: "B12", quantity: 1 }], settings: {}, shipping_info: {} };
    sendJson
      .mockResolvedValueOnce({ id: "job-1", state: "completed", candidate: { id: "candidate-1" }, comparison_estimate: estimate })
      .mockResolvedValueOnce(accepted);

    const { result } = renderHook(() => usePackingJobs(() => request));
    let packed;
    await act(async () => { packed = await result.current.search(request); });

    expect(packed).toBe(accepted);
    expect(result.current.getComparisonEstimate()).toBe(estimate);
    const acceptancePayload = sendJson.mock.calls[1][2];
    expect(acceptancePayload).not.toHaveProperty("comparison_estimate");
    expect(packed).not.toHaveProperty("comparison_estimate", estimate);
  });
});
