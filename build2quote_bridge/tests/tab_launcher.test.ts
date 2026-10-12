import { describe, expect, it, vi } from "vitest";
import { launchAdjacentTabs, launchBrokerTabs } from "../src/tab_launcher";
import type { Broker } from "../src/types";

function launch(broker: Broker, index: number) {
  const payload = btoa(JSON.stringify({ request_id: `request-${index}`, token: "x".repeat(32), broker,
    bridge_origin: "http://127.0.0.1:8000", expires_at: new Date(Date.now() + 300000).toISOString() }));
  const hosts = { echoship: "echoship.com/new-quote/ltl", tql: "trax.tqlapp.com/quote-details",
    dsv: "mytms.us.dsv.com/dsvweb/dashboard/quote", myunishippers: "www.myunishippers.com/ship/ltl",
    odfl: "www.odfl.com/us/en/tools/freight-shipping-rate-estimate/ltl-rate-estimate.html" };
  return { broker, url: `https://${hosts[broker]}#build2quote=${payload}` };
}

describe("adjacent broker tab launcher", () => {
  it("removes a created tab even when storage and session cleanup both fail", async () => {
    const tabs = { create: vi.fn(async () => ({ id: 30 }) as chrome.tabs.Tab),
      update: vi.fn(async (id: number) => ({ id }) as chrome.tabs.Tab), remove: vi.fn(async () => undefined) };
    const sessions = { store: vi.fn(async () => { throw new Error("storage failed"); }),
      clear: vi.fn(async () => { throw new Error("cleanup failed"); }) } as any;
    await expect(launchAdjacentTabs(tabs, sessions, { id: 9, index: 1, windowId: 1 }, [launch("tql", 1)])).rejects.toThrow("storage failed");
    expect(tabs.remove).toHaveBeenCalledWith([30]);
    expect(tabs.update).toHaveBeenLastCalledWith(9, { active: true });
  });
  it("stores before navigation, preserves order, and restores Quote Helper focus", async () => {
    let nextId = 20;
    const calls: string[] = [];
    const tabs = {
      create: vi.fn(async ({ index }: chrome.tabs.CreateProperties) => { calls.push(`create:${index}`); return { id: nextId++ } as chrome.tabs.Tab; }),
      update: vi.fn(async (id: number, value: chrome.tabs.UpdateProperties) => { calls.push(`update:${id}:${value.url || value.active}`); return { id } as chrome.tabs.Tab; }),
      remove: vi.fn(async () => undefined),
    };
    const sessions = { store: vi.fn(async (id: number) => { calls.push(`store:${id}`); return null; }), clear: vi.fn(async () => undefined) } as any;
    const launches = (["echoship", "tql", "dsv", "myunishippers", "odfl"] as Broker[]).map(launch);
    expect(await launchAdjacentTabs(tabs, sessions, { id: 9, index: 3, windowId: 1 }, launches)).toEqual([20, 21, 22, 23, 24]);
    expect(tabs.create.mock.calls.map(call => call[0].index)).toEqual([4, 5, 6, 7, 8]);
    expect(calls.indexOf("store:20")).toBeLessThan(calls.findIndex(value => value.startsWith("update:20:https://echoship")));
    expect(tabs.update).toHaveBeenLastCalledWith(9, { active: true });
  });

  it("notifies the previous broker tab only after replacement storage succeeds", async () => {
    const tabs = { create: vi.fn(async () => ({ id: 30 }) as chrome.tabs.Tab),
      update: vi.fn(async (id: number) => ({ id }) as chrome.tabs.Tab), remove: vi.fn(async () => undefined) };
    const previous = { tab_id: 12, session: { ...JSON.parse(atob(launch("dsv", 1).url.split("#build2quote=")[1])), request_id: "old" } };
    const sessions = { store: vi.fn(async () => previous), clear: vi.fn(async () => undefined) } as any;
    const notify = vi.fn(async () => undefined);
    await launchAdjacentTabs(tabs, sessions, { id: 9, index: 1, windowId: 1 }, [launch("dsv", 2)], notify);
    expect(notify).toHaveBeenCalledWith(previous);
  });

  it("fills a new broker window, reusing its blank first tab and leaving focus to the run", async () => {
    let nextId = 40;
    const calls: string[] = [];
    const tabs = {
      create: vi.fn(async (value: chrome.tabs.CreateProperties) => { calls.push(`create:${value.windowId}:${value.index}`); return { id: nextId++ } as chrome.tabs.Tab; }),
      update: vi.fn(async (id: number, value: chrome.tabs.UpdateProperties) => { calls.push(`update:${id}:${value.active}`); return { id } as chrome.tabs.Tab; }),
      remove: vi.fn(async () => undefined),
    };
    const sessions = { store: vi.fn(async (id: number) => { calls.push(`store:${id}`); return null; }), clear: vi.fn(async () => undefined) } as any;
    const launches = (["tql", "dsv", "odfl"] as Broker[]).map(launch);
    const ids = await launchBrokerTabs(tabs, sessions, { id: 9, index: 0, windowId: 1 },
      { windowId: 77, firstTabId: 30, restoreFocus: false }, launches);
    expect(ids).toEqual([30, 40, 41]);
    expect(calls).toEqual([
      "store:30", "update:30:true",
      "create:77:undefined", "store:40", "update:40:false",
      "create:77:undefined", "store:41", "update:41:false",
    ]);
    expect(tabs.create.mock.calls.every(call => call[0].openerTabId === undefined)).toBe(true);
    expect(tabs.update).not.toHaveBeenCalledWith(9, { active: true });
  });

  it("rolls back all created tabs and sessions when a batch fails", async () => {
    let created = 0;
    const tabs = {
      create: vi.fn(async () => ({ id: 30 + created++ }) as chrome.tabs.Tab),
      update: vi.fn(async (id: number) => { if (id === 31) throw new Error("navigation failed"); return { id } as chrome.tabs.Tab; }),
      remove: vi.fn(async () => undefined),
    };
    const sessions = { store: vi.fn(async () => undefined), clear: vi.fn(async () => undefined) } as any;
    await expect(launchAdjacentTabs(tabs, sessions, { id: 9, index: 1, windowId: 1 }, [launch("echoship", 1), launch("tql", 2)])).rejects.toThrow("navigation failed");
    expect(tabs.remove).toHaveBeenCalledWith([30]);
    expect(tabs.remove).toHaveBeenCalledWith([31]);
    expect(sessions.clear).toHaveBeenCalledTimes(2);
  });
});
