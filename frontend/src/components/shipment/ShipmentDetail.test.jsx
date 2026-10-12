import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { getJson, sendJson } from "../../api";
import Shipments from "../../pages/Shipments";

const app = vi.hoisted(() => ({
  route: { path: "/shipments/s1", parts: ["shipments", "s1"], query: {} },
  go: () => {}, notify: () => {}, company: { id: "c1" }, multiCompany: false,
}));
vi.mock("../../shell/AppShell", () => ({ useApp: () => app }));
vi.mock("../../api", () => ({ getJson: vi.fn(), sendJson: vi.fn() }));

const view = (overrides = {}) => ({
  id: "s1", number: "S-000002", name: "SO81002 + SO81003", status: "in_transit", carrier: "Pitt Ohio", broker: "DSV",
  pro_number: "773300119", bol_number: "", pickup_date: "2026-10-07", delivery_date: null, expected_delivery_date: "2026-10-08",
  destination: { name: "", street: "", city: "Boulder", state: "CO", postal_code: "80301", phone: "", email: "" },
  customer_charge_cents: 41500, notes: "", purpose: "standard", charge_source: "quote", rep_charge_cents: null, backcharge_cents: null,
  company_id: "c1", order_ids: ["o1"], combined_quote_id: "q1", actual: null, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-07T00:00:00Z",
  orders: [{ id: "o1", order_number: "SO81002", customer_name: "Front Range Remodel", ship_to: { postal_code: "80301" }, unit_count: 10 }],
  warnings: [], individual_quotes: { o1: [] },
  combined_quote: { id: "q1", number: "Q-000004", kind: "packed", status: "final", orders: [{ id: "o1", order_number: "SO81002" }],
    summary: { pallets: 1, total_weight: 513 }, cost: { amount_cents: 35900, customer_price_cents: 41500 }, created_at: "2026-10-01T00:00:00Z" },
  expenses: [], expense_total_cents: 0, allocation: null, allocation_stale: false, revenue: [], revenue_total_cents: 0,
  comparison: {
    quoted: { quote_id: "q1", number: "Q-000004", pallets: 1, total_weight: 513, rows: [] }, actual: null,
    weight_variance_lb: null, weight_variance_percent: null,
    cost: { expected_cents: 35900, actual_cents: null, variance_cents: null, variance_percent: null },
    revenue: { customer_charge_cents: 41500, extra_cents: 0, total_cents: 41500, charge_source: "quote" },
    profit_cents: 5600, profit_estimated: true, margin_percent: 13.5, flags: { weight_over: false, pallets_changed: false, cost_over: false },
  },
  locked: false, history: [],
  ...overrides,
});

beforeEach(() => {
  getJson.mockImplementation(async (path) => (path.startsWith("/api/emails") ? [] : view()));
  sendJson.mockImplementation(async (path, method, payload) => view({ ...payload, updated_at: "2026-10-09T00:00:00Z" }));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe("Shipment detail", () => {
  it("marks a shipment delivered with the full shipment body and suggests the delivered email", async () => {
    render(<Shipments />);
    await screen.findByText("Shipment details");
    expect(screen.getByText("Q-000004 · 1 pallet · 513 lb")).toBeTruthy();
    fireEvent.click(screen.getAllByText("Mark delivered")[0]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelector('input[type="date"]').value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    fireEvent.change(dialog.querySelector('input[type="date"]'), { target: { value: "2026-10-09" } });
    fireEvent.submit(dialog.querySelector("#status-form"));
    await waitFor(() => expect(sendJson).toHaveBeenCalled());
    const [path, method, payload] = sendJson.mock.calls[0];
    expect(path).toBe("/api/freight-shipments/s1");
    expect(method).toBe("PUT");
    expect(payload).toEqual({
      name: "SO81002 + SO81003", status: "delivered", carrier: "Pitt Ohio", broker: "DSV", pro_number: "773300119", bol_number: "",
      pickup_date: "2026-10-07", delivery_date: "2026-10-09", expected_delivery_date: "2026-10-08",
      destination: view().destination, purpose: "standard", notes: "", charge_source: "quote",
      customer_charge_cents: 41500, rep_charge_cents: null, backcharge_cents: null,
    });
    expect(await screen.findByText(/Send the delivered email/)).toBeTruthy();
  });

  it("asks for a reason before reopening a closed shipment", async () => {
    getJson.mockImplementation(async (path) => (path.startsWith("/api/emails") ? [] : view({ status: "closed", locked: true })));
    render(<Shipments />);
    fireEvent.click(await screen.findByText("Reopen…"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.submit(dialog.querySelector("#reason-form"));
    expect(screen.getByText("A reason is required")).toBeTruthy();
    expect(sendJson).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Why is it being reopened/), { target: { value: "Corrected bill" } });
    fireEvent.submit(dialog.querySelector("#reason-form"));
    await waitFor(() => expect(sendJson).toHaveBeenCalled());
    expect(sendJson.mock.calls[0][0]).toBe("/api/freight-shipments/s1?reason=Corrected%20bill");
    expect(sendJson.mock.calls[0][2].status).toBe("delivered");
  });
});
