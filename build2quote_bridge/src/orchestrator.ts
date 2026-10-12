/**
 * One-pass broker run, as a pure state machine the background service worker drives.
 *
 * Chrome throttles hidden tabs (timers clamped, animation frames paused), so broker pages that
 * fill in background tabs stall until someone looks at them. A run gives every broker tab a
 * visible turn: it activates the tab, waits until its content script reports an outcome
 * (ready, review, needs the user, stopped) or the turn's time limit passes, and moves on. Tabs
 * keep filling in the background between turns, so most are finished when their turn comes.
 * After the pass, tabs that need the person (sign-in, ODFL account choice) are shown one at a
 * time; once the person resolves one, the run continues. At the end the first tab that needs the
 * person, else the first ready tab, is shown.
 *
 * The run never fights the person: when they activate a broker tab themselves, rotation pauses
 * until they leave it (another tab, Quote Helper, another Chrome window) or, if that tab was
 * waiting for them, until it finishes. The broker window is only brought to the front while the
 * person has not moved to another window.
 *
 * Every function here is pure: the caller persists the state (MV3 workers can be suspended at any
 * time), performs the returned commands, and feeds Chrome events back in.
 */
import type { Broker } from "./types";

export type ReportedStatus = "working" | "ready" | "review" | "needs_user" | "stopped";
export type TabStatus = "loading" | ReportedStatus | "closed";
export type RunPhase = "rotating" | "attention" | "paused" | "finished";

export const REPORTED_STATUSES: readonly ReportedStatus[] = ["working", "ready", "review", "needs_user", "stopped"];
/** Visible time per tab on its first turn; the tab keeps filling in the background afterwards. */
export const TURN_MS = 45_000;
/** A second, shorter turn for a tab that was still loading or filling after its first turn. */
export const REVISIT_TURN_MS = 30_000;
export const MAX_TURNS = 2;
/** A tab that returns from sign-in gets fresh turns, at most this many times. */
export const MAX_REVISITS = 3;
/** The handoff itself expires after 15 minutes; the run ends a little later at the latest. */
export const RUN_LIFETIME_MS = 20 * 60_000;
/** A tab activation this soon after the run asked for it is the run's own, not the person's. */
export const EXPECTED_ACTIVATION_MS = 4000;
export const WINDOW_ID_NONE = -1;

export interface RunTab {
  tabId: number;
  broker: Broker;
  status: TabStatus;
  turns: number;
  revisits: number;
  /** The last outcome was a failure produced while hidden; the content script retries once on its turn. */
  visibleRetry: boolean;
}

export interface RunState {
  version: 1;
  runId: string;
  windowId: number;
  originTabId: number | null;
  originWindowId: number | null;
  tabs: RunTab[];
  phase: RunPhase;
  resumePhase: "rotating" | "attention" | null;
  currentTabId: number | null;
  turnDeadline: number | null;
  pausedTabId: number | null;
  pausedOnNeedsUser: boolean;
  /** Bring the broker window to the front when activating a tab; false once the person works in another window. */
  focusWindow: boolean;
  expected: { tabId: number; at: number } | null;
  startedAt: number;
  updatedAt: number;
}

export type RunEvent =
  | { type: "status"; tabId: number; status: ReportedStatus; visibleRetry?: boolean }
  | { type: "tick" }
  | { type: "activated"; tabId: number; windowId: number }
  | { type: "focus"; windowId: number }
  | { type: "removed"; tabId: number }
  | { type: "windowRemoved"; windowId: number };

export type RunCommand =
  | { type: "activate"; tabId: number; windowId: number; focusWindow: boolean }
  /** The tab's visible turn started: show the label and let the content script retry a hidden failure. */
  | { type: "turn"; tabId: number; label: string }
  /** Wake the run at this time (turn limit or run lifetime); null cancels the wake-up. */
  | { type: "deadline"; at: number | null };

export interface StepResult { state: RunState; commands: RunCommand[] }

export interface RunLaunch {
  runId: string;
  windowId: number;
  originTabId?: number | null;
  originWindowId?: number | null;
  tabs: Array<{ tabId: number; broker: Broker }>;
}

