import { DsvAdapter } from "./adapters/dsv";
import { EchoAdapter } from "./adapters/echo";
import { MyUnishippersAdapter } from "./adapters/myunishippers";
import { OdflAdapter } from "./adapters/odfl";
import { TqlAdapter } from "./adapters/tql";
import { BridgeIndicator } from "./indicator";
import { assessStructuralHealth, fillWithBoundedRefill, SerializedAsyncRunner } from "./durability";
import { parseLaunchSession, removeLaunchFragment } from "./session";
import { FillRunGate, isManualQuoteRequest, isMatchingSupersession, isTrustedFieldChange } from "./autofill_lifecycle";
import { blockingFailures, manualNoteSummary, notesFirst, withAccessorialNotes } from "./accessorials";
import { runStatusForState, type ReportedStatus } from "./orchestrator";
import { pageHidden, sleep, waitFor } from "./timing";
import type { BridgeSession, CarrierAdapter, FieldResult, QuotePacket } from "./types";

const VERSION = chrome.runtime.getManifest().version;
const MAX_STRUCTURAL_REPAIRS = 3;
const HEALTH_VERIFY_INTERVAL_MS = 3000;
const adapters: CarrierAdapter[] = [new EchoAdapter(), new TqlAdapter(), new DsvAdapter(), new MyUnishippersAdapter(), new OdflAdapter()];
let session: BridgeSession | null = null;
let packet: QuotePacket | null = null;
let adapter: CarrierAdapter | undefined;
let indicator: BridgeIndicator | null = null;
const fillGate = new FillRunGate();
let repairCount = 0;
let enforcementDisarmed = false;
let trustedChangeGeneration = 0;
let trustedChangePending = false;
let verifiedRoot: Element | null = null;
let healthObserver: MutationObserver | null = null;
let healthTimer = 0;
let healthInterval = 0;
let expiryTimer = 0;
let monitorEvents: AbortController | null = null;
let latestState = "Connected";
let latestMessage = "Loading the quote packet.";
let latestFields: FieldResult[] = [];
let loginObserver: MutationObserver | null = null;
let prerequisiteObserver: MutationObserver | null = null;
let prerequisiteEvents: AbortController | null = null;
let eventQueue: Promise<void> = Promise.resolve();
/** The last fill ended in a failure while this tab was hidden (throttled); retry once when it is visible. */
let fillOutcomeHidden = false;
let visibleRetryUsed = false;
let reportedRunStatus = "";

function runtimeMessage<T>(message: unknown): Promise<T> {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
      else resolve(response as T);
    });
  });
}

async function bridge(path: string, method = "GET", body?: unknown, activeSession = session) {
  if (!activeSession) throw new Error("No active handoff session");
  const join = path.includes("?") ? "&" : "?";
  const url = `${activeSession.bridge_origin}${path}${join}token=${encodeURIComponent(activeSession.token)}`;
  const response = await runtimeMessage<{ ok: boolean; data?: Record<string, unknown>; error?: string; status?: number }>({
    type: "bridgeFetch", url, method, body,
  });
  if (!response?.ok) {
    const detail = response?.data?.detail;
    throw new Error(typeof detail === "string" ? detail : response?.error || `Bridge returned ${response?.status}`);
  }
  return response.data || {};
}

function copySummary(): void {
  const failures = latestFields.filter((field) => !field.ok);
  const lines = [
    `Build2Quote Bridge ${VERSION}`,
    `${latestState}: ${latestMessage}`,
    ...failures.map((field) => `${field.field}: ${field.message}`),
  ];
  void navigator.clipboard.writeText(lines.join("\n"));
}

async function cancel(): Promise<void> {
  disposeMonitoring();
  try {
    if (session) await bridge(`/api/quote-handoffs/${session.request_id}/cancel`, "POST");
  } finally {
    await runtimeMessage({ type: "bridgeSessionClear", request_id: session?.request_id }).catch(() => undefined);
    indicator?.destroy();
    indicator = null;
    session = null;
  }
}

