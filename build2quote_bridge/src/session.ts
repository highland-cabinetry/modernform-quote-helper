import type { BridgeSession, Broker } from "./types";

export const ACTIVE_SESSION_KEY = "build2quote.activeHandoff";
export const sessionKey = (broker: Broker): string => `${ACTIVE_SESSION_KEY}.${broker}`;

export function expectedBrokerHost(broker: Broker, url: string): boolean {
  const hosts: Record<Broker, string[]> = {
    echoship: ["echoship.com"], tql: ["trax.tqlapp.com", "trax.tql.com"],
    dsv: ["mytms.us.dsv.com"], myunishippers: ["myunishippers.com", "www.myunishippers.com"],
    odfl: ["www.odfl.com"],
  };
  try { const parsed = new URL(url); return parsed.protocol === "https:" && hosts[broker].includes(parsed.hostname); }
  catch { return false; }
}

export interface StoredBridgeSession {
  tab_id: number;
  session: BridgeSession;
}

interface SessionStorageArea {
  get(key: string): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
  remove(key: string): Promise<void>;
}

export const BROKERS: Broker[] = ["echoship", "tql", "dsv", "myunishippers", "odfl"];

export function knownAuthenticationHost(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    const host = parsed.hostname.toLowerCase();
    return host === "login.microsoftonline.com" || host.endsWith(".b2clogin.com")
      || (host === "mytms.us.dsv.com" && parsed.pathname.startsWith("/auth/realms/"));
  } catch { return false; }
}

export function parseLaunchSession(hash: string): BridgeSession | null {
  const match = hash.match(/(?:^#|[&#])build2quote=([^&]+)/);
  if (!match) return null;
  try {
    const normalized = match[1].replace(/-/g, "+").replace(/_/g, "/");
    const value = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="))) as Partial<BridgeSession>;
    const bridge = new URL(value.bridge_origin || "");
    if (
      !value.request_id
      || !value.token
      || !value.expires_at
      || !BROKERS.includes(value.broker as Broker)
      || bridge.protocol !== "http:"
      || bridge.hostname !== "127.0.0.1"
    ) return null;
    return value as BridgeSession;
  } catch {
    return null;
  }
}

export function sessionIsUsable(
  stored: StoredBridgeSession | null,
  tabId: number,
  broker: Broker,
  now = Date.now(),
): boolean {
  if (!stored || stored.tab_id !== tabId || stored.session.broker !== broker) return false;
  const expires = Date.parse(stored.session.expires_at);
  return Number.isFinite(expires) && expires > now;
}

export class SessionRepository {
  constructor(private readonly storage: SessionStorageArea) {}

  async store(tabId: number, session: BridgeSession): Promise<StoredBridgeSession | null> {
    if (!sessionIsUsable({ tab_id: tabId, session }, tabId, session.broker)) throw new Error("Handoff expired");
    const key = sessionKey(session.broker);
    const values = await this.storage.get(key);
    const previous = (values[key] || null) as StoredBridgeSession | null;
    await this.storage.set({ [key]: { tab_id: tabId, session } satisfies StoredBridgeSession });
    await this.storage.remove(ACTIVE_SESSION_KEY);
    return previous && sessionIsUsable(previous, previous.tab_id, previous.session.broker) ? previous : null;
  }

  async load(tabId: number, broker: Broker, now = Date.now()): Promise<BridgeSession | null> {
    const key = sessionKey(broker);
    const values = await this.storage.get(key);
    const stored = (values[key] || null) as StoredBridgeSession | null;
    if (stored && sessionIsUsable(stored, tabId, broker, now)) return stored.session;
    if (stored && Date.parse(stored.session.expires_at) <= now) await this.storage.remove(key);
    return null;
  }

  async loadForTab(tabId: number, now = Date.now()): Promise<BridgeSession | null> {
    for (const broker of BROKERS) {
      const value = await this.load(tabId, broker, now);
      if (value) return value;
    }
    return null;
  }

  async clear(tabId?: number, requestId?: string): Promise<void> {
    for (const broker of BROKERS) {
      const key = sessionKey(broker);
      const values = await this.storage.get(key);
      const stored = (values[key] || null) as StoredBridgeSession | null;
      if (!stored) continue;
      if (tabId !== undefined && stored.tab_id !== tabId) continue;
      if (requestId && stored.session.request_id !== requestId) continue;
      await this.storage.remove(key);
    }
  }
}

export function removeLaunchFragment(): void {
  const parameters = new URLSearchParams(location.hash.replace(/^#/, ""));
  parameters.delete("build2quote");
  const suffix = parameters.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}${suffix ? `#${suffix}` : ""}`);
}
