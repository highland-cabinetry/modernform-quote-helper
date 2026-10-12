/** Observe a user action only. This module never clicks or reads rate results. */
export function isManualQuoteRequest(event: Pick<Event, "isTrusted" | "target">): boolean {
  if (!event.isTrusted || !(event.target instanceof Element)) return false;
  const target = event.target.closest("button, input[type='submit'], a");
  if (!target) return false;
  const text = `${target.textContent || ""} ${(target as HTMLInputElement).value || ""}`.trim().replace(/\s+/g, " ");
  return /^(get|request|view|show)\s+(a\s+)?(quote|quotes|rate|rates)$/i.test(text)
    || /get quotes|get rates|request rates/i.test(text)
    || (/^submit$/i.test(text) && /(^|\.)odfl\.com$/i.test(globalThis.location?.hostname || ""));
}

export function isTrustedFieldChange(event: Pick<Event, "isTrusted" | "target">): boolean {
  if (!event.isTrusted || !(event.target instanceof Element)) return false;
  return Boolean(event.target.closest("input, select, textarea, [contenteditable='true']"));
}

export function isMatchingSupersession(message: unknown, requestId: string | undefined): boolean {
  if (!requestId || !message || typeof message !== "object") return false;
  const value = message as { type?: unknown; request_id?: unknown };
  return value.type === "BUILD2QUOTE_SESSION_SUPERSEDED" && value.request_id === requestId;
}

/** Coalesces any number of Retry clicks during one fill into one follow-up run. */
export class FillRunGate {
  running = false;
  retryQueued = false;

  tryStart(): boolean {
    if (this.running) return false;
    this.running = true;
    return true;
  }

  queueRetry(): boolean {
    if (!this.running) return false;
    this.retryQueued = true;
    return true;
  }

  finish(): boolean {
    this.running = false;
    const queued = this.retryQueued;
    this.retryQueued = false;
    return queued;
  }

  clear(): void {
    this.running = false;
    this.retryQueued = false;
  }
}
