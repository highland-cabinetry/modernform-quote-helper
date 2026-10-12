import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, LoaderCircle } from "lucide-react";
import { APP_NAME, SETUP_PREFIX } from "../edition";

// Offers an update package found by the installed Quote Helper (see desktop/desktop_update.py).
// The /__desktop routes exist only when the app runs from the installed desktop launcher; in
// development and Preview they answer 404 (or the dev server's index.html), and nothing is shown.

const STATUS_URL = "/__desktop/update-status";
const APPLY_URL = "/__desktop/apply-update";
const HEALTH_URL = "/api/health";
const CHECK_EVERY_MS = 30 * 60 * 1000;
const POLL_EVERY_MS = 2000;
const GIVE_UP_AFTER_MS = 10 * 60 * 1000;

async function readJson(url, options) {
  const response = await fetch(url, { cache: "no-store", ...options, headers: { Accept: "application/json", ...(options?.headers || {}) } });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json().catch(() => null) : null;
  return { ok: response.ok, status: response.status, payload };
}

async function readStatus() {
  try {
    const { ok, payload } = await readJson(STATUS_URL);
    return ok && payload && payload.supported ? payload : null;
  } catch {
    return null;
  }
}

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export default function UpdateBanner() {
  const [status, setStatus] = useState(null);
  const [laterFor, setLaterFor] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | updating | failed | slow
  const [message, setMessage] = useState("");
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    const next = await readStatus();
    if (alive.current) setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = setInterval(refresh, CHECK_EVERY_MS);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  const install = useCallback(async () => {
    const target = status?.available?.version;
    if (!target) return;
    setPhase("updating");
    setMessage("");
    const startedAt = Date.now();
    try {
      const { ok, payload } = await readJson(APPLY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Quote-Helper": "1" },
        body: JSON.stringify({ version: target }),
      });
      if (!ok) {
        setPhase("failed");
        setMessage(payload?.detail || "The update could not be started.");
        return;
      }
    } catch {
      setPhase("failed");
      setMessage(`${APP_NAME} did not answer. Try again.`);
      return;
    }
    // The server stops, its files are replaced and it starts again. Wait for the new version.
    let wentAway = false;
    while (alive.current && Date.now() - startedAt < GIVE_UP_AFTER_MS) {
      await wait(POLL_EVERY_MS);
      let health = null;
      try {
        const { ok, payload } = await readJson(HEALTH_URL);
        health = ok ? payload : null;
      } catch {
        health = null;
      }
      if (!health) {
        wentAway = true;
        continue;
      }
      if (health.version === target) {
        window.location.reload();
        return;
      }
      if (wentAway || Date.now() - startedAt > 15000) {
        // Back, but still the old version: the update step records why it did not install.
        const latest = await readStatus();
        const result = latest?.last_result;
        const finished = result?.finished_at ? Date.parse(result.finished_at) : 0;
        if (result && !result.ok && finished >= startedAt - 60000) {
          if (alive.current) {
            setStatus(latest);
            setPhase("failed");
            setMessage(result.message || "The update was not installed.");
          }
          return;
        }
      }
    }
    if (alive.current) {
      setPhase("slow");
      setMessage(`The update is taking longer than expected. If ${APP_NAME} does not come back, open it again from the Start menu.`);
    }
  }, [status]);

  if (phase === "updating") {
    return (
      <div className="app-banner app-banner-info" role="status" aria-live="polite">
        <LoaderCircle className="spin" size={16} aria-hidden="true" />
        <span>Updating {APP_NAME} to {status?.available?.version}… the window reloads when it is done.</span>
      </div>
    );
  }
  if (phase === "failed" || phase === "slow") {
    return (
      <div className="app-banner app-banner-danger" role="alert">
        <AlertTriangle size={16} aria-hidden="true" />
        <span><b>{phase === "failed" ? "The update was not installed." : "Still updating."}</b> {message}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setPhase("idle"); refresh(); }}>Close</button>
        </span>
      </div>
    );
  }

  const available = status?.available;
  if (!available || status.applying || laterFor === available.version) return null;
  if (available.can_apply) {
    return (
      <div className="app-banner app-banner-info" role="status">
        <Download size={16} aria-hidden="true" />
        <span>
          <b>{APP_NAME} {available.version} is ready to install</b> (you have {status.installed_version}). {APP_NAME}
          closes for a moment and reopens; your data and settings are kept.
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button type="button" className="btn btn-primary btn-sm" onClick={install}>Install now</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLaterFor(available.version)}>Later</button>
        </span>
      </div>
    );
  }
  if (available.needs_installer && available.version) {
    return (
      <div className="app-banner app-banner-info" role="status">
        <Download size={16} aria-hidden="true" />
        <span>
          <b>{APP_NAME} {available.version} is available</b> but needs the full installer
          ({SETUP_PREFIX}{available.version}.exe). Your data and settings are kept.
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setLaterFor(available.version)}>Later</button>
        </span>
      </div>
    );
  }
  return null;
}
