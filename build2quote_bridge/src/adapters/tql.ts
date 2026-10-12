import { clean, fieldResult, setChecked, setValue, visible } from "../dom";
import type { AddressInfo, FieldResult, PalletRow, QuotePacket } from "../types";
import { BaseAdapter } from "./base";
import { looksResolved, matchesPostalAddress, selectExactPostalCode } from "../autocomplete";
import { sleep, waitFor, waitForQuiet, waitForStable } from "../timing";

/** TRAX re-renders its Kendo editors after an edit; wait for that, at most one fixed pause's length. */
const KENDO_QUIET_MS = 50;
const KENDO_MAX_WAIT_MS = 150;
/** Verified carrier model must hold this long, then the quote form must stop re-rendering. */
const VERIFIED_STABLE_MS = 1000;
const VERIFIED_QUIET_MS = 600;
const VERIFIED_MAX_STABLE_MS = 3000;
const VERIFIED_FAILURE_GRACE_MS = 400;
const VERIFIED_TIMEOUT_MS = 6000;
/** Upper bound for verification plus at most two numeric restores (was 22 s). */
const SETTLE_DEADLINE_MS = 12000;

function waitUntil(predicate: () => boolean, timeoutMs = 6000): Promise<boolean> {
  return waitFor(predicate, { timeoutMs, pollMs: 40 });
}

function quoteFormRoot(): Node | null {
  return document.querySelector("#pickup-details")?.closest("form") || document.querySelector("#add-item") || document.body;
}

function kendoSettled(): Promise<void> {
  return waitForQuiet(quoteFormRoot(), KENDO_QUIET_MS, KENDO_MAX_WAIT_MS);
}

function valueMatches(control: HTMLInputElement | null, expected: string | number): boolean {
  if (!control || !control.isConnected || !visible(control)) return false;
  const expectedText = clean(expected);
  const actualText = clean(control.value);
  const expectedNumber = Number(expectedText);
  const actualNumber = Number(actualText);
  if (Number.isFinite(expectedNumber) && Number.isFinite(actualNumber)) {
    return Math.abs(expectedNumber - actualNumber) < 0.01;
  }
  return actualText.toLowerCase() === expectedText.toLowerCase();
}

async function setAndConfirm(control: HTMLInputElement | null, value: string | number): Promise<boolean> {
  if (!control || !setValue(control, value)) return false;
  await sleep(15);
  return waitUntil(() => valueMatches(control, value), 600);
}

async function setKendoNumericBatch(
  values: Array<[() => HTMLInputElement | null, string | number]>,
): Promise<boolean[]> {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  if (!setter) return values.map(() => false);

  const results: boolean[] = [];
  // Resolve after every Kendo render: TRAX can replace an input when another
  // numeric field commits, leaving previously collected nodes detached.
  for (const [getControl, value] of values) {
    let saved = false;
    for (let attempt = 0; attempt < 2 && !saved; attempt += 1) {
      const control = getControl();
      if (!control || !control.isConnected || !visible(control)) break;
      control.focus();
      const next = String(value);
      setter.call(control, next);
      control.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: next }));
      await kendoSettled();
      const current = getControl();
      if (!current || !current.isConnected || !visible(current)) break;
      if (current !== control && !valueMatches(current, value)) {
        current.focus();
        setter.call(current, next);
        current.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: next }));
        await kendoSettled();
      }
      const beforeBlur = getControl();
      if (!beforeBlur || !beforeBlur.isConnected || !visible(beforeBlur)) break;
      beforeBlur.dispatchEvent(new Event("change", { bubbles: true }));
      beforeBlur.blur();
      await kendoSettled();
      saved = valueMatches(getControl(), value);
    }
    results.push(saved);
  }
  return results;
}

function handlingRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("#add-item .commodity-row")).filter(visible);
}

function rowInput(row: HTMLElement, name: string): HTMLInputElement | null {
  return row.querySelector<HTMLInputElement>(`input[name='${name}']`);
}

function choiceText(row: HTMLElement, kind: "unit" | "class"): string {
  const combo = row.querySelector<HTMLElement>(`[id^='input-${kind}-'][role='combobox']`);
  if (clean(combo?.textContent)) return clean(combo?.textContent);
  const select = row.querySelector<HTMLSelectElement>(`select[aria-label='${kind === "unit" ? "Unit" : "Class"}']`);
  return clean(select?.selectedOptions[0]?.textContent);
}

