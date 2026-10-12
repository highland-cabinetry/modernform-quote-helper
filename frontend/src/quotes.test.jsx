import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import QuoteDetail from "./components/QuoteDetail";
import OrderDrawer from "./components/OrderDrawer";
import { orderStage } from "./components/OrderQuoteHistory";
import { manualNoteSummary } from "./BrokerPanel";
import { getJson, sendJson } from "./api";

vi.mock("./api", () => ({ getJson: vi.fn(), sendJson: vi.fn() }));
// These tests cover the full freight workflow (customer answers, approval → shipment).
vi.mock("./edition", async (importOriginal) => ({ ...(await importOriginal()), FEATURES: { freightWorkflow: true }, brokerEnabled: () => true }));

const RULE = { markup_percent: 0, markup_fixed_cents: 0, round_up_to_cents: 500, minimum_cents: 0 };
const option = (id, source, carrier, price_cents, extra = {}) => ({
  id, source, source_label: source.toUpperCase(), carrier, service: "", transit_days: 3, price_cents, reference: "", expires_on: null,
  notes: "", recorded_at: "2026-10-01T12:00:00Z", ...extra,
});

function makeQuote(changes = {}) {
  return {
    id: "q1", number: "Q-000010", company_id: "c1", kind: "packed", status: "final", role: "individual",
    created_at: "2026-10-01T12:00:00Z", updated_at: "2026-10-01T12:00:00Z",
    order_ids: ["o1"], orders: [{ id: "o1", order_number: "SO81001" }],
    summary: { pallets: 2, standard_pallets: 2, long_pallets: 0, total_weight: 900, item_weight: 800, total_units: 10,
      rows: [{ pallet: 1, dimensions: "48 x 40 x 50 in", weight: 450, freight_class: "85", density_pcf: 12 }] },
    cost: { amount_cents: null, carrier: "", broker: "", reference: "", expires_on: null, customer_price_cents: null },
    terms: { postal_code: "87501", city: "Santa Fe", state: "NM", location_type: "residential", liftgate: true, appointment: false,
      call_ahead: false, inside_delivery: false, notes: "" },
    terms_labels: ["Residential", "Liftgate"], terms_source: "recorded",
    options: [option("a", "tql", "Saia", 96888), option("b", "echo", "Estes", 101240)],
    selected_option_id: null, selected_option: null, price_manual: false,
    customer_status: "not_sent", sent_at: null, responded_at: null, response_note: "", counted: false, shipment: null,
    ...changes,
  };
}

function api(routes) {
  const handle = (method) => async (path, ...rest) => {
    const key = `${method} ${path.split("?")[0]}`;
    const found = routes[key];
    if (!found) throw new Error(`Unexpected ${key}`);
    return typeof found === "function" ? found(...rest) : found;
  };
  getJson.mockImplementation(handle("GET"));
  sendJson.mockImplementation((path, method, ...rest) => handle(method)(path, ...rest));
}

beforeEach(() => { window.location.hash = "#/quotes"; });
afterEach(() => { cleanup(); vi.resetAllMocks(); document.body.innerHTML = ""; });

