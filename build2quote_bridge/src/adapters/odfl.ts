import { matchesPostalAddress, selectExactPostalCode } from "../autocomplete";
import { clean, fieldResult, setChecked, setValue, visible } from "../dom";
import type { AddressInfo, FieldResult, PalletRow, QuotePacket } from "../types";
import { waitFor } from "../timing";
import { BaseAdapter } from "./base";

const PAGE_PATH = "/us/en/tools/freight-shipping-rate-estimate/ltl-rate-estimate.html";
const LOCATION_COMMIT_TIMEOUT_MS = 35000;
const SUGGESTION_GRACE_MS = 2000;
type LocationKind = "origin" | "destination";

interface LocationControls {
  address: HTMLInputElement | null;
  city: HTMLSelectElement | null;
  state: HTMLInputElement | null;
  country: HTMLSelectElement | null;
  postal: HTMLInputElement | null;
}

function waitUntil(predicate: () => boolean, timeoutMs = 4000): Promise<boolean> {
  return waitFor(predicate, { timeoutMs, pollMs: 40 });
}

function input(id: string): HTMLInputElement | null {
  return document.getElementById(id) as HTMLInputElement | null;
}

function select(id: string): HTMLSelectElement | null {
  return document.getElementById(id) as HTMLSelectElement | null;
}

function locationControls(kind: LocationKind): LocationControls {
  if (kind === "origin") return {
    address: input("ltl_origin_address"), city: select("ltl_origin_city"), state: input("ltl_originState"),
    country: select("ltl_origin_country"), postal: input("ltl_originZipcode"),
  };
  return {
    address: input("ltl_destination_address"), city: select("ltl_destination_city"), state: input("ltl_destState"),
    country: select("ltl_destination_country"), postal: input("ltl_destZipcode"),
  };
}