async function chooseKendo(row: HTMLElement, kind: "unit" | "class", value: string): Promise<boolean> {
  if (choiceText(row, kind).toLowerCase() === value.toLowerCase()) return true;
  const combo = row.querySelector<HTMLElement>(`[id^='input-${kind}-'][role='combobox']`);
  if (!combo) return false;
  combo.click();
  const listboxId = combo.getAttribute("aria-owns") || combo.getAttribute("aria-controls");
  let option: HTMLElement | undefined;
  const found = await waitUntil(() => {
    const listbox = listboxId ? document.getElementById(listboxId) : document;
    option = Array.from(listbox?.querySelectorAll<HTMLElement>("[role='option'], li") || []).find((candidate) => (
      visible(candidate) && clean(candidate.textContent).toLowerCase() === value.toLowerCase()
    ));
    return Boolean(option);
  });
  if (!found || !option) return false;
  option.click();
  return waitUntil(() => choiceText(row, kind).toLowerCase() === value.toLowerCase(), 3500);
}

function postalInput(section: "pickup" | "delivery"): HTMLInputElement | null {
  return document.querySelector<HTMLInputElement>(
    `#${section}-details input[placeholder='Postal Code, City, or Saved Address'][role='combobox']`,
  );
}

function resolvedPostal(section: "pickup" | "delivery", address: AddressInfo): boolean {
  const input = postalInput(section);
  if (looksResolved(input, address.postal_code)) return true;
  if (!input || !/^\d{5}(?:-\d{4})?\s+-\s+.+,.+/.test(clean(input.value))) return false;
  const value = document.createElement("span");
  value.textContent = input.value;
  // A Favorites popup may reopen over a committed carrier location. Its open
  // state does not undo the independently generated full city/state value.
  return input.getAttribute("aria-invalid") !== "true" && matchesPostalAddress(value, address);
}

function matchesTqlPostalOption(candidate: HTMLElement, address: AddressInfo): boolean {
  if (!matchesPostalAddress(candidate, address)) return false;
  const kindLabels = Array.from(candidate.querySelectorAll<HTMLElement>(".k-chip-content"))
    .map(element => clean(element.textContent).toLowerCase())
    .filter(Boolean);
  // Kendo's current TRAX results can contain saved addresses and favorites
  // alongside a postal-code result for the same ZIP. Prefer the typed result
  // when its chip identifies it; retain text matching for older markup.
  return kindLabels.length === 0 || kindLabels.includes("postal code");
}

async function dismissCommittedPostalMenu(section: "pickup" | "delivery", address: AddressInfo): Promise<void> {
  const input = postalInput(section);
  if (!input || input.getAttribute("aria-expanded") !== "true"
    || !/^\d{5}(?:-\d{4})?\s+-\s+.+,.+/.test(clean(input.value))) return;
  const value = document.createElement("span");
  value.textContent = input.value;
  if (!matchesPostalAddress(value, address)) return;
  // TRAX can reopen Favorites after replacing a committed control. Escape
  // dismisses that menu without choosing an ambiguous saved address.
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", code: "Escape", bubbles: true }));
  input.blur();
  await sleep(40);
}

async function selectPostal(section: "pickup" | "delivery", address: AddressInfo): Promise<boolean> {
  await dismissCommittedPostalMenu(section, address);
  return selectExactPostalCode({
    getControl: () => postalInput(section), zip: address.postal_code,
    matchesOption: candidate => matchesTqlPostalOption(candidate, address),
    optionSelector: "[role='option']", timeoutMs: 45000, clearFirst: false,
    activeDescendantIsFresh: true,
    verifyResolved: () => resolvedPostal(section, address),
  });
}

function locationRadio(name: "Residential" | "Commercial"): HTMLInputElement | null {
  const label = document.querySelector<HTMLLabelElement>(`#delivery-details label[aria-label='${name}']`);
  return label?.htmlFor ? document.getElementById(label.htmlFor) as HTMLInputElement | null : null;
}

function deliveryLiftgate(): HTMLInputElement | null {
  return document.querySelector<HTMLInputElement>("#delivery-details input[type='checkbox'][aria-label='Lift Gate']");
}

