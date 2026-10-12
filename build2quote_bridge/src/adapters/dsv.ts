import { clean, fieldResult, setValue, visible } from "../dom";
import type { AddressInfo, FieldResult, PalletRow, QuotePacket } from "../types";
import { BaseAdapter } from "./base";
import { containsExactZip, matchesPostalAddress, selectExactPostalCode } from "../autocomplete";
import { sleep, waitFor } from "../timing";

function waitUntil(predicate: () => boolean, timeoutMs = 5000): Promise<boolean> {
  return waitFor(predicate, { timeoutMs, pollMs: 75 });
}

function postalInputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input[role='searchbox'][aria-autocomplete='list']"))
    .filter(visible);
}

function sectionPostal(kind: "pickup" | "delivery"): HTMLInputElement | null {
  const positive = kind === "pickup" ? /pickup|origin|sender/i : /delivery|destination|recipient/i;
  const negative = kind === "pickup" ? /delivery|destination|recipient/i : /pickup|origin|sender/i;
  const matches = postalInputs().filter(input => {
    const label = input.labels ? Array.from(input.labels).map(value => value.textContent).join(" ") : "";
    const direct = `${input.id} ${input.name} ${input.getAttribute("aria-label") || ""} ${label}`;
    const context = positive.test(direct) ? direct : `${direct} ${input.closest("section, fieldset, [class*='address'], [class*='location'], .row")?.textContent || ""}`;
    return positive.test(context) && !negative.test(context);
  });
  return matches.length === 1 ? matches[0] : null;
}

function dsvPostalPanels(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>(
    "body > .p-autocomplete-panel, body > [role='listbox']",
  )).filter(visible);
}

function dsvPostalOptions(control: HTMLInputElement): HTMLElement[] {
  if (document.activeElement !== control) return [];
  const localOptions = Array.from(control.closest("p-autocomplete")?.querySelectorAll<HTMLElement>(
    ".p-autocomplete-items [role='option'], .p-autocomplete-items .p-autocomplete-item, [role='listbox'] [role='option']",
  ) || []);
  const portalOptions = dsvPostalPanels().flatMap(panel => Array.from(panel.querySelectorAll<HTMLElement>(
    "[role='option'], .p-autocomplete-item",
  )));
  return [...new Set([...localOptions, ...portalOptions])].filter(visible);
}

function dsvPostalMarker(kind: "pickup" | "delivery"): HTMLElement | null {
  return sectionPostal(kind)?.closest(".row")?.querySelector<HTMLElement>(".pi-compass")?.parentElement || null;
}

function dsvPostalResolved(kind: "pickup" | "delivery", zip: string): boolean {
  const input = sectionPostal(kind);
  const marker = dsvPostalMarker(kind);
  const markerText = clean(marker?.textContent);
  // DSV's committed autocomplete chip is the positive selection signal. Its
  // input does not expose Angular ng-valid/ng-invalid classes in the live form.
  const valid = Boolean(input && !input.classList.contains("ng-invalid")
    && input.getAttribute("aria-invalid") !== "true");
  const localPanel = input?.closest("p-autocomplete")?.querySelector<HTMLElement>(
    ".p-autocomplete-items, [role='listbox']",
  );
  const panelOpen = dsvPostalPanels().length > 0 || Boolean(localPanel && visible(localPanel));
  return Boolean(input && marker && /^postal code\s*:/i.test(markerText)
    && containsExactZip(markerText, zip) && containsExactZip(input.value, zip) && valid && !panelOpen);
}

async function selectPostal(kind: "pickup" | "delivery", address: AddressInfo): Promise<boolean> {
  return selectExactPostalCode({ getControl: () => sectionPostal(kind), zip: address.postal_code,
    timeoutMs: 45000,
    getOptions: dsvPostalOptions,
    matchesOption: candidate => matchesPostalAddress(candidate, address),
    verifyResolved: () => dsvPostalResolved(kind, address.postal_code),
    requireCarrierVerification: true,
  });
}

function goodsRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input[formcontrolname='length']"))
    .filter(visible)
    .map((length) => {
      let root: HTMLElement | null = length.parentElement;
      while (root?.parentElement) {
        if (
          root.querySelector("input[formcontrolname='description']")
          && root.querySelector("p-inputnumber[formcontrolname='weight'] input")
          && root.querySelector("p-dropdown[formcontrolname='freightClass']")
        ) return root;
        root = root.parentElement;
      }
      return length.parentElement || document.body;
    });
}

function rowInput(row: HTMLElement, formControlName: string): HTMLInputElement | null {
  if (formControlName === "weight") {
    return row.querySelector<HTMLInputElement>("p-inputnumber[formcontrolname='weight'] input");
  }
  return row.querySelector<HTMLInputElement>(`input[formcontrolname='${formControlName}']`);
}