function ensureIndicator(): BridgeIndicator {
  if (!indicator) {
    indicator = new BridgeIndicator({
      retry: () => {
        if (!session) { render("Review required", "Start a new handoff from Quote Helper."); return; }
        repairCount = 0;
        enforcementDisarmed = false;
        trustedChangePending = false;
        if (fillGate.queueRetry()) {
          render("Retry queued", "The current fill will finish safely, then retry once.", latestFields);
          return;
        }
        void fillAndVerify("retry");
      },
      copy: copySummary,
      cancel: () => { void cancel().catch(() => undefined); },
    });
  }
  return indicator;
}

function render(state: string, message: string, fields: FieldResult[] = []): void {
  latestState = state;
  latestMessage = message;
  latestFields = fields;
  ensureIndicator().render(state, message, fields);
  reportRunStatus();
}

function currentRunStatus(): ReportedStatus {
  return runStatusForState(latestState);
}

function visibleRetryAvailable(): boolean {
  return fillOutcomeHidden && !visibleRetryUsed && Boolean(session) && Boolean(packet) && Boolean(adapter)
    && !enforcementDisarmed && !trustedChangePending;
}

/** Tell the background run how this tab is doing; its answer carries the progress label to show. */
function reportRunStatus(): void {
  if (!session) return;
  const status = currentRunStatus();
  const retry = visibleRetryAvailable();
  const key = `${status}|${retry}`;
  if (key === reportedRunStatus) return;
  reportedRunStatus = key;
  runtimeMessage<{ ok?: boolean; label?: string | null }>({ type: "bridgeRunStatus", status, visible_retry: retry })
    .then((response) => { if (response?.ok && response.label !== undefined) indicator?.setProgress(response.label ?? null); })
    .catch(() => undefined);
}

/**
 * A fill that failed while the tab was hidden may only have failed because Chrome throttled the
 * page (dropdowns that never rendered). Retry it once, now that the page is visible.
 */
function runVisibleRetry(): boolean {
  if (pageHidden() || fillGate.running || !visibleRetryAvailable()) return false;
  visibleRetryUsed = true;
  fillOutcomeHidden = false;
  repairCount = 0;
  void fillAndVerify("retry");
  return true;
}

/** Show the outcome of a fill or a recovered health check, keeping manual notes visible. */
function presentOutcome(verified: FieldResult[]): FieldResult[] {
  const fields = notesFirst(packet ? withAccessorialNotes(verified, packet, adapter?.supportedAccessorials) : verified);
  const blocking = blockingFailures(fields);
  const notes = manualNoteSummary(fields);
  if (blocking.length) {
    render("Review required", `${blocking.length} field(s) need manual attention.${notes ? ` ${notes}` : ""}`, fields);
    reportEvent("user_action", `Some fields could not be verified.${notes ? ` ${notes}` : ""}`, fields);
  } else if (notes) {
    render("Ready for review", `Filled and verified. ${notes}`, fields);
    reportEvent("user_action", `Quote details filled and verified. ${notes} Request rates manually when ready.`, fields);
  } else {
    render("Filled and verified", "Review the form, then request rates manually.", fields);
    reportEvent("filled", "Quote details filled and verified. Request rates manually when ready.", fields);
  }
  return blocking;
}

function reportEvent(state: string, message: string, fields: FieldResult[] = []): void {
  const activeSession = session;
  if (!activeSession) return;
  eventQueue = eventQueue
    .then(() => bridge(
      `/api/quote-handoffs/${activeSession.request_id}/events`,
      "POST",
      { state, message, fields },
      activeSession,
    ).then(() => undefined))
    .catch(() => undefined);
}

function failureFingerprint(fields: FieldResult[]): string {
  return blockingFailures(fields).map(field => `${field.field}\u0000${field.message}`).sort().join("\u0001");
}

function renderManualReview(failures: FieldResult[]): void {
  if (latestState === "Review required" && failureFingerprint(latestFields) === failureFingerprint(failures)) return;
  const message = trustedChangePending
    ? "A carrier field was changed manually; it was not overwritten."
    : "A carrier field changed after verification; review it. It was not overwritten.";
  const fields = notesFirst(packet ? withAccessorialNotes(failures, packet, adapter?.supportedAccessorials) : failures);
  render("Review required", message, fields);
  reportEvent("user_action", "A field differs from the verified handoff and was preserved for review.", fields);
}

