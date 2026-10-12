import React, { useState } from "react";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readDraft, useDraft, writeDraft } from "./drafts";
import DraftBar from "./DraftBar";

beforeEach(() => { globalThis.indexedDB = new IDBFactory(); });
afterEach(cleanup);
function Harness() {
  const [input, setInput] = useState("");
  const draft = useDraft("test", { input }, value => setInput(value.input));
  return <><input aria-label="Input" value={input} onChange={e => setInput(e.target.value)} /><DraftBar draft={draft} /></>;
}
it("recovers incomplete input without applying it until Restore is chosen", async () => {
  await writeDraft("test", { input: "12." }, 0);
  render(<Harness />);
  expect(await screen.findByText("Restore draft")).toBeTruthy();
  expect(screen.getByLabelText("Input").value).toBe("");
  fireEvent.click(screen.getByText("Restore draft"));
  expect(screen.getByLabelText("Input").value).toBe("12.");
});
it("atomically rejects concurrent-tab overwrites", async () => {
  const results = await Promise.allSettled([writeDraft("same", { input: "A" }, 0), writeDraft("same", { input: "B" }, 0)]);
  expect(results.filter(r => r.status === "fulfilled")).toHaveLength(1);
  expect(results.filter(r => r.status === "rejected")[0].reason.message).toContain("Another tab");
  expect((await readDraft("same")).revision).toBe(1);
});
it("persists edits and allows undo/redo without changing saved shipments", async () => {
  render(<Harness />);
  await waitFor(() => expect(screen.queryByText("Restore draft")).toBeNull());
  // Wait for IndexedDB initialization before editing.
  await new Promise(resolve => setTimeout(resolve, 20));
  fireEvent.change(screen.getByLabelText("Input"), { target: { value: "first" } });
  await waitFor(async () => expect((await readDraft("test"))?.value.input).toBe("first"));
  fireEvent.change(screen.getByLabelText("Input"), { target: { value: "second" } });
  await waitFor(() => expect(screen.getByText("Undo edit").disabled).toBe(false));
  fireEvent.click(screen.getByText("Undo edit"));
  expect(screen.getByLabelText("Input").value).toBe("first");
  fireEvent.click(screen.getByText("Redo edit"));
  expect(screen.getByLabelText("Input").value).toBe("second");
});
