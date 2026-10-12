import { afterEach, describe, expect, it, vi } from "vitest";

import { HIDDEN_SLEEP_LIMIT_MS, sleep, waitFor, waitForQuiet, waitForStable, type Clock } from "../src/timing";

const hiddenClock: Clock = { hidden: () => true, now: () => Date.now() };
const visibleClock: Clock = { hidden: () => false, now: () => Date.now() };

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("throttle-resistant sleep", () => {
  it("finishes a short hidden-page sleep without relying on a timer", async () => {
    // Simulate Chrome's hidden-tab clamp: timers never fire during the test.
    const timeout = vi.spyOn(globalThis, "setTimeout").mockImplementation((() => 0) as unknown as typeof setTimeout);
    vi.spyOn(globalThis, "clearTimeout").mockImplementation(() => undefined);
    const started = Date.now();
    await sleep(40, hiddenClock);
    expect(Date.now() - started).toBeGreaterThanOrEqual(39);
    expect(timeout).toHaveBeenCalledOnce();
  });

  it("uses an ordinary timer when the page is visible or the wait is long", async () => {
    vi.useFakeTimers();
    let done = false;
    void sleep(150, visibleClock).then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(149);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toBe(true);

    let long = false;
    void sleep(HIDDEN_SLEEP_LIMIT_MS + 1, hiddenClock).then(() => { long = true; });
    await vi.advanceTimersByTimeAsync(HIDDEN_SLEEP_LIMIT_MS + 1);
    expect(long).toBe(true);
  });
});

describe("condition-based waits", () => {
  it("wakes on a DOM mutation instead of waiting for the poll", async () => {
    document.body.innerHTML = "<div id='host'></div>";
    const started = Date.now();
    setTimeout(() => document.getElementById("host")!.append(document.createElement("li")), 20);
    expect(await waitFor(() => document.querySelectorAll("li").length === 1, { timeoutMs: 3000, pollMs: 2000 })).toBe(true);
    expect(Date.now() - started).toBeLessThan(1000);
  });

  it("returns the final predicate value at the timeout", async () => {
    expect(await waitFor(() => false, { timeoutMs: 30, pollMs: 10 })).toBe(false);
  });

  it("requires the stable window and restarts it when the predicate fails", async () => {
    vi.useFakeTimers();
    let valid = true;
    const result = waitForStable(() => valid, { stableMs: 600, timeoutMs: 5000, pollMs: 50 });
    await vi.advanceTimersByTimeAsync(300);
    valid = false;
    await vi.advanceTimersByTimeAsync(100);
    valid = true;
    let settled = false;
    void result.then(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(500);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    expect(settled).toBe(true);
    expect(await result).toBe(true);
  });

  it("gives up after the failure grace period", async () => {
    vi.useFakeTimers();
    const result = waitForStable(() => false, { stableMs: 600, failureGraceMs: 400, timeoutMs: 5000, pollMs: 50 });
    await vi.advanceTimersByTimeAsync(450);
    expect(await result).toBe(false);
  });

  it("waits for the page to stop re-rendering, but not forever", async () => {
    document.body.innerHTML = "<form id='quote'></form>";
    const form = document.getElementById("quote")!;
    const started = Date.now();
    const mutate = setInterval(() => form.append(document.createElement("span")), 20);
    setTimeout(() => clearInterval(mutate), 300);
    expect(await waitForStable(() => true, { stableMs: 100, quietMs: 200, quietRoot: () => form, timeoutMs: 3000, pollMs: 20 })).toBe(true);
    const quietAfter = Date.now() - started;
    expect(quietAfter).toBeGreaterThanOrEqual(450);

    const busy = setInterval(() => form.append(document.createElement("span")), 20);
    try {
      const capped = Date.now();
      expect(await waitForStable(() => true, {
        stableMs: 100, quietMs: 200, quietRoot: () => form, maxStableMs: 400, timeoutMs: 3000, pollMs: 20,
      })).toBe(true);
      expect(Date.now() - capped).toBeLessThan(1500);
    } finally {
      clearInterval(busy);
    }
  });

  it("waitForQuiet returns after a quiet period or the cap", async () => {
    document.body.innerHTML = "<div id='row'></div>";
    const row = document.getElementById("row")!;
    const started = Date.now();
    await waitForQuiet(row, 30, 150);
    expect(Date.now() - started).toBeLessThan(140);
    const busy = setInterval(() => row.append(document.createElement("i")), 10);
    try {
      const capped = Date.now();
      await waitForQuiet(row, 50, 150);
      expect(Date.now() - capped).toBeGreaterThanOrEqual(140);
      expect(Date.now() - capped).toBeLessThan(600);
    } finally {
      clearInterval(busy);
    }
  });
});