async function ensureHandlingRows(count: number): Promise<FieldResult> {
  let rows = handlingRows();
  if (rows.length > count) {
    return fieldResult("handling unit count", false, `TQL already contains ${rows.length} rows; expected ${count}. Remove extras manually.`);
  }
  while (rows.length < count) {
    const before = rows.length;
    const add = Array.from(document.querySelectorAll<HTMLButtonElement>("#add-item button")).find((button) => (
      visible(button) && clean(button.textContent).toLowerCase() === "handling unit"
    ));
    if (!add) return fieldResult("handling unit count", false, "TQL's Add Handling Unit button was not found");
    add.click();
    if (!await waitUntil(() => handlingRows().length > before, 5000)) {
      return fieldResult("handling unit count", false, "TQL did not create the requested handling-unit row");
    }
    rows = handlingRows();
  }
  return fieldResult("handling unit count", rows.length === count, `${rows.length} handling unit(s) ready`);
}

function liveRow(index: number): HTMLElement | null {
  return handlingRows()[index] || null;
}

function tqlNumericValues(pallet: PalletRow): Array<[string, string, number]> {
  return [
    ["quantity", "quantity", 1],
    ["weight", "weight", Math.round(pallet.weight)],
    ["length", "length", Math.ceil(pallet.length)],
    ["width", "width", Math.ceil(pallet.width)],
    ["height", "height", Math.ceil(pallet.height)],
  ];
}

function displayedNumber(element: HTMLElement | null | undefined): number | null {
  if (!element || !element.isConnected || !visible(element)) return null;
  const value = Number.parseFloat(clean(element.textContent).replaceAll(",", ""));
  return Number.isFinite(value) ? value : null;
}

function tqlModelNumericMatches(
  packet: QuotePacket,
  index: number,
  row: HTMLElement,
  field: string,
): boolean | null {
  const summarySelectors = [
    "[data-summary-field='handling-unit'] .item-summary-value",
    "[data-summary-field='total-unit-weight'] .item-summary-value",
    "[data-summary-field='calculated-linear-feet'] .item-summary-value",
  ];
  const densityDisplays = Array.from(document.querySelectorAll<HTMLElement>(
    "[data-testid^='input-field-group-density-'] .display-field",
  )).filter(visible);
  if (!summarySelectors.every(selector => document.querySelector(selector))
    || densityDisplays.length < packet.pallets.length) return null;

  const handlingUnits = displayedNumber(document.querySelector<HTMLElement>(summarySelectors[0]));
  const totalWeight = displayedNumber(document.querySelector<HTMLElement>(summarySelectors[1]));
  const linearFeet = displayedNumber(document.querySelector<HTMLElement>(summarySelectors[2]));
  const densityDisplay = displayedNumber(
    row.querySelector<HTMLElement>("[data-testid^='input-field-group-density-'] .display-field")
      || densityDisplays[index],
  );
  const expectedUnits = packet.pallets.length;
  const expectedWeight = packet.pallets.reduce((total, pallet) => total + Math.round(pallet.weight), 0);
  const expectedLinearFeet = packet.pallets.reduce((total, pallet) => (
    total + Math.ceil(pallet.length) * Math.ceil(pallet.width) / 480
  ), 0);
  const pallet = packet.pallets[index];
  const expectedDensity = Math.round(pallet.weight) * 1728 / (
    Math.ceil(pallet.length) * Math.ceil(pallet.width) * Math.ceil(pallet.height)
  );
  const densityMatches = densityDisplay !== null && Math.abs(densityDisplay - expectedDensity) < 0.05;
  switch (field) {
    case "quantity":
      return handlingUnits !== null && Math.abs(handlingUnits - expectedUnits) < 0.01;
    case "weight":
      return totalWeight !== null && Math.abs(totalWeight - expectedWeight) < 0.01 && densityMatches;
    case "length":
    case "width":
      return linearFeet !== null && Math.abs(linearFeet - expectedLinearFeet) < 0.01 && densityMatches;
    case "height":
      return densityMatches;
    default:
      return false;
  }
}

function tqlModelSummaryMatches(packet: QuotePacket, index: number, row: HTMLElement): boolean | null {
  const checks = ["quantity", "weight", "length", "width", "height"].map(field => (
    tqlModelNumericMatches(packet, index, row, field)
  ));
  if (checks.some(check => check === null)) return null;
  return checks.every(check => check === true);
}

