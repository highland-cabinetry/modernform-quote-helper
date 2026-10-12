import { History, Redo2, Undo2 } from "lucide-react";

const shortDate = (value) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export default function DraftBar({ draft }) {
  if (draft.recovery) {
    return <div className="draft-bar draft-recovery" aria-label="Draft recovery and edit history">
      <History size={16} aria-hidden="true" />
      <span>Unfinished draft from {shortDate(draft.recovery.updatedAt)}</span>
      <button type="button" className="btn btn-primary btn-sm" onClick={draft.restore}>Restore draft</button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={draft.discard}>Discard draft</button>
      {draft.error && <span className="draft-error" role="alert">{draft.error} <button type="button" className="link-btn" onClick={draft.reload}>Reload draft</button></span>}
    </div>;
  }
  return <div className="draft-bar" aria-label="Draft recovery and edit history">
    {draft.message && <span className="draft-status" role="status">{draft.message}</span>}
    <button type="button" className="btn btn-ghost btn-sm" disabled={!draft.canUndo} onClick={draft.undo} title="Undo edit"><Undo2 size={15} />Undo edit</button>
    <button type="button" className="btn btn-ghost btn-sm" disabled={!draft.canRedo} onClick={draft.redo} title="Redo edit"><Redo2 size={15} />Redo edit</button>
    {draft.error && <span className="draft-error" role="alert">{draft.error} <button type="button" className="link-btn" onClick={draft.reload}>Reload draft</button></span>}
  </div>;
}
