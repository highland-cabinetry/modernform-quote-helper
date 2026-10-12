import { ACTIVE_SESSION_KEY, expectedBrokerHost, knownAuthenticationHost, SessionRepository, sessionIsUsable, type StoredBridgeSession } from "./session";
import { launchAdjacentTabs, launchBrokerTabs, type PreparedLaunch } from "./tab_launcher";
import { handleReloadRequest, isQuoteHelperSender, type ReloadRecord } from "./external";
import {
  isActive, labelChanges, labelFor, REPORTED_STATUSES, startRun, step, WINDOW_ID_NONE,
  type ReportedStatus, type RunCommand, type RunEvent, type RunState,
} from "./orchestrator";
import type { BridgeSession, Broker } from "./types";

interface BridgeFetchMessage { type: "bridgeFetch"; url: string; method?: string; body?: unknown; }
interface StoreSessionMessage { type: "bridgeSessionStore"; session: BridgeSession; }
interface LoadSessionMessage { type: "bridgeSessionLoad"; broker: Broker; }
interface LoadSessionForTabMessage { type: "bridgeSessionLoadForTab"; }
interface ClearSessionMessage { type: "bridgeSessionClear"; request_id?: string; }
interface RunStatusMessage { type: "bridgeRunStatus"; status: ReportedStatus; visible_retry?: boolean; }
type InternalMessage = BridgeFetchMessage | StoreSessionMessage | LoadSessionMessage | LoadSessionForTabMessage
  | ClearSessionMessage | RunStatusMessage;

const sessions = new SessionRepository(chrome.storage.session);
const RUN_KEY = "build2quote.run";
const RUN_ALARM = "build2quote.run.wake";
const BROKER_WINDOW_KEY = "build2quote.brokerWindow";
const RELOAD_KEY = "build2quote.lastReload";

// ---------------------------------------------------------------- one-pass run driver
// The run's state lives in chrome.storage.session so a suspended service worker resumes it on
// the next event (tab activation, content-script report, or the wake-up alarm).

let runQueue: Promise<void> = Promise.resolve();
let wakeTimer: ReturnType<typeof setTimeout> | undefined;

async function loadRun(): Promise<RunState | null> {
  const values = await chrome.storage.session.get(RUN_KEY);
  const state = values[RUN_KEY] as RunState | undefined;
  return state && state.version === 1 ? state : null;
}

async function saveRun(state: RunState | null): Promise<void> {
  if (isActive(state)) await chrome.storage.session.set({ [RUN_KEY]: state });
  else await chrome.storage.session.remove(RUN_KEY);
}

function enqueue(task: () => Promise<void>): Promise<void> {
  runQueue = runQueue.then(task).catch(() => undefined);
  return runQueue;
}

function dispatch(event: RunEvent): Promise<void> {
  return enqueue(async () => {
    const before = await loadRun();
    if (!isActive(before)) return;
    const { state, commands } = step(before, event, Date.now());
    await saveRun(state);
    await execute(before, state, commands);
  });
}

async function sendToTab(tabId: number, message: unknown): Promise<unknown> {
  try { return await chrome.tabs.sendMessage(tabId, message); }
  catch { return undefined; }
}

async function execute(before: RunState | null, after: RunState, commands: RunCommand[]): Promise<void> {
  const turned = new Set<number>();
  for (const command of commands) {
    if (command.type === "activate") {
      await chrome.tabs.update(command.tabId, { active: true }).catch(() => undefined);
      if (command.focusWindow) await chrome.windows.update(command.windowId, { focused: true }).catch(() => undefined);
    } else if (command.type === "turn") {
      turned.add(command.tabId);
      void sendToTab(command.tabId, { type: "BUILD2QUOTE_RUN_TURN", run_id: after.runId, label: command.label })
        .then((response) => {
          const value = response as { status?: ReportedStatus; visible_retry?: boolean } | undefined;
          if (value?.status && REPORTED_STATUSES.includes(value.status)) {
            void dispatch({ type: "status", tabId: command.tabId, status: value.status, visibleRetry: value.visible_retry });
          }
        });
    } else if (command.type === "deadline") {
      scheduleWake(command.at);
    }
  }
  for (const change of labelChanges(before, after)) {
    if (turned.has(change.tabId)) continue;
    void sendToTab(change.tabId, { type: "BUILD2QUOTE_RUN_LABEL", run_id: after.runId, label: change.label });
  }
}

