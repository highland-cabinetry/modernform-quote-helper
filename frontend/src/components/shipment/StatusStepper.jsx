import { Check } from "lucide-react";
import { STEPS, stepIndex } from "./model";
import "../../styles/shipments.css";

// Planning → Booked → In transit → Delivered → Closed. Steps are buttons so a status can also be moved back.
export default function StatusStepper({ status, disabled = false, onStep }) {
  const current = stepIndex(status);
  const cancelled = status === "cancelled";
  return (
    <ol className={`status-stepper ${cancelled ? "is-cancelled" : ""}`} aria-label="Shipment status">
      {STEPS.map((step, index) => {
        const state = cancelled ? "todo" : index < current ? "done" : index === current ? "current" : "todo";
        return (
          <li key={step.key} className={`step step-${state}`} aria-current={state === "current" ? "step" : undefined}>
            <button type="button" className="step-btn" disabled={disabled || state === "current"} onClick={() => onStep?.(step.key)}
              title={state === "current" ? "Current status" : index < current ? `Move back to ${step.label}` : `Set to ${step.label}`}>
              <span className="step-dot" aria-hidden="true">{state === "done" ? <Check size={12} /> : index + 1}</span>
              <span className="step-label">{step.label}</span>
              <span className="sr-only">{state === "done" ? " (done)" : state === "current" ? " (current)" : ""}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