const SETTLED: readonly TabStatus[] = ["ready", "review", "needs_user", "stopped", "closed"];

export function createRun(launch: RunLaunch, now: number): RunState {
  return {
    version: 1,
    runId: launch.runId,
    windowId: launch.windowId,
    originTabId: launch.originTabId ?? null,
    originWindowId: launch.originWindowId ?? null,
    tabs: launch.tabs.map(tab => ({ ...tab, status: "loading", turns: 0, revisits: 0, visibleRetry: false })),
    phase: "rotating",
    resumePhase: null,
    currentTabId: null,
    turnDeadline: null,
    pausedTabId: null,
    pausedOnNeedsUser: false,
    focusWindow: true,
    expected: null,
    startedAt: now,
    updatedAt: now,
  };
}

/** Start the run: the first tab gets its turn right away. */
export function startRun(launch: RunLaunch, now: number): StepResult {
  return step(createRun(launch, now), { type: "tick" }, now);
}

export function isActive(state: RunState | null | undefined): state is RunState {
  return Boolean(state) && state!.phase !== "finished";
}

function clone(state: RunState): RunState {
  return { ...state, tabs: state.tabs.map(tab => ({ ...tab })), expected: state.expected ? { ...state.expected } : null };
}

const findTab = (state: RunState, tabId: number | null) => tabId === null ? undefined : state.tabs.find(tab => tab.tabId === tabId);
const open = (tab: RunTab | undefined): tab is RunTab => Boolean(tab) && tab!.status !== "closed";

function settled(tab: RunTab): boolean {
  return SETTLED.includes(tab.status) && !tab.visibleRetry;
}

function eligibleForTurn(tab: RunTab): boolean {
  if (tab.turns >= MAX_TURNS) return false;
  return tab.status === "loading" || tab.status === "working"
    || ((tab.status === "review" || tab.status === "stopped") && tab.visibleRetry);
}

/** A tab waiting for the person: sign-in or account choice, or a page the Bridge never reached (login on another site). */
function needsPerson(tab: RunTab): boolean {
  return tab.status === "needs_user" || (tab.status === "loading" && tab.turns >= MAX_TURNS);
}

/** Label for the on-page indicator of `tabId`, or null for none. */
export function labelFor(state: RunState | null | undefined, tabId: number): string | null {
  if (!state || state.phase === "finished") return null;
  const index = state.tabs.findIndex(tab => tab.tabId === tabId);
  if (index < 0) return null;
  const total = state.tabs.length;
  const tab = state.tabs[index];
  if (state.phase === "paused") {
    return state.pausedTabId === tabId ? "Bridge paused while you use this tab. Return to Quote Helper to continue." : null;
  }
  if (state.currentTabId !== tabId) return null;
  if (state.phase === "attention" && needsPerson(tab)) {
    const waiting = state.tabs.filter(other => other.status !== "closed" && needsPerson(other)).length;
    return waiting > 1
      ? `Needs you: sign in or choose the account here (${waiting} pages wait for you).`
      : "Needs you: sign in or choose the account here. Filling continues automatically.";
  }
  return `Filling ${index + 1} of ${total}…`;
}

function lifetimeEnd(state: RunState): number {
  return state.startedAt + RUN_LIFETIME_MS;
}

function wake(state: RunState): RunCommand {
  if (state.phase === "finished") return { type: "deadline", at: null };
  const end = lifetimeEnd(state);
  return { type: "deadline", at: state.turnDeadline === null ? end : Math.min(state.turnDeadline, end) };
}

function activate(state: RunState, tab: RunTab, now: number): RunCommand {
  state.expected = { tabId: tab.tabId, at: now };
  return { type: "activate", tabId: tab.tabId, windowId: state.windowId, focusWindow: state.focusWindow };
}

