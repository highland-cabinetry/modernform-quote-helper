import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AttentionList, attentionHref } from "./components/insights/Attention";
import EmailTemplates, { renderTemplate } from "./components/insights/EmailTemplates";
import NumTable from "./components/insights/NumTable";
import PricingSettings from "./components/insights/PricingSettings";
import RepsEditor from "./components/insights/RepsEditor";
import Reports from "./components/insights/Reports";
import { rangeFor } from "./components/insights/shared";

// Synthetic company and records only.
const company = () => ({
  id: "c1", code: "TST", name: "Test Cabinets", active: true, is_default: true, created_at: "2026-01-01T00:00:00Z",
  config: {
    catalog: "default", rule_set: "none", adapters: ["table"], bare_number_prefix: "INV",
    pricing: { markup_percent: 15, markup_fixed_cents: 0, round_up_to_cents: 500, minimum_cents: 0 },
    reps: [{ name: "Alex Rivera", email: "alex@example.com", active: true }],
    email: { signature: "Freight Desk", cc_rep: true, templates: {} },
  },
});

let routes;
let calls;
function respond(status, data) {
  return { ok: status < 400, status, json: async () => data };
}
beforeEach(() => {
  routes = [];
  calls = [];
  globalThis.fetch = vi.fn(async (url, options = {}) => {
    const method = options.method || "GET";
    let body = options.body;
    if (typeof body === "string") body = JSON.parse(body);
    calls.push({ url, method, body });
    for (const [test, handler] of routes) {
      if (test(method, url)) {
        const result = await handler({ url, method, body });
        return Array.isArray(result) && typeof result[0] === "number" && result.length === 2 ? respond(result[0], result[1]) : respond(200, result);
      }
    }
    return respond(404, { detail: `no route for ${method} ${url}` });
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const route = (method, prefix, handler) => routes.push([(m, url) => m === method && url.startsWith(prefix), handler]);
const callsTo = (method, prefix) => calls.filter((call) => call.method === method && call.url.startsWith(prefix));

describe("Pricing settings", () => {
  it("previews the unsaved rule through the API and saves only config.pricing", async () => {
    const stored = company();
    route("POST", "/api/quotes/pricing-preview", ({ body }) => {
      const raw = Math.ceil(body.carrier_cents * (1 + body.pricing.markup_percent / 100)) + body.pricing.markup_fixed_cents;
      const step = body.pricing.round_up_to_cents;
      const price = step ? Math.ceil(raw / step) * step : raw;
      return { carrier_cents: body.carrier_cents, customer_price_cents: price, markup_cents: price - body.carrier_cents, rule: body.pricing };
    });
    route("GET", "/api/companies", () => [stored]);
    route("PUT", "/api/companies/c1", ({ body }) => ({ ...stored, ...body }));
    const onSaved = vi.fn();
    render(<PricingSettings company={stored} notify={vi.fn()} onSaved={onSaved} />);
    expect(await screen.findByText("$970.00")).toBeTruthy(); // $842.50 + 15 %, up to $5

    fireEvent.change(screen.getByLabelText("Markup percent"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("radio", { name: "$10" }));
    await waitFor(() => expect(callsTo("POST", "/api/quotes/pricing-preview").some((call) => call.body.pricing.markup_percent === 20
      && call.body.pricing.round_up_to_cents === 1000 && call.body.carrier_cents === 84250 && call.body.company_id === "c1")).toBe(true));
    expect(await screen.findByText("$1,020.00")).toBeTruthy();
    expect(screen.getByText(/Preview of the unsaved rule/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Save pricing/ }));
    await waitFor(() => expect(callsTo("PUT", "/api/companies/c1")).toHaveLength(1));
    const saved = callsTo("PUT", "/api/companies/c1")[0].body;
    expect(saved.config.pricing).toEqual({ markup_percent: 20, markup_fixed_cents: 0, round_up_to_cents: 1000, minimum_cents: 0 });
    expect(saved.config.reps).toEqual(stored.config.reps);
    expect(saved.config.email).toEqual(stored.config.email);
    expect(saved).not.toHaveProperty("id");
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("blocks saving an invalid custom rounding step", async () => {
    route("POST", "/api/quotes/pricing-preview", ({ body }) => ({ carrier_cents: body.carrier_cents, customer_price_cents: body.carrier_cents, markup_cents: 0 }));
    render(<PricingSettings company={company()} notify={vi.fn()} />);
    fireEvent.click(screen.getByRole("radio", { name: "Custom" }));
    fireEvent.change(screen.getByLabelText("Custom rounding step in dollars"), { target: { value: "abc" } });
    expect(screen.getByText("$0.01 to $1,000")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Save pricing/ }).disabled).toBe(true);
  });
});

describe("Sales reps editor", () => {
  it("adds, edits, deactivates and removes reps, refusing duplicate names", async () => {
    const stored = company();
    stored.config.reps.push({ name: "Sam Lee", email: "sam@example.com", active: true });
    route("GET", "/api/companies", () => [stored]);
    route("PUT", "/api/companies/c1", ({ body }) => body);
    render(<RepsEditor company={stored} notify={vi.fn()} />);
    const save = screen.getByRole("button", { name: /Save reps/ });
    expect(save.disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /Add rep/ }));
    fireEvent.change(screen.getByLabelText("Rep 3 name"), { target: { value: "alex rivera" } });
    expect(screen.getByText("Already listed")).toBeTruthy();
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Rep 3 name"), { target: { value: " Jo New " } });
    fireEvent.change(screen.getByLabelText("Rep 3 email"), { target: { value: "jo@" } });
    expect(screen.getByText("Not an email address")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Rep 3 email"), { target: { value: "jo@example.com" } });
    fireEvent.click(screen.getByLabelText("Rep 1 active"));
    fireEvent.click(screen.getByRole("button", { name: "Remove Sam Lee" }));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(callsTo("PUT", "/api/companies/c1")).toHaveLength(1));
    const body = callsTo("PUT", "/api/companies/c1")[0].body;
    expect(body.config.reps).toEqual([
      { name: "Alex Rivera", email: "alex@example.com", active: false },
      { name: "Jo New", email: "jo@example.com", active: true },
    ]);
    expect(body.config.pricing).toEqual(stored.config.pricing);
  });
});

describe("Customer email templates", () => {
  const catalog = () => {
    const defaults = {
      quote: { subject: "Freight quote for {orders}", body: "Hello {contact},\n\nPrice {price}.[[ Valid until {expires}.]]\n\n{signature}" },
      follow_up: { subject: "Following up: {orders}", body: "Hello {contact}" },
      ready: { subject: "{orders} is ready", body: "Ready" },
      shipped: { subject: "{orders} has shipped", body: "Shipped[[ PRO {pro}]]" },
      delivered: { subject: "{orders} was delivered", body: "Delivered" },
    };
    return {
      kinds: Object.keys(defaults),
      templates: Object.fromEntries(Object.entries(defaults).map(([kind, value]) => [kind, { ...value, custom: false, default: value }])),
      placeholders: { orders: "Order numbers", contact: "Customer contact", customer: "Customer name", price: "Customer freight price", expires: "Quote valid until", signature: "Signature", pro: "PRO number" },
      signature: "Freight Desk", cc_rep: true,
    };
  };

  it("inserts a placeholder at the cursor, resets to the default and saves only changed kinds", async () => {
    const stored = company();
    route("GET", "/api/emails/templates", () => catalog());
    route("GET", "/api/companies", () => [stored]);
    route("PUT", "/api/companies/c1", ({ body }) => body);
    render(<EmailTemplates company={stored} notify={vi.fn()} />);
    const body = await screen.findByLabelText(/^Body/);
    // Preview renders sample values and drops nothing when every value is present.
    expect(screen.getByText(/Valid until Mon Oct 19, 2026\./)).toBeTruthy();

    act(() => { body.focus(); body.setSelectionRange(6, 6); });
    fireEvent.focusIn(body); // after "Hello "
    fireEvent.click(screen.getByRole("button", { name: /^\{customer\}/ }));
    expect(body.value.startsWith("Hello {customer}{contact},")).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(body));
    await waitFor(() => expect(body.selectionStart).toBe(6 + "{customer}".length));

    const subject = screen.getByLabelText(/^Subject/);
    act(() => { subject.focus(); subject.setSelectionRange(subject.value.length, subject.value.length); });
    fireEvent.focusIn(subject);
    fireEvent.click(screen.getByRole("button", { name: /^\{price\}/ }));
    expect(subject.value).toBe("Freight quote for {orders}{price}");

    const reset = screen.getByRole("button", { name: /Reset to default/ });
    expect(reset.disabled).toBe(false);
    fireEvent.click(reset);
    expect(body.value).toBe(catalog().templates.quote.default.body);
    expect(subject.value).toBe("Freight quote for {orders}");
    expect(reset.disabled).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: /Shipped/ }));
    fireEvent.change(screen.getByLabelText(/^Subject/), { target: { value: "On its way: {orders}" } });
    fireEvent.change(screen.getByLabelText(/^Signature/), { target: { value: "Freight Team" } });
    fireEvent.click(screen.getByLabelText(/CC the order's sales rep/));
    fireEvent.click(screen.getByRole("button", { name: /Save email settings/ }));
    await waitFor(() => expect(callsTo("PUT", "/api/companies/c1")).toHaveLength(1));
    const email = callsTo("PUT", "/api/companies/c1")[0].body.config.email;
    expect(email).toEqual({ signature: "Freight Team", cc_rep: false, templates: { shipped: { subject: "On its way: {orders}", body: "Shipped[[ PRO {pro}]]" } } });
  });

  it("renders templates like the server: optional segments, unknown names and tidy spacing", () => {
    const values = { contact: "Jo", expires: "", price: "$10.00", terms: "" };
    expect(renderTemplate("Hello {contact} ,[[ Valid until {expires}.]] {price} ({terms}) {unknown}", values))
      .toBe("Hello Jo, $10.00 {unknown}");
  });
});

