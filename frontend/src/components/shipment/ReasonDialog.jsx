import { useEffect, useRef, useState } from "react";
import { Field, Modal } from "../Common";

// Ask for a required reason (void an entry, reopen a shipment). Optional extra content above the reason.
export default function ReasonDialog({ open, title, description, label = "Reason", confirmLabel = "Confirm", tone, onClose, onConfirm, children, busy = false }) {
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const input = useRef(null);
  // Runs after the modal's own focus handling (child effects run first), so typing can start at once.
  useEffect(() => { if (open) { setReason(""); setTried(false); input.current?.focus(); } }, [open]);
  const submit = (event) => {
    event?.preventDefault();
    setTried(true);
    if (!reason.trim()) return;
    onConfirm(reason.trim());
  };
  return (
    <Modal open={open} title={title} description={description} onClose={onClose} tone={tone}
      footer={<>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="submit" form="reason-form" className={`btn ${tone === "danger" ? "btn-danger-solid" : "btn-primary"}`} disabled={busy}>{confirmLabel}</button>
      </>}>
      <form id="reason-form" className="form-grid" onSubmit={submit}>
        {children}
        <Field label={label} wide error={tried && !reason.trim() ? "A reason is required" : ""}>
          <input ref={input} value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}