function clearPrerequisiteWait(): void {
  prerequisiteObserver?.disconnect();
  prerequisiteObserver = null;
  prerequisiteEvents?.abort();
  prerequisiteEvents = null;
}

function disposeMonitoring(): void {
  enforcementDisarmed = true;
  healthObserver?.disconnect();
  healthObserver = null;
  monitorEvents?.abort();
  monitorEvents = null;
  loginObserver?.disconnect();
  loginObserver = null;
  clearPrerequisiteWait();
  window.clearTimeout(healthTimer);
  window.clearInterval(healthInterval);
  window.clearTimeout(expiryTimer);
}

function waitForManualPrerequisite(): void {
  if (!adapter || prerequisiteObserver || !document.documentElement) return;
  const resume = () => {
    if (!session || !adapter || fillGate.running) return;
    const prerequisite = adapter.manualPrerequisite?.();
    if (prerequisite && !prerequisite.ok) return;
    clearPrerequisiteWait();
    void fillAndVerify("initial");
  };
  prerequisiteObserver = new MutationObserver(resume);
  prerequisiteObserver.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
  prerequisiteEvents = new AbortController();
  const options = { signal: prerequisiteEvents.signal };
  window.addEventListener("focus", resume, options);
  window.addEventListener("pageshow", resume, options);
}

function waitForLoginReturn(): void {
  if (!adapter || loginObserver || !document.documentElement) return;
  const resume = () => {
    if (!session || !adapter || adapter.isLoginPage()) return;
    loginObserver?.disconnect();
    loginObserver = null;
    void fillAndVerify("initial");
  };
  loginObserver = new MutationObserver(resume);
  loginObserver.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
  window.addEventListener("focus", resume, { once: true });
  window.addEventListener("pageshow", resume, { once: true });
}

async function finishAutofill(): Promise<void> {
  disposeMonitoring();
  const needsReview = latestFields.some(field => !field.ok);
  indicator?.setProgress(null);
  const message = "Autofill ended. Continue on the broker website; no rates are collected.";
  render(needsReview ? "Review required" : "Autofill finished", message, latestFields);
  reportEvent("completed", message, latestFields);
  await runtimeMessage({ type: "bridgeSessionClear", request_id: session?.request_id }).catch(() => undefined);
  session = null;
}

async function waitForFields(timeoutMs = 45000) {
  let stopped = false;
  // Wakes on every DOM mutation; the 400 ms poll only covers changes that are not mutations.
  const found = await waitFor(() => {
    if (!session || enforcementDisarmed) { stopped = true; return true; }
    const discovered = adapter?.discoverFields() || [];
    return discovered.length > 0 && discovered.every((field) => field.ok);
  }, { timeoutMs, pollMs: 400 });
  if (stopped || !session || enforcementDisarmed) throw new Error("Autofill stopped");
  if (!found) throw new Error("Carrier form fields did not appear within 45 seconds");
}

function scheduleHealthCheck(): void {
  if (enforcementDisarmed) return;
  window.clearTimeout(healthTimer);
  healthTimer = window.setTimeout(() => {
    void healthChecks.request().catch(() => undefined);
  }, 260);
}

function installInteractionMonitor(): void {
  if (monitorEvents) return;
  monitorEvents = new AbortController();
  const options = { capture: true, signal: monitorEvents.signal };
  const trustedChange = (changeEvent: Event) => {
    if (!isTrustedFieldChange(changeEvent)) return;
    trustedChangeGeneration += 1;
    trustedChangePending = true;
    scheduleHealthCheck();
  };
  document.addEventListener("input", trustedChange, options);
  document.addEventListener("change", trustedChange, options);
  document.addEventListener("click", (clickEvent) => {
    if (isManualQuoteRequest(clickEvent)) void finishAutofill();
  }, options);
}

function installDurabilityMonitor(): void {
  installInteractionMonitor();
  if (healthObserver || !document.body) return;
  healthObserver = new MutationObserver(scheduleHealthCheck);
  healthObserver.observe(document.body, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["value", "checked", "aria-selected"],
  });
  healthInterval = window.setInterval(() => {
    void healthChecks.request().catch(() => undefined);
  }, HEALTH_VERIFY_INTERVAL_MS);
}