describe("QuoteDetail", () => {
  it("uses the selected broker price and shows the customer price from the pricing rule", async () => {
    const quote = makeQuote();
    const selected = makeQuote({ selected_option_id: "a", selected_option: quote.options[0], counted: true,
      cost: { ...quote.cost, amount_cents: 96888, carrier: "Saia", broker: "TQL", customer_price_cents: 97000 } });
    api({
      "GET /api/quotes/q1/detail": quote,
      "GET /api/orders/o1/quote-history": { order: { order_number: "SO81001" }, quoted_count: 2, quotes: [{ id: "q1", time_quoted: 2 }] },
      "POST /api/quotes/q1/select": selected,
      "POST /api/quotes/pricing-preview": { carrier_cents: 96888, customer_price_cents: 97000, markup_cents: 112, rule: RULE },
    });
    const onChanged = vi.fn();
    const notify = vi.fn();
    render(<QuoteDetail quoteId="q1" onClose={vi.fn()} onChanged={onChanged} notify={notify} />);
    const radio = await screen.findByRole("radio", { name: /Use TQL Saia \$968\.88 for the quote/ });
    expect(screen.getByText("Not priced yet")).toBeTruthy();
    fireEvent.click(radio);
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/quotes/q1/select", "POST", { option_id: "a" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(selected));
    expect(radio.checked).toBe(true);
    expect(screen.getAllByText("$970.00").length).toBeGreaterThan(0);
    expect(await screen.findByText(/round up to \$5/)).toBeTruthy();
    expect(screen.getAllByText("$1.12").length).toBeGreaterThan(0); // expected margin = customer price − price used
    expect(notify).toHaveBeenCalledWith(expect.stringContaining("Customer price $970.00"));
    await waitFor(() => expect(screen.getByText(/Quoted 2 of 2/)).toBeTruthy());
  });

  it("approves with one invoice field per order and links the new shipment", async () => {
    const two = makeQuote({
      order_ids: ["o1", "o2"], orders: [{ id: "o1", order_number: "SO81001" }, { id: "o2", order_number: "SO81002" }],
      selected_option_id: "a", customer_status: "sent", sent_at: "2026-10-02T12:00:00Z", counted: true,
      cost: { amount_cents: 96888, carrier: "Saia", broker: "TQL", reference: "", expires_on: null, customer_price_cents: 97000 },
    });
    const approved = { ...two, customer_status: "approved", responded_at: "2026-10-03T12:00:00Z", role: "combined",
      shipment: { id: "s9", number: "S-000009", status: "planning", is_combined_quote: true } };
    api({
      "GET /api/quotes/q1/detail": two,
      "GET /api/orders/o1/quote-history": { order: { order_number: "SO81001" }, quoted_count: 1, quotes: [{ id: "q1", time_quoted: 1 }] },
      "POST /api/quotes/pricing-preview": { carrier_cents: 96888, customer_price_cents: 97000, markup_cents: 112, rule: RULE },
      "POST /api/quotes/q1/approve": { quote: approved, shipment: { id: "s9", number: "S-000009" }, shipment_created: true },
    });
    render(<QuoteDetail quoteId="q1" onClose={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Approved…/ }));
    const dialog = await screen.findByRole("dialog", { name: "Approve Q-000010" });
    fireEvent.change(within(dialog).getByLabelText(/Invoice for SO81001/), { target: { value: "68801" } });
    fireEvent.change(within(dialog).getByLabelText(/Invoice for SO81002/), { target: { value: "68802, 68803" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/quotes/q1/approve", "POST",
      { invoice_numbers: [], order_invoices: { o1: ["68801"], o2: ["68802", "68803"] }, note: "" }));
    expect(await screen.findByText(/Shipment created:/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "S-000009" })[0].getAttribute("href")).toBe("#/shipments/s9");
    expect(screen.queryByRole("dialog", { name: "Approve Q-000010" })).toBeNull();
  });

  it("explains why approval is not possible without a price", async () => {
    api({ "GET /api/quotes/q1/detail": makeQuote() });
    render(<QuoteDetail quoteId="q1" onClose={vi.fn()} />);
    const approve = await screen.findByRole("button", { name: /Approved…/ });
    expect(approve.disabled).toBe(true);
    expect(screen.getByText(/Approving needs a price/)).toBeTruthy();
  });

  it("adds a broker price from the keyboard and suggests the next broker", async () => {
    const quote = makeQuote({ options: [option("b", "echo", "Estes", 101240)] });
    const added = makeQuote({ options: [...quote.options, option("c", "myunishippers", "XPO", 101240)] });
    api({ "GET /api/quotes/q1/detail": quote, "POST /api/quotes/q1/options": added });
    render(<QuoteDetail quoteId="q1" onClose={vi.fn()} notify={vi.fn()} />);
    const source = await screen.findByLabelText("Source");
    expect(source.value).toBe("myunishippers"); // EchoShip is entered already
    const carrier = screen.getByLabelText("Carrier");
    const price = screen.getByLabelText("Price ($)");
    fireEvent.change(carrier, { target: { value: "XPO" } });
    fireEvent.change(price, { target: { value: "abc" } });
    fireEvent.keyDown(price, { key: "Enter" });
    expect(await screen.findByText(/Enter the price in dollars/)).toBeTruthy();
    expect(sendJson).not.toHaveBeenCalledWith("/api/quotes/q1/options", "POST", expect.anything());
    fireEvent.change(price, { target: { value: "$1,012.40" } });
    fireEvent.keyDown(price, { key: "Enter" });
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/quotes/q1/options", "POST", {
      source: "myunishippers", carrier: "XPO", service: "", transit_days: null, price_cents: 101240, reference: "",
      expires_on: null, notes: "", select: false,
    }));
    await waitFor(() => expect(screen.getByLabelText("Source").value).toBe("dsv"));
    expect(screen.getByLabelText("Carrier").value).toBe("");
    expect(screen.getAllByRole("radio", { name: /for the quote/ })).toHaveLength(2);
  });

  it("asks to mark the quote sent after the quote email is prepared", async () => {
    const priced = makeQuote({ selected_option_id: "a", counted: true,
      cost: { amount_cents: 96888, carrier: "Saia", broker: "TQL", reference: "", expires_on: null, customer_price_cents: 97000 } });
    api({
      "GET /api/quotes/q1/detail": priced,
      "GET /api/orders/o1/quote-history": { order: { order_number: "SO81001" }, quoted_count: 1, quotes: [{ id: "q1", time_quoted: 1 }] },
      "POST /api/quotes/pricing-preview": { carrier_cents: 96888, customer_price_cents: 97000, markup_cents: 112, rule: RULE },
      "POST /api/emails/draft": { to: ["buyer@example.com"], cc: [], subject: "Freight quote", body: "Hello", entity_type: "quote", entity_id: "q1" },
      "POST /api/emails/log": {},
      "POST /api/quotes/q1/sent": { ...priced, customer_status: "sent", sent_at: "2026-10-04T12:00:00Z" },
    });
    render(<QuoteDetail quoteId="q1" onClose={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Email quote/ }));
    const copy = await screen.findByRole("button", { name: /Copy/ });
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn(async () => {}) }, configurable: true });
    fireEvent.click(copy);
    const mark = await screen.findByRole("button", { name: "Mark as sent" });
    fireEvent.click(mark);
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/quotes/q1/sent", "POST", {}));
    expect(await screen.findByText(/Waiting for the customer's answer/)).toBeTruthy();
  });
});