function scheduleWake(at: number | null): void {
  if (wakeTimer !== undefined) clearTimeout(wakeTimer);
  wakeTimer = undefined;
  if (at === null) { void chrome.alarms.clear(RUN_ALARM).catch(() => undefined); return; }
  // In-memory timer while the worker is alive; the alarm resumes a suspended worker.
  wakeTimer = setTimeout(() => { wakeTimer = undefined; void dispatch({ type: "tick" }); }, Math.max(0, at - Date.now()) + 50);
  void chrome.alarms.create(RUN_ALARM, { when: Math.max(at, Date.now() + 1000) + 500 }).catch(() => undefined);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RUN_ALARM) void dispatch({ type: "tick" });
});
chrome.tabs.onActivated.addListener(({ tabId, windowId }) => { void dispatch({ type: "activated", tabId, windowId }); });
chrome.windows.onFocusChanged.addListener((windowId) => { void dispatch({ type: "focus", windowId: windowId ?? WINDOW_ID_NONE }); });
chrome.windows.onRemoved.addListener((windowId) => { void dispatch({ type: "windowRemoved", windowId }); });

async function brokerWindow(origin: chrome.tabs.Tab): Promise<{ windowId: number; firstTabId?: number } | null> {
  // One dedicated normal window holds the broker tabs. Quote Helper runs as a Chrome app window,
  // which cannot hold tabs, and rotating tabs there (or in the person's own browsing window)
  // would disturb other work. The window is reused while it stays open.
  const stored = (await chrome.storage.session.get(BROKER_WINDOW_KEY))[BROKER_WINDOW_KEY];
  if (typeof stored === "number" && stored !== origin.windowId) {
    const existing = await chrome.windows.get(stored).catch(() => null);
    if (existing?.type === "normal" && existing.id !== undefined) {
      await chrome.windows.update(existing.id, { focused: true }).catch(() => undefined);
      return { windowId: existing.id };
    }
  }
  const created = await chrome.windows.create({ url: "about:blank", focused: true, type: "normal" }).catch(() => null);
  if (!created || created.id === undefined) return null;
  await chrome.storage.session.set({ [BROKER_WINDOW_KEY]: created.id });
  return { windowId: created.id, firstTabId: created.tabs?.[0]?.id };
}

async function notifySuperseded(previous: StoredBridgeSession): Promise<void> {
  await chrome.tabs.sendMessage(previous.tab_id, {
    type: "BUILD2QUOTE_SESSION_SUPERSEDED",
    request_id: previous.session.request_id,
  });
}

async function openBrokerTabs(origin: chrome.tabs.Tab, launches: PreparedLaunch[]): Promise<number[]> {
  const originTab = { id: origin.id!, index: origin.index, windowId: origin.windowId };
  const target = await brokerWindow(origin).catch(() => null);
  if (!target) {
    // Without a broker window, fall back to tabs next to Quote Helper; the run still rotates them.
    const ids = await launchAdjacentTabs(chrome.tabs, sessions, originTab, launches, notifySuperseded);
    const first = await chrome.tabs.get(ids[0]).catch(() => null);
    await beginRun(origin, first?.windowId ?? origin.windowId, ids, launches);
    return ids;
  }
  const ids = await launchBrokerTabs(chrome.tabs, sessions, originTab, {
    windowId: target.windowId, firstTabId: target.firstTabId, restoreFocus: false,
  }, launches, notifySuperseded);
  await beginRun(origin, target.windowId, ids, launches);
  return ids;
}

async function beginRun(origin: chrome.tabs.Tab, windowId: number, tabIds: number[], launches: PreparedLaunch[]): Promise<void> {
  await enqueue(async () => {
    const previous = await loadRun();
    const { state, commands } = startRun({
      runId: crypto.randomUUID(),
      windowId,
      originTabId: origin.id ?? null,
      originWindowId: origin.windowId ?? null,
      tabs: tabIds.map((tabId, index) => ({ tabId, broker: launches[index].broker })),
    }, Date.now());
    await saveRun(state);
    // A newer run replaces an older one; the old tabs only lose their progress labels.
    if (isActive(previous)) {
      for (const tab of previous.tabs) void sendToTab(tab.tabId, { type: "BUILD2QUOTE_RUN_LABEL", run_id: previous.runId, label: null });
    }
    await execute(null, state, commands);
  });
}

