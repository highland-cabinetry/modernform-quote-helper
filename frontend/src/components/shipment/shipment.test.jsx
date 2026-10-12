import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ComparisonCard from "./ComparisonCard";
import ActualsEditor, { rowsFromQuoted, validateActuals, stateFromActual } from "./ActualsEditor";
import ChargeEditor from "./ChargeEditor";
import { chargeErrors, formFromShipment, recordFlags, shipmentPayload } from "./model";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const comparison = (overrides = {}) => ({
  quoted: { quote_id: "q1", number: "Q-000003", pallets: 1, total_weight: 492,
    rows: [{ pallet: 1, length: 48, width: 40, height: 30, weight: 492, freight_class: "85" }] },
  actual: { pallets: 2, total_weight: 1030, billed_weight: null, billed_class: "", weight: 1030,
    rows: [{ length: 48, width: 40, height: 52, weight: 610, quantity: 1, note: "" }, { length: 96, width: 40, height: 30, weight: 420, quantity: 1, note: "" }],
    source: "warehouse", measured_on: "2026-09-30", notes: "" },
  weight_variance_lb: 538, weight_variance_percent: 109.3,
  cost: { expected_cents: 52300, actual_cents: 59800, variance_cents: 7500, variance_percent: 14.3 },
  revenue: { customer_charge_cents: 60500, extra_cents: 0, total_cents: 60500, charge_source: "quote" },
  profit_cents: 700, profit_estimated: false, margin_percent: 1.2,
  flags: { weight_over: true, pallets_changed: true, cost_over: true },
  ...overrides,
});

describe("ComparisonCard", () => {
  it("shows quoted against actual side by side with every raised flag", () => {
    render(<ComparisonCard comparison={comparison()} />);
    const flags = screen.getByLabelText("Comparison flags");
    expect(within(flags).getByText("Heavier than quoted")).toBeTruthy();
    expect(within(flags).getByText("Pallet count changed")).toBeTruthy();
    expect(within(flags).getByText("Cost over quote")).toBeTruthy();
    expect(screen.getByText("Q-000003").getAttribute("href")).toBe("#/quotes?quote=q1");
    expect(screen.getByText("$523.00")).toBeTruthy();
    expect(screen.getByText("$598.00")).toBeTruthy();
    expect(screen.getByText("+$75.00")).toBeTruthy();
    expect(screen.queryByText("estimated")).toBeNull();
    expect(screen.getByText("1.2%")).toBeTruthy();
  });

  it("marks profit as estimated before any bill and offers to record actuals", () => {
    const onRecordActuals = vi.fn();
    render(<ComparisonCard onRecordActuals={onRecordActuals} comparison={comparison({
      actual: null, weight_variance_lb: null, weight_variance_percent: null,
      cost: { expected_cents: 35900, actual_cents: null, variance_cents: null, variance_percent: null },
      revenue: { customer_charge_cents: 41500, extra_cents: 0, total_cents: 41500, charge_source: "quote" },
      profit_cents: 5600, profit_estimated: true, margin_percent: 13.5,
      flags: { weight_over: false, pallets_changed: false, cost_over: false },
    })} />);
    expect(screen.getByText("estimated")).toBeTruthy();
    expect(screen.getByText("$56.00")).toBeTruthy();
    expect(screen.getByText("no bills yet")).toBeTruthy();
    expect(within(screen.getByLabelText("Comparison flags")).queryAllByText(/./)).toHaveLength(0);
    fireEvent.click(screen.getByText("Record actual measurements"));
    expect(onRecordActuals).toHaveBeenCalled();
  });

  it("says the shipment is in line when measured and billed without flags", () => {
    render(<ComparisonCard comparison={comparison({ flags: { weight_over: false, pallets_changed: false, cost_over: false } })} />);
    expect(screen.getByText("In line with the quote")).toBeTruthy();
  });
});

const quoted = {
  number: "Q-000004", total_weight: 1400,
  rows: [
    { pallet: 1, length: 48, width: 40, height: 50, weight: 500 },
    { pallet: 2, length: 48, width: 40, height: 50, weight: 500 },
    { pallet: 3, length: 96, width: 40, height: 30, weight: 400 },
  ],
};