async function checkStructuralHealth(): Promise<void> {
  if (!adapter || !packet || !session || fillGate.running || enforcementDisarmed) return;
  const currentRoot = adapter.formRoot();
  const rootForAssessment = verifiedRoot && !verifiedRoot.isConnected ? verifiedRoot : currentRoot;
  const health = assessStructuralHealth(rootForAssessment, adapter.discoverFields(), adapter.verify(packet));
  if (!health.failures.length) {
    trustedChangePending = false;
    verifiedRoot = currentRoot;
    if (latestState === "Review required") presentOutcome(adapter.verify(packet));
    return;
  }
  if (trustedChangePending) {
    renderManualReview(health.failures);
    return;
  }
  if (health.structuralReset) {
    if (repairCount >= MAX_STRUCTURAL_REPAIRS) {
      const message = "The carrier reset the form more than three times. Reload the quote page or retry manually.";
      render("Stopped safely", message, health.failures);
      reportEvent("error", message, health.failures);
      enforcementDisarmed = true;
      return;
    }
    repairCount += 1;
    const backoff = [250, 650, 1200][repairCount - 1];
    render("Repairing", `${health.reason} Restoring the quote (${repairCount}/${MAX_STRUCTURAL_REPAIRS}).`);
    reportEvent("repairing", health.reason, health.failures);
    await sleep(backoff);
    await fillAndVerify("repair");
  } else if (health.manualReview) {
    renderManualReview(health.failures);
  }
}

const healthChecks = new SerializedAsyncRunner(checkStructuralHealth);

async function fillAndVerify(reason: "initial" | "retry" | "repair" = "initial") {
  if (!session || !adapter || !packet || fillGate.running) return;
  if (adapter.isLoginPage()) {
    render("Sign-in required", "Sign in normally. The bridge will resume without storing credentials.");
    reportEvent("login_required", "Sign in to the broker, then return to the quote page.");
    waitForLoginReturn();
    return;
  }
  const prerequisite = adapter.manualPrerequisite?.();
  if (prerequisite && !prerequisite.ok) {
    render("Action required", prerequisite.message, [prerequisite]);
    reportEvent("user_action", prerequisite.message, [prerequisite]);
    waitForManualPrerequisite();
    return;
  }
  clearPrerequisiteWait();
  if (!fillGate.tryStart()) return;
  fillOutcomeHidden = false;
  try {
    render(reason === "repair" ? "Repairing" : "Discovering", reason === "repair" ? "Waiting for the rebuilt carrier form." : "Waiting for the carrier quote form.");
    reportEvent(reason === "repair" ? "repairing" : "discovering", `Waiting for the ${adapter.broker} quote form.`);
    await waitForFields();
    if (!session || enforcementDisarmed) return;
    render("Filling", `Filling ${adapter.broker} quote fields.`);
    reportEvent("filling", `Filling ${adapter.broker} quote fields.`);
    const changeGeneration = trustedChangeGeneration;
    const outcome = await fillWithBoundedRefill({
      fill: () => adapter!.fill(packet!),
      verify: () => adapter!.verify(packet!),
      canRefill: () => reason !== "repair" && !fillGate.retryQueued && !trustedChangePending
        && trustedChangeGeneration === changeGeneration && Boolean(session) && !enforcementDisarmed,
      beforeRefill: () => {
        render("Refilling", `Retrying ${adapter!.broker} once after the carrier form did not stabilize.`);
        reportEvent("filling", `Retrying ${adapter!.broker} quote fields once.`);
      },
    });
    if (!session || enforcementDisarmed) return;
    const failed = blockingFailures(outcome.verified);
    // Recorded before rendering so the status report already offers the visible retry.
    fillOutcomeHidden = failed.length > 0 && pageHidden();
    presentOutcome(outcome.verified);
    if (!failed.length) {
      trustedChangePending = false;
      verifiedRoot = adapter.formRoot();
      installDurabilityMonitor();
    }
  } catch (error) {
    if (!session || enforcementDisarmed) return;
    fillOutcomeHidden = pageHidden();
    render("Stopped safely", String(error));
    reportEvent("error", String(error));
  } finally {
    const runQueuedRetry = fillGate.finish() && Boolean(session) && !enforcementDisarmed;
    if (runQueuedRetry) void fillAndVerify("retry");
  }
}

