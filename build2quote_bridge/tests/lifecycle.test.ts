import { beforeEach, describe, expect, it, vi } from "vitest";

import { BridgeIndicator } from "../src/indicator";
import { assessStructuralHealth, fillWithBoundedRefill, SerializedAsyncRunner, waitForStableVerification } from "../src/durability";
import { sessionKey, expectedBrokerHost, knownAuthenticationHost, parseLaunchSession, SessionRepository } from "../src/session";
import type { BridgeSession } from "../src/types";

const future = "2099-01-01T00:00:00.000Z";
const bridgeSession: BridgeSession = {
  request_id: "request-one",
  token: "token-value-long-enough",
  bridge_origin: "http://127.0.0.1:8000",
  broker: "tql",
  expires_at: future,
};

class MemoryStorage {
  values: Record<string, unknown> = {};
  async get(key: string) { return { [key]: this.values[key] }; }
  async set(items: Record<string, unknown>) { Object.assign(this.values, items); }
  async remove(key: string) { delete this.values[key]; }
}

function encodedSession(session: BridgeSession): string {
  return btoa(JSON.stringify(session)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

describe("handoff session lifecycle", () => {
  it("parses the launch payload and rejects non-loopback bridge origins", () => {
    expect(parseLaunchSession(`#build2quote=${encodedSession(bridgeSession)}`)).toEqual(bridgeSession);
    expect(parseLaunchSession(`#build2quote=${encodedSession({ ...bridgeSession, bridge_origin: "https://example.com" })}`)).toBeNull();
  });

  it("restores only in the same tab and matching broker", async () => {
    const storage = new MemoryStorage();
    const repository = new SessionRepository(storage);
    await repository.store(17, bridgeSession);
    expect(await repository.load(17, "tql")).toEqual(bridgeSession);
    expect(await repository.load(18, "tql")).toBeNull();
    expect(await repository.load(17, "echoship")).toBeNull();
    expect(await repository.loadForTab(17)).toEqual(bridgeSession);
    expect(await repository.loadForTab(18)).toBeNull();
    expect(knownAuthenticationHost("https://login.microsoftonline.com/common/oauth2/authorize")).toBe(true);
    expect(knownAuthenticationHost("https://tenant.b2clogin.com/oauth2/v2.0/authorize")).toBe(true);
    expect(knownAuthenticationHost("https://tenant.b2clogin.com.evil.test/")).toBe(false);
    expect(knownAuthenticationHost("https://mytms.us.dsv.com/auth/realms/road/protocol/openid-connect/auth")).toBe(true);
    expect(knownAuthenticationHost("https://mytms.us.dsv.com/dsvweb/dashboard/quote")).toBe(false);
    expect(knownAuthenticationHost("http://mytms.us.dsv.com/auth/realms/road")).toBe(false);
    expect(knownAuthenticationHost("https://mytms.us.dsv.com.evil.test/auth/realms/road")).toBe(false);
  });

  it("returns the superseded session, cancels, and expires stored sessions", async () => {
    const storage = new MemoryStorage();
    const repository = new SessionRepository(storage);
    await repository.store(17, bridgeSession);
    const newer = { ...bridgeSession, request_id: "request-two" };
    expect(await repository.store(18, newer)).toEqual({ tab_id: 17, session: bridgeSession });
    expect(await repository.load(17, "tql")).toBeNull();
    expect(await repository.load(18, "tql")).toEqual(newer);
    await repository.clear(18, "wrong-request");
    expect(storage.values[sessionKey("tql")]).toBeTruthy();
    await repository.clear(18, "request-two");
    expect(storage.values[sessionKey("tql")]).toBeUndefined();

    await expect(repository.store(18, { ...bridgeSession, expires_at: "2020-01-01T00:00:00.000Z" })).rejects.toThrow("expired");
    expect(await repository.load(18, "tql", Date.now())).toBeNull();
    expect(storage.values[sessionKey("tql")]).toBeUndefined();
  });

  it("keeps all five brokers independent across supersession, tab closure and expiry", async () => {
    const repository = new SessionRepository(new MemoryStorage());
    const all = ["echoship", "tql", "dsv", "myunishippers", "odfl"] as const;
    for (const [i, broker] of all.entries()) await repository.store(i + 1, { ...bridgeSession, broker, request_id: broker });
    await repository.store(9, { ...bridgeSession, request_id: "new-tql" });
    expect(await repository.load(2, "tql")).toBeNull();
    expect((await repository.load(9, "tql"))?.request_id).toBe("new-tql");
    await repository.clear(9);
    for (const [i, broker] of all.entries()) if (broker !== "tql") expect((await repository.load(i + 1, broker))?.request_id).toBe(broker);
    expect(await repository.load(1, "echoship", Date.parse(future) + 1)).toBeNull();
    expect(await repository.load(3, "dsv")).not.toBeNull();
    expect(expectedBrokerHost("tql", "https://trax.tqlapp.com/quote-details")).toBe(true);
    expect(expectedBrokerHost("tql", "https://trax.tqlapp.com.evil.test/")).toBe(false);
    expect(expectedBrokerHost("tql", "https://echoship.com/")).toBe(false);
    expect(expectedBrokerHost("odfl", "https://www.odfl.com/us/en/tools/freight-shipping-rate-estimate/ltl-rate-estimate.html")).toBe(true);
  });
});

describe("minimal bridge indicator", () => {
  beforeEach(() => { document.documentElement.innerHTML = "<head></head><body></body>"; });

  it("stays a status dot until the user opens its compact popover", () => {
    const actions = { retry: vi.fn(), copy: vi.fn(), cancel: vi.fn() };
    const indicator = new BridgeIndicator(actions);
    const toggle = () => indicator.shadow.querySelector<HTMLButtonElement>("#b2q-toggle")!;
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(indicator.shadow.querySelector(".dot")).toBeTruthy();
    expect(indicator.shadow.querySelector(".popover")).toBeNull();
    toggle().click();
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    indicator.setExpanded(false);
    indicator.render("Review required", "One field needs attention", [{ field: "delivery ZIP", ok: false, message: "Not selected" }]);
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(indicator.shadow.querySelector(".popover")).toBeNull();
    toggle().click();
    expect(indicator.shadow.textContent).toContain("One field needs attention");
    expect(indicator.shadow.textContent).not.toContain("delivery ZIP");
    indicator.shadow.querySelector<HTMLButtonElement>("#b2q-retry")!.click();
    expect(actions.retry).toHaveBeenCalledOnce();
  });

  it("shows the run progress next to the dot and clears it", () => {
    const indicator = new BridgeIndicator({ retry: vi.fn(), copy: vi.fn(), cancel: vi.fn() });
    expect(indicator.shadow.querySelector(".progress-label")).toBeNull();
    indicator.setProgress("Filling 2 of 5…");
    expect(indicator.shadow.querySelector(".progress-label")?.textContent).toBe("Filling 2 of 5…");
    expect(indicator.shadow.querySelector("#b2q-toggle")?.getAttribute("title")).toMatch(/^Filling 2 of 5… · /);
    indicator.setProgress(null);
    expect(indicator.shadow.querySelector(".progress-label")).toBeNull();
    indicator.destroy();
  });

  it("reattaches if the carrier removes the host element", async () => {
    const indicator = new BridgeIndicator({ retry: vi.fn(), copy: vi.fn(), cancel: vi.fn() });
    indicator.host.remove();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(indicator.host.isConnected).toBe(true);
    indicator.destroy();
    expect(indicator.host.isConnected).toBe(false);
  });
});

describe("durable form health", () => {
  beforeEach(() => { document.documentElement.innerHTML = "<head></head><body><form id='quote'></form></body>"; });

  it("distinguishes one manual edit from a structural reset", () => {
    const root = document.querySelector("form");
    const fields = [
      { field: "delivery ZIP", ok: false, message: "changed" },
      { field: "pallet weight", ok: true, message: "" },
      { field: "pallet dimensions", ok: true, message: "" },
    ];
    expect(assessStructuralHealth(root, [{ field: "form", ok: true, message: "" }], fields)).toMatchObject({
      structuralReset: false, manualReview: true,
    });
    expect(assessStructuralHealth(root, [{ field: "form", ok: true, message: "" }], [
      ...fields.slice(0, 1),
      { field: "pallet weight", ok: false, message: "cleared" },
      { field: "pallet dimensions", ok: false, message: "cleared" },
    ])).toMatchObject({ structuralReset: true });
  });

  it("detects removed handling-unit rows and replaced roots", () => {
    const root = document.querySelector("form")!;
    expect(assessStructuralHealth(root, [], [
      { field: "handling unit count", ok: false, message: "0 rows; expected 3" },
    ]).structuralReset).toBe(true);
    root.remove();
    expect(assessStructuralHealth(root, [], []).structuralReset).toBe(true);
  });

  it("requires an uninterrupted stable verification window", async () => {
    vi.useFakeTimers();
    let valid = false;
    const promise = waitForStableVerification(() => [{ field: "form", ok: valid, message: "" }], 1500, 4000);
    await vi.advanceTimersByTimeAsync(600);
    valid = true;
    await vi.advanceTimersByTimeAsync(1600);
    expect((await promise).every((field) => field.ok)).toBe(true);
    vi.useRealTimers();
  });

  it("stabilizes verification after a fill failure without forcing a refill", async () => {
    const fill = vi.fn(async () => [{ field: "postal", ok: false, message: "carrier pending" }]);
    const outcome = await fillWithBoundedRefill({
      fill,
      verify: () => [{ field: "postal verification", ok: true, message: "committed" }],
      canRefill: () => true,
      stableMs: 0,
      timeoutMs: 100,
    });
    expect(fill).toHaveBeenCalledOnce();
    expect(outcome.refilled).toBe(false);
    expect(outcome.verified.every(field => field.ok)).toBe(true);
  });

  it("performs at most one idempotent refill and honors a trusted-change veto", async () => {
    let fillCount = 0;
    const outcome = await fillWithBoundedRefill({
      fill: async () => {
        fillCount += 1;
        return [{ field: "form", ok: fillCount > 1, message: "" }];
      },
      verify: () => [{ field: "form", ok: fillCount > 1, message: "" }],
      canRefill: () => true,
      stableMs: 0,
      timeoutMs: 1,
    });
    expect(fillCount).toBe(2);
    expect(outcome).toMatchObject({ refilled: true, verified: [{ ok: true }] });

    const protectedFill = vi.fn(async () => [{ field: "form", ok: false, message: "changed" }]);
    const protectedOutcome = await fillWithBoundedRefill({
      fill: protectedFill,
      verify: () => [{ field: "form", ok: false, message: "changed" }],
      canRefill: () => false,
      stableMs: 0,
      timeoutMs: 1,
    });
    expect(protectedFill).toHaveBeenCalledOnce();
    expect(protectedOutcome.refilled).toBe(false);
  });

  it("does not wait, refill, or repair for manual notes", async () => {
    const notes = [
      { field: "Limited access", ok: false, message: "Set manually", manual: "set" as const },
      { field: "pallet 1 dimensions manual review required", ok: false, message: "TQL blank editors", manual: "check" as const },
    ];
    const fill = vi.fn(async () => [{ field: "zip", ok: true, message: "" }]);
    const started = Date.now();
    const outcome = await fillWithBoundedRefill({
      fill, verify: () => [{ field: "zip", ok: true, message: "" }, ...notes], canRefill: () => true, stableMs: 50, timeoutMs: 5000,
    });
    expect(fill).toHaveBeenCalledOnce();
    expect(outcome.refilled).toBe(false);
    expect(Date.now() - started).toBeLessThan(1000);
    const root = document.querySelector("form");
    expect(assessStructuralHealth(root, [{ field: "form", ok: true, message: "" }], [{ field: "zip", ok: true, message: "" }, ...notes]))
      .toMatchObject({ structuralReset: false, manualReview: false, failures: [] });
  });

  it("stops waiting when the same failures persist instead of running out the timeout", async () => {
    vi.useFakeTimers();
    try {
      let settled = false;
      const result = waitForStableVerification(() => [{ field: "zip", ok: false, message: "not committed" }], 800, 10000, 3000)
        .then((value) => { settled = true; return value; });
      await vi.advanceTimersByTimeAsync(2900);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(200);
      expect(settled).toBe(true);
      expect((await result)[0].ok).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("serializes overlapping health checks and coalesces the pending work", async () => {
    let releaseFirst!: () => void;
    const firstBlocked = new Promise<void>(resolve => { releaseFirst = resolve; });
    let active = 0;
    let maximumActive = 0;
    let runs = 0;
    const runner = new SerializedAsyncRunner(async () => {
      runs += 1;
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (runs === 1) await firstBlocked;
      active -= 1;
    });
    const completed = runner.request();
    void runner.request();
    void runner.request();
    releaseFirst();
    await completed;
    expect(runs).toBe(2);
    expect(maximumActive).toBe(1);
  });
});
