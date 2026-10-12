import { BaseAdapter } from "./base";
import {
  candidateItemRoots,
  checkboxByNames,
  choiceTextIn,
  clean,
  controlIn,
  fieldResult,
  inputByNames,
  setChecked,
  setChoiceIn,
  setValue,
  visible,
} from "../dom";
import { sleep, waitFor, waitForStable } from "../timing";
import type { FieldResult, PalletRow, QuotePacket } from "../types";

function waitUntil(predicate: () => boolean, timeoutMs = 2500): Promise<boolean> {
  return waitFor(predicate, { timeoutMs, pollMs: 40 });
}

function myUnishippersRoots(): HTMLElement[] {
  const lengths = Array.from(document.querySelectorAll<HTMLInputElement>("input[formcontrolname='length']"));
  if (!lengths.length) return candidateItemRoots();
  return lengths.map((length) => {
    let root: HTMLElement = length.parentElement || document.body;
    while (
      root.parentElement
      && (!root.querySelector("[formcontrolname='handlingUnitTypes']") || !root.querySelector("[formcontrolname='weight']"))
    ) root = root.parentElement;
    return root;
  });
}

function clickAddHandlingUnit(): boolean {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((candidate) => (
    visible(candidate) && /handling unit/i.test(clean(candidate.textContent)) && !candidate.disabled
  ));
  button?.click();
  return Boolean(button);
}

function directControl(root: HTMLElement, names: string[]): HTMLInputElement | HTMLSelectElement | null {
  for (const name of names) {
    const host = root.querySelector<HTMLElement>(`[formcontrolname='${name}']`);
    if (host instanceof HTMLInputElement || host instanceof HTMLSelectElement) return host;
    const nested = host?.querySelector<HTMLInputElement | HTMLSelectElement>("input, select");
    if (nested) return nested;
  }
  return null;
}

function rowControl(root: HTMLElement, formNames: string[], labels: string[]) {
  return directControl(root, formNames) || controlIn(root, labels);
}

function valueMatches(control: HTMLInputElement | HTMLSelectElement | null, expected: string | number): boolean {
  if (!control) return false;
  const actual = clean(control.value).replaceAll(",", "");
  const wanted = clean(expected).replaceAll(",", "");
  const actualNumber = Number(actual);
  const wantedNumber = Number(wanted);
  if (Number.isFinite(actualNumber) && Number.isFinite(wantedNumber)) return Math.abs(actualNumber - wantedNumber) < 0.01;
  return actual.toLowerCase() === wanted.toLowerCase();
}

function primeChoiceText(root: HTMLElement, formNames: string[], labels: string[]): string {
  for (const name of formNames) {
    const text = clean(root.querySelector<HTMLElement>(`p-dropdown[formcontrolname='${name}'] .p-dropdown-label`)?.textContent);
    if (text) return text;
  }
  return choiceTextIn(root, labels);
}

async function chooseRow(root: HTMLElement, formNames: string[], labels: string[], value: string): Promise<boolean> {
  const direct = directControl(root, formNames);
  if (direct && setValue(direct, value)) return true;
  for (const name of formNames) {
    const trigger = root.querySelector<HTMLElement>(
      `p-dropdown[formcontrolname='${name}'] .p-dropdown, [formcontrolname='${name}'][role='combobox']`,
    );
    if (!trigger) continue;
    if (primeChoiceText(root, formNames, labels).toLowerCase() === value.toLowerCase()) return true;
    trigger.click();
    let option: HTMLElement | undefined;
    if (!await waitUntil(() => {
      option = Array.from(document.querySelectorAll<HTMLElement>("[role='option'], .p-dropdown-item"))
        .find((candidate) => visible(candidate) && clean(candidate.textContent).toLowerCase() === value.toLowerCase());
      return Boolean(option);
    })) return false;
    option?.click();
    return waitUntil(() => primeChoiceText(root, formNames, labels).toLowerCase() === value.toLowerCase());
  }
  return setChoiceIn(root, labels, value);
}

function myValues(pallet: PalletRow): Array<[string, string[], string[], number]> {
  return [
    ["quantity", ["numberOfUnits", "numUnits", "quantity"], ["number of units", "numunits", "quantity"], 1],
    ["weight", ["weight", "totalWeight"], ["total weight", "weight"], Math.round(pallet.weight)],
    ["length", ["length"], ["length"], Math.ceil(pallet.length)],
    ["width", ["width"], ["width"], Math.ceil(pallet.width)],
    ["height", ["height"], ["height"], Math.ceil(pallet.height)],
  ];
}

