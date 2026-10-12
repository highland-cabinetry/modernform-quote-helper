import { ExternalLink, Hand, RefreshCw } from "lucide-react";
import { brokers } from "./brokers";
import { Checklist } from "./ui";
import "./styles/quotes.css";
import { APP_NAME } from "./edition";

// Services the Bridge never selects on a broker page (build2quote_bridge/src/accessorials.ts,
// OPTIONAL_ACCESSORIALS): the person sets them by hand.
const MANUAL_SERVICES = { limited_access: "Limited access", inside_delivery: "Inside delivery" };

/** "Set manually: Limited access. Check manually: pallet 1 dimensions." from a handoff's field results. */
export function manualNoteSummary(fields = []) {
  const notes = (fields || []).filter((field) => !field.ok && field.manual);
  const unique = (values) => [...new Set(values)];
  const set = unique(notes.filter((note) => note.manual === "set").map((note) => note.field));
  const check = unique(notes.filter((note) => note.manual === "check").map((note) => note.field.replace(/\s+manual review required$/i, "")));
  return [set.length ? `Set manually: ${set.join(", ")}.` : "", check.length ? `Check manually: ${check.join(", ")}.` : ""].filter(Boolean).join(" ");
}

const bridgeLabels = {
  connected: "Bridge connected",
  checking: "Checking bridge…",
  "update-required": "Bridge update required",
  "not-installed": "Bridge not set up",
};

export function bridgeHelp(bridge, minimumVersion) {
  if (bridge.state === "connected") return `Build2Quote Bridge ${bridge.version} is ready.`;
  if (bridge.state === "update-required") return `Bridge ${bridge.version || "unknown"} is loaded. Restart Chrome, or reload the Bridge in chrome://extensions, to load v${minimumVersion}.`;
  if (bridge.state === "checking") return "Looking for the Build2Quote Bridge extension…";
  return `Load the Bridge once in chrome://extensions (Developer mode → Load unpacked): the folder is Start menu › ${APP_NAME} Tools › Build2Quote Bridge folder (build2quote_bridge/dist when running from source). Then check again.`;
}

/**
 * Broker send-off panel shared by Builder and Quick Quote.
 * Purely presentational: callers own every gate that decides `canSend`.
 */
export default function BrokerPanel({
  steps,
  bridge,
  minimumVersion,
  onCheckBridge,
  canSend,
  busy,
  onOpenAll,
  onOpenBroker,
  blockedReason,
  sessions = {},
  services = {},
  children,
}) {
  const bridgeOk = bridge?.state === "connected";
  const notes = Object.entries(sessions || {}).map(([broker, session]) => [broker, manualNoteSummary(session?.fields)]).filter(([, text]) => text);
  const manual = Object.entries(MANUAL_SERVICES).filter(([key]) => services?.[key]).map(([, label]) => label);
  return (
    <div className="broker-panel">
      <Checklist items={steps} />
      <div className="broker-launch">
        <button type="button" className="btn btn-primary btn-lg btn-block" disabled={!canSend || busy} onClick={onOpenAll}>
          <ExternalLink size={17} />Open all brokers
        </button>
        <div className="broker-grid" aria-label="Open a single broker">
          {Object.entries(brokers).map(([key, label]) => (
            <button key={key} type="button" className="btn btn-secondary btn-sm" disabled={!canSend || busy} onClick={() => onOpenBroker(key)}>
              {label}
            </button>
          ))}
        </div>
        {!canSend && blockedReason && <p className="broker-blocked">{blockedReason}</p>}
      </div>
      {notes.length > 0 ? <ul className="bp-notes" aria-label="Broker notes">
        {notes.map(([broker, text]) => <li key={broker}><Hand size={13} aria-hidden="true" /><b>{brokers[broker] || broker}</b><span>{text}</span></li>)}
      </ul> : manual.length > 0 && <p className="bp-notes" role="note"><Hand size={13} aria-hidden="true" />
        <span>{manual.join(" and ")} {manual.length === 1 ? "is" : "are"} not filled by the Bridge: set {manual.length === 1 ? "it" : "them"} on each broker page.</span></p>}
      {children}
      <div className={`bridge-row ${bridgeOk ? "is-ok" : ""}`}>
        <span className={`dot ${bridgeOk ? "dot-ok" : bridge?.state === "checking" ? "dot-info" : "dot-warn"}`} aria-hidden="true" />
        <span className="bridge-row-text">
          <b>{bridgeLabels[bridge?.state] || "Bridge not set up"}</b>
          <small>{bridgeHelp(bridge || {}, minimumVersion)}</small>
        </span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCheckBridge} title="Check the Build2Quote Bridge connection">
          <RefreshCw size={14} />Check
        </button>
      </div>
      <p className="fine-print">Autofill only. The bridge stores no broker credentials and never requests rates or books freight — you review prices on each broker site.</p>
    </div>
  );
}
