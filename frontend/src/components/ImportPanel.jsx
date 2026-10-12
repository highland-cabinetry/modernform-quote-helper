import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, CopyCheck, FileUp, FileWarning, LoaderCircle, RefreshCcw, XCircle } from "lucide-react";
import { getJson, sendJson, uploadFiles } from "../api";
import { plural } from "../lib/format";
import { Badge } from "./Common";

const STATUS = {
  imported: { icon: CheckCircle2, tone: "ok", label: "Imported" },
  partial: { icon: AlertTriangle, tone: "warn", label: "Partly read" },
  duplicate: { icon: CopyCheck, tone: "info", label: "Already imported" },
  unsupported: { icon: FileWarning, tone: "warn", label: "Not recognised" },
  failed: { icon: XCircle, tone: "danger", label: "Failed" },
};

export function Dropzone({ busy, onFiles, compact = false, label = "Drop packing slips, invoices or sales orders here" }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);
  return (
    <>
      <input ref={input} hidden multiple type="file" accept=".pdf,.csv,.tsv,.xlsx,.xlsm,.zip" onChange={(event) => { onFiles([...event.target.files]); event.target.value = ""; }} />
      <div className={`dropzone ${dragging ? "is-dragging" : ""} ${compact ? "is-compact" : ""}`} role="button" tabIndex={0} aria-disabled={busy}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.current?.click(); } }}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); if (!busy) onFiles([...event.dataTransfer.files]); }}
        onClick={() => !busy && input.current?.click()}>
        {busy ? <LoaderCircle className="spin" size={compact ? 18 : 26} /> : <FileUp size={compact ? 18 : 28} aria-hidden="true" />}
        <span className="dropzone-text">
          <b>{busy ? "Reading documents…" : label}</b>
          <small>PDF, CSV, Excel or a ZIP of them · several files at once · click to browse</small>
        </span>
      </div>
    </>
  );
}

export async function importFiles(files, companyId) {
  return uploadFiles("/api/imports", files, "files", companyId ? { company_id: companyId } : {});
}

// Per-file outcome of an import batch, with a one-click way to accept changed lines.
export function ImportResults({ result, onChanged, notify }) {
  const [busy, setBusy] = useState("");
  if (!result) return null;
  const { summary } = result;
  const replace = async (conflict) => {
    setBusy(conflict.order_id);
    try {
      const trace = await getJson(`/api/orders/${conflict.order_id}`);
      const { order } = trace;
      await sendJson(`/api/orders/${conflict.order_id}`, "PUT", {
        order_number: order.order_number, references: order.references, customer_name: order.customer_name,
        po_number: order.po_number, order_date: order.order_date, ready_date: order.ready_date, sales_rep: order.sales_rep,
        ship_to: order.ship_to, residential: order.residential, liftgate: order.liftgate, packing_state: order.packing_state,
        notes: order.notes, lines: conflict.lines, reason: `Lines replaced from ${conflict.file}`,
      });
      notify?.(`${conflict.order_number} now uses the lines from ${conflict.file}.`);
      onChanged?.();
    } catch (error) { notify?.(error.message, "danger"); }
    finally { setBusy(""); }
  };
  return (
    <section className="import-results" aria-label="Import results">
      <p className="import-summary">
        <b>{plural(summary.created, "new order")}</b>
        {summary.updated ? `, ${summary.updated} filled in` : ""}
        {summary.existing ? `, ${summary.existing} already on file` : ""}
        {summary.conflicts ? `, ${plural(summary.conflicts, "conflict")}` : ""}
        {summary.failed ? `, ${plural(summary.failed, "file")} not read` : ""}
        <span className="muted"> · {summary.seconds}s</span>
      </p>
      <ul className="file-results">
        {result.files.map((file) => {
          const status = STATUS[file.status] || STATUS.failed;
          const Icon = status.icon;
          return (
            <li key={`${file.file}-${file.sha256 || ""}`} className={`file-result tone-${status.tone}`}>
              <Icon size={16} aria-hidden="true" />
              <div>
                <b>{file.file}</b> <Badge tone={status.tone}>{status.label}</Badge>
                {file.adapter && <small className="muted"> · {file.adapter.replaceAll("_", " ")}</small>}
                {(file.messages || []).map((message) => <small key={message} className="file-message">{message}</small>)}
                {file.ignored_rows?.length > 0 && (
                  <details className="disclosure inline-disclosure">
                    <summary>{plural(file.ignored_rows.length, "row")} not imported</summary>
                    <ul className="plain-list">
                      {file.ignored_rows.map((row, index) => <li key={`${row.source_ref}-${row.row}-${index}`}>
                        <span><b>{row.original_sku || "Blank"}</b><small>{row.source_ref}, row {row.row}</small></span><span>{row.reason}</span>
                      </li>)}
                    </ul>
                  </details>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {result.conflicts?.length > 0 && <div className="callout callout-warn">
        <AlertTriangle size={18} />
        <div>
          <b>Some orders already exist with different lines.</b>
          <p>The stored order was not changed. Replace its lines only if the new document is the correct version.</p>
          <ul className="conflict-list">
            {result.conflicts.map((conflict) => (
              <li key={`${conflict.order_id}-${conflict.file}`}>
                <span><b>{conflict.order_number}</b> in {conflict.file}: {(conflict.differences || []).slice(0, 4).join("; ") || "lines differ"}</span>
                <button type="button" className="btn btn-secondary btn-sm" disabled={Boolean(busy)} onClick={() => replace(conflict)}>
                  {busy === conflict.order_id ? <LoaderCircle className="spin" size={14} /> : <RefreshCcw size={14} />}Use new lines
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>}
    </section>
  );
}
