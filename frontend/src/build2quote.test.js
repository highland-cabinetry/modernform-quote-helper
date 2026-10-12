import { afterEach, expect, it, vi } from "vitest";
import { BUILD2QUOTE_EXTENSION_ID, Build2QuoteLaunchTimeoutError, openPreparedBrokerTabs } from "./build2quote";

afterEach(() => { vi.unstubAllGlobals(); });
it("sends the complete prepared batch to the installed extension", async () => {
  const sendMessage = vi.fn((_id, _message, callback) => callback({ ok: true, tab_ids: [11, 12] }));
  vi.stubGlobal("chrome", { runtime: { sendMessage } });
  const handoffs = [{ broker: "tql", url: "https://trax.tqlapp.com/quote-details#test" }, { broker: "dsv", url: "https://mytms.us.dsv.com/quote#test" }];
  expect(await openPreparedBrokerTabs(handoffs)).toEqual({ ok: true, tab_ids: [11, 12] });
  expect(sendMessage).toHaveBeenCalledWith(BUILD2QUOTE_EXTENSION_ID, { type: "BUILD2QUOTE_OPEN_TABS", handoffs }, expect.any(Function));
});
it("reports extension failure without a window.open fallback", async () => {
  vi.stubGlobal("chrome", { runtime: { sendMessage: (_id, _message, callback) => callback({ok:false,error:"storage failed"}) } });
  const error = await openPreparedBrokerTabs([]).catch(value => value);
  expect(error).toEqual(expect.objectContaining({ message: expect.stringContaining("storage failed") }));
  expect(error.ambiguous).not.toBe(true);
});
it("waits 45 seconds for a tab batch and marks only its timeout as ambiguous", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("chrome", { runtime: { sendMessage: vi.fn() } });
  const handoffs = Array.from({ length: 5 }, (_, index) => ({ broker: `broker-${index}`, url: `https://broker-${index}.test` }));
  const launch = openPreparedBrokerTabs(handoffs);
  const rejected = expect(launch).rejects.toEqual(expect.objectContaining({
    name: "Build2QuoteLaunchTimeoutError",
    ambiguous: true,
  }));
  await vi.advanceTimersByTimeAsync(44999);
  await vi.advanceTimersByTimeAsync(1);
  await rejected;
  expect(await launch.catch(error => error)).toBeInstanceOf(Build2QuoteLaunchTimeoutError);
  vi.useRealTimers();
});
