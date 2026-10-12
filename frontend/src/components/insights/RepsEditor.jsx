import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, Plus, RotateCcw, Save, Trash2, Users } from "lucide-react";
import { EmptyState } from "../Common";
import { saveCompanyConfig } from "./shared";

let nextId = 0;
const withIds = (reps = []) => reps.map((rep) => ({ name: rep.name || "", email: rep.email || "", active: rep.active !== false, _id: `rep-${nextId++}` }));
const strip = (reps) => reps.map(({ _id, ...rep }) => ({ name: rep.name.trim(), email: rep.email.trim(), active: rep.active }));
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function repErrors(reps) {
  const seen = new Map();
  return reps.map((rep) => {
    const name = rep.name.trim();
    const errors = {};
    if (!name) errors.name = "Enter a name";
    else if (name.length > 80) errors.name = "80 characters at most";
    else if (seen.has(name.toLowerCase())) errors.name = "Already listed";
    if (name) seen.set(name.toLowerCase(), true);
    if (rep.email.trim() && !EMAIL.test(rep.email.trim())) errors.email = "Not an email address";
    return errors;
  });
}

// Sales reps: matched by name to each order's sales rep and copied on customer emails.
export default function RepsEditor({ company, notify, onSaved }) {
  const saved = company?.config?.reps;
  const [reps, setReps] = useState(() => withIds(saved));
  const [busy, setBusy] = useState(false);
  useEffect(() => { setReps(withIds(saved)); }, [saved]);

  const errors = useMemo(() => repErrors(reps), [reps]);
  const invalid = errors.some((entry) => Object.keys(entry).length);
  const dirty = JSON.stringify(strip(reps)) !== JSON.stringify(strip(withIds(saved)));
  const update = (id, field, value) => setReps((current) => current.map((rep) => (rep._id === id ? { ...rep, [field]: value } : rep)));
  const add = () => {
    const rep = { name: "", email: "", active: true, _id: `rep-${nextId++}` };
    setReps((current) => [...current, rep]);
    requestAnimationFrame(() => document.getElementById(`${rep._id}-name`)?.focus());
  };

  const save = async (event) => {
    event.preventDefault();
    if (invalid) return;
    setBusy(true);
    try {
      await saveCompanyConfig(company.id, "reps", strip(reps));
      notify("Sales reps saved.");
      await onSaved?.();
    } catch (error) { notify(error.message, "danger"); }
    finally { setBusy(false); }
  };

  return (
    <form className="card" onSubmit={save} aria-labelledby="reps-title" noValidate>
      <header className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="reps-title">Sales reps</h2>
          <p className="card-description">Matched by name to each order's sales rep. Active reps with an email are copied (CC) on customer emails when that is turned on under Customer emails.</p>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={add}><Plus size={14} />Add rep</button>
      </header>
      {reps.length ? (
        <div className="ins-reps" role="table" aria-label="Sales reps">
          <div className="ins-reps-row ins-reps-head" role="row">
            <span role="columnheader">Name</span><span role="columnheader">Email</span><span role="columnheader">Active</span><span role="columnheader"><span className="sr-only">Remove</span></span>
          </div>
          {reps.map((rep, index) => (
            <div key={rep._id} className={`ins-reps-row ${rep.active ? "" : "is-inactive"}`} role="row">
              <span role="cell" className="ins-reps-cell">
                <input id={`${rep._id}-name`} value={rep.name} maxLength={80} placeholder="Name as on the order" aria-label={`Rep ${index + 1} name`}
                  aria-invalid={Boolean(errors[index].name)} onChange={(event) => update(rep._id, "name", event.target.value)} />
                {errors[index].name && <small className="field-error">{errors[index].name}</small>}
              </span>
              <span role="cell" className="ins-reps-cell">
                <input type="email" value={rep.email} maxLength={200} placeholder="name@company.com" aria-label={`Rep ${index + 1} email`}
                  aria-invalid={Boolean(errors[index].email)} onChange={(event) => update(rep._id, "email", event.target.value)} />
                {errors[index].email && <small className="field-error">{errors[index].email}</small>}
              </span>
              <span role="cell" className="ins-reps-active">
                <label className="ins-toggle"><input type="checkbox" checked={rep.active} aria-label={`Rep ${index + 1} active`} onChange={(event) => update(rep._id, "active", event.target.checked)} /><span aria-hidden="true">{rep.active ? "Active" : "Inactive"}</span></label>
              </span>
              <span role="cell">
                <button type="button" className="icon-btn icon-btn-danger" aria-label={`Remove ${rep.name || `rep ${index + 1}`}`}
                  onClick={() => setReps((current) => current.filter((entry) => entry._id !== rep._id))}><Trash2 size={15} /></button>
              </span>
            </div>
          ))}
        </div>
      ) : <EmptyState icon={Users} title="No sales reps yet">Add the reps whose names appear on your orders so they are copied on customer emails.</EmptyState>}
      <div className="card-actions-row">
        <button type="submit" className="btn btn-primary" disabled={busy || invalid || !dirty}>{busy ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}Save reps</button>
        {dirty && <button type="button" className="btn btn-ghost" onClick={() => setReps(withIds(saved))}><RotateCcw size={15} />Discard changes</button>}
        {!dirty && <span className="muted ins-saved">{reps.length ? `${reps.filter((rep) => rep.active).length} active of ${reps.length}` : ""}</span>}
      </div>
    </form>
  );
}