async function handoffsActive(now = Date.now()): Promise<boolean> {
  const values = await chrome.storage.session.get(null);
  return Object.entries(values).some(([key, value]) => {
    const stored = value as StoredBridgeSession | undefined;
    return key.startsWith(`${ACTIVE_SESSION_KEY}.`) && Boolean(stored?.session)
      && sessionIsUsable(stored!, stored!.tab_id, stored!.session.broker, now);
  });
}

// ---------------------------------------------------------------- content-script messages

chrome.runtime.onMessage.addListener((message: InternalMessage, sender, sendResponse) => {
  if (message?.type === "bridgeRunStatus") {
    const tabId = sender.tab?.id;
    if (tabId === undefined || !REPORTED_STATUSES.includes(message.status)) {
      sendResponse({ ok: false });
      return false;
    }
    void dispatch({ type: "status", tabId, status: message.status, visibleRetry: Boolean(message.visible_retry) })
      .then(loadRun)
      .then((state) => sendResponse({ ok: true, label: labelFor(state, tabId) }))
      .catch(() => sendResponse({ ok: true, label: null }));
    return true;
  }
  if (message?.type === "bridgeSessionStore") {
    if (sender.tab?.id === undefined || !expectedBrokerHost(message.session.broker, sender.url || "")) {
      sendResponse({ ok: false, error: "Handoff session was not associated with a Chrome tab" });
      return false;
    }
    sessions.store(sender.tab.id, message.session)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type === "bridgeSessionLoad") {
    if (sender.tab?.id === undefined || !expectedBrokerHost(message.broker, sender.url || "")) {
      sendResponse({ ok: true, session: null });
      return false;
    }
    sessions.load(sender.tab.id, message.broker)
      .then((session) => sendResponse({ ok: true, session }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type === "bridgeSessionLoadForTab") {
    if (sender.tab?.id === undefined || !knownAuthenticationHost(sender.url || "")) {
      sendResponse({ ok: true, session: null });
      return false;
    }
    sessions.loadForTab(sender.tab.id)
      .then((session) => sendResponse({ ok: true, session }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type === "bridgeSessionClear") {
    sessions.clear(sender.tab?.id, message.request_id)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }
  if (message?.type !== "bridgeFetch") return false;
  const url = new URL(message.url);
  if (url.hostname !== "127.0.0.1" || !url.pathname.startsWith("/api/quote-handoffs/")) {
    sendResponse({ ok: false, error: "Blocked non-local bridge request" });
    return false;
  }
  fetch(url, {
    method: message.method || "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Build2Quote-Extension-Id": chrome.runtime.id,
    },
    body: message.body === undefined ? undefined : JSON.stringify(message.body),
    cache: "no-store",
  })
    .then(async (response) => ({ ok: response.ok, status: response.status, data: await response.json() }))
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: String(error) }));
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void sessions.clear(tabId);
  void dispatch({ type: "removed", tabId });
});

// ---------------------------------------------------------------- Quote Helper page messages

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (!isQuoteHelperSender(sender.url)) return false;
  if (message?.type === "BUILD2QUOTE_PING") {
    sendResponse({ connected: true, name: "Build2Quote Bridge", version: chrome.runtime.getManifest().version, reload: true });
    return false;
  }
  if (message?.type === "BUILD2QUOTE_RELOAD") {
    handleReloadRequest(message, {
      runningVersion: chrome.runtime.getManifest().version,
      now: () => Date.now(),
      busy: async () => isActive(await loadRun()) || await handoffsActive(),
      lastReload: async () => ((await chrome.storage.local.get(RELOAD_KEY))[RELOAD_KEY] as ReloadRecord | undefined) || null,
      rememberReload: async (record) => { await chrome.storage.local.set({ [RELOAD_KEY]: record }); },
      reload: () => chrome.runtime.reload(),
      schedule: (callback, milliseconds) => { setTimeout(callback, milliseconds); },
    })
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, reloading: false, error: String(error) }));
    return true;
  }
  if (message?.type !== "BUILD2QUOTE_OPEN_TABS") return false;
  const origin = sender.tab;
  if (origin?.id === undefined || origin.index === undefined || origin.windowId === undefined) {
    sendResponse({ ok: false, error: "Quote Helper browser tab could not be identified" });
    return false;
  }
  openBrokerTabs(origin, message.handoffs as PreparedLaunch[])
    .then((tabIds) => sendResponse({ ok: true, tab_ids: tabIds }))
    .catch((error) => sendResponse({ ok: false, error: String(error) }));
  return true;
});
