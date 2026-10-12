/**
 * Waits used by the content scripts while broker pages fill in background tabs.
 *
 * Chrome clamps timers in hidden tabs (one wake-up per second, later less) and pauses
 * requestAnimationFrame. Two rules follow:
 * - Short fixed pauses that do not need rendering use `sleep`, which keeps time with a
 *   MessageChannel loop while the page is hidden (message tasks are not clamped).
 * - Everything else waits for its actual condition with `waitFor` / `waitForStable`, which
 *   wake on DOM mutations (delivered as microtasks, never clamped) and fall back to a
 *   timer poll for changes that are not mutations, such as an input's value property.
 */

/** Hidden-page sleeps up to this length avoid the timer clamp; longer ones accept it. */
export const HIDDEN_SLEEP_LIMIT_MS = 1000;

export interface Clock {
  hidden(): boolean;
  now(): number;
}

export function pageHidden(): boolean {
  try { return typeof document !== "undefined" && document.visibilityState === "hidden"; }
  catch { return false; }
}

export const realClock: Clock = { hidden: pageHidden, now: () => Date.now() };

/** A setTimeout pause that stays accurate for short waits while the tab is hidden. */
export function sleep(milliseconds: number, clock: Clock = realClock): Promise<void> {
  const delay = Math.max(0, milliseconds);
  if (delay === 0 || !clock.hidden() || delay > HIDDEN_SLEEP_LIMIT_MS || typeof MessageChannel === "undefined") {
    return new Promise(resolve => setTimeout(resolve, delay));
  }
  return new Promise(resolve => {
    const until = clock.now() + delay;
    const channel = new MessageChannel();
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      channel.port1.onmessage = null;
      channel.port1.close();
      channel.port2.close();
      resolve();
    };
    // Also the fallback when the page becomes visible again (timers are accurate then).
    const timer = setTimeout(finish, delay);
    channel.port1.onmessage = () => {
      if (done) return;
      if (clock.now() >= until) { finish(); return; }
      if (!clock.hidden()) return;
      channel.port2.postMessage(0);
    };
    channel.port2.postMessage(0);
  });
}

function documentRoot(): Node | null {
  return typeof document !== "undefined" ? document.documentElement : null;
}

/** Resolve on the next DOM mutation under `root` or after `milliseconds`, whichever comes first. */
export function nextChange(milliseconds: number, root: Node | null = documentRoot()): Promise<void> {
  return new Promise(resolve => {
    let settled = false;
    let observer: MutationObserver | null = null;
    const done = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      observer?.disconnect();
      resolve();
    };
    const timer = setTimeout(done, Math.max(0, milliseconds));
    if (root && typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(done);
      observer.observe(root, { childList: true, subtree: true, attributes: true, characterData: true });
    }
  });
}

export interface WaitOptions {
  timeoutMs?: number;
  /** Longest gap between checks when nothing in the DOM changes. */
  pollMs?: number;
  root?: Node | null;
}

/** True as soon as `predicate` holds; after `timeoutMs` the predicate's final value. */
export async function waitFor(predicate: () => boolean, options: WaitOptions = {}): Promise<boolean> {
  const timeoutMs = options.timeoutMs ?? 5000;
  const pollMs = options.pollMs ?? 50;
  const started = Date.now();
  if (predicate()) return true;
  while (Date.now() - started < timeoutMs) {
    const remaining = Math.max(0, timeoutMs - (Date.now() - started));
    await nextChange(Math.min(pollMs, remaining), options.root === undefined ? documentRoot() : options.root);
    if (predicate()) return true;
  }
  return predicate();
}

export interface StableOptions extends WaitOptions {
  /** The predicate must hold without interruption for this long. */
  stableMs: number;
  /** Give up once the predicate has failed continuously for this long. */
  failureGraceMs?: number;
  /** Also require this long without DOM mutations under `quietRoot()` (the page stopped re-rendering). */
  quietMs?: number;
  quietRoot?: () => Node | null;
  /** Accept after this much continuous success even when the page never goes quiet. */
  maxStableMs?: number;
}

/** Condition-based stability window: holds `stableMs`, then also waits for a quiet page if asked. */
export async function waitForStable(predicate: () => boolean, options: StableOptions): Promise<boolean> {
  const timeoutMs = options.timeoutMs ?? 10000;
  const pollMs = options.pollMs ?? 50;
  const quietMs = options.quietMs ?? 0;
  const started = Date.now();
  let stableSince = 0;
  let failedSince = 0;
  let lastMutation = started;
  let watched: Node | null = null;
  let observer: MutationObserver | null = null;
  const watchQuietRoot = () => {
    if (!quietMs || typeof MutationObserver === "undefined") return;
    const root = options.quietRoot?.() ?? documentRoot();
    if (root === watched) return;
    observer?.disconnect();
    observer = null;
    watched = root;
    if (!root) return;
    observer = new MutationObserver(() => { lastMutation = Date.now(); });
    observer.observe(root, { childList: true, subtree: true, attributes: true, characterData: true });
  };
  try {
    while (Date.now() - started < timeoutMs) {
      watchQuietRoot();
      const now = Date.now();
      if (predicate()) {
        failedSince = 0;
        if (!stableSince) stableSince = now;
        const heldFor = now - stableSince;
        const quiet = now - lastMutation >= quietMs;
        if (heldFor >= options.stableMs
          && (quiet || (options.maxStableMs !== undefined && heldFor >= options.maxStableMs))) return true;
      } else {
        stableSince = 0;
        if (!failedSince) failedSince = now;
        if (options.failureGraceMs !== undefined && now - failedSince >= options.failureGraceMs) return false;
      }
      const remaining = Math.max(0, timeoutMs - (Date.now() - started));
      await nextChange(Math.min(pollMs, remaining), options.root === undefined ? documentRoot() : options.root);
    }
    return false;
  } finally {
    (observer as MutationObserver | null)?.disconnect();
  }
}

/**
 * Wait until `root` has had no DOM mutations for `quietMs` (at most `maxMs`). Used where a
 * framework may re-render a control right after an edit: a fixed pause becomes "as long as
 * the page keeps changing, and no longer".
 */
export async function waitForQuiet(root: Node | null, quietMs: number, maxMs: number): Promise<void> {
  if (!root || typeof MutationObserver === "undefined") { await sleep(Math.min(quietMs, maxMs)); return; }
  const started = Date.now();
  let lastMutation = started;
  const observer = new MutationObserver(() => { lastMutation = Date.now(); });
  observer.observe(root, { childList: true, subtree: true, attributes: true, characterData: true });
  try {
    while (Date.now() - started < maxMs) {
      const quietFor = Date.now() - lastMutation;
      if (quietFor >= quietMs) return;
      await sleep(Math.min(quietMs - quietFor, maxMs - (Date.now() - started)));
    }
  } finally {
    observer.disconnect();
  }
}