describe("ActualsEditor", () => {
  it("groups identical quoted pallets into one row with a quantity", () => {
    const rows = rowsFromQuoted(quoted.rows);
    expect(rows.map(({ length, width, height, weight, quantity }) => ({ length, width, height, weight, quantity }))).toEqual([
      { length: "48", width: "40", height: "50", weight: "500", quantity: "2" },
      { length: "96", width: "40", height: "30", weight: "400", quantity: "1" },
    ]);
  });

  it("starts from the quoted pallets, adds a row on Enter and saves numbers", () => {
    const onSave = vi.fn();
    render(<ActualsEditor actual={null} quoted={quoted} onSave={onSave} version="1" />);
    fireEvent.click(screen.getByText("Start from quoted pallets"));
    expect(screen.getByLabelText("Pallet 1 quantity").value).toBe("2");
    expect(screen.getByLabelText("Pallet 2 length").value).toBe("96");
    fireEvent.change(screen.getByLabelText("Pallet 2 weight"), { target: { value: "455.5" } });
    fireEvent.keyDown(screen.getByLabelText("Pallet 2 note"), { key: "Enter" });
    expect(screen.getByLabelText("Pallet 3 length")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByLabelText("Pallet 3 length"));
    fireEvent.change(screen.getByLabelText(/Billed weight/), { target: { value: "1460" } });
    fireEvent.click(screen.getByText("Save measurements"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.pallets).toEqual([
      { length: 48, width: 40, height: 50, weight: 500, quantity: 2, note: "" },
      { length: 96, width: 40, height: 30, weight: 455.5, quantity: 1, note: "" },
    ]);
    expect(payload.billed_weight).toBe(1460);
    expect(payload.source).toBe("warehouse");
  });

  it("blocks saving incomplete or impossible pallets and explains why", () => {
    const onSave = vi.fn();
    render(<ActualsEditor actual={null} quoted={quoted} onSave={onSave} version="1" />);
    fireEvent.change(screen.getByLabelText("Pallet 1 length"), { target: { value: "abc" } });
    expect(screen.getByText("Number")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Pallet 1 length"), { target: { value: "48" } });
    fireEvent.change(screen.getByLabelText("Pallet 1 quantity"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByText("Save measurements"));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText("Check the highlighted pallet fields.")).toBeTruthy();
    expect(screen.getByText("Whole number, 1 or more")).toBeTruthy();
    expect(screen.getAllByText("Required").length).toBe(3);
  });

  it("requires a pallet or a billed weight and validates limits", () => {
    const empty = stateFromActual(null);
    expect(validateActuals(empty).errors.form).toMatch(/at least one pallet/);
    const billedOnly = { ...empty, billed_weight: "575", billed_class: "70" };
    expect(validateActuals(billedOnly).payload).toMatchObject({ pallets: [], billed_weight: 575, billed_class: "70" });
    const tooWide = { ...empty, rows: [{ id: "x", length: "48", width: "130", height: "40", weight: "300", quantity: "1", note: "" }] };
    expect(validateActuals(tooWide).errors.rows.x.width).toBe("At most 120");
  });

  it("is read-only when the shipment is locked", () => {
    render(<ActualsEditor actual={null} quoted={quoted} locked version="1" />);
    expect(screen.getByText(/Reopen it to change the measurements/)).toBeTruthy();
    expect(screen.queryByText("Save measurements")).toBeNull();
  });
});

const shipment = (overrides = {}) => ({
  id: "s1", status: "delivered", name: "SO1", carrier: "Saia", broker: "TQL", pro_number: "", bol_number: "",
  pickup_date: null, delivery_date: null, expected_delivery_date: null, destination: { postal_code: "87507" },
  purpose: "standard", notes: "", charge_source: "quote", customer_charge_cents: 60500, rep_charge_cents: null, backcharge_cents: null,
  combined_quote: { id: "q1", number: "Q-000003", status: "final", cost: { customer_price_cents: 60500 } },
  ...overrides,
});

function ChargeHarness({ initial }) {
  const [form, setForm] = React.useState(formFromShipment(initial));
  return <><ChargeEditor form={form} shipment={initial} onChange={(patch) => setForm({ ...form, ...patch })} />
    <output data-testid="payload">{JSON.stringify(shipmentPayload(form, initial))}</output></>;
}
const payload = () => JSON.parse(screen.getByTestId("payload").textContent);

describe("Customer charge source", () => {
  it("follows the approved quote read-only and ignores a typed amount", () => {
    render(<ChargeHarness initial={shipment({ customer_charge_cents: 50000 })} />);
    expect(screen.getByTestId("charge-amount").textContent).toBe("$605.00");
    expect(screen.getByText("Q-000003").getAttribute("href")).toBe("#/quotes?quote=q1");
    expect(screen.queryByLabelText("Customer charge ($)")).toBeNull();
    expect(payload()).toMatchObject({ charge_source: "quote", customer_charge_cents: 60500 });
  });

  it("keeps the saved charge while the quote has no customer price", () => {
    const initial = shipment({ customer_charge_cents: 41000, combined_quote: { id: "q1", number: "Q-000003", status: "final", cost: { customer_price_cents: null } } });
    render(<ChargeHarness initial={initial} />);
    expect(screen.getByTestId("charge-amount").textContent).toBe("$410.00");
    expect(screen.getByText(/has no customer price yet/)).toBeTruthy();
    expect(payload().customer_charge_cents).toBe(41000);
  });

  it("asks for the rep charge and uses it as the customer charge", () => {
    render(<ChargeHarness initial={shipment()} />);
    fireEvent.click(screen.getByLabelText(/Rep-assigned charge/, { selector: "input[type=radio]" }));
    const field = screen.getByLabelText(/Rep-assigned charge \(\$\)/);
    expect(field.value).toBe("605.00");
    fireEvent.change(field, { target: { value: "" } });
    expect(screen.getByText("Enter the charge the rep assigned")).toBeTruthy();
    fireEvent.change(field, { target: { value: "450" } });
    expect(payload()).toMatchObject({ charge_source: "rep_assigned", rep_charge_cents: 45000, customer_charge_cents: 45000 });
  });

  it("uses the backcharge, a typed amount or nothing", () => {
    render(<ChargeHarness initial={shipment()} />);
    fireEvent.click(screen.getByLabelText(/^Backcharge/, { selector: "input[type=radio]" }));
    fireEvent.change(screen.getByLabelText(/Backcharge \(\$\)/), { target: { value: "120.50" } });
    expect(payload()).toMatchObject({ charge_source: "backcharge", backcharge_cents: 12050, customer_charge_cents: 12050 });
    fireEvent.click(screen.getByLabelText(/Entered by hand/, { selector: "input[type=radio]" }));
    expect(screen.getByLabelText(/Customer charge \(\$\)/).value).toBe("605.00");
    fireEvent.change(screen.getByLabelText(/Customer charge \(\$\)/), { target: { value: "12.3x" } });
    expect(screen.getByText("Use dollars and cents, e.g. 450.00")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/No charge/, { selector: "input[type=radio]" }));
    expect(screen.getByTestId("charge-amount").textContent).toBe("$0.00");
    expect(payload()).toMatchObject({ charge_source: "none", customer_charge_cents: 0 });
  });

  it("flags missing amounts for the chosen source only", () => {
    const form = formFromShipment(shipment());
    expect(chargeErrors({ ...form, charge_source: "backcharge" })).toEqual({ backcharge: "Enter the backcharge amount" });
    expect(chargeErrors({ ...form, charge_source: "quote" })).toEqual({});
  });
});

describe("Shipment list flags", () => {
  it("derives the same flags as the comparison from a Freight Data row", () => {
    expect(recordFlags({ status: "delivered", weight_over: true, pallets: 1, actual_pallets: 2, actual_cents: 59800,
      expected_cost_cents: 52300, expense_count: 2, net_cents: 700 })).toEqual(["weight_over", "pallets_changed", "cost_over"]);
    expect(recordFlags({ status: "delivered", weight_over: false, pallets: 1, actual_pallets: null, actual_cents: null,
      expected_cost_cents: 30000, expense_count: 0, net_cents: -500 })).toEqual(["missing_cost", "loss"]);
  });
});