function choiceText(row: HTMLElement, formControlName: string): string {
  return clean(row.querySelector<HTMLElement>(
    `p-dropdown[formcontrolname='${formControlName}'] .p-dropdown-label`,
  )?.textContent);
}

async function choosePrime(row: HTMLElement, formControlName: string, value: string): Promise<boolean> {
  if (choiceText(row, formControlName).toLowerCase() === value.toLowerCase()) return true;
  const dropdown = row.querySelector<HTMLElement>(`p-dropdown[formcontrolname='${formControlName}'] .p-dropdown`);
  if (!dropdown) return false;
  dropdown.click();
  let option: HTMLElement | undefined;
  const found = await waitUntil(() => {
    option = Array.from(document.querySelectorAll<HTMLElement>("[role='option'], .p-dropdown-item"))
      .find((candidate) => visible(candidate) && clean(candidate.textContent).toLowerCase() === value.toLowerCase());
    return Boolean(option);
  });
  if (!found || !option) return false;
  option.click();
  return waitUntil(() => choiceText(row, formControlName).toLowerCase() === value.toLowerCase(), 3000);
}

function numericValue(control: HTMLInputElement | null): number {
  return Number(clean(control?.value).replaceAll(",", ""));
}

function numericMatches(control: HTMLInputElement | null, expected: number): boolean {
  return Number.isFinite(numericValue(control)) && Math.abs(numericValue(control) - expected) < 0.01;
}

function dsvValues(pallet: PalletRow): Array<[string, string, number]> {
  return [
    ["quantity", "totalPackages", 1],
    ["weight", "weight", Math.round(pallet.weight)],
    ["length", "length", Math.ceil(pallet.length)],
    ["width", "width", Math.ceil(pallet.width)],
    ["height", "height", Math.ceil(pallet.height)],
  ];
}

async function addGoodsRow(): Promise<boolean> {
  const before = goodsRows().length;
  const add = Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => visible(button) && clean(button.textContent).toLowerCase() === "add item");
  if (!add) return false;
  add.click();
  return waitUntil(() => goodsRows().length > before, 4000);
}

function accessorial(value: string): HTMLInputElement | null {
  return document.querySelector<HTMLInputElement>(`input[type='checkbox'][value='${value}']`);
}

async function setPrimeChecked(control: HTMLInputElement | null, checked: boolean): Promise<boolean> {
  if (!control) return false;
  if (control.checked === checked) return true;
  const box = control.closest("p-checkbox")?.querySelector<HTMLElement>(".p-checkbox-box");
  (box || control).click();
  return waitUntil(() => control.checked === checked, 1500);
}

async function fillGoodsRow(pallet: PalletRow, index: number): Promise<FieldResult[]> {
  const number = index + 1;
  const row = goodsRows()[index];
  if (!row) return [fieldResult(`pallet ${number}`, false, "DSV goods row is missing")];
  const results: FieldResult[] = [];
  results.push(fieldResult(`pallet ${number} description`, setValue(rowInput(row, "description"), pallet.commodity_description)));
  results.push(fieldResult(`pallet ${number} NMFC`, setValue(rowInput(row, "nmfcCode"), pallet.nmfc)));
  results.push(fieldResult(`pallet ${number} packaging`, await choosePrime(row, "packaging", "Pallets")));
  results.push(fieldResult(`pallet ${number} weight unit`, await choosePrime(row, "weightUnits", "lb")));
  results.push(fieldResult(`pallet ${number} dimension unit`, await choosePrime(row, "dimensionUnits", "in")));
  results.push(fieldResult(`pallet ${number} freight class`, await choosePrime(row, "freightClass", pallet.freight_class)));
  results.push(fieldResult(`pallet ${number} stackable`, await setPrimeChecked(
    row.querySelector<HTMLInputElement>("p-checkbox[formcontrolname='stackable'] input[type='checkbox']"), false,
  ), "Set to Do Not Stack"));
  for (const [label, formControlName, value] of dsvValues(pallet)) {
    const control = rowInput(goodsRows()[index] || row, formControlName);
    const changed = setValue(control, value);
    // PrimeNG number inputs format on the next tick; keep the short pause (throttle-resistant now).
    await sleep(35);
    results.push(fieldResult(`pallet ${number} ${label}`, changed && numericMatches(control, value)));
  }
  return results;
}