function startTurn(state: RunState, tab: RunTab, now: number): RunCommand[] {
  tab.turns += 1;
  if (tab.visibleRetry) {
    tab.visibleRetry = false;
    tab.status = "working";
  }
  state.phase = "rotating";
  state.currentTabId = tab.tabId;
  state.turnDeadline = now + (tab.turns === 1 ? TURN_MS : REVISIT_TURN_MS);
  return [activate(state, tab, now), { type: "turn", tabId: tab.tabId, label: labelFor(state, tab.tabId) || "" }];
}

function nextEligible(state: RunState): RunTab | undefined {
  const start = state.tabs.findIndex(tab => tab.tabId === state.currentTabId);
  const ordered = start < 0 ? state.tabs : [...state.tabs.slice(start + 1), ...state.tabs.slice(0, start + 1)];
  return ordered.find(eligibleForTurn);
}

function finish(state: RunState, now: number, showResult = true): RunCommand[] {
  state.phase = "finished";
  state.resumePhase = null;
  state.currentTabId = null;
  state.turnDeadline = null;
  state.pausedTabId = null;
  const openTabs = state.tabs.filter(open);
  const target = openTabs.find(needsPerson)
    || openTabs.find(tab => tab.status === "ready")
    || openTabs.find(tab => tab.status === "review")
    || openTabs[0];
  const commands: RunCommand[] = [];
  if (showResult && target) commands.push(activate(state, target, now));
  return commands;
}

function attend(state: RunState, tab: RunTab, now: number): RunCommand[] {
  state.phase = "attention";
  state.currentTabId = tab.tabId;
  state.turnDeadline = null;
  return [activate(state, tab, now), { type: "turn", tabId: tab.tabId, label: labelFor(state, tab.tabId) || "" }];
}

/** Decide what the run does next. No-op while paused or finished. */
function advance(state: RunState, now: number): RunCommand[] {
  if (state.phase === "paused" || state.phase === "finished") return [];
  if (!state.tabs.some(open)) return finish(state, now, false);
  const current = findTab(state, state.currentTabId);
  const deadlinePassed = state.turnDeadline !== null && now >= state.turnDeadline;

  if (state.phase === "attention" && open(current)) {
    // Waiting for the person on this tab: no time limit until they act.
    if (needsPerson(current)) return [];
    if (current.status === "working" || current.status === "loading" || current.visibleRetry) {
      if (state.turnDeadline === null) { state.turnDeadline = now + TURN_MS; return []; }
      if (!deadlinePassed) return [];
    }
  } else if (state.phase === "rotating" && open(current) && !settled(current) && !deadlinePassed) {
    return [];
  }

  const next = nextEligible(state);
  if (next) return startTurn(state, next, now);
  const waiting = state.tabs.find(tab => open(tab) && needsPerson(tab));
  if (waiting) {
    if (state.phase === "attention" && state.currentTabId === waiting.tabId) return [];
    return attend(state, waiting, now);
  }
  return finish(state, now);
}

function resume(state: RunState, now: number): RunCommand[] {
  const resumeTo = state.resumePhase || "rotating";
  state.pausedTabId = null;
  state.pausedOnNeedsUser = false;
  state.resumePhase = null;
  state.phase = resumeTo;
  const current = findTab(state, state.currentTabId);
  if (resumeTo === "attention" && open(current) && needsPerson(current)) return attend(state, current, now);
  // The paused tab's turn is over; continue with whichever tab is next.
  if (resumeTo === "rotating" && open(current) && !settled(current)) state.turnDeadline = now;
  return advance(state, now);
}

function pause(state: RunState, tabId: number, needsUser: boolean): RunCommand[] {
  if (state.phase !== "paused") state.resumePhase = state.phase === "attention" ? "attention" : "rotating";
  state.phase = "paused";
  state.pausedTabId = tabId;
  state.pausedOnNeedsUser = needsUser;
  state.turnDeadline = null;
  return [];
}

