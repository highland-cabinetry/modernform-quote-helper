import { describe, expect, it } from "vitest";

import {
  labelChanges, labelFor, MAX_TURNS, REVISIT_TURN_MS, RUN_LIFETIME_MS, runStatusForState, startRun, step, TURN_MS, WINDOW_ID_NONE,
  type RunCommand, type RunEvent, type RunState,
} from "../src/orchestrator";
import type { Broker } from "../src/types";

const BROKER_WINDOW = 50;
const QUOTE_HELPER_WINDOW = 10;
const QUOTE_HELPER_TAB = 9;
const brokers: Broker[] = ["echoship", "tql", "dsv", "myunishippers", "odfl"];

/** Drives the pure run like the background worker does, recording every command. */
class FakeRun {
  state: RunState;
  now = 1_000;
  log: RunCommand[] = [];

  constructor(count = 5) {
    const result = startRun({
      runId: "run-1", windowId: BROKER_WINDOW, originTabId: QUOTE_HELPER_TAB, originWindowId: QUOTE_HELPER_WINDOW,
      tabs: brokers.slice(0, count).map((broker, index) => ({ tabId: 100 + index, broker })),
    }, this.now);
    this.state = result.state;
    this.log.push(...result.commands);
    this.echoActivations(result.commands);
  }

  /** Chrome reports the run's own activations back as onActivated events. */
  private echoActivations(commands: RunCommand[]) {
    for (const command of commands) {
      if (command.type !== "activate") continue;
      const echoed = step(this.state, { type: "activated", tabId: command.tabId, windowId: command.windowId }, this.now);
      this.state = echoed.state;
      expect(echoed.commands.filter(value => value.type !== "deadline")).toEqual([]);
    }
  }

  send(event: RunEvent, advanceMs = 0): RunCommand[] {
    this.now += advanceMs;
    const result = step(this.state, event, this.now);
    this.state = result.state;
    this.log.push(...result.commands);
    this.echoActivations(result.commands);
    return result.commands;
  }

  status(tabId: number, status: "working" | "ready" | "review" | "needs_user" | "stopped", visibleRetry = false) {
    return this.send({ type: "status", tabId, status, visibleRetry });
  }

  activations(commands = this.log) {
    return commands.filter((command): command is Extract<RunCommand, { type: "activate" }> => command.type === "activate")
      .map(command => command.tabId);
  }
}

