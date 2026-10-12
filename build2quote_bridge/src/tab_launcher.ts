import { expectedBrokerHost, parseLaunchSession, type SessionRepository, type StoredBridgeSession } from "./session";
import type { Broker } from "./types";

export interface PreparedLaunch { broker: Broker; url: string; request_id?: string; }
export interface OriginTab { id: number; index: number; windowId: number; }
export interface TabsApi {
  create(properties: chrome.tabs.CreateProperties): Promise<chrome.tabs.Tab>;
  update(tabId: number, properties: chrome.tabs.UpdateProperties): Promise<chrome.tabs.Tab | undefined>;
  remove(tabIds: number[]): Promise<void>;
}

/** Where broker tabs go: next to Quote Helper (default) or into the run's broker window. */
export interface LaunchTarget {
  windowId: number;
  /** Index of the first broker tab; omitted = append at the end of the window. */
  index?: number;
  openerTabId?: number;
  /** An existing blank tab (a new window's first tab) to use for the first broker. */
  firstTabId?: number;
  /** Re-activate Quote Helper after launching (the one-pass run activates tabs itself instead). */
  restoreFocus: boolean;
}

export async function launchAdjacentTabs(
  tabs: TabsApi,
  sessions: SessionRepository,
  origin: OriginTab,
  launches: PreparedLaunch[],
  onSuperseded?: (previous: StoredBridgeSession) => Promise<void>,
): Promise<number[]> {
  return launchBrokerTabs(tabs, sessions, origin, {
    windowId: origin.windowId, index: origin.index + 1, openerTabId: origin.id, restoreFocus: true,
  }, launches, onSuperseded);
}

export async function launchBrokerTabs(
  tabs: TabsApi,
  sessions: SessionRepository,
  origin: OriginTab,
  target: LaunchTarget,
  launches: PreparedLaunch[],
  onSuperseded?: (previous: StoredBridgeSession) => Promise<void>,
): Promise<number[]> {
  if (!launches.length || new Set(launches.map(item => item.broker)).size !== launches.length) {
    throw new Error("Prepared broker launches must be non-empty and unique");
  }
  const created: Array<{ id: number; requestId: string }> = [];
  try {
    for (const [offset, launch] of launches.entries()) {
      const parsedUrl = new URL(launch.url);
      const session = parseLaunchSession(parsedUrl.hash);
      if (!session || session.broker !== launch.broker || !expectedBrokerHost(launch.broker, launch.url)) {
        throw new Error(`Invalid ${launch.broker} handoff`);
      }
      let tabId: number | undefined;
      if (offset === 0 && target.firstTabId !== undefined) {
        tabId = target.firstTabId;
      } else {
        const properties: chrome.tabs.CreateProperties = { url: "about:blank", active: false, windowId: target.windowId };
        if (target.index !== undefined) properties.index = target.index + offset;
        if (target.openerTabId !== undefined) properties.openerTabId = target.openerTabId;
        const tab = await tabs.create(properties);
        tabId = tab.id;
      }
      if (tabId === undefined) throw new Error(`Chrome did not create the ${launch.broker} tab`);
      created.push({ id: tabId, requestId: session.request_id });
      const previous = await sessions.store(tabId, session);
      if (previous && previous.tab_id !== tabId && previous.session.request_id !== session.request_id) {
        await onSuperseded?.(previous).catch(() => undefined);
      }
      parsedUrl.hash = "";
      await tabs.update(tabId, { url: parsedUrl.toString(), active: offset === 0 && !target.restoreFocus });
    }
    if (target.restoreFocus) await tabs.update(origin.id, { active: true });
    return created.map(value => value.id);
  } catch (error) {
    const incomplete: string[] = [];
    for (const value of created) await sessions.clear(value.id, value.requestId).catch(() => { incomplete.push(`session for tab ${value.id}`); });
    for (const value of created) await tabs.remove([value.id]).catch(() => { incomplete.push(`tab ${value.id}`); });
    await tabs.update(origin.id, { active: true }).catch(() => { incomplete.push("application focus"); });
    if (incomplete.length) throw new Error(`${String(error)}. Cleanup needs attention: ${incomplete.join(", ")}. Close leftover broker tabs before retrying.`);
    throw error;
  }
}
