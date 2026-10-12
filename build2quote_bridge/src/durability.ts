import { blockingFailures } from "./accessorials";
import { nextChange } from "./timing";
import type { FieldResult } from "./types";

export interface StructuralHealth {
  structuralReset: boolean;
  manualReview: boolean;
  failures: FieldResult[];
  reason: string;
}

export interface StableFillOutcome {
  fillResults: FieldResult[];
  verified: FieldResult[];
  refilled: boolean;
}

/** Coalesce overlapping requests while guaranteeing that the task never overlaps itself. */
export class SerializedAsyncRunner {
  private running: Promise<void> | null = null;
  private queued = false;

  constructor(private readonly task: () => Promise<void>) {}

  request(): Promise<void> {
    this.queued = true;
    if (!this.running) this.running = this.drain();
    return this.running;
  }

  private async drain(): Promise<void> {
    try {
      while (this.queued) {
        this.queued = false;
        await this.task();
      }
    } finally {
      this.running = null;
    }
  }
}

export function assessStructuralHealth(
  root: Element | null,
  discovered: FieldResult[],
  verified: FieldResult[],
): StructuralHealth {
  const failures = blockingFailures(verified);
  const discoveryFailures = discovered.filter((field) => !field.ok);
  const rowLoss = failures.some((field) => /(?:row|handling unit|goods).*(?:count|missing)|(?:count).*(?:row|unit)/i.test(`${field.field} ${field.message}`));
  const blocking = verified.filter(field => !field.manual);
  const mostlyCleared = blocking.length > 0 && failures.length > blocking.length / 2;
  const formMissing = !root || !root.isConnected;
  const discoveryCollapsed = discovered.length > 0 && discoveryFailures.length > discovered.length / 2;
  const structuralReset = formMissing || rowLoss || mostlyCleared || discoveryCollapsed;
  return {
    structuralReset,
    manualReview: !structuralReset && failures.length > 0,
    failures,
    reason: formMissing
      ? "The carrier replaced the quote form."
      : rowLoss
        ? "Carrier handling-unit rows disappeared."
        : mostlyCleared || discoveryCollapsed
          ? "The carrier cleared most required quote fields."
          : failures.length
            ? `${failures.length} field(s) differ from the verified handoff.`
            : "The carrier form remains complete.",
  };
}

/** Default verification windows (see docs/BUILD2QUOTE_VERIFICATION.md, "3.2 timing changes"). */
export const STABLE_VERIFICATION_MS = 800;
export const VERIFICATION_TIMEOUT_MS = 10000;
/** Stop waiting once the same blocking failures have persisted this long: the page is not converging. */
export const FAILURE_SETTLE_MS = 3000;

const failureKey = (fields: FieldResult[]) => blockingFailures(fields)
  .map(field => `${field.field}\u0000${field.message}`).sort().join("\u0001");

/**
 * Verify until every blocking field has held for `stableMs`. Manual notes never block. Wakes on
 * DOM mutations (so a hidden tab is not limited to one check per second) and returns early when
 * the same failures persist for `failureSettleMs`, instead of always waiting out `timeoutMs`.
 */
export async function waitForStableVerification(
  verify: () => FieldResult[],
  stableMs = STABLE_VERIFICATION_MS,
  timeoutMs = VERIFICATION_TIMEOUT_MS,
  failureSettleMs = FAILURE_SETTLE_MS,
): Promise<FieldResult[]> {
  const started = Date.now();
  let stableSince = 0;
  let failureSince = 0;
  let failureFingerprint = "";
  let latest: FieldResult[] = [];
  while (Date.now() - started < timeoutMs) {
    latest = verify();
    const now = Date.now();
    if (latest.length > 0 && blockingFailures(latest).length === 0) {
      failureSince = 0;
      if (!stableSince) stableSince = now;
      if (now - stableSince >= stableMs) return latest;
    } else {
      stableSince = 0;
      const fingerprint = failureKey(latest);
      if (!failureSince || fingerprint !== failureFingerprint) { failureSince = now; failureFingerprint = fingerprint; }
      else if (now - failureSince >= failureSettleMs) return latest;
    }
    await nextChange(Math.min(75, Math.max(0, timeoutMs - (Date.now() - started))));
  }
  return latest;
}

/** Fill once, verify through a stable window, then permit at most one safe idempotent refill. */
export async function fillWithBoundedRefill(options: {
  fill: () => Promise<FieldResult[]>;
  verify: () => FieldResult[];
  canRefill: () => boolean;
  beforeRefill?: () => void;
  stableMs?: number;
  timeoutMs?: number;
  failureSettleMs?: number;
}): Promise<StableFillOutcome> {
  const settle = () => waitForStableVerification(
    options.verify,
    options.stableMs ?? STABLE_VERIFICATION_MS,
    options.timeoutMs ?? VERIFICATION_TIMEOUT_MS,
    options.failureSettleMs ?? FAILURE_SETTLE_MS,
  );
  let fillResults = await options.fill();
  let verified = await settle();
  let refilled = false;
  if ((verified.length === 0 || blockingFailures(verified).length > 0) && options.canRefill()) {
    refilled = true;
    options.beforeRefill?.();
    fillResults = await options.fill();
    verified = await settle();
  }
  return { fillResults, verified: verified.length ? verified : fillResults, refilled };
}