export class MyUnishippersAdapter extends BaseAdapter {
  broker = "myunishippers" as const;
  detect() { return /(^|\.)myunishippers\.com$/.test(location.hostname); }
  formRoot() { return document.querySelector("wwex-ui-book-shipment") || document.querySelector("form") || myUnishippersRoots()[0]?.parentElement || null; }

  private addressPostal(index: number): HTMLInputElement | null {
    const section = Array.from(
      document.querySelectorAll<HTMLElement>("wwex-ui-book-shipment-destination-form"),
    )[index];
    return Array.from(section?.querySelectorAll<HTMLInputElement>("input") || [])
      .find((control) => /postal code|zip code/i.test(clean(control.closest("mat-form-field")?.textContent))) || null;
  }

  protected deliveryZip() {
    const addressSections = document.querySelectorAll("wwex-ui-book-shipment-destination-form");
    const scopedPostal = addressSections.length ? this.addressPostal(addressSections.length - 1) : null;
    if (scopedPostal) return scopedPostal;
    const named = inputByNames(["destination postal", "delivery postal", "destination zip", "delivery zip", "to zip"]);
    if (named) return named;
    const postalControls = Array.from(document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select")).filter((control) => {
      const label = control.labels ? Array.from(control.labels).map((item) => item.textContent).join(" ") : "";
      const fieldText = control.closest("mat-form-field, [class*='form-field']")?.textContent || "";
      return /postal|zip/i.test(`${label} ${fieldText} ${control.name} ${control.id} ${control.getAttribute("placeholder") || ""}`);
    });
    return postalControls.at(-1) || null;
  }

  discoverFields(): FieldResult[] {
    return [
      fieldResult("delivery postal code", Boolean(this.deliveryZip())),
      fieldResult("handling unit", myUnishippersRoots().length > 0),
    ];
  }

  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const senderPostal = this.addressPostal(0);
    const senderAlreadyMatched = senderPostal?.value.includes(packet.origin.postal_code) ?? false;
    const senderReady = senderAlreadyMatched
      || setValue(senderPostal, packet.origin.postal_code);
    const results: FieldResult[] = [
      fieldResult(
        "sender postal code",
        Boolean(senderReady),
        senderAlreadyMatched
          ? "Verified the MyUnishippers sender ZIP"
          : "Updated the MyUnishippers sender ZIP to match Quote Helper",
      ),
      fieldResult("delivery postal code", setValue(this.deliveryZip(), packet.destination.postal_code)),
    ];
    await sleep(75);
    let roots = myUnishippersRoots();
    if (roots.length > packet.pallets.length) {
      results.push(fieldResult("handling unit count", false, `MyUnishippers has ${roots.length} rows; expected ${packet.pallets.length}. Remove extras manually.`));
      return results;
    }
    while (roots.length < packet.pallets.length && clickAddHandlingUnit()) {
      const before = roots.length;
      if (!await waitUntil(() => myUnishippersRoots().length > before, 3000)) break;
      roots = myUnishippersRoots();
    }
    results.push(fieldResult("handling unit count", roots.length === packet.pallets.length));
    if (roots.length !== packet.pallets.length) return results;

    for (const [index, pallet] of packet.pallets.entries()) {
      const root = roots[index];
      for (const [label, formNames, labels, value] of myValues(pallet)) {
        results.push(fieldResult(`pallet ${index + 1} ${label}`, setValue(rowControl(root, formNames, labels), value)));
      }
      results.push(fieldResult(`pallet ${index + 1} type`, await chooseRow(
        root, ["handlingUnitTypes", "handlingUnitType"], ["type"], "Pallet",
      )));
      results.push(fieldResult(`pallet ${index + 1} stackable`, await chooseRow(
        root, ["stackable"], ["stackable"], "No",
      )));
      results.push(fieldResult(`pallet ${index + 1} description`, setValue(
        rowControl(root, ["description", "commodityDescription"], ["description"]), pallet.commodity_description,
      )));
      results.push(fieldResult(`pallet ${index + 1} freight class`, await chooseRow(
        root, ["commodityClass", "freightClass", "class"], ["freight class", "class"], pallet.freight_class,
      )));
      if (pallet.nmfc) results.push(fieldResult(`pallet ${index + 1} NMFC`, setValue(
        rowControl(root, ["nmfc", "nmfcCode"], ["nmfc"]), pallet.nmfc,
      )));
    }
    const destinationSection = Array.from(
      document.querySelectorAll<HTMLElement>("wwex-ui-book-shipment-destination-form"),
    ).at(-1);
    const state = destinationSection?.querySelector<HTMLElement>("[formcontrolname='state']") || null;
    const city = destinationSection?.querySelector<HTMLInputElement>("input[formcontrolname='city']") || null;
    if (state || city) {
      let lastLocation = "";
      // City and state must hold unchanged for 600 ms (a changed value restarts the window).
      const locationResolved = await waitForStable(() => {
        const cityValue = clean(city?.value);
        const signature = `${clean(state?.textContent)}|${cityValue}`;
        const valid = Boolean(clean(state?.textContent) && cityValue)
          && (!packet.destination.city || cityValue.toLowerCase() === clean(packet.destination.city).toLowerCase());
        const unchanged = signature === lastLocation;
        lastLocation = signature;
        return valid && unchanged;
      }, { stableMs: 600, timeoutMs: 10000, pollMs: 40 });
      results.push(fieldResult(
        "destination city and state resolved from ZIP", locationResolved,
        "MyUnishippers must resolve both city and state from the destination ZIP",
      ));
    }
    results.push(fieldResult(
      "residential delivery",
      setChecked(checkboxByNames(["residential delivery"]), Boolean(packet.accessorials.residential)),
    ));
    results.push(fieldResult(
      "liftgate delivery",
      setChecked(checkboxByNames(["liftgate delivery", "lift gate delivery"]), Boolean(packet.accessorials.liftgate)),
    ));
    for (const [key, id] of [["appointment_delivery", "appointmentDelivery-input"], ["delivery_call_ahead", "notifyBeforeDelivery-input"]]) {
      const control = document.getElementById(id) as HTMLInputElement | null;
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? setChecked(control, expected) : !expected,
        control ? "Delivery service applied" : "Delivery service control unavailable"));
    }
    return results;
  }

  verify(packet: QuotePacket): FieldResult[] {
    const roots = myUnishippersRoots();
    const results: FieldResult[] = [
      fieldResult("sender postal code", this.addressPostal(0)?.value.includes(packet.origin.postal_code)
        ?? clean(document.body.textContent).includes(packet.origin.postal_code)),
      fieldResult("delivery postal code", this.deliveryZip()?.value.includes(packet.destination.postal_code) ?? false),
      fieldResult("handling unit count", roots.length === packet.pallets.length),
      fieldResult("residential delivery", checkboxByNames(["residential delivery"])?.checked === Boolean(packet.accessorials.residential)),
      fieldResult("liftgate delivery", checkboxByNames(["liftgate delivery", "lift gate delivery"])?.checked === Boolean(packet.accessorials.liftgate)),
    ];
    for (const [key, id] of [["appointment_delivery", "appointmentDelivery-input"], ["delivery_call_ahead", "notifyBeforeDelivery-input"]]) {
      const control = document.getElementById(id) as HTMLInputElement | null;
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? control.checked === expected : !expected));
    }
    packet.pallets.forEach((pallet, index) => {
      const root = roots[index];
      if (!root) { results.push(fieldResult(`pallet ${index + 1}`, false, "Handling-unit row is missing")); return; }
      for (const [label, formNames, labels, value] of myValues(pallet)) {
        results.push(fieldResult(`pallet ${index + 1} ${label}`, valueMatches(rowControl(root, formNames, labels), value)));
      }
      results.push(fieldResult(`pallet ${index + 1} type`, primeChoiceText(
        root, ["handlingUnitTypes", "handlingUnitType"], ["type"],
      ).toLowerCase().includes("pallet")));
      results.push(fieldResult(`pallet ${index + 1} stackable`, primeChoiceText(
        root, ["stackable"], ["stackable"],
      ).toLowerCase().includes("no")));
      results.push(fieldResult(`pallet ${index + 1} description`, valueMatches(
        rowControl(root, ["description", "commodityDescription"], ["description"]), pallet.commodity_description,
      )));
      results.push(fieldResult(`pallet ${index + 1} freight class`, primeChoiceText(
        root, ["commodityClass", "freightClass", "class"], ["freight class", "class"],
      ) === pallet.freight_class));
      if (pallet.nmfc) results.push(fieldResult(`pallet ${index + 1} NMFC`, valueMatches(
        rowControl(root, ["nmfc", "nmfcCode"], ["nmfc"]), pallet.nmfc,
      )));
    });
    return results;
  }
}
