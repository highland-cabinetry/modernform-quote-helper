import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { compareVersions, handleReloadRequest, isQuoteHelperSender, RELOAD_COOLDOWN_MS, type ReloadDeps, type ReloadRecord } from "../src/external";
// The Quote Helper side of the update handshake (frontend/src/build2quote.js).
import { ensureBridgeCurrent, resetBridgeReloadGuardForTests } from "../../frontend/src/build2quote.js";

function deps(overrides: Partial<ReloadDeps> = {}) {
  let record: ReloadRecord | null = null;
  const value = {
    runningVersion: "3.1.0",
    now: () => 1_000_000,
    busy: vi.fn(async () => false),
    lastReload: vi.fn(async () => record),
    rememberReload: vi.fn(async (next: ReloadRecord) => { record = next; }),
    reload: vi.fn(),
    schedule: vi.fn((callback: () => void) => callback()),
    ...overrides,
  };
  return value;
}

describe("BUILD2QUOTE_RELOAD in the Bridge", () => {
  it("accepts only the local Quote Helper page and compares dotted versions", () => {
    expect(isQuoteHelperSender("http://127.0.0.1:8765/#/quote")).toBe(true);
    expect(isQuoteHelperSender("http://localhost:5173/")).toBe(true);
    expect(isQuoteHelperSender("https://127.0.0.1/")).toBe(false);
    expect(isQuoteHelperSender("http://127.0.0.1.evil.test/")).toBe(false);
    expect(isQuoteHelperSender(undefined)).toBe(false);
    expect(compareVersions("3.10.0", "3.9.9")).toBeGreaterThan(0);
    expect(compareVersions("3.1", "3.1.0")).toBe(0);
    expect(compareVersions("3.1.0", "3.2.0")).toBeLessThan(0);
  });

  it("reloads an older Bridge once and refuses a second reload of the same version", async () => {
    const bridge = deps();
    expect(await handleReloadRequest({ expected_version: "3.2.0" }, bridge)).toEqual({ ok: true, reloading: true, version: "3.1.0" });
    expect(bridge.reload).toHaveBeenCalledOnce();
    const again = await handleReloadRequest({ expected_version: "3.2.0" }, bridge);
    expect(again).toMatchObject({ ok: false, reloading: false });
    expect(bridge.reload).toHaveBeenCalledOnce();
    const later = deps({ now: () => 1_000_000 + RELOAD_COOLDOWN_MS, lastReload: async () => ({ at: 1_000_000, from: "3.1.0" }) });
    expect(await handleReloadRequest({ expected_version: "3.2.0" }, later)).toMatchObject({ reloading: true });
  });

  it("does not reload a current Bridge or one that is filling broker pages", async () => {
    const current = deps();
    expect(await handleReloadRequest({ expected_version: "3.1.0" }, current)).toEqual({ ok: true, reloading: false, version: "3.1.0" });
    expect(current.reload).not.toHaveBeenCalled();
    const busy = deps({ busy: vi.fn(async () => true) });
    expect(await handleReloadRequest({ expected_version: "3.2.0" }, busy)).toMatchObject({ ok: false, busy: true });
    expect(busy.reload).not.toHaveBeenCalled();
    expect(busy.rememberReload).not.toHaveBeenCalled();
  });
});

describe("ensureBridgeCurrent in Quote Helper", () => {
  let running = "3.1.0";
  let reloads = 0;
  let reloadSupported = true;
  let reloadResponse: Record<string, unknown> = { ok: true, reloading: true };

  beforeEach(() => {
    running = "3.1.0";
    reloads = 0;
    reloadSupported = true;
    reloadResponse = { ok: true, reloading: true };
    window.localStorage.clear();
    resetBridgeReloadGuardForTests();
    const runtime = {
      lastError: undefined as undefined | { message: string },
      sendMessage: vi.fn((_id: string, message: { type: string }, callback: (response?: unknown) => void) => {
        runtime.lastError = undefined;
        if (message.type === "BUILD2QUOTE_PING") { callback({ connected: true, version: running }); return; }
        if (message.type === "BUILD2QUOTE_RELOAD") {
          if (!reloadSupported) { runtime.lastError = { message: "The message port closed before a response was received." }; callback(undefined); return; }
          reloads += 1;
          callback({ version: running, ...reloadResponse });
          if (reloadResponse.reloading) setTimeout(() => { running = "3.2.0"; }, 30);
        }
      }),
    };
    vi.stubGlobal("chrome", { runtime });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("reloads an older Bridge and reports the new version", async () => {
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 2000, pollMs: 20 })).toEqual({ state: "connected", version: "3.2.0" });
    expect(reloads).toBe(1);
  });

  it("shares one check between callers that start together", async () => {
    const [shell, page] = await Promise.all([
      ensureBridgeCurrent("3.2.0", { waitMs: 2000, pollMs: 20 }),
      ensureBridgeCurrent("3.2.0", { waitMs: 2000, pollMs: 20 }),
    ]);
    expect(shell).toEqual({ state: "connected", version: "3.2.0" });
    expect(page).toEqual(shell);
    expect(reloads).toBe(1);
  });

  it("asks at most once per page load and not again for the same versions after a page reload", async () => {
    reloadResponse = { ok: true, reloading: true };
    const stuck = vi.fn();
    // The Bridge reloads but the installed files are still old.
    (globalThis as any).chrome.runtime.sendMessage.mockImplementation((_id: string, message: { type: string }, callback: (response?: unknown) => void) => {
      if (message.type === "BUILD2QUOTE_PING") { callback({ connected: true, version: "3.1.0" }); return; }
      stuck(); callback({ ok: true, reloading: true, version: "3.1.0" });
    });
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 100, pollMs: 20 })).toEqual({ state: "update-required", version: "3.1.0" });
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 100, pollMs: 20 })).toEqual({ state: "update-required", version: "3.1.0" });
    resetBridgeReloadGuardForTests();
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 100, pollMs: 20 })).toEqual({ state: "update-required", version: "3.1.0" });
    expect(stuck).toHaveBeenCalledOnce();
  });

  it("reports an old Bridge without the reload message as update-required, and a missing one as not-installed", async () => {
    reloadSupported = false;
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 100, pollMs: 20 })).toEqual({ state: "update-required", version: "3.1.0" });
    expect(await ensureBridgeCurrent("3.1.0")).toEqual({ state: "connected", version: "3.1.0" });
    vi.stubGlobal("chrome", undefined);
    expect(await ensureBridgeCurrent("3.1.0")).toEqual({ state: "not-installed", version: "" });
  });

  it("may ask again on the same page after a busy refusal", async () => {
    reloadResponse = { ok: false, reloading: false, busy: true, error: "Broker autofill is active, so the Bridge was not reloaded." };
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 100, pollMs: 20 })).toEqual({ state: "update-required", version: "3.1.0" });
    reloadResponse = { ok: true, reloading: true };
    expect(await ensureBridgeCurrent("3.2.0", { waitMs: 2000, pollMs: 20 })).toEqual({ state: "connected", version: "3.2.0" });
    expect(reloads).toBe(2);
  });
});
