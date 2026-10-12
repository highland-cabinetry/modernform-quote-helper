import { afterEach, describe, expect, it, vi } from "vitest";
import { FillRunGate, isManualQuoteRequest, isMatchingSupersession, isTrustedFieldChange } from "../src/autofill_lifecycle";
import { EchoAdapter } from "../src/adapters/echo";
import { TqlAdapter } from "../src/adapters/tql";
import { DsvAdapter } from "../src/adapters/dsv";
import { MyUnishippersAdapter } from "../src/adapters/myunishippers";
import { OdflAdapter } from "../src/adapters/odfl";

describe("autofill-only contract", () => {
  afterEach(() => vi.unstubAllGlobals());
  it.each([new EchoAdapter(), new TqlAdapter(), new DsvAdapter(), new MyUnishippersAdapter()])("has no rate reader: $broker", adapter => {
    expect("readRates" in adapter).toBe(false);
  });
  it("keeps ODFL fill-only", () => {
    expect("readRates" in new OdflAdapter()).toBe(false);
  });
  it.each(["Get Quotes", "Get Quote", "Get Rates", "View Rates"])("disarms only on the user's %s action", label => {
    document.body.innerHTML = `<button><span>${label}</span></button>`;
    const target = document.querySelector("span")!;
    expect(isManualQuoteRequest({ target, isTrusted: true })).toBe(true);
    expect(isManualQuoteRequest({ target, isTrusted: false })).toBe(false);
  });
  it("does not confuse normal fill controls with quote submission", () => {
    document.body.innerHTML = "<button>Add handling unit</button>";
    expect(isManualQuoteRequest({ target: document.querySelector("button")!, isTrusted: true })).toBe(false);
  });
  it("recognizes only a trusted ODFL Submit as the manual quote request", () => {
    document.body.innerHTML = "<button><span>Submit</span></button>";
    const target = document.querySelector("span")!;
    vi.stubGlobal("location", { hostname: "www.odfl.com" });
    expect(isManualQuoteRequest({ target, isTrusted: true })).toBe(true);
    expect(isManualQuoteRequest({ target, isTrusted: false })).toBe(false);
    vi.stubGlobal("location", { hostname: "example.com" });
    expect(isManualQuoteRequest({ target, isTrusted: true })).toBe(false);
  });
  it("identifies trusted field changes and the matching supersession only", () => {
    document.body.innerHTML = "<input><button>Other</button>";
    expect(isTrustedFieldChange({ target: document.querySelector("input")!, isTrusted: true })).toBe(true);
    expect(isTrustedFieldChange({ target: document.querySelector("input")!, isTrusted: false })).toBe(false);
    expect(isTrustedFieldChange({ target: document.querySelector("button")!, isTrusted: true })).toBe(false);
    expect(isMatchingSupersession({ type: "BUILD2QUOTE_SESSION_SUPERSEDED", request_id: "one" }, "one")).toBe(true);
    expect(isMatchingSupersession({ type: "BUILD2QUOTE_SESSION_SUPERSEDED", request_id: "two" }, "one")).toBe(false);
  });
  it("coalesces repeated Retry clicks during one fill", () => {
    const gate = new FillRunGate();
    expect(gate.tryStart()).toBe(true);
    expect(gate.queueRetry()).toBe(true);
    expect(gate.queueRetry()).toBe(true);
    expect(gate.tryStart()).toBe(false);
    expect(gate.finish()).toBe(true);
    expect(gate.finish()).toBe(false);
  });
});