function normalized(value: unknown): string { return clean(value).toLowerCase(); }
function selectedText(control: HTMLSelectElement | null): string {
  return clean(control?.selectedOptions[0]?.textContent || control?.value);
}
function hasExactToken(value: string, expected: string): boolean {
  if (!clean(expected)) return Boolean(clean(value));
  const escaped = clean(expected).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|[^a-z0-9])${escaped}(?:[^a-z0-9]|$)`, "i").test(clean(value));
}
function isUsCountry(control: HTMLSelectElement | null): boolean {
  const value = normalized(`${control?.value || ""} ${selectedText(control)}`);
  return /(?:^|\s)(?:us|usa)(?:\s|$)/.test(value) || value.includes("united states");
}

function locationResolved(kind: LocationKind, address: AddressInfo): boolean {
  const controls = locationControls(kind);
  const city = selectedText(controls.city);
  const state = clean(controls.state?.value);
  return hasExactToken(clean(controls.postal?.value), address.postal_code)
    && Boolean(city) && !/^(?:select|choose)(?:\s|$)/i.test(city)
    && Boolean(state)
    && (!address.city || normalized(city) === normalized(address.city))
    && (!address.state || hasExactToken(state, address.state))
    && isUsCountry(controls.country);
}
function postalCommitted(kind: LocationKind, address: AddressInfo): boolean {
  const postal = locationControls(kind).postal;
  return Boolean(postal && hasExactToken(clean(postal.value), address.postal_code));
}

function postalOptions(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(".pac-container .pac-item")).filter(visible);
}
function requestCarrierLocationCommit(control: HTMLInputElement): () => void {
  const form = control.form;
  const blockSyntheticSubmit = (event: Event) => {
    if (!event.isTrusted) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  form?.addEventListener("submit", blockSyntheticSubmit, true);
  for (const type of ["keydown", "keypress", "keyup"] as const) {
    const event = new KeyboardEvent(type, { key: "Enter", code: "Enter", bubbles: true, cancelable: true });
    control.dispatchEvent(event);
    event.preventDefault();
  }
  return () => form?.removeEventListener("submit", blockSyntheticSubmit, true);
}
async function selectLocation(kind: LocationKind, address: AddressInfo): Promise<boolean> {
  if (!Object.values(locationControls(kind)).every(Boolean)) return false;
  const control = locationControls(kind).address!;
  const selection = selectExactPostalCode({
    getControl: () => locationControls(kind).address,
    zip: address.postal_code,
    getOptions: postalOptions,
    matchesOption: (candidate) => matchesPostalAddress(candidate, address),
    keyboardLookup: true,
    // Only the read-only ZIP populated by ODFL counts as a committed location.
    verifyResolved: () => postalCommitted(kind, address),
    requireCarrierVerification: true,
    timeoutMs: LOCATION_COMMIT_TIMEOUT_MS,
  });
  let releaseSubmitGuard: () => void = () => {};
  try {
    const popupOrCarrierCommit = await waitUntil(() => postalCommitted(kind, address)
      || postalOptions().some((option) => matchesPostalAddress(option, address)), SUGGESTION_GRACE_MS);
    if (!popupOrCarrierCommit && !postalCommitted(kind, address)) {
      // After account selection ODFL may rebuild this input without its Places
      // popup. Enter asks ODFL to resolve the typed ZIP asynchronously. Keep
      // the carrier's read-only postal and location fields as the commit check.
      releaseSubmitGuard = requestCarrierLocationCommit(control);
    }
    const selected = await selection;
    if (!selected) return false;
    if (locationResolved(kind, address)) return true;
    const city = locationControls(kind).city;
    if (!city) return false;
    city.focus();
    city.click();
    return waitUntil(() => {
      if (address.city) {
        const option = Array.from(city.options).find((candidate) => normalized(candidate.textContent) === normalized(address.city)
          || normalized(candidate.value) === normalized(address.city));
        if (option && city.value !== option.value) setValue(city, option.value);
      }
      return locationResolved(kind, address);
    });
  } finally {
    releaseSubmitGuard();
  }
}

function indexedControls(prefix: string): Map<number, HTMLInputElement | HTMLSelectElement> {
  const controls = new Map<number, HTMLInputElement | HTMLSelectElement>();
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>(`[id^='${prefix}']`).forEach((control) => {
    const match = control.id.match(new RegExp(`^${prefix}(\\d+)$`));
    if (match) controls.set(Number(match[1]), control);
  });
  return controls;
}
function rowIndexes(): number[] { return [...indexedControls("ltl_weight").keys()].sort((a, b) => a - b); }
function dimensionIndexes(): number[] { return [...indexedControls("ltl_length").keys()].sort((a, b) => a - b); }
function addButton(selector: string): HTMLButtonElement | null {
  const button = document.querySelector<HTMLButtonElement>(selector);
  return button && visible(button) && !button.disabled ? button : null;
}
async function ensureRows(expected: number, indexes: () => number[], selector: string): Promise<boolean> {
  let current = indexes();
  if (current.length > expected) return false;
  while (current.length < expected) {
    const button = addButton(selector);
    if (!button) return false;
    const before = current.length;
    button.click();
    if (!await waitUntil(() => indexes().length > before)) return false;
    current = indexes();
  }
  return current.length === expected;
}

function exactValue(control: HTMLInputElement | HTMLSelectElement | null, expected: string | number): boolean {
  if (!control) return false;
  const actual = clean(control.value).replaceAll(",", "");
  const wanted = clean(expected).replaceAll(",", "");
  const actualNumber = Number(actual); const wantedNumber = Number(wanted);
  if (actual !== "" && wanted !== "" && Number.isFinite(actualNumber) && Number.isFinite(wantedNumber)) {
    return Math.abs(actualNumber - wantedNumber) < 0.01;
  }
  return normalized(actual) === normalized(wanted);
}
function setExactChoice(control: HTMLSelectElement | null, aliases: string[]): boolean {
  if (!control) return false;
  const wanted = aliases.map(normalized);
  const option = Array.from(control.options).find((candidate) => (
    [normalized(candidate.value), normalized(candidate.textContent)].some((value) => wanted.includes(value))
  ));
  return Boolean(option && setValue(control, option.value));
}
function choiceMatches(control: HTMLSelectElement | null, aliases: string[]): boolean {
  if (!control) return false;
  const current = [normalized(control.value), normalized(selectedText(control))];
  const wanted = aliases.map(normalized);
  return current.some((value) => wanted.includes(value));
}
function freightClassNumber(value: string): number | null {
  const match = clean(value).match(/(?:^|\b)(\d{1,3}(?:\.\d+)?)(?:\b|$)/);
  if (!match) return null;
  const number = Number(match[1]);
  return Number.isFinite(number) ? number : null;
}

function freightClassOption(control: HTMLSelectElement, expected: string): HTMLOptionElement | undefined {
  const target = freightClassNumber(expected);
  return Array.from(control.options).find((option) => {
    const candidate = freightClassNumber(option.textContent) ?? freightClassNumber(option.value);
    return target !== null && candidate !== null
      ? Math.abs(candidate - target) < 0.01
      : normalized(option.value) === normalized(expected) || normalized(option.textContent) === normalized(expected);
  });
}
async function setFreightClass(control: HTMLSelectElement | null, expected: string): Promise<boolean> {
  if (!control) return false;
  let option = freightClassOption(control, expected);
  if (!option) {
    // ODFL lazily loads class choices when the Class control is opened.
    control.focus();
    control.click();
    await waitUntil(() => Boolean(freightClassOption(control, expected)), 1500);
    option = freightClassOption(control, expected);
  }
  return Boolean(option && setValue(control, option.value));
}
function freightClassMatches(control: HTMLSelectElement | null, expected: string): boolean {
  if (!control) return false;
  const target = freightClassNumber(expected);
  const selected = freightClassNumber(selectedText(control)) ?? freightClassNumber(control.value);
  return target !== null && selected !== null
    ? Math.abs(target - selected) < 0.01
    : normalized(control.value) === normalized(expected) || normalized(selectedText(control)) === normalized(expected);
}

async function fillCommodityRow(index: number, pallet: PalletRow, packetNmfc: string): Promise<FieldResult[]> {
  const number = index + 1;
  const nmfc = clean(pallet.nmfc || packetNmfc);
  const classFilled = await setFreightClass(select(`ltl_class${index}`), pallet.freight_class);
  const results = [
    fieldResult(`pallet ${number} weight`, setValue(input(`ltl_weight${index}`), Math.round(pallet.weight))),
    fieldResult(`pallet ${number} freight class`, classFilled, classFilled ? "" : `Select freight class ${pallet.freight_class} manually; it was not available in the ODFL class list.`),
  ];
  if (nmfc) {
    const nmfcFilled = setValue(input(`ltl_nmfc${index}`), nmfc);
    results.push(fieldResult(`pallet ${number} NMFC`, nmfcFilled,
      nmfcFilled ? "" : `Enter NMFC ${nmfc} manually in pallet ${number}'s ODFL NMFC field.`));
  }
  else results.push(fieldResult(`pallet ${number} NMFC`, true, "No NMFC was provided; the existing carrier value was preserved for manual review."));
  // The quote packet has no NMFC subcode. Preserve any value already entered
  // on ODFL's form instead of silently erasing information the packet cannot represent.
  return results;
}
function verifyCommodityRow(index: number, pallet: PalletRow, packetNmfc: string): FieldResult[] {
  const number = index + 1;
  const nmfc = clean(pallet.nmfc || packetNmfc);
  const results = [
    fieldResult(`pallet ${number} weight verification`, exactValue(input(`ltl_weight${index}`), Math.round(pallet.weight))),
    fieldResult(`pallet ${number} class verification`, freightClassMatches(select(`ltl_class${index}`), pallet.freight_class)),
  ];
  if (nmfc) results.push(fieldResult(`pallet ${number} NMFC verification`, exactValue(input(`ltl_nmfc${index}`), nmfc)));
  return results;
}
function fillDimensionRow(index: number, pallet: PalletRow): FieldResult[] {
  const number = index + 1;
  return [
    fieldResult(`pallet ${number} length`, setValue(input(`ltl_length${index}`), Math.ceil(pallet.length))),
    fieldResult(`pallet ${number} width`, setValue(input(`ltl_width${index}`), Math.ceil(pallet.width))),
    fieldResult(`pallet ${number} height`, setValue(input(`ltl_height${index}`), Math.ceil(pallet.height))),
    fieldResult(`pallet ${number} dimension unit`, setExactChoice(select(`ltl_lengthUnit${index}`), ["IN", "Inch", "Inches"])),
    fieldResult(`pallet ${number} handling units`, setValue(input(`ltl_unitCount${index}`), 1)),
  ];
}
function verifyDimensionRow(index: number, pallet: PalletRow): FieldResult[] {
  const number = index + 1;
  return [
    fieldResult(`pallet ${number} length verification`, exactValue(input(`ltl_length${index}`), Math.ceil(pallet.length))),
    fieldResult(`pallet ${number} width verification`, exactValue(input(`ltl_width${index}`), Math.ceil(pallet.width))),
    fieldResult(`pallet ${number} height verification`, exactValue(input(`ltl_height${index}`), Math.ceil(pallet.height))),
    fieldResult(`pallet ${number} dimension unit verification`, choiceMatches(select(`ltl_lengthUnit${index}`), ["IN", "Inch", "Inches"])),
    fieldResult(`pallet ${number} handling units verification`, exactValue(input(`ltl_unitCount${index}`), 1)),
  ];
}