async function restoreOrCaptureSession(currentAdapter: CarrierAdapter): Promise<BridgeSession | null> {
  const launched = parseLaunchSession(location.hash);
  if (launched) {
    if (launched.broker !== currentAdapter.broker) throw new Error("Handoff broker does not match this page");
    if (Date.parse(launched.expires_at) <= Date.now()) throw new Error("Quote handoff expired before the page loaded");
    const stored = await runtimeMessage<{ ok: boolean; error?: string }>({ type: "bridgeSessionStore", session: launched });
    if (!stored?.ok) throw new Error(stored?.error || "Could not preserve the handoff across page loading");
    removeLaunchFragment();
    return launched;
  }
  const restored = await runtimeMessage<{ ok: boolean; session?: BridgeSession | null; error?: string }>({
    type: "bridgeSessionLoad", broker: currentAdapter.broker,
  });
  if (!restored?.ok) throw new Error(restored?.error || "Could not restore the quote handoff");
  return restored.session || null;
}

async function start() {
  adapter = adapters.find((candidate) => candidate.detect());
  if (!adapter) {
    const restored = await runtimeMessage<{ ok: boolean; session?: BridgeSession | null }>({ type: "bridgeSessionLoadForTab" });
    if (!restored?.session) return;
    session = restored.session;
    render("Sign-in required", "Sign in normally. Autofill will resume in this tab after authentication.");
    reportEvent("login_required", "Sign in to the broker; Build2Quote never reads or fills credentials.");
    const remaining = Math.max(0, Date.parse(session.expires_at) - Date.now());
    expiryTimer = window.setTimeout(() => {
      void runtimeMessage({ type: "bridgeSessionClear", request_id: session?.request_id }).catch(() => undefined);
      session = null;
      render("Review required", "Login window expired. Start a new handoff from Quote Helper.");
    }, remaining);
    return;
  }
  session = await restoreOrCaptureSession(adapter);
  if (!session) return;
  expiryTimer = window.setTimeout(() => {
    const wasFilled = Boolean(verifiedRoot) && blockingFailures(latestFields).length === 0;
    disposeMonitoring();
    void runtimeMessage({ type: "bridgeSessionClear", request_id: session?.request_id }).catch(() => undefined);
    session = null;
    render(wasFilled ? "Filled and verified" : "Review required",
      wasFilled ? "Autofill is complete. Monitoring has ended." : "Autofill timed out. Start a new handoff from Quote Helper.");
  }, Math.max(0, Date.parse(session.expires_at) - Date.now()));
  render("Connected", `Loading the ${adapter.broker} quote packet.`);
  reportEvent("page_seen", `Build2Quote Bridge sees the ${adapter.broker} page.`);
  const response = await bridge(`/api/quote-handoffs/${session.request_id}/packet`);
  packet = response.packet as unknown as QuotePacket;
  if (packet?.schema_version !== 2) throw new Error("Extension update required: quote packet schema is not supported.");
  installInteractionMonitor();
  await fillAndVerify();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const runMessage = message as { type?: unknown; label?: unknown } | null;
  if (runMessage?.type === "BUILD2QUOTE_RUN_TURN" || runMessage?.type === "BUILD2QUOTE_RUN_LABEL") {
    if (!session) { sendResponse({ ok: false }); return false; }
    const label = typeof runMessage.label === "string" && runMessage.label ? runMessage.label : null;
    ensureIndicator().setProgress(label);
    const retried = runMessage.type === "BUILD2QUOTE_RUN_TURN" && runVisibleRetry();
    sendResponse({ ok: true, status: retried ? "working" : currentRunStatus(), visible_retry: visibleRetryAvailable() });
    return false;
  }
  if (!isMatchingSupersession(message, session?.request_id)) return false;
  disposeMonitoring();
  fillGate.clear();
  session = null;
  packet = null;
  render("Stopped safely", "Superseded by a newer handoff for this broker. Continue in the newer broker tab.", latestFields);
  return false;
});

document.addEventListener("visibilitychange", () => {
  if (!pageHidden()) runVisibleRetry();
});

void start().catch((error) => render("Connection error", String(error)));
