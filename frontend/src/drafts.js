import { useEffect, useRef, useState } from "react";

const database = () => new Promise((resolve, reject) => {
  if (!globalThis.indexedDB) return reject(new Error("This browser cannot save drafts."));
  const request = indexedDB.open("quote-helper-drafts", 1);
  request.onupgradeneeded = () => request.result.createObjectStore("drafts", { keyPath: "key" });
  request.onerror = () => reject(request.error);
  request.onsuccess = () => resolve(request.result);
});

export async function readDraft(key) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts");
    const request = tx.objectStore("drafts").get(key);
    tx.oncomplete = () => { db.close(); resolve(request.result); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export async function writeDraft(key, value, expectedRevision) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", "readwrite");
    const store = tx.objectStore("drafts");
    const request = store.get(key);
    let record;
    let conflict = false;
    request.onsuccess = () => {
      if ((request.result?.revision || 0) !== expectedRevision) { conflict = true; tx.abort(); return; }
      record = { key, schema: 1, revision: expectedRevision + 1, updatedAt: Date.now(), value };
      store.put(record);
    };
    tx.oncomplete = () => { db.close(); resolve(record); };
    tx.onabort = tx.onerror = () => { db.close(); reject(new Error(conflict
      ? "Another tab changed this draft. Reload its draft before continuing to save. Your current edits remain on screen."
      : "Draft storage failed. Keep this page open and save the shipment.")); };
  });
}

export function useDraft(key, value, restore) {
  const text = JSON.stringify(value);
  const current = useRef({ text, restore }); current.current = { text, restore };
  const revision = useRef(0);
  const last = useRef(text);
  const pending = useRef(Promise.resolve());
  const history = useRef([]);
  const future = useRef([]);
  const applying = useRef(null);
  const [ready, setReady] = useState(false);
  const [recovery, setRecovery] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [, redraw] = useState(0);
  useEffect(() => {
    let live = true;
    setReady(false);
    readDraft(key).then(record => {
      if (!live) return;
      revision.current = record?.revision || 0;
      last.current = current.current.text;
      history.current = [last.current]; future.current = [];
      if (record?.value && record.schema === 1) setRecovery(record);
      else setReady(true);
    }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [key]);
  const persist = () => {
    if (!ready || error || current.current.text === last.current) return;
    const value = JSON.parse(current.current.text);
    const meaningful = key === "builder" ? value.lines?.some(line => line.original_sku?.trim())
      || value.shipmentName !== "New shipment" : key === "quick-quote" ? value.name || value.rows?.some(row => row.length || row.weight) : true;
    if (!meaningful && revision.current === 0) { setMessage(""); return; }
    const snapshot = current.current.text;
    last.current = snapshot;
    pending.current = pending.current.then(async () => {
      try {
        const record = await writeDraft(key, JSON.parse(snapshot), revision.current);
        revision.current = record.revision; setMessage("Draft saved on this browser");
      } catch (e) { setError(e.message); }
    });
  };
  const persistRef = useRef(persist); persistRef.current = persist;
  useEffect(() => {
    if (!ready) return;
    if (applying.current === text) applying.current = null;
    else if (history.current.at(-1) !== text) {
      history.current = [...history.current.slice(-49), text]; future.current = [];
      redraw(n => n + 1);
    }
    if (text !== last.current && !error) setMessage("Saving draft…");
    const timer = setTimeout(() => persistRef.current(), 300);
    return () => clearTimeout(timer);
  }, [text, ready]);
  useEffect(() => {
    const flush = () => persistRef.current();
    window.addEventListener("pagehide", flush); document.addEventListener("visibilitychange", flush);
    return () => { window.removeEventListener("pagehide", flush); document.removeEventListener("visibilitychange", flush); };
  }, []);
  const apply = snapshot => { applying.current = snapshot; current.current.restore(JSON.parse(snapshot)); redraw(n => n + 1); };
  return {
    recovery, message, error, canUndo: history.current.length > 1, canRedo: future.current.length > 0,
    restore: () => { apply(JSON.stringify(recovery.value)); setRecovery(null); setReady(true); },
    discard: async () => {
      try { const record = await writeDraft(key, null, revision.current); revision.current = record.revision;
        setRecovery(null); setReady(true); } catch (e) { setError(e.message); }
    },
    reload: async () => { try { const record = await readDraft(key); revision.current = record?.revision || 0;
      setRecovery(record?.value ? record : null); setError(""); setReady(!record?.value); last.current = current.current.text;
    } catch (e) { setError(e.message); } },
    undo: () => { if (history.current.length > 1) { future.current.push(history.current.pop()); apply(history.current.at(-1)); } },
    redo: () => { if (future.current.length) { const next = future.current.pop(); history.current.push(next); apply(next); } },
  };
}