const services = [
  ["residential", "RDC", /residential|non-commercial/i],
  ["liftgate", "HYD", /liftgate/i],
  ["appointment_delivery", "CA", /scheduled appointment/i],
  ["delivery_call_ahead", "ARN", /notification prior to delivery/i],
] as const;
function serviceControl(value: string, labelPattern: RegExp): HTMLInputElement | null {
  const controls = Array.from(document.querySelectorAll<HTMLInputElement>(
    ".cmp-ltlRateEstimate__optionalServicesContainer input[type='checkbox']",
  ));
  const byLabel = controls.find((control) => {
    const label = control.labels ? Array.from(control.labels).map((candidate) => candidate.textContent).join(" ") : "";
    return labelPattern.test(clean(`${label} ${control.closest("label")?.textContent || ""}`));
  });
  if (byLabel) return byLabel;
  return controls.find((control) => control.value === value) || null;
}
function outboundControl(): HTMLInputElement | null {
  return Array.from(document.querySelectorAll<HTMLInputElement>(".js-ltl-form input[type='radio']")).find((control) => {
    const label = control.labels ? Array.from(control.labels).map((candidate) => candidate.textContent).join(" ") : "";
    return normalized(control.value) === "outbound"
      || /\boutbound\b/i.test(clean(`${label} ${control.closest("label")?.textContent || ""}`));
  }) || null;
}
function accountSectionReady(): boolean {
  const form = document.querySelector<HTMLElement>(".js-ltl-form");
  if (!form) return false;
  const selectAccount = Array.from(form.querySelectorAll<HTMLElement>("button, a, [role='button']"))
    .some((control) => visible(control) && /^select\s+(?:an?\s+)?account$/i.test(clean(control.textContent)));
  return !selectAccount;
}

