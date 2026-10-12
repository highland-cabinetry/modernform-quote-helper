import { describe, expect, it } from "vitest";

import {
  blockingFailures, manualAccessorialNotes, manualNoteSummary, notesFirst, withAccessorialNotes,
} from "../src/accessorials";
import { DsvAdapter } from "../src/adapters/dsv";
import { EchoAdapter } from "../src/adapters/echo";
import { MyUnishippersAdapter } from "../src/adapters/myunishippers";
import { OdflAdapter } from "../src/adapters/odfl";
import { TqlAdapter } from "../src/adapters/tql";
import type { QuotePacket } from "../src/types";

const packet = {
  accessorials: { residential: false, liftgate: true, appointment_delivery: true, limited_access: true, inside_delivery: true },
} as unknown as QuotePacket;

describe("accessorials the Bridge cannot select", () => {
  it("reports one 'Set manually' note per requested service on every broker", () => {
    for (const adapter of [new EchoAdapter(), new TqlAdapter(), new DsvAdapter(), new MyUnishippersAdapter(), new OdflAdapter()]) {
      const notes = manualAccessorialNotes(packet, adapter.supportedAccessorials);
      expect(notes.map(note => note.field)).toEqual(["Limited access", "Inside delivery"]);
      expect(notes.every(note => note.manual === "set" && note.ok === false)).toBe(true);
    }
    expect(manualAccessorialNotes({ accessorials: { limited_access: false } } as unknown as QuotePacket)).toEqual([]);
    expect(manualAccessorialNotes(packet, ["inside_delivery"]).map(note => note.field)).toEqual(["Limited access"]);
  });

  it("summarizes notes and keeps them out of the failures that trigger refills", () => {
    const fields = withAccessorialNotes([
      { field: "pickup postal code", ok: true, message: "" },
      { field: "pallet 1 dimensions manual review required", ok: false, message: "TQL blank editors", manual: "check" },
    ], packet);
    expect(blockingFailures(fields)).toEqual([]);
    expect(manualNoteSummary(fields)).toBe("Set manually: Limited access, Inside delivery. Check manually: pallet 1 dimensions.");
    expect(notesFirst([{ field: "zip", ok: false, message: "" }, ...fields]).map(field => field.field).slice(0, 3))
      .toEqual(["Limited access", "Inside delivery", "pallet 1 dimensions manual review required"]);
    // Re-adding notes (repeated verification) does not duplicate them.
    expect(withAccessorialNotes(fields, packet).filter(field => field.field === "Limited access")).toHaveLength(1);
    expect(blockingFailures([{ field: "zip", ok: false, message: "" }])).toHaveLength(1);
  });
});