async function fillRow(pallet: PalletRow, index: number): Promise<FieldResult[]> {
  const number = index + 1;
  const results: FieldResult[] = [];
  const values: Array<[string, string, string | number]> = [
    ["description", "description", pallet.commodity_description],
    ["NMFC", "nmfc", pallet.nmfc],
  ];
  for (const [label, name, value] of values) {
    const row = liveRow(index);
    results.push(fieldResult(`pallet ${number} ${label}`, await setAndConfirm(row && rowInput(row, name), value)));
  }
  let row = liveRow(index);
  results.push(fieldResult(`pallet ${number} unit`, row ? await chooseKendo(row, "unit", "Pallets") : false));
  row = liveRow(index);
  results.push(fieldResult(`pallet ${number} class`, row ? await chooseKendo(row, "class", pallet.freight_class) : false));
  row = liveRow(index);
  results.push(fieldResult(
    `pallet ${number} stackable`,
    row ? setChecked(rowInput(row, "stackable"), false) : false,
    "Set to Do Not Stack",
  ));
  row = liveRow(index);
  const numericValues = tqlNumericValues(pallet);
  const numericResults = await setKendoNumericBatch(numericValues.map(([, name, value]) => [
    () => {
      const currentRow = liveRow(index);
      return currentRow && rowInput(currentRow, name);
    }, value,
  ]));
  results.push(fieldResult(`pallet ${number} numeric values supplied`, numericResults.every(Boolean)));
  return results;
}

async function restoreNumericValues(packet: QuotePacket): Promise<boolean> {
  let editorValuesRestored = true;
  for (let index = 0; index < packet.pallets.length; index += 1) {
    const results = await setKendoNumericBatch(tqlNumericValues(packet.pallets[index]).map(([, name, value]) => [
      () => {
        const row = liveRow(index);
        return row && rowInput(row, name);
      }, value,
    ]));
    if (!results.every(Boolean)) editorValuesRestored = false;
  }
  const currentRows = handlingRows();
  let modelAvailable = currentRows.length === packet.pallets.length;
  let modelMatches = modelAvailable;
  packet.pallets.forEach((_, index) => {
    const row = currentRows[index];
    const checks = row ? [tqlModelSummaryMatches(packet, index, row)] : null;
    if (!checks || checks.some(check => check === null)) modelAvailable = false;
    if (!checks || checks.some(check => check !== true)) modelMatches = false;
  });
  return modelAvailable ? modelMatches : editorValuesRestored;
}

export class TqlAdapter extends BaseAdapter {
  broker = "tql" as const;

  formRoot() { return document.querySelector("#pickup-details")?.closest("form") || document.querySelector("#pickup-details")?.parentElement || null; }

  detect() {
    const host = location.hostname.toLowerCase();
    const path = new URL(location.href).pathname;
    return ["trax.tqlapp.com", "trax.tql.com"].includes(host) && path.startsWith("/quote-details");
  }

  discoverFields(): FieldResult[] {
    return [
      fieldResult("pickup postal code", Boolean(postalInput("pickup"))),
      fieldResult("delivery postal code", Boolean(postalInput("delivery"))),
      fieldResult("handling unit form", handlingRows().length > 0),
      fieldResult("pallet unit selector", Boolean(handlingRows()[0]?.querySelector("[id^='input-unit-'][role='combobox']"))),
      fieldResult("freight class selector", Boolean(handlingRows()[0]?.querySelector("[id^='input-class-'][role='combobox']"))),
    ];
  }

  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const results: FieldResult[] = [];
    results.push(fieldResult("pickup postal code", await selectPostal("pickup", packet.origin)));
    results.push(fieldResult("delivery postal code", await selectPostal("delivery", packet.destination)));

    await dismissCommittedPostalMenu("pickup", packet.origin);
    await dismissCommittedPostalMenu("delivery", packet.destination);

