import { useEffect, useState } from "react";
import { Copy, Mail } from "lucide-react";
import { sendJson } from "../api";
import { EMAIL_KINDS } from "../lib/format";
import { ErrorBlock, Field, Loading, Modal } from "./Common";

// Prepared customer email: the app fills in the template, the person reviews it and sends it
// from Outlook (mailto). Nothing is sent by Quote Helper.
//   <EmailDraft open kind="quote" quoteId=… | shipmentId=… onClose onPrepared={(draft) => …} />
export default function EmailDraft({ open, kind, quoteId, shipmentId, onClose, onPrepared, notify }) {
  const [draft, setDraft] = useState(null);
  const [form, setForm] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setDraft(null); setForm(null); setError("");
    sendJson("/api/emails/draft", "POST", { kind, ...(quoteId ? { quote_id: quoteId } : { shipment_id: shipmentId }) })
      .then((value) => { setDraft(value); setForm({ to: value.to.join("; "), cc: value.cc.join("; "), subject: value.subject, body: value.body }); })
      .catch((reason) => setError(reason.message));
  }, [open, kind, quoteId, shipmentId]);

  const list = (text) => text.split(/[;,]/).map((value) => value.trim()).filter(Boolean);
  const mailto = () => {
    const params = [form.cc && `cc=${encodeURIComponent(list(form.cc).join(","))}`, `subject=${encodeURIComponent(form.subject)}`,
      `body=${encodeURIComponent(form.body)}`].filter(Boolean).join("&");
    return `mailto:${list(form.to).map(encodeURIComponent).join(",")}?${params}`;
  };
  const record = async () => {
    try {
      await sendJson("/api/emails/log", "POST", { kind, entity_type: draft.entity_type, entity_id: draft.entity_id,
        to: list(form.to), cc: list(form.cc), subject: form.subject });
    } catch (reason) { notify?.(`The email opened, but it could not be logged: ${reason.message}`, "danger"); }
    onPrepared?.(draft);
  };
  const open_ = async () => {
    const link = mailto();
    if (link.length > 1900) { notify?.("This email is long, so Outlook may cut it off. Use Copy instead.", "danger"); }
    window.location.href = link;
    await record();
  };
  const copy = async () => {
    const text = `To: ${form.to}\n${form.cc ? `Cc: ${form.cc}\n` : ""}Subject: ${form.subject}\n\n${form.body}`;
    try { await navigator.clipboard.writeText(text); notify?.("Email copied. Paste it into Outlook."); await record(); }
    catch { notify?.("Copy did not work in this window; select the text and copy it.", "danger"); }
  };

  return (
    <Modal open={open} onClose={onClose} title={EMAIL_KINDS[kind] || "Email"}
      description="Review the email, then open it in Outlook to send it. Quote Helper never sends email itself."
      footer={form && <>
        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-secondary" onClick={copy}><Copy size={15} />Copy</button>
        <button type="button" className="btn btn-primary" onClick={open_} disabled={!form.to.trim()}><Mail size={15} />Open in Outlook</button>
      </>}>
      {error && <ErrorBlock message={error} />}
      {!error && !form && <Loading label="Preparing the email…" />}
      {form && <div className="form-grid email-draft">
        {draft.warnings?.length > 0 && <p className="field-wide warn-text">{draft.warnings.join(" ")}</p>}
        <Field label="To" wide hint={form.to ? "" : "Add the customer's email on the order to fill this in."}>
          <input value={form.to} onChange={(event) => setForm({ ...form, to: event.target.value })} /></Field>
        <Field label="Cc" wide><input value={form.cc} onChange={(event) => setForm({ ...form, cc: event.target.value })} /></Field>
        <Field label="Subject" wide><input value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })} /></Field>
        <Field label="Message" wide><textarea rows={10} value={form.body} onChange={(event) => setForm({ ...form, body: event.target.value })} /></Field>
      </div>}
    </Modal>
  );
}
