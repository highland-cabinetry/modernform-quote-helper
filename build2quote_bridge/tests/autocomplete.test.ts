import { beforeEach, describe, expect, it, vi } from "vitest";
import { looksResolved, matchesPostalAddress, selectExactPostalCode } from "../src/autocomplete";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{} as DOMRect]);
  document.body.innerHTML = '<input id="zip" aria-controls="suggestions"><div id="suggestions"></div><div role="option">59714 unrelated popup</div>';
});
const control = () => document.querySelector<HTMLInputElement>("#zip");
function respond(options: string[], commit: boolean) {
  control()!.addEventListener("input", () => {
    document.querySelector("#suggestions")!.innerHTML = options.map(text => `<button role="option">${text}</button>`).join("");
    document.querySelectorAll("#suggestions button").forEach(option => option.addEventListener("click", () => {
      if (commit) control()!.value = option.textContent!;
    }));
  });
}
describe("postal selection evidence", () => {
  it("accepts a plain ZIP only when selection produces an independent carrier marker", async () => {
    let selected = false;
    control()!.addEventListener("input", () => {
      document.querySelector("#suggestions")!.innerHTML = '<button role="option">59714 Belgrade MT</button>';
      document.querySelector("#suggestions button")!.addEventListener("click", () => { selected = true; });
    });
    // Keep this browser-style timing assertion tolerant of parallel Vitest
    // worker load; the carrier marker and exact ZIP checks remain mandatory.
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", verifySelection: () => selected, timeoutMs: 1500 })).toBe(true);
    expect(looksResolved(control(), "59714")).toBe(true);
  });
  it("selects the exact active option from a reused Kendo list only when opted in", async () => {
    const input = control()!;
    input.setAttribute("aria-activedescendant", "current-postal-option");
    const list = document.querySelector("#suggestions")!;
    list.innerHTML = '<li id="current-postal-option" role="option">59106 - Billings, MT, USA</li>';
    let selected = false;
    list.querySelector("[role='option']")!.addEventListener("click", () => {
      selected = true;
      input.value = "59106 - Billings, MT, USA";
    });
    const address = { name: "", street: "", city: "Billings", state: "MT", postal_code: "59106", phone: "", email: "" };

    expect(await selectExactPostalCode({
      getControl: control,
      zip: address.postal_code,
      matchesOption: candidate => matchesPostalAddress(candidate, address),
      verifyResolved: () => control()?.value === "59106 - Billings, MT, USA",
      activeDescendantIsFresh: true,
      timeoutMs: 500,
    })).toBe(true);
    expect(selected).toBe(true);
    expect(looksResolved(control(), "59106")).toBe(true);
  });
  it("uses supplied city and state to distinguish cities sharing one ZIP", async () => {
    respond(["80022 Commerce City CO", "80022 Adams City CO"], true);
    const address = { name: "", street: "", city: "Commerce City", state: "CO", postal_code: "80022", phone: "", email: "" };
    expect(await selectExactPostalCode({ getControl: control, zip: address.postal_code,
      matchesOption: candidate => matchesPostalAddress(candidate, address), timeoutMs: 150 })).toBe(true);
    expect(control()!.value).toBe("80022 Commerce City CO");
  });
  it("accepts one current associated option only after carrier commit", async () => {
    respond(["59714 Belgrade MT"], true);
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", timeoutMs: 150 })).toBe(true);
    expect(looksResolved(control(), "59714")).toBe(true);
    control()!.value = "80011";
    expect(looksResolved(control(), "59714")).toBe(false);
  });
  it("accepts an asynchronous carrier commit after the suggestion list disappears", async () => {
    let resolved = false;
    control()!.addEventListener("input", () => {
      document.querySelector("#suggestions")!.replaceChildren();
      setTimeout(() => {
        control()!.value = "59714 Belgrade MT";
        resolved = true;
      }, 25);
    });
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", verifyResolved: () => resolved, timeoutMs: 150 })).toBe(true);
    expect(looksResolved(control(), "59714")).toBe(true);
  });
  it("does not accept rich raw text when a carrier verifier rejects the commit", async () => {
    respond(["59714 Belgrade MT"], true);
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", verifySelection: () => false,
      requireCarrierVerification: true, timeoutMs: 150 })).toBe(false);
    expect(looksResolved(control(), "59714")).toBe(false);
  });
  it.each(["duplicate", "no-op", "unrelated", "stale"])("rejects %s suggestions", async kind => {
    if (kind === "duplicate") respond(["59714 A", "59714 B"], true);
    if (kind === "no-op") respond(["59714 Belgrade MT"], false);
    if (kind === "stale") document.querySelector("#suggestions")!.innerHTML = '<button role="option">59714 old response</button>';
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", timeoutMs: 100 })).toBe(false);
    expect(looksResolved(control(), "59714")).toBe(false);
  });
  it("cancels a replaced control before selecting", async () => {
    control()!.addEventListener("input", () => { control()!.outerHTML = '<input id="zip" aria-controls="suggestions">'; });
    expect(await selectExactPostalCode({ getControl: control, zip: "59714", timeoutMs: 100 })).toBe(false);
  });
});