describe("Order quote history", () => {
  const trace = {
    order: { id: "o1", order_number: "SO81001", status: "ready", customer_name: "Mesa Kitchens", document_type: "packing_slip",
      ship_to: { postal_code: "87501" }, references: [], issues: [], revision: 1, packing_state: "flatpack", notes: "",
      customer_contact: "Dana", customer_emails: ["orders@mesa.example.com"], customer_phone: "",
      lines: [{ id: "l1", original_sku: "B30", description: "", quantity: 2, packing_state: "flatpack", source_ref: "SO81001.pdf" }] },
    source_file: null, quotes: [], shipment: null, revisions: [], history: [],
  };
  const history = {
    order: { id: "o1", order_number: "SO81001" }, quoted_count: 2, approved_quote_id: null,
    quotes: [
      { ...makeQuote({ id: "q3", number: "Q-000003", customer_status: "declined", counted: true, terms_labels: ["Limited access", "Appointment"],
        terms: { ...makeQuote().terms, postal_code: "87507" }, selected_option_id: "a",
        cost: { amount_cents: 96888, customer_price_cents: 97000 } }), time_quoted: 2 },
      { ...makeQuote({ id: "q2", number: "Q-000002", customer_status: "sent", sent_at: "2026-10-01T13:00:00Z", counted: true }), time_quoted: 1 },
      { ...makeQuote({ id: "q1", number: "Q-000001" }), time_quoted: null },
    ],
  };

  it("shows how many times the order was quoted, with one card per quote, and opens the quote", async () => {
    api({
      "GET /api/orders/o1": trace,
      "GET /api/orders/o1/quote-history": history,
      "GET /api/quotes/q3/detail": history.quotes[0],
      "POST /api/quotes/pricing-preview": { carrier_cents: 96888, customer_price_cents: 97000, markup_cents: 112, rule: RULE },
    });
    render(<OrderDrawer orderId="o1" onClose={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("Quoted 2 times")).toBeTruthy();
    expect(screen.getByText("1 draft not priced")).toBeTruthy();
    const cards = screen.getAllByRole("button", { name: /Open quote Q-/ });
    expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual(["Open quote Q-000003", "Open quote Q-000002", "Open quote Q-000001"]);
    expect(within(cards[0]).getByText("Declined")).toBeTruthy();
    expect(within(cards[0]).getByText("Limited access")).toBeTruthy();
    expect(within(cards[0]).getByText("ZIP 87507")).toBeTruthy();
    expect(within(cards[0]).getByText("$970.00")).toBeTruthy();
    const current = screen.getByRole("list", { name: /Order progress: Sent \(declined\)/ }).querySelector('[aria-current="step"]');
    expect(current.textContent).toContain("Sent");
    fireEvent.click(cards[0]);
    await waitFor(() => expect(getJson).toHaveBeenCalledWith("/api/quotes/q3/detail"));
    expect(await screen.findByRole("dialog", { name: /Quote Q-000003/ })).toBeTruthy();
  });

  it("saves the customer contact fields", async () => {
    api({
      "GET /api/orders/o1": trace,
      "GET /api/orders/o1/quote-history": { ...history, quoted_count: 0, quotes: [] },
      "PUT /api/orders/o1": { ...trace.order, revision: 2 },
    });
    render(<OrderDrawer orderId="o1" onClose={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("Not quoted yet")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Details" }));
    fireEvent.change(screen.getByLabelText(/Customer emails/), { target: { value: "orders@mesa.example.com; ap@mesa.example.com," } });
    fireEvent.change(screen.getByLabelText("Customer phone"), { target: { value: "505-555-0100" } });
    fireEvent.click(screen.getByRole("button", { name: /Save correction/ }));
    await waitFor(() => expect(sendJson).toHaveBeenCalledWith("/api/orders/o1", "PUT", expect.objectContaining({
      customer_contact: "Dana", customer_emails: ["orders@mesa.example.com", "ap@mesa.example.com"], customer_phone: "505-555-0100",
    })));
  });
});

describe("helpers", () => {
  it("derives the order stage from quotes and the shipment", () => {
    expect(orderStage({ quotes: [] }, null).stage).toBe(0);
    expect(orderStage({ quotes: [{ counted: true, customer_status: "not_sent" }] }, null).stage).toBe(1);
    expect(orderStage({ approved_quote_id: "q", quotes: [{ counted: true, customer_status: "approved" }] }, { status: "planning" }).stage).toBe(3);
    expect(orderStage({ approved_quote_id: "q", quotes: [{ counted: true, customer_status: "approved" }] }, { status: "in_transit" }).stage).toBe(5);
    expect(orderStage({ quotes: [{ counted: true, customer_status: "expired" }] }, null).note.label).toBe("Expired");
  });

  it("summarises the Bridge's manual notes per broker", () => {
    expect(manualNoteSummary([
      { field: "Limited access", ok: false, manual: "set", message: "" },
      { field: "Inside delivery", ok: false, manual: "set", message: "" },
      { field: "Pallet 1 dimensions manual review required", ok: false, manual: "check", message: "" },
      { field: "Origin ZIP", ok: true, message: "" },
    ])).toBe("Set manually: Limited access, Inside delivery. Check manually: Pallet 1 dimensions.");
    expect(manualNoteSummary(undefined)).toBe("");
  });
});