    const locationsStable = await waitForStable(() => (
      resolvedPostal("pickup", packet.origin)
      && resolvedPostal("delivery", packet.destination)
      && handlingRows().length > 0
    ), { stableMs: 500, timeoutMs: 10000, pollMs: 40 });
    results[0] = fieldResult("pickup postal code", resolvedPostal("pickup", packet.origin));
    results[1] = fieldResult("delivery postal code", resolvedPostal("delivery", packet.destination));
    results.push(fieldResult(
      "quote form stabilization",
      locationsStable,
      locationsStable ? "Destination and handling-unit form are stable" : "TRAX rebuilt or cleared the quote form",
    ));
    if (!locationsStable || results.some(result => !result.ok)) return results;

    if (packet.accessorials.limited_access) {
      // Limited access is a location type of its own; leave the selector for the manual choice.
      results.push(fieldResult("delivery location type", true, "Left for the manual Limited access selection"));
    } else {
      const residential = locationRadio(packet.accessorials.residential ? "Residential" : "Commercial");
      results.push(fieldResult(
        "delivery location type",
        setChecked(residential, true),
        packet.accessorials.residential ? "Residential Delivery selected" : "Commercial Delivery selected",
      ));
    }
    results.push(fieldResult(
      "liftgate delivery",
      setChecked(deliveryLiftgate(), Boolean(packet.accessorials.liftgate)),
      packet.accessorials.liftgate ? "Lift Gate selected" : "Lift Gate cleared",
    ));