export class DsvAdapter extends BaseAdapter {
  broker = "dsv" as const;
  formRoot() { return document.querySelector("form") || document.querySelector("app-quote") || null; }
  detect() {
    return location.hostname === "mytms.us.dsv.com" && new URL(location.href).pathname.includes("/dashboard/quote");
  }
  discoverFields(): FieldResult[] {
    return [
      fieldResult("pickup postal code", Boolean(sectionPostal("pickup"))),
      fieldResult("delivery postal code", Boolean(sectionPostal("delivery"))),
      fieldResult("goods row", goodsRows().length > 0),
      fieldResult("freight class selector", Boolean(goodsRows()[0]?.querySelector("p-dropdown[formcontrolname='freightClass']"))),
    ];
  }
  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const results: FieldResult[] = [
      fieldResult("pickup postal code", await selectPostal("pickup", packet.origin)),
      fieldResult("delivery postal code", await selectPostal("delivery", packet.destination)),
    ];
    if (results.some(result => !result.ok)) return results;
    results.push(fieldResult(
      "residential delivery",
      await setPrimeChecked(accessorial("DELIVERY_RESIDENTIAL"), Boolean(packet.accessorials.residential)),
    ));
    results.push(fieldResult(
      "liftgate delivery",
      await setPrimeChecked(accessorial("DELIVERY_LIFTGATE"), Boolean(packet.accessorials.liftgate)),
    ));
    for (const [key, value] of [["appointment_delivery", "DELIVERY_APT"], ["delivery_call_ahead", "DELIVERY_NOTIFY"]]) {
      const control = accessorial(value);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? await setPrimeChecked(control, expected) : !expected));
    }
    if (goodsRows().length > packet.pallets.length) {
      results.push(fieldResult("goods row count", false, `DSV has ${goodsRows().length} goods rows; expected ${packet.pallets.length}. Remove extras manually.`));
      return results;
    }
    for (let index = 0; index < packet.pallets.length; index += 1) {
      if (!goodsRows()[index]) {
        const added = await addGoodsRow();
        results.push(fieldResult(`pallet ${index + 1} row`, added, added ? "Goods row created" : "DSV did not create the requested goods row"));
        if (!added) return results;
      }
      results.push(...await fillGoodsRow(packet.pallets[index], index));
    }
    // No fixed 75 ms per row or 300 ms at the end: the verification window that follows the fill
    // waits for the values to hold.
    results.push(fieldResult("goods row count", goodsRows().length === packet.pallets.length, `${goodsRows().length} goods row(s) ready`));
    return results;
  }
  verify(packet: QuotePacket): FieldResult[] {
    const results: FieldResult[] = [
      fieldResult("pickup postal code verification", dsvPostalResolved("pickup", packet.origin.postal_code)),
      fieldResult("delivery postal code verification", dsvPostalResolved("delivery", packet.destination.postal_code)),
      fieldResult("residential delivery verification", accessorial("DELIVERY_RESIDENTIAL")?.checked === Boolean(packet.accessorials.residential)),
      fieldResult("liftgate delivery verification", accessorial("DELIVERY_LIFTGATE")?.checked === Boolean(packet.accessorials.liftgate)),
      fieldResult("goods row count verification", goodsRows().length === packet.pallets.length),
    ];
    for (const [key, value] of [["appointment_delivery", "DELIVERY_APT"], ["delivery_call_ahead", "DELIVERY_NOTIFY"]]) {
      const control = accessorial(value);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? control.checked === expected : !expected));
    }
    packet.pallets.forEach((pallet, index) => {
      const row = goodsRows()[index];
      const number = index + 1;
      if (!row) { results.push(fieldResult(`pallet ${number} verification`, false, "DSV goods row is missing")); return; }
      for (const [label, formControlName, value] of dsvValues(pallet)) {
        results.push(fieldResult(`pallet ${number} ${label} verification`, numericMatches(rowInput(row, formControlName), value)));
      }
      results.push(fieldResult(`pallet ${number} description verification`, clean(rowInput(row, "description")?.value) === clean(pallet.commodity_description)));
      results.push(fieldResult(`pallet ${number} NMFC verification`, clean(rowInput(row, "nmfcCode")?.value) === clean(pallet.nmfc)));
      results.push(fieldResult(`pallet ${number} packaging verification`, choiceText(row, "packaging").toLowerCase() === "pallets"));
      results.push(fieldResult(`pallet ${number} weight unit verification`, choiceText(row, "weightUnits").toLowerCase() === "lb"));
      results.push(fieldResult(`pallet ${number} dimension unit verification`, choiceText(row, "dimensionUnits").toLowerCase() === "in"));
      results.push(fieldResult(`pallet ${number} class verification`, choiceText(row, "freightClass") === pallet.freight_class));
      results.push(fieldResult(`pallet ${number} stackable verification`, row.querySelector<HTMLInputElement>(
        "p-checkbox[formcontrolname='stackable'] input[type='checkbox']",
      )?.checked === false));
    });
    return results;
  }
}
