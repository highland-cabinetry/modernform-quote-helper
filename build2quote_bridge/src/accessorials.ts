import type { FieldResult, QuotePacket } from "./types";

export const ACCESSORIAL_LABELS: Record<string, string> = {
  residential: "Residential delivery",
  liftgate: "Liftgate delivery",
  appointment_delivery: "Appointment delivery",
  delivery_call_ahead: "Delivery call-ahead",
  limited_access: "Limited access",
  inside_delivery: "Inside delivery",
};

/**
 * Services the quote packet can request that an adapter fills only when it declares them in
 * `supportedAccessorials`. No adapter does today: none of their controls has been identified
 * from a fixture or a live check, and the Bridge never guesses a carrier selector.
 */
export const OPTIONAL_ACCESSORIALS = ["limited_access", "inside_delivery"] as const;

/** One "Set manually" note per requested service this adapter cannot select. */
export function manualAccessorialNotes(packet: QuotePacket, supported: readonly string[] = []): FieldResult[] {
  return OPTIONAL_ACCESSORIALS
    .filter(key => packet.accessorials?.[key] === true && !supported.includes(key))
    .map(key => ({
      field: ACCESSORIAL_LABELS[key],
      ok: false,
      manual: "set" as const,
      message: "Set manually on the broker page; the Bridge does not select this service here.",
    }));
}

/** Failures that need a refill, a repair, or a review of the fill itself (manual notes excluded). */
export function blockingFailures(fields: FieldResult[]): FieldResult[] {
  return fields.filter(field => !field.ok && !field.manual);
}

export function manualNotes(fields: FieldResult[]): FieldResult[] {
  return fields.filter(field => !field.ok && Boolean(field.manual));
}

/** e.g. "Set manually: Limited access, Inside delivery. Check manually: pallet 1 dimensions." */
export function manualNoteSummary(fields: FieldResult[]): string {
  const notes = manualNotes(fields);
  const unique = (values: string[]) => [...new Set(values)];
  const set = unique(notes.filter(note => note.manual === "set").map(note => note.field));
  const check = unique(notes.filter(note => note.manual === "check")
    .map(note => note.field.replace(/\s+manual review required$/i, "")));
  return [
    set.length ? `Set manually: ${set.join(", ")}.` : "",
    check.length ? `Check manually: ${check.join(", ")}.` : "",
  ].filter(Boolean).join(" ");
}

/** Notes first (Quote Helper shows the first few failing fields), then everything else in order. */
export function notesFirst(fields: FieldResult[]): FieldResult[] {
  return [...manualNotes(fields), ...fields.filter(field => field.ok || !field.manual)];
}

/** Add notes for the packet to `fields`, replacing earlier copies so repeated verification stays flat. */
export function withAccessorialNotes(fields: FieldResult[], packet: QuotePacket, supported: readonly string[] = []): FieldResult[] {
  const notes = manualAccessorialNotes(packet, supported);
  const names = new Set(notes.map(note => note.field));
  return [...notes, ...fields.filter(field => !(field.manual === "set" && names.has(field.field)))];
}