describe("Needs attention", () => {
  const items = [
    { kind: "quote_unanswered", severity: "danger", title: "Q-1 unanswered for 15 days", detail: "SO1", entity_type: "quote", entity_id: "q1", number: "Q-1", age_days: 15 },
    { kind: "overdue_delivery", severity: "warn", title: "S-1 is 2 day(s) late", detail: "SO2", entity_type: "shipment", entity_id: "s1", number: "S-1", age_days: 2 },
    { kind: "missing_cost", severity: "warn", title: "S-2 has no carrier bill", detail: "SO3", entity_type: "shipment", entity_id: "s2", number: "S-2", age_days: 1 },
    { kind: "unassigned_expense", severity: "warn", title: "2 bill(s) without a shipment", detail: "$200.00", entity_type: "unassigned_expense", entity_id: "", number: "", age_days: 0 },
    ...Array.from({ length: 5 }, (_, index) => ({ kind: "missing_actuals", severity: "info", title: `S-${10 + index} has no actual measurements`, detail: "", entity_type: "shipment", entity_id: `m${index}`, number: `S-${10 + index}`, age_days: index })),
  ];

  it("groups by kind, most severe first, with one-click links to the right place", () => {
    render(<AttentionList items={items} />);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(headings[0]).toMatch(/^Quotes waiting for an answer/);
    expect(headings.at(-1)).toMatch(/^Missing actual measurements5$/);
    expect(screen.getByRole("link", { name: /Q-1 unanswered/ }).getAttribute("href")).toBe("#/quotes?quote=q1");
    expect(screen.getByRole("link", { name: /S-1 is 2 day/ }).getAttribute("href")).toBe("#/shipments/s1");
    expect(screen.getByRole("link", { name: /S-2 has no carrier bill/ }).getAttribute("href")).toBe("#/shipments/s2?tab=costs");
    expect(screen.getByRole("link", { name: /bill\(s\) without a shipment/ }).getAttribute("href")).toBe("#/freight?tab=unassigned");
    expect(screen.getByRole("link", { name: /Q-1 unanswered/ }).textContent).toMatch(/Urgent/);
    // Long groups show four and expand on request.
    expect(screen.queryByText("S-14 has no actual measurements")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show all 5" }));
    expect(screen.getByText("S-14 has no actual measurements")).toBeTruthy();
    expect(attentionHref({ entity_type: "shipment", entity_id: "x", kind: "pickup_today" })).toBe("#/shipments/x");
  });

  it("says when nothing needs attention", () => {
    render(<AttentionList items={[]} />);
    expect(screen.getByText("Nothing needs attention")).toBeTruthy();
  });
});

describe("Profit report", () => {
  const report = (group) => ({
    group, from: null, to: null, exclude_sister: false,
    rows: group === "rep"
      ? [{ key: "Sam Lee", shipments: 3, revenue_cents: 90000, cost_cents: 60000, net_cents: 30000, margin_percent: 33.3, quotes_sent: 4, quotes_approved: 3, quotes_declined: 1, conversion_percent: 75, loss_shipments: 0, shipments_without_cost: 0, extra_revenue_cents: 0, customer_charge_cents: 90000 },
        { key: "(none)", shipments: 1, revenue_cents: 10000, cost_cents: 12000, net_cents: -2000, margin_percent: -20, quotes_sent: 0, quotes_approved: 0, quotes_declined: 0, conversion_percent: null, loss_shipments: 1, shipments_without_cost: 0, extra_revenue_cents: 0, customer_charge_cents: 10000 }]
      : [{ key: "2026-09", shipments: 4, revenue_cents: 100000, cost_cents: 72000, net_cents: 28000, margin_percent: 28, quotes_sent: 4, quotes_approved: 3, quotes_declined: 1, conversion_percent: 75, loss_shipments: 1, shipments_without_cost: 0, extra_revenue_cents: 0, customer_charge_cents: 100000 }],
    totals: { key: "total", shipments: 4, revenue_cents: 100000, cost_cents: 72000, net_cents: 28000, margin_percent: 28, quotes_sent: 4, quotes_approved: 3, quotes_declined: 1, conversion_percent: 75, loss_shipments: 1, shipments_without_cost: 0, extra_revenue_cents: 0, customer_charge_cents: 100000, unassigned_expense_cents: 1850, unassigned_revenue_cents: 1200, net_with_unassigned_cents: 27350 },
  });

  function Harness({ onDrill }) {
    const [state, setState] = useState({ group: "month", sister: "" });
    return <Reports company={company()} multiCompany={false} range={{ range: "all", from: null, to: null }} group={state.group}
      excludeSister={state.sister === "exclude"} onChange={(changes) => setState((current) => ({ ...current, ...changes }))} onDrill={onDrill} />;
  }

  it("switches grouping, keeps totals with unassigned money and drills into the ledger", async () => {
    route("GET", "/api/reports/summary", ({ url }) => report(new URLSearchParams(url.split("?")[1]).get("group")));
    const onDrill = vi.fn();
    render(<Harness onDrill={onDrill} />);
    expect(await screen.findByRole("button", { name: "Sep 2026" })).toBeTruthy();
    expect(screen.getByText("Net incl. unassigned")).toBeTruthy();
    expect(screen.getAllByText("$273.50").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toBe("/api/reports/summary.csv?group=month");

    fireEvent.click(screen.getByRole("button", { name: "Sales rep" }));
    expect(await screen.findByRole("button", { name: "Sam Lee" })).toBeTruthy();
    expect(callsTo("GET", "/api/reports/summary").at(-1).url).toBe("/api/reports/summary?group=rep");
    expect(screen.getByRole("button", { name: "Not recorded" })).toBeTruthy(); // "(none)" key
    expect(screen.getByRole("columnheader", { name: /Sales rep/ })).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Exclude sister company"));
    await waitFor(() => expect(callsTo("GET", "/api/reports/summary").at(-1).url).toBe("/api/reports/summary?group=rep&exclude_sister=true"));

    fireEvent.click(await screen.findByRole("button", { name: "Sam Lee" }));
    expect(onDrill).toHaveBeenCalledWith("rep", "Sam Lee");
  });
});

describe("Number table and periods", () => {
  it("renders only a window of rows for large tables and sorts numbers descending first", () => {
    const rows = Array.from({ length: 2000 }, (_, index) => ({ id: `r${index}`, n: index }));
    render(<NumTable rows={rows} caption="Big" columns={[{ key: "id", label: "Row" }, { key: "n", label: "Value", num: true }]} />);
    expect(screen.getAllByRole("row").length).toBeLessThan(100);
    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("2001");
    fireEvent.click(screen.getByRole("button", { name: "Value" }));
    expect(screen.getAllByRole("row")[1].textContent).toContain("1999");
  });

  it("computes preset ranges in local days", () => {
    const today = new Date(2026, 9, 9);
    expect(rangeFor("last_month", {}, today)).toEqual({ from: "2026-09-01", to: "2026-09-30" });
    expect(rangeFor("this_quarter", {}, today)).toEqual({ from: "2026-10-01", to: null });
    expect(rangeFor("last_year", {}, today)).toEqual({ from: "2025-01-01", to: "2025-12-31" });
    expect(rangeFor("custom", { from: "2026-01-05" }, today)).toEqual({ from: "2026-01-05", to: null });
  });
});