    for (const [key, label] of [["appointment_delivery", "Appointment Delivery"], ["delivery_call_ahead", "Call Ahead"]]) {
      const control = document.querySelector<HTMLInputElement>(`#delivery-details input[type='checkbox'][aria-label='${label}']`);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? setChecked(control, expected) : !expected));
    }

    const rowResult = await ensureHandlingRows(packet.pallets.length);
    results.push(rowResult);
    if (!rowResult.ok) return results;
    for (let index = 0; index < packet.pallets.length; index += 1) {
      results.push(...await fillRow(packet.pallets[index], index));
    }

    const reference = packet.customer_references.join(" + ");
    if (reference) {
      results.push(fieldResult(
        "customer references",
        await setAndConfirm(document.querySelector<HTMLInputElement>("#customerReference"), reference),
      ));
    }
    let persisted = false;
    const settleDeadline = Date.now() + SETTLE_DEADLINE_MS;
    let repairs = 0;
    while (Date.now() < settleDeadline) {
      const timeoutMs = Math.min(VERIFIED_TIMEOUT_MS, settleDeadline - Date.now());
      // The carrier model must verify for 1 s and TRAX must then stop re-rendering the quote form
      // for 0.6 s (late Kendo control replacements restart the quiet period). This replaces a fixed
      // 5.2 s window; the durability monitor keeps verifying after the fill.
      if (await waitForStable(() => this.verify(packet).every(field => (
        field.ok || field.field.endsWith("dimensions manual review required")
      )), {
        stableMs: VERIFIED_STABLE_MS, quietMs: VERIFIED_QUIET_MS, quietRoot: quoteFormRoot,
        maxStableMs: VERIFIED_MAX_STABLE_MS, failureGraceMs: VERIFIED_FAILURE_GRACE_MS, timeoutMs, pollMs: 40,
      })) {
        persisted = true;
        break;
      }
      const failedFields = this.verify(packet).filter(field => !field.ok);
      const numericOnly = failedFields.length > 0 && failedFields.every(field => (
        /^pallet \d+ (quantity|weight|length|width|height) verification$/.test(field.field)
        || field.field.endsWith("dimension summary verification")
        || field.field.endsWith("dimensions manual review required")
      ));
      if (failedFields.some(field => field.field.endsWith("dimensions manual review required"))) {
        // This is an operator review boundary, not a carrier-model mismatch.
        // Do not rewrite controls that TQL deliberately renders blank.
        if (failedFields.every(field => field.field.endsWith("dimensions manual review required"))) {
          persisted = true;
          break;
        }
      }
      if (!numericOnly || repairs >= 2 || !await restoreNumericValues(packet)) break;
      repairs += 1;
    }
    results.push(fieldResult(
      "numeric values persisted",
      persisted,
      persisted ? "Shipment details retained in TRAX's settled form model" : "TRAX shipment summary did not match the requested pallet details",
    ));
    if (persisted) {
      const verifiedNumericFields = this.verify(packet);
      results.forEach((result, resultIndex) => {
        const match = result.field.match(/^pallet (\d+) numeric values supplied$/);
        if (!match) return;
        const row = liveRow(Number(match[1]) - 1);
        const summaryMatches = row
          ? tqlModelSummaryMatches(packet, Number(match[1]) - 1, row)
          : false;
        results[resultIndex] = fieldResult(
          `pallet ${match[1]} carrier model summary`,
          summaryMatches === true,
          "Quantity, weight, linear feet, and density match TQL's settled shipment summary",
        );
      });
      results.push(...verifiedNumericFields.filter(field => field.field.endsWith("dimensions manual review required")));
    }
    return results;
  }

  verify(packet: QuotePacket): FieldResult[] {
    const results: FieldResult[] = [
      fieldResult("pickup postal code verification", resolvedPostal("pickup", packet.origin)),
      fieldResult("delivery postal code verification", resolvedPostal("delivery", packet.destination)),
    ];
    const expectedLocation = locationRadio(packet.accessorials.residential ? "Residential" : "Commercial");
    results.push(fieldResult("delivery location verification",
      packet.accessorials.limited_access ? true : Boolean(expectedLocation?.checked)));
    results.push(fieldResult("liftgate verification", deliveryLiftgate()?.checked === Boolean(packet.accessorials.liftgate)));

    for (const [key, label] of [["appointment_delivery", "Appointment Delivery"], ["delivery_call_ahead", "Call Ahead"]]) {
      const control = document.querySelector<HTMLInputElement>(`#delivery-details input[type='checkbox'][aria-label='${label}']`);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? control.checked === expected : !expected));
    }
    const rows = handlingRows();
    results.push(fieldResult("handling unit count verification", rows.length === packet.pallets.length));
    packet.pallets.forEach((pallet, index) => {
      const row = rows[index];
      const number = index + 1;
      if (!row) {
        results.push(fieldResult(`pallet ${number} verification`, false, "Handling-unit row is missing"));
        return;
      }
      const summaryAvailable = tqlModelSummaryMatches(packet, index, row) !== null;
      if (summaryAvailable) {
        results.push(fieldResult(
          `pallet ${number} quantity verification`,
          tqlModelNumericMatches(packet, index, row, "quantity") === true,
        ));
        results.push(fieldResult(
          `pallet ${number} weight verification`,
          tqlModelNumericMatches(packet, index, row, "weight") === true,
        ));
        const dimensions = ["length", "width", "height"] as const;
        const dimensionReadback = dimensions.map(name => valueMatches(rowInput(row, name), tqlNumericValues(pallet).find(value => value[1] === name)![2]));
        if (dimensionReadback.every(Boolean)) {
          dimensions.forEach(name => results.push(fieldResult(`pallet ${number} ${name} verification`, true)));
        } else {
          results.push(fieldResult(
            `pallet ${number} dimension summary verification`,
            ["length", "width", "height"].every(name => tqlModelNumericMatches(packet, index, row, name) === true),
            "Carrier lineal-foot and density summaries match the requested dimensions",
          ));
          results.push({
            ...fieldResult(
              `pallet ${number} dimensions manual review required`,
              false,
              "TQL renders its settled length, width, and height editors blank; lineal feet and density cannot prove their independent values or exclude swapped dimensions.",
            ),
            manual: "check",
          });
        }
      } else {
        tqlNumericValues(pallet).forEach(([label, name, value]) => {
          results.push(fieldResult(`pallet ${number} ${label} verification`, valueMatches(rowInput(row, name), value)));
        });
      }
      results.push(fieldResult(`pallet ${number} description verification`, valueMatches(
        rowInput(row, "description"), pallet.commodity_description,
      )));
      results.push(fieldResult(`pallet ${number} NMFC verification`, valueMatches(rowInput(row, "nmfc"), pallet.nmfc)));
      results.push(fieldResult(`pallet ${number} unit verification`, choiceText(row, "unit").toLowerCase() === "pallets"));
      results.push(fieldResult(`pallet ${number} class verification`, choiceText(row, "class") === pallet.freight_class));
      results.push(fieldResult(`pallet ${number} stackable verification`, rowInput(row, "stackable")?.checked === false));
    });
    const reference = packet.customer_references.join(" + ");
    if (reference) results.push(fieldResult("customer references verification", valueMatches(
      document.querySelector<HTMLInputElement>("#customerReference"), reference,
    )));
    return results;
  }

}