export function step(previous: RunState, event: RunEvent, now: number): StepResult {
  const state = clone(previous);
  state.updatedAt = now;
  let commands: RunCommand[] = [];
  if (state.phase === "finished") return { state, commands: [] };

  switch (event.type) {
    case "status": {
      const tab = findTab(state, event.tabId);
      if (!open(tab) || !REPORTED_STATUSES.includes(event.status)) return { state: previous, commands: [] };
      const before = tab.status;
      tab.status = event.status;
      tab.visibleRetry = Boolean(event.visibleRetry) && (event.status === "review" || event.status === "stopped");
      if (before === "needs_user" && event.status === "working" && tab.revisits < MAX_REVISITS) {
        // Back from sign-in: the tab earns fresh visible turns.
        tab.revisits += 1;
        tab.turns = 0;
      }
      if (state.phase === "paused") {
        if (state.pausedTabId === tab.tabId && state.pausedOnNeedsUser && settled(tab) && tab.status !== "needs_user") {
          commands = resume(state, now);
        } else if (state.pausedTabId === tab.tabId && tab.status === "needs_user") {
          state.pausedOnNeedsUser = true;
        }
        break;
      }
      commands = advance(state, now);
      break;
    }
    case "tick":
      if (now >= lifetimeEnd(state)) { commands = finish(state, now, false); break; }
      commands = advance(state, now);
      break;
    case "activated": {
      if (state.expected && state.expected.tabId === event.tabId && now - state.expected.at <= EXPECTED_ACTIVATION_MS) {
        state.expected = null;
        return { state, commands: [] };
      }
      if (event.windowId !== state.windowId) {
        // Quote Helper's own tab (when it runs as a normal tab) counts as returning to Quote Helper.
        if (event.tabId === state.originTabId && state.phase === "paused") commands = resume(state, now);
        break;
      }
      const tab = findTab(state, event.tabId);
      if (open(tab)) {
        if (state.phase === "attention" && state.currentTabId === tab.tabId) break;
        commands = pause(state, tab.tabId, tab.status === "needs_user");
      } else if (event.tabId === state.originTabId) {
        // Quote Helper shares the run's window (fallback launch): rotating would hide it, so wait.
        commands = pause(state, event.tabId, false);
      } else if (state.phase === "paused") {
        commands = resume(state, now);
      }
      break;
    }
    case "focus":
      if (event.windowId === state.windowId) { state.focusWindow = true; break; }
      state.focusWindow = false;
      // Leaving for Quote Helper or another Chrome window ends a pause. WINDOW_ID_NONE (another
      // application, or a transient state while switching) only stops the run from raising its window.
      if (event.windowId !== WINDOW_ID_NONE && state.phase === "paused") commands = resume(state, now);
      break;
    case "removed": {
      const tab = findTab(state, event.tabId);
      if (!open(tab)) return { state: previous, commands: [] };
      tab.status = "closed";
      tab.visibleRetry = false;
      if (state.pausedTabId === tab.tabId) { commands = resume(state, now); break; }
      if (state.currentTabId === tab.tabId) { state.currentTabId = null; state.turnDeadline = null; }
      commands = advance(state, now);
      break;
    }
    case "windowRemoved":
      if (event.windowId === state.windowId) commands = finish(state, now, false);
      break;
  }
  return { state, commands: [...commands, wake(state)] };
}

/** Map an indicator state rendered by the content script to the status a run tracks. */
export function runStatusForState(state: string): ReportedStatus {
  const normalized = state.toLowerCase();
  if (/sign-in required|action required/.test(normalized)) return "needs_user";
  if (/stopped|connection error|expired/.test(normalized)) return "stopped";
  if (/review required/.test(normalized)) return "review";
  // "Ready for review": filled and verified, with notes such as "Set manually: Limited access".
  if (/filled and verified|autofill finished|ready for review/.test(normalized)) return "ready";
  return "working";
}

/** Tabs whose indicator label differs between two states. */
export function labelChanges(before: RunState | null, after: RunState | null): Array<{ tabId: number; label: string | null }> {
  const ids = new Set([...(before?.tabs || []), ...(after?.tabs || [])].map(tab => tab.tabId));
  const changes: Array<{ tabId: number; label: string | null }> = [];
  for (const tabId of ids) {
    const old = labelFor(before, tabId);
    const next = labelFor(after, tabId);
    if (old !== next) changes.push({ tabId, label: next });
  }
  return changes;
}