describe("one-pass broker run", () => {
  it("gives every tab a visible turn in order and ends on the first ready tab", () => {
    const run = new FakeRun();
    expect(run.activations()).toEqual([100]);
    expect(labelFor(run.state, 100)).toBe("Filling 1 of 5…");
    expect(run.log).toContainEqual({ type: "turn", tabId: 100, label: "Filling 1 of 5…" });
    // Tab 3 finished while hidden: it needs no turn.
    run.status(102, "ready");
    expect(run.activations()).toEqual([100]);
    run.status(100, "working");
    expect(run.activations()).toEqual([100]);
    run.status(100, "ready");
    expect(run.activations()).toEqual([100, 101]);
    expect(labelFor(run.state, 101)).toBe("Filling 2 of 5…");
    expect(labelFor(run.state, 100)).toBeNull();
    run.status(101, "review");
    expect(run.activations()).toEqual([100, 101, 103]);
    run.status(103, "ready");
    run.status(104, "ready");
    expect(run.state.phase).toBe("finished");
    // Final focus: the first ready tab.
    expect(run.activations().at(-1)).toBe(100);
    expect(run.log.at(-1)).toEqual({ type: "deadline", at: null });
  });

  it("moves on at the turn limit, revisits once, then stops rotating", () => {
    const run = new FakeRun(2);
    run.status(100, "working");
    run.send({ type: "tick" }, TURN_MS - 1);
    expect(run.activations()).toEqual([100]);
    run.send({ type: "tick" }, 1);
    expect(run.activations()).toEqual([100, 101]);
    run.status(101, "ready");
    // Tab 1 is still filling after its first turn: one shorter revisit.
    expect(run.activations()).toEqual([100, 101, 100]);
    expect(run.state.turnDeadline).toBe(run.now + REVISIT_TURN_MS);
    run.send({ type: "tick" }, REVISIT_TURN_MS);
    expect(run.state.tabs[0].turns).toBe(MAX_TURNS);
    expect(run.state.phase).toBe("finished");
    expect(run.activations().at(-1)).toBe(101);
  });

  it("waits for the person on sign-in tabs after the pass, then continues with the next", () => {
    const run = new FakeRun(3);
    run.status(100, "needs_user");
    expect(run.activations()).toEqual([100, 101]);
    run.status(101, "needs_user");
    run.status(102, "ready");
    // Pass done: show the first tab that needs the person, with no time limit.
    expect(run.state.phase).toBe("attention");
    expect(run.activations().at(-1)).toBe(100);
    expect(run.state.turnDeadline).toBeNull();
    expect(labelFor(run.state, 100)).toMatch(/Needs you.*2 pages wait/);
    run.send({ type: "tick" }, 10 * 60_000);
    expect(run.state.phase).toBe("attention");
    // The person signs in; the page fills while visible, then the run moves to the next sign-in.
    run.status(100, "working");
    expect(run.state.turnDeadline).toBe(run.now + TURN_MS);
    expect(labelFor(run.state, 100)).toBe("Filling 1 of 3…");
    run.status(100, "ready");
    expect(run.activations().at(-1)).toBe(101);
    expect(labelFor(run.state, 101)).toMatch(/^Needs you: sign in/);
    run.status(101, "working");
    run.status(101, "ready");
    expect(run.state.phase).toBe("finished");
    expect(run.activations().at(-1)).toBe(100);
  });

  it("revisits a tab that returns from sign-in while hidden", () => {
    const run = new FakeRun(2);
    run.status(100, "needs_user");
    run.status(101, "needs_user");
    expect(run.state.phase).toBe("attention");
    expect(run.activations()).toEqual([100, 101, 100]);
    // The person's sign-in also unlocked tab 2 in the background: it earns fresh turns.
    run.status(101, "working");
    expect(run.state.tabs[1]).toMatchObject({ turns: 0, revisits: 1 });
    run.status(100, "working");
    run.status(100, "ready");
    expect(run.state.phase).toBe("rotating");
    expect(run.activations().at(-1)).toBe(101);
    expect(labelFor(run.state, 101)).toBe("Filling 2 of 2…");
    run.status(101, "ready");
    expect(run.state.phase).toBe("finished");

    // A page the Bridge never reached (for example sign-in on another site) counts as waiting for the person.
    const silent = new FakeRun(2);
    silent.status(101, "ready");
    silent.send({ type: "tick" }, TURN_MS);
    silent.send({ type: "tick" }, REVISIT_TURN_MS);
    expect(silent.state.phase).toBe("attention");
    expect(silent.state.currentTabId).toBe(100);
  });

  it("pauses when the person activates a broker tab and resumes when they return to Quote Helper", () => {
    const run = new FakeRun(3);
    run.send({ type: "activated", tabId: 102, windowId: BROKER_WINDOW }, 5000);
    expect(run.state.phase).toBe("paused");
    expect(labelFor(run.state, 102)).toMatch(/paused/i);
    expect(labelFor(run.state, 100)).toBeNull();
    const before = run.activations().length;
    run.status(100, "ready");
    run.send({ type: "tick" }, TURN_MS * 3);
    expect(run.activations().length).toBe(before);
    expect(run.state.focusWindow).toBe(true);
    run.send({ type: "focus", windowId: QUOTE_HELPER_WINDOW });
    expect(run.state.phase).toBe("rotating");
    expect(run.state.focusWindow).toBe(false);
    const resumed = run.log.filter(command => command.type === "activate").at(-1);
    expect(resumed).toEqual({ type: "activate", tabId: 101, windowId: BROKER_WINDOW, focusWindow: false });
  });

  it("does not resume or raise its window when Chrome loses focus to another application", () => {
    const run = new FakeRun(2);
    run.send({ type: "activated", tabId: 101, windowId: BROKER_WINDOW }, 5000);
    run.send({ type: "focus", windowId: WINDOW_ID_NONE });
    expect(run.state.phase).toBe("paused");
    expect(run.state.focusWindow).toBe(false);
    // Leaving the paused tab for another tab in the broker window resumes.
    run.send({ type: "activated", tabId: 999, windowId: BROKER_WINDOW });
    expect(run.state.phase).toBe("rotating");
  });

  it("resumes when the tab the person signed in on finishes", () => {
    const run = new FakeRun(2);
    run.status(101, "needs_user");
    run.send({ type: "activated", tabId: 101, windowId: BROKER_WINDOW }, 5000);
    expect(run.state.pausedOnNeedsUser).toBe(true);
    run.status(101, "working");
    expect(run.state.phase).toBe("paused");
    run.status(101, "ready");
    expect(run.state.phase).toBe("rotating");
    expect(run.activations().at(-1)).toBe(100);
  });

  it("gives a hidden failure one visible retry turn", () => {
    const run = new FakeRun(2);
    run.status(101, "review", true);
    run.status(100, "ready");
    expect(run.activations()).toEqual([100, 101]);
    expect(run.state.tabs[1]).toMatchObject({ status: "working", visibleRetry: false, turns: 1 });
    run.status(101, "review", false);
    expect(run.state.phase).toBe("finished");
  });

  it("handles closed tabs, a closed window, and the run lifetime", () => {
    const run = new FakeRun(3);
    run.send({ type: "removed", tabId: 100 });
    expect(run.activations().at(-1)).toBe(101);
    run.send({ type: "removed", tabId: 101 });
    run.send({ type: "removed", tabId: 102 });
    expect(run.state.phase).toBe("finished");

    const closed = new FakeRun(2);
    closed.send({ type: "windowRemoved", windowId: BROKER_WINDOW });
    expect(closed.state.phase).toBe("finished");

    const expired = new FakeRun(2);
    expired.status(100, "needs_user");
    expired.status(101, "needs_user");
    expect(expired.state.phase).toBe("attention");
    expect(expired.log.at(-1)).toEqual({ type: "deadline", at: expired.state.startedAt + RUN_LIFETIME_MS });
    const count = expired.activations().length;
    expired.send({ type: "tick" }, RUN_LIFETIME_MS);
    expect(expired.state.phase).toBe("finished");
    expect(expired.activations().length).toBe(count);
  });

  it("ignores events after finishing and reports from tabs outside the run", () => {
    const run = new FakeRun(1);
    run.status(100, "ready");
    expect(run.state.phase).toBe("finished");
    expect(step(run.state, { type: "activated", tabId: 100, windowId: BROKER_WINDOW }, run.now).commands).toEqual([]);
    const other = new FakeRun(1);
    expect(step(other.state, { type: "status", tabId: 555, status: "ready" }, other.now).state).toBe(other.state);
  });

  it("pauses when Quote Helper shares the run window and is activated by the person", () => {
    const run = new FakeRun(2);
    run.state = { ...run.state, windowId: QUOTE_HELPER_WINDOW };
    run.send({ type: "activated", tabId: QUOTE_HELPER_TAB, windowId: QUOTE_HELPER_WINDOW }, 5000);
    expect(run.state.phase).toBe("paused");
    run.status(100, "ready");
    expect(run.activations()).toEqual([100]);
  });

  it("maps indicator states and diffs labels", () => {
    expect(runStatusForState("Filled and verified")).toBe("ready");
    expect(runStatusForState("Ready for review")).toBe("ready");
    expect(runStatusForState("Review required")).toBe("review");
    expect(runStatusForState("Sign-in required")).toBe("needs_user");
    expect(runStatusForState("Action required")).toBe("needs_user");
    expect(runStatusForState("Stopped safely")).toBe("stopped");
    expect(runStatusForState("Connection error")).toBe("stopped");
    expect(runStatusForState("Filling")).toBe("working");
    const run = new FakeRun(2);
    const before = run.state;
    run.status(100, "ready");
    expect(labelChanges(before, run.state)).toEqual([
      { tabId: 100, label: null },
      { tabId: 101, label: "Filling 2 of 2…" },
    ]);
  });
});