export class OdflAdapter extends BaseAdapter {
  broker = "odfl" as const;

  detect(): boolean {
    try {
      const current = new URL(location.href);
      return current.protocol === "https:" && current.hostname === "www.odfl.com" && current.pathname === PAGE_PATH;
    } catch { return false; }
  }
  formRoot(): Element | null { return document.querySelector(".cmp-ltlRateEstimate__form.js-ltl-form"); }
  manualPrerequisite(): FieldResult {
    return fieldResult("ODFL account selection", accountSectionReady(),
      "Select the ODFL account manually; Build2Quote does not choose customer accounts.");
  }

  discoverFields(): FieldResult[] {
    const origin = locationControls("origin"); const destination = locationControls("destination");
    return [
      this.manualPrerequisite(),
      fieldResult("outbound shipment", Boolean(outboundControl())),
      fieldResult("origin location", Object.values(origin).every(Boolean)),
      fieldResult("destination location", Object.values(destination).every(Boolean)),
      fieldResult("commodity row", rowIndexes().length > 0),
      fieldResult("dimension row", dimensionIndexes().length > 0),
      ...rowIndexes().map((index) => fieldResult(`pallet ${index + 1} freight class control`, Boolean(select(`ltl_class${index}`)))),
      ...rowIndexes().map((index) => fieldResult(`pallet ${index + 1} NMFC control`, Boolean(input(`ltl_nmfc${index}`)))),
      fieldResult("add commodity row", Boolean(addButton(".js-addwtclass-fields"))),
      fieldResult("add dimension row", Boolean(addButton(".js-add-dimensions-fields"))),
      ...services.map(([field, value, label]) => fieldResult(field, Boolean(serviceControl(value, label)))),
    ];
  }

  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const prerequisite = this.manualPrerequisite();
    if (!prerequisite.ok) return [prerequisite];
    const originResolved = await selectLocation("origin", packet.origin);
    if (!originResolved) {
      throw new Error(`ODFL could not confirm the origin ZIP ${packet.origin.postal_code}. Its autocomplete did not return a usable suggestion or finish City, State, and Country. Reload the ODFL quote page or select the origin manually, then click Build2Quote Retry. No pallet or accessorial fields were filled.`);
    }
    const destinationResolved = await selectLocation("destination", packet.destination);
    if (!destinationResolved) {
      throw new Error(`ODFL could not confirm the destination ZIP ${packet.destination.postal_code}. Its autocomplete did not return a usable suggestion or finish City, State, and Country. Reload the ODFL quote page or select the destination manually, then click Build2Quote Retry. No pallet or accessorial fields were filled.`);
    }
    const results: FieldResult[] = [
      prerequisite,
      fieldResult("outbound shipment", setChecked(outboundControl(), true)),
      fieldResult("origin location", originResolved,
        originResolved ? "" : "Select the origin ZIP suggestion and confirm its City, State, and Country in ODFL."),
      fieldResult("destination location", destinationResolved,
        destinationResolved ? "" : "Select the destination ZIP suggestion and confirm its City, State, and Country in ODFL."),
    ];
    if (results.some((result) => !result.ok)) return results;
    const commodityRowsReady = await ensureRows(packet.pallets.length, rowIndexes, ".js-addwtclass-fields");
    const dimensionRowsReady = await ensureRows(packet.pallets.length, dimensionIndexes, ".js-add-dimensions-fields");
    results.push(fieldResult("commodity row count", commodityRowsReady,
      commodityRowsReady ? "" : "Adjust ODFL commodity rows so there is exactly one row per pallet."));
    results.push(fieldResult("dimension row count", dimensionRowsReady,
      dimensionRowsReady ? "" : "Adjust ODFL dimension rows so there is exactly one row per pallet."));
    if (!commodityRowsReady || !dimensionRowsReady) return results;
    for (const [offset, pallet] of packet.pallets.entries()) {
      results.push(...await fillCommodityRow(rowIndexes()[offset], pallet, packet.nmfc));
      results.push(...fillDimensionRow(dimensionIndexes()[offset], pallet));
    }
    for (const [field, value, label] of services) {
      results.push(fieldResult(field, setChecked(serviceControl(value, label), Boolean(packet.accessorials[field]))));
    }
    return results;
  }

  verify(packet: QuotePacket): FieldResult[] {
    const rows = rowIndexes(); const dimensions = dimensionIndexes();
    const results: FieldResult[] = [
      this.manualPrerequisite(),
      fieldResult("outbound shipment verification", outboundControl()?.checked === true),
      fieldResult("origin location verification", locationResolved("origin", packet.origin)),
      fieldResult("destination location verification", locationResolved("destination", packet.destination)),
      fieldResult("commodity row count verification", rows.length === packet.pallets.length),
      fieldResult("dimension row count verification", dimensions.length === packet.pallets.length),
    ];
    packet.pallets.forEach((pallet, offset) => {
      const commodityIndex = rows[offset]; const dimensionIndex = dimensions[offset];
      if (commodityIndex === undefined) results.push(fieldResult(`pallet ${offset + 1} commodity row`, false, "ODFL commodity row is missing"));
      else results.push(...verifyCommodityRow(commodityIndex, pallet, packet.nmfc));
      if (dimensionIndex === undefined) results.push(fieldResult(`pallet ${offset + 1} dimension row`, false, "ODFL dimension row is missing"));
      else results.push(...verifyDimensionRow(dimensionIndex, pallet));
    });
    for (const [field, value, label] of services) {
      const control = serviceControl(value, label);
      results.push(fieldResult(`${field} verification`, Boolean(control) && control?.checked === Boolean(packet.accessorials[field])));
    }
    return results;
  }
}
