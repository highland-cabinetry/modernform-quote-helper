/** Messages from the Quote Helper page (externally_connectable: http://127.0.0.1 and http://localhost). */

export function isQuoteHelperSender(url: string | undefined): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

/** Numeric dotted-version comparison: negative when a < b, 0 when equal, positive when a > b. */
export function compareVersions(a: string, b: string): number {
  const left = String(a || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  const right = String(b || "").split(".").map(part => Number.parseInt(part, 10) || 0);
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference;
  }
  return 0;
}

/** A second reload from the same running version inside this window is refused (reload-loop guard). */
export const RELOAD_COOLDOWN_MS = 2 * 60_000;
export const RELOAD_DELAY_MS = 150;

export interface ReloadRecord { at: number; from: string; }

export interface ReloadDeps {
  runningVersion: string;
  now(): number;
  /** A one-pass run is active or an unexpired handoff is stored: a reload would cut it off. */
  busy(): Promise<boolean>;
  lastReload(): Promise<ReloadRecord | null>;
  rememberReload(record: ReloadRecord): Promise<void>;
  reload(): void;
  schedule(callback: () => void, milliseconds: number): void;
}

export interface ReloadResponse {
  ok: boolean;
  reloading: boolean;
  version: string;
  busy?: boolean;
  error?: string;
}

/**
 * BUILD2QUOTE_RELOAD: restart the Bridge so Chrome loads the files the Quote Helper installer
 * replaced (Chrome keeps running the old code of an unpacked extension until it is reloaded).
 * The response is sent before the reload. Refused while broker autofill is active and when the
 * same running version already reloaded within RELOAD_COOLDOWN_MS.
 */
export async function handleReloadRequest(
  message: { expected_version?: unknown },
  deps: ReloadDeps,
): Promise<ReloadResponse> {
  const running = deps.runningVersion;
  const expected = typeof message.expected_version === "string" ? message.expected_version : "";
  if (expected && compareVersions(running, expected) >= 0) return { ok: true, reloading: false, version: running };
  if (await deps.busy()) {
    return {
      ok: false, reloading: false, busy: true, version: running,
      error: "Broker autofill is active, so the Bridge was not reloaded. Check the Bridge again when the broker tabs are finished.",
    };
  }
  const last = await deps.lastReload();
  const now = deps.now();
  if (last && last.from === running && now - last.at >= 0 && now - last.at < RELOAD_COOLDOWN_MS) {
    return {
      ok: false, reloading: false, version: running,
      error: `The Bridge already reloaded and is still version ${running}. Reinstall the Quote Helper update, or reload the Bridge in chrome://extensions.`,
    };
  }
  await deps.rememberReload({ at: now, from: running });
  deps.schedule(() => deps.reload(), RELOAD_DELAY_MS);
  return { ok: true, reloading: true, version: running };
}
