import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import QuickQuote, { BrokerProgress, manualPayload } from "./QuoteWorkflows";
import { getJson, sendJson } from "./api";
vi.mock("./PalletViewer", () => ({ default: () => <div>Viewer</div> }));
vi.mock("./api", () => ({ getJson: vi.fn(async () => []), sendJson: vi.fn(async () => ({ quote_packet: { pallets: [] }, handoff_validation: { ready: false } })) }));
const settings = { quote_origin: { postal_code: "80022" }, default_commodity_description: "Cabinets", default_nmfc: "79300" };
beforeEach(() => {
  getJson.mockResolvedValue([]);
  sendJson.mockResolvedValue({ quote_packet: { pallets: [] }, handoff_validation: { ready: false } });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
describe("Quick Quote", () => {
  it("uses launchPrepared for Open all brokers instead of the legacy callback", async () => {
    const launches = [{ broker: "tql", request_id: "one", url: "https://trax.tqlapp.com/quote-details" }];
    const launchPrepared = vi.fn(async () => undefined);
    const onPrepared = vi.fn();
    sendJson.mockImplementation(async (url) => {
      if (url === "/api/manual-quotes/preview") return { quote_packet: { pallets: [], total_weight: 100 }, handoff_validation: {ready:true} };
      if (url === "/api/manual-quotes") return {id:"saved", revision:1, shipment_name:"TEST"};
      if (url === "/api/quote-handoffs/prepare-all") return { handoffs: launches };
      return {};
    });
    render(<QuickQuote settings={settings} bridgeConnection={{state:"connected"}} launchPrepared={launchPrepared} onPrepared={onPrepared} />);
    fireEvent.change(await screen.findByLabelText("Shipment / order reference"), {target:{value:"TEST"}});
    for (const [field,value] of Object.entries({length:"48",width:"40",height:"30",weight:"100"}))
      fireEvent.change(screen.getByLabelText(`Pallet 1 ${field}`), {target:{value}});
    await waitFor(() => expect(screen.getByText("Open all brokers").disabled).toBe(false));
    fireEvent.click(screen.getByText("Open all brokers"));
    await waitFor(() => expect(launchPrepared).toHaveBeenCalledWith(launches, expect.objectContaining({id:"saved"})));
    expect(onPrepared).not.toHaveBeenCalled();
  });
  it("preserves decimals and rejects invalid quantities instead of adding tare", () => {
    const row = { id: "one", length: "48.12", width: 40, height: 30, weight: "100.5", quantity: "2" };
    expect(manualPayload("SO123", [row], {}).pallets[0]).toEqual({ ...row, length: 48.12, weight: 100.5, quantity: 2 });
    expect(() => manualPayload("SO123", [{ ...row, quantity: 1.5 }], {})).toThrow("whole number");
    expect(() => manualPayload("", [row], {})).toThrow("reference");
    expect(() => manualPayload("SO123", [{ ...row, weight: "" }], {})).toThrow("positive");
  });
  it("supports add, duplicate, remove and saves only manual quote endpoints", async () => {
    render(<QuickQuote settings={settings} bridgeConnection={{}} onDefaults={vi.fn()} onPrepared={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Shipment / order reference"), { target: { value: "SO123" } });
    for (const [field, value] of Object.entries({ length: "48.1", width: "40", height: "30", weight: "123.5", quantity: "2" }))
      fireEvent.change(screen.getByLabelText(`Pallet 1 ${field}`), { target: { value } });
    fireEvent.click(screen.getByText("Duplicate"));
    expect(screen.getByLabelText("Pallet 2 weight").value).toBe("123.5");
    fireEvent.click(screen.getAllByText("Remove")[1]);
    fireEvent.click(screen.getByText("Save shipment"));
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/manual-quotes", "POST", expect.objectContaining({ shipment_name: "SO123" })));
    expect(sendJson.mock.calls.some(([path]) => /shipments\/pack|\/api\/settings/.test(path))).toBe(false);
  });
  it("saves and hands manual pallets to each broker independently, with fresh review after edits", async () => {
    let revision = 0;
    sendJson.mockImplementation(async (path, method, payload) => {
      if (path.endsWith("/preview")) return { quote_packet: { pallets: [], total_weight: 248 },
        handoff_validation: { ready: payload.shipping_info.confirmed, missing_fields: ["Review measurements"] } };
      if (path.includes("/prepare")) return { request_id: path, state: "ready" };
      return { id: "manual-one", revision: ++revision, ...payload };
    });
    const onPrepared = vi.fn();
    render(<QuickQuote settings={settings} bridgeConnection={{ state: "connected", version: "2.5.1" }} onPrepared={onPrepared} />);
    fireEvent.change(await screen.findByLabelText("Shipment / order reference"), { target: { value: "TEST-QQ" } });
    fireEvent.change(screen.getByLabelText("Delivery ZIP"), { target: { value: "90210" } });
    for (const [field, value] of Object.entries({ length: "48.1", width: "40", height: "30.01", weight: "123.5", quantity: "2" }))
      fireEvent.change(screen.getByLabelText(`Pallet 1 ${field}`), { target: { value } });
    const review = screen.getByLabelText(/I reviewed the ZIPs/);
    fireEvent.click(review);
    for (const [broker, label] of Object.entries({ echoship: "EchoShip", tql: "TQL TRAX", dsv: "DSV myTMS", myunishippers: "MyUnishippers", odfl: "Old Dominion" })) {
      await waitFor(() => expect(screen.getByText(label).disabled).toBe(false));
      fireEvent.click(screen.getByText(label));
      await waitFor(() => expect(onPrepared).toHaveBeenCalledWith(broker, expect.objectContaining({ shipment_name: "TEST-QQ" })));
      expect(sendJson).toHaveBeenCalledWith(`/api/quote-handoffs/${broker}/prepare`, "POST", { shipment_id: "manual-one", source_kind: "manual", replace_active: false });
    }
    fireEvent.change(screen.getByLabelText("Pallet 1 weight"), { target: { value: "124.5" } });
    expect(review.checked).toBe(false);
    expect(screen.getByText("EchoShip").disabled).toBe(true);
    expect(sendJson.mock.calls.some(([path]) => /shipments\/pack/.test(path))).toBe(false);
  });
  it("replaces an active broker handoff only after explicit confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    sendJson.mockImplementation(async (path, method, payload) => {
      if (path.endsWith("/preview")) return { quote_packet: { pallets: [], total_weight: 100 }, handoff_validation: { ready: true } };
      if (path === "/api/manual-quotes") return { id: "manual-replace", revision: 1, shipment_name: "REPLACE" };
      if (path === "/api/quote-handoffs/odfl/prepare" && !payload.replace_active) {
        const error = new Error("Active handoff already exists for: odfl");
        error.status = 409;
        throw error;
      }
      if (path === "/api/quote-handoffs/odfl/prepare") return { broker: "odfl", request_id: "new", state: "ready" };
      return {};
    });
    const onPrepared = vi.fn();
    render(<QuickQuote settings={settings} bridgeConnection={{ state: "connected", version: "2.8.0" }} onPrepared={onPrepared} />);
    fireEvent.change(await screen.findByLabelText("Shipment / order reference"), { target: { value: "REPLACE" } });
    fireEvent.change(screen.getByLabelText("Delivery ZIP"), { target: { value: "90210" } });
    for (const [field, value] of Object.entries({ length: "48", width: "40", height: "30", weight: "100" }))
      fireEvent.change(screen.getByLabelText(`Pallet 1 ${field}`), { target: { value } });
    fireEvent.click(screen.getByLabelText(/I reviewed the ZIPs/));
    await waitFor(() => expect(screen.getByText("Old Dominion").disabled).toBe(false));
    fireEvent.click(screen.getByText("Old Dominion"));
    await waitFor(() => expect(onPrepared).toHaveBeenCalledWith("odfl", expect.objectContaining({ request_id: "new" })));
    expect(confirm).toHaveBeenCalledOnce();
    expect(sendJson).toHaveBeenCalledWith("/api/quote-handoffs/odfl/prepare", "POST", expect.objectContaining({ replace_active: true }));
  });
  it("reopens saved precision without displaying legacy rates", async () => {
    getJson.mockResolvedValue({ id: "saved", shipment_name: "SAVED", revision: 2,
      pallets: [{ id: "p", length: 48.12, width: 40, height: 30.01, weight: 123.5, quantity: 2 }],
      shipping_info: { confirmed: true, origin: { postal_code: "80022" }, destination: { postal_code: "90210" }, accessorials: {}, commodity_description: "Cabinets", nmfc: "79300" },
      broker_rates: [{ carrier: "OLD-RATE", price: 999 }], quote_history: [{ rates: [{ carrier: "OLD-RATE", price: 999 }] }] });
    render(<QuickQuote settings={settings} quoteId="saved" bridgeConnection={{}} />);
    await waitFor(() => expect(screen.getByLabelText("Shipment / order reference").value).toBe("SAVED"));
    expect(screen.getByLabelText("Pallet 1 weight").value).toBe("123.5");
    expect(screen.queryByText(/OLD-RATE/)).toBeNull();
  });
  it("clears stored city/state hints when a saved ZIP changes without removing those contract fields", async () => {
    getJson.mockResolvedValue({ id: "saved-zip", shipment_name: "ZIP TEST", revision: 1,
      pallets: [{ id: "pallet-1", length: 48, width: 40, height: 30, weight: 100, quantity: 1 }],
      shipping_info: { confirmed: false, origin: { city: "Old Origin", state: "CO", postal_code: "80022" }, destination: { city: "Old Destination", state: "CA", postal_code: "90210" }, accessorials: {}, commodity_description: "Cabinets", nmfc: "79300" } });
    sendJson.mockImplementation(async (path, method, payload) => {
      if (path.endsWith("/preview")) return { quote_packet: { pallets: [], total_weight: 100 }, handoff_validation: { ready: false } };
      return { id: "saved-zip", revision: 2, ...payload };
    });
    render(<QuickQuote settings={settings} quoteId="saved-zip" bridgeConnection={{}} />);
    const originZip = await screen.findByLabelText("Origin ZIP");
    expect(originZip.value).toBe("80022");
    expect(screen.queryByText("City and state for broker location matching")).toBeNull();
    fireEvent.change(originZip, { target: { value: "80023" } });
    fireEvent.click(screen.getByText("Save shipment"));
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/manual-quotes/saved-zip", "PUT", expect.objectContaining({
      shipping_info: expect.objectContaining({
        origin: expect.objectContaining({ postal_code: "80023", city: "", state: "" }),
        destination: expect.objectContaining({ city: "Old Destination", state: "CA" }),
      }),
    })));
  });
  it("explains an old backend and does not attempt unavailable Quick Quote APIs", async () => {
    render(<QuickQuote settings={settings} backendReady={false} bridgeConnection={{}} />);
    expect(await screen.findByText(/Restart .*Quote Helper \(Start menu/)).toBeTruthy();
    expect(screen.getByText("Save shipment").disabled).toBe(true);
    expect(sendJson).not.toHaveBeenCalled();
  });
  it("shows only compact progress, never prices, and stops polling completed handoffs", async () => {
    vi.useFakeTimers();
    render(<BrokerProgress sessions={{ dsv: { request_id: "done", state: "completed", shipment_name: "TEST", rates: [{ carrier: "OLD-RATE", price: 999 }] } }} setSessions={vi.fn()} />);
    expect(screen.getByText("Filled")).toBeTruthy();
    expect(screen.queryByText(/OLD-RATE/)).toBeNull();
    expect(document.querySelector("details").open).toBe(false);
    await vi.advanceTimersByTimeAsync(3000);
    expect(getJson).not.toHaveBeenCalled();
  });
});
