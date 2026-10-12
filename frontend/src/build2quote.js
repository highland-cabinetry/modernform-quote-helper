export const BUILD2QUOTE_EXTENSION_ID = "gnnpjdpaadkdbeceeheoiocbjccndlbb";
import release from "../../release.json";
export const MINIMUM_BUILD2QUOTE_VERSION = release.minimum_bridge_version;

export class Build2QuoteLaunchTimeoutError extends Error {
  constructor() {
    super("Build2Quote Bridge is still opening broker tabs. Check the opened tabs before retrying.");
    this.name = "Build2QuoteLaunchTimeoutError";
    this.ambiguous = true;
  }
}

function externalMessage(message, timeout = 5000, timeoutError = () => new Error("Build2Quote Bridge did not respond.")) {
  return new Promise((resolve, reject) => {
    if (!window.chrome?.runtime?.sendMessage) { reject(new Error("Build2Quote Bridge is not available.")); return; }
    let finished = false;
    const timer = window.setTimeout(() => {
      if (!finished) { finished = true; reject(timeoutError()); }
    }, timeout);
    window.chrome.runtime.sendMessage(BUILD2QUOTE_EXTENSION_ID, message, response => {
      if (finished) return;
      finished = true; window.clearTimeout(timer);
      const runtimeError = window.chrome.runtime.lastError;
      if (runtimeError) reject(new Error(runtimeError.message));
      else if (response?.ok === false) reject(new Error(response.error || "Build2Quote Bridge could not open the broker tab."));
      else resolve(response || {});
    });
  });
}

export async function pingBuild2QuoteBridge() {
  try { return await externalMessage({ type: "BUILD2QUOTE_PING" }, 1500); }
  catch { return { connected: false }; }
}

export function openPreparedBrokerTabs(handoffs) {
  return externalMessage(
    { type: "BUILD2QUOTE_OPEN_TABS", handoffs },
    45000,
    () => new Build2QuoteLaunchTimeoutError(),
  );
}

/** Numeric dotted-version comparison: negative when a < b, 0 when equal, positive when a > b. */
export function compareBridgeVersions(a, b) {
  const left = String(a || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  const right = String(b || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference;
  }
  return 0;
}

/** Remembers a reload attempt across page loads so a Bridge that stays old is not reloaded in a loop. */
const RELOAD_ATTEMPT_KEY = "b2q-bridge-reload-attempt";
const RELOAD_ATTEMPT_WINDOW_MS = 10 * 60 * 1000;
let reloadRequestedThisPage = false;

function readReloadAttempt() {
  try { return JSON.parse(window.localStorage.getItem(RELOAD_ATTEMPT_KEY) || "null"); }
  catch { return null; }
}

function writeReloadAttempt(value) {
  try { window.localStorage.setItem(RELOAD_ATTEMPT_KEY, JSON.stringify(value)); }
  catch { /* storage unavailable: the per-page guard still applies */ }
}

const pause = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));

/**
 * Make sure Chrome runs the Bridge files the installer shipped. Chrome keeps running an unpacked
 * extension's old code until it is reloaded, so when the running Bridge is older than
 * `expectedVersion` this asks it to reload itself (at most once per page load, and not again for
 * the same versions within ten minutes), then waits for it to come back.
 *
 * Resolves to `{ state, version }`; state is "connected", "update-required" or "not-installed".
 * A Bridge older than the first version that handles BUILD2QUOTE_RELOAD stays "update-required"
 * until it is reloaded once by hand in chrome://extensions.
 */
export function ensureBridgeCurrent(expectedVersion, options = {}) {
  // Callers mounting at the same time (app shell and quote page) share one check and one reload.
  const key = String(expectedVersion || "");
  if (!pendingChecks.has(key)) {
    pendingChecks.set(key, checkBridgeCurrent(expectedVersion, options).finally(() => pendingChecks.delete(key)));
  }
  return pendingChecks.get(key);
}

const pendingChecks = new Map();

async function checkBridgeCurrent(expectedVersion, { waitMs = 8000, pollMs = 400 } = {}) {
  const first = await pingBuild2QuoteBridge();
  if (!first.connected) return { state: "not-installed", version: "" };
  const current = value => !expectedVersion || compareBridgeVersions(value, expectedVersion) >= 0;
  if (current(first.version)) return { state: "connected", version: first.version || "" };
  const attempt = readReloadAttempt();
  const since = Date.now() - Number(attempt?.at);
  const recentlyTried = Boolean(attempt) && attempt.from === first.version && attempt.expected === expectedVersion
    && since >= 0 && since < RELOAD_ATTEMPT_WINDOW_MS;
  if (reloadRequestedThisPage || recentlyTried) return { state: "update-required", version: first.version || "" };
  reloadRequestedThisPage = true;
  let response;
  try {
    response = await externalMessage({ type: "BUILD2QUOTE_RELOAD", expected_version: expectedVersion }, 2000);
  } catch (error) {
    // A Bridge busy with broker autofill may be asked again later on this page.
    if (/autofill is active/i.test(error?.message || "")) reloadRequestedThisPage = false;
    return { state: "update-required", version: first.version || "" };
  }
  if (!response?.reloading) {
    const version = response?.version || first.version || "";
    return { state: current(version) ? "connected" : "update-required", version };
  }
  writeReloadAttempt({ from: first.version, expected: expectedVersion, at: Date.now() });
  const deadline = Date.now() + waitMs;
  let latest = { connected: false };
  await pause(Math.min(pollMs, 500));
  while (Date.now() < deadline) {
    latest = await pingBuild2QuoteBridge();
    if (latest.connected && current(latest.version)) return { state: "connected", version: latest.version || "" };
    await pause(pollMs);
  }
  if (!latest.connected) return { state: "not-installed", version: "" };
  return { state: current(latest.version) ? "connected" : "update-required", version: latest.version || "" };
}

/** Test hook: forget this page's reload attempt. */
export function resetBridgeReloadGuardForTests() {
  reloadRequestedThisPage = false;
}
