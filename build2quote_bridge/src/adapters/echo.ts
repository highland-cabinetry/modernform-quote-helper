import { BaseAdapter } from "./base";
import {
  checkboxByNames,
  clickAddItem,
  fieldResult,
  setChecked,
  setValue,
  clean,
  visible,
} from "../dom";
import { containsExactZip, looksResolved, matchesPostalAddress, selectExactPostalCode } from "../autocomplete";
import { waitFor } from "../timing";
import type { AddressInfo, FieldResult, PalletRow, QuotePacket } from "../types";

interface EchoRow {
  root: HTMLElement;
  quantity: HTMLInputElement | null;
  handling: HTMLSelectElement | null;
  length: HTMLInputElement;
  width: HTMLInputElement | null;
  height: HTMLInputElement | null;
  weight: HTMLInputElement | null;
  freightClass: HTMLSelectElement | null;
  nmfc: HTMLInputElement | null;
  description: HTMLInputElement | null;
}

function echoRows(): EchoRow[] {
  return Array.from(document.querySelectorAll<HTMLInputElement>("input[id^='home-ship-length-']")).map((length) => {
    const suffix = length.id.replace("home-ship-length-", "");
    const byId = <T extends HTMLElement>(prefix: string) => document.getElementById(`${prefix}${suffix}`) as T | null;
    const handling = byId<HTMLSelectElement>("home-ship-handling-");
    let root: HTMLElement = length.parentElement || document.body;
    while (root.parentElement && handling && !root.contains(handling)) root = root.parentElement;
    return {
      root,
      quantity: byId<HTMLInputElement>("home-ship-quan-"),
      handling,
      length,
      width: byId<HTMLInputElement>("home-ship-width-"),
      height: byId<HTMLInputElement>("home-ship-height-"),
      weight: byId<HTMLInputElement>("home-ship-weight-"),
      freightClass: byId<HTMLSelectElement>("home-ship-freight-")
        || root.querySelector<HTMLSelectElement>("select[id*='freight-class']"),
      nmfc: byId<HTMLInputElement>("home-ship-nmfc-"),
      description: root.querySelector<HTMLInputElement>("input[placeholder*='item description']"),
    };
  });
}

function destinationCheckbox(kind: "residential" | "liftgate"): HTMLInputElement | null {
  const labels = Array.from(document.querySelectorAll<HTMLLabelElement>("label[id*='destination']"));
  const pattern = kind === "residential" ? /residential/i : /lift.?gate/i;
  return labels.find((label) => pattern.test(clean(label.textContent)))?.querySelector("input[type='checkbox']") || null;
}

function liftgateControl(): HTMLInputElement | null {
  return destinationCheckbox("liftgate") || checkboxByNames(["lift gate delivery", "liftgate delivery"]);
}

const DELIVERY_SERVICE_IDS = [
  ["appointment_delivery", "ltl-quote-destination-appointment-required"],
  ["delivery_call_ahead", "ltl-quote-destination-notify-prior-to-delivery"],
] as const;

function deliveryServiceControl(id: string): HTMLInputElement | null {
  return document.getElementById(id)?.querySelector<HTMLInputElement>("input[type='checkbox']") || null;
}

function destinationLocationType(): HTMLSelectElement | null {
  return document.querySelector<HTMLOptionElement>("option#ltl-quote-destination-residential")
    ?.parentElement as HTMLSelectElement | null;
}

function echoValues(pallet: PalletRow): Array<[keyof Pick<EchoRow, "quantity" | "length" | "width" | "height" | "weight">, number]> {
  return [
    ["quantity", 1],
    ["length", Math.ceil(pallet.length)],
    ["width", Math.ceil(pallet.width)],
    ["height", Math.ceil(pallet.height)],
    ["weight", Math.round(pallet.weight)],
  ];
}

function numericMatches(control: HTMLInputElement | null, expected: number): boolean {
  const actual = Number(clean(control?.value).replaceAll(",", ""));
  return Number.isFinite(actual) && Math.abs(actual - expected) < 0.01;
}

function selectedText(control: HTMLSelectElement | null): string {
  return clean(control?.selectedOptions[0]?.textContent || control?.value);
}

export class EchoAdapter extends BaseAdapter {
  broker = "echoship" as const;
  detect() { return location.hostname === "echoship.com"; }
  formRoot() { return document.querySelector("#ltl-quote-origin-entry")?.closest("form") || document.querySelector("#ltl-quote-origin-entry")?.parentElement || null; }
  protected deliveryZip() {
    return document.querySelector<HTMLInputElement>("#ltl-quote-destination-entry input[name='locationTypeaheadDestination'], #ltl-quote-destination-entry input[placeholder*='ZIP']");
  }
  private originResolved(zip: string): boolean {
    const cards = Array.from(document.querySelectorAll("#ltl-quote-origin-entry .location-card")).filter(visible);
    return cards.length === 1 && containsExactZip(cards[0].textContent, zip);
  }
  private locationResolved(kind: "origin" | "destination", zip: string): boolean {
    const cards = Array.from(document.querySelectorAll(`#ltl-quote-${kind}-entry .location-card`)).filter(visible);
    return cards.length === 1 && containsExactZip(cards[0].textContent, zip);
  }
  private postalOptions(kind: "origin" | "destination", control: HTMLInputElement): HTMLElement[] {
    const popupId = control.getAttribute("aria-controls") || control.getAttribute("aria-owns");
    const popup = popupId ? document.getElementById(popupId)
      : document.querySelector(`#ltl-quote-${kind}-entry #ltl-typeahead-dropdown-multi-${kind}`);
    return Array.from(popup?.querySelectorAll<HTMLElement>("li, [role='option']") || []);
  }
  private async fillOrigin(address: AddressInfo): Promise<boolean> {
    const postalCode = address.postal_code;
    const root = document.querySelector<HTMLElement>("#ltl-quote-origin-entry");
    if (!root) return false;
    if (this.originResolved(postalCode)) return true;
    const originInput = () => document.querySelector("#ltl-quote-origin-entry")?.querySelector<HTMLInputElement>("input[name*='Origin'], input[placeholder*='ZIP']") || null;
    root.querySelector<HTMLElement>(".location-card .icon-close-round")?.click();
    // Wait for the card to give way to the origin input instead of a fixed 300 ms pause.
    await waitFor(() => Boolean(originInput()), { timeoutMs: 2000 });
    return selectExactPostalCode({
      getControl: originInput,
      zip: postalCode,
      getOptions: control => this.postalOptions("origin", control),
      matchesOption: candidate => matchesPostalAddress(candidate, address),
      keyboardLookup: true,
      timeoutMs: 45000,
      verifyResolved: () => this.originResolved(postalCode),
    });
  }
  discoverFields(): FieldResult[] {
    return [
      fieldResult("destination postal code", Boolean(this.deliveryZip())),
      fieldResult("shipment item row", echoRows().length > 0),
    ];
  }
  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const results: FieldResult[] = [];
    results.push(fieldResult(
      "origin postal code",
      await this.fillOrigin(packet.origin),
      "Verified or selected the EchoShip origin ZIP",
    ));
    const destinationFilled = await selectExactPostalCode({
      getControl: () => this.deliveryZip(), zip: packet.destination.postal_code,
      getOptions: control => this.postalOptions("destination", control),
      matchesOption: candidate => matchesPostalAddress(candidate, packet.destination),
      keyboardLookup: true,
      timeoutMs: 45000,
      verifyResolved: () => this.locationResolved("destination", packet.destination.postal_code),
    });
    results.push(fieldResult("destination postal code", destinationFilled));
    if (results.some(result => !result.ok)) return results;
    let rows = echoRows();
    if (rows.length > packet.pallets.length) {
      results.push(fieldResult("shipment item row count", false, `EchoShip has ${rows.length} rows; expected ${packet.pallets.length}. Remove extras manually.`));
      return results;
    }
    const clickAddEchoItem = () => {
      const button = document.querySelector<HTMLButtonElement>("#add-new-quote-item-btn");
      if (button) { button.click(); return true; }
      return clickAddItem();
    };
    while (rows.length < packet.pallets.length) {
      const before = rows.length;
      if (!clickAddEchoItem()) break;
      // Wait for the new row (not a fixed 500 ms per row).
      if (!await waitFor(() => echoRows().length > before, { timeoutMs: 3000 })) break;
      rows = echoRows();
    }
    for (const [index, pallet] of packet.pallets.entries()) {
      const row = rows[index];
      if (!row) { results.push(fieldResult(`pallet ${index + 1}`, false, "Shipment item row was not created")); continue; }
      results.push(fieldResult(`pallet ${index + 1} handling unit`, setValue(row.handling, "Skids")));
      for (const [key, value] of echoValues(pallet)) {
        results.push(fieldResult(`pallet ${index + 1} ${key}`, setValue(row[key], value)));
      }
      results.push(fieldResult(`pallet ${index + 1} description`, setValue(row.description, pallet.commodity_description)));
      results.push(fieldResult(`pallet ${index + 1} freight class`, setValue(row.freightClass, pallet.freight_class)));
      if (pallet.nmfc) results.push(fieldResult(`pallet ${index + 1} NMFC`, setValue(row.nmfc, pallet.nmfc)));
      results.push(fieldResult(`pallet ${index + 1} do not stack`, setChecked(checkboxByNames(["do not stack"], row.root), true)));
    }
    // Delivery services live behind EchoShip's "Delivery Accessorials" toggle. It used to be
    // opened only for residential or liftgate, so a requested appointment (on by default) failed
    // whenever neither was requested. Open it whenever a control this fill needs is not rendered,
    // and never when everything is already there (a second click would close the panel on retry).
    const missingControl = () => (!packet.accessorials.limited_access && !destinationLocationType())
      || !liftgateControl()
      || DELIVERY_SERVICE_IDS.some(([key, id]) => packet.accessorials[key] && !deliveryServiceControl(id));
    const toggle = document.querySelector<HTMLButtonElement>("#da-dropdown");
    if (toggle && missingControl()) {
      toggle.click();
      await waitFor(() => !missingControl(), { timeoutMs: 1500 });
    }
    if (packet.accessorials.limited_access) {
      // Limited access is a location type of its own; leave the selector for the manual choice.
      results.push(fieldResult("destination location type", true, "Left for the manual Limited access selection"));
    } else {
      results.push(fieldResult(
        "destination location type",
        setValue(destinationLocationType(), packet.accessorials.residential ? "Residential" : "Business"),
        packet.accessorials.residential ? "Residential selected" : "Business selected",
      ));
    }
    results.push(fieldResult(
      "liftgate delivery",
      setChecked(liftgateControl(), Boolean(packet.accessorials.liftgate)),
    ));
    for (const [key, id] of DELIVERY_SERVICE_IDS) {
      const control = deliveryServiceControl(id);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? setChecked(control, expected) : !expected));
    }
    return results;
  }
  verify(packet: QuotePacket): FieldResult[] {
    const results = [
      fieldResult("origin postal code", this.originResolved(packet.origin.postal_code)),
      fieldResult("destination postal code", this.locationResolved("destination", packet.destination.postal_code) || looksResolved(this.deliveryZip(), packet.destination.postal_code)),
      fieldResult("shipment item row count", echoRows().length === packet.pallets.length),
      fieldResult("destination location type", packet.accessorials.limited_access
        ? true
        : clean(destinationLocationType()?.selectedOptions[0]?.textContent).toLowerCase() === (packet.accessorials.residential ? "residential" : "business")),
      fieldResult("liftgate delivery", liftgateControl()?.checked === Boolean(packet.accessorials.liftgate)),
    ];
    echoRows().forEach((row, index) => {
      if (index >= packet.pallets.length) return;
      const doNotStack = checkboxByNames(["do not stack"], row.root);
      echoValues(packet.pallets[index]).forEach(([key, value]) => {
        results.push(fieldResult(`pallet ${index + 1} ${key}`, numericMatches(row[key], value)));
      });
      results.push(fieldResult(`pallet ${index + 1} handling unit`, selectedText(row.handling).toLowerCase().includes("skid")));
      results.push(fieldResult(`pallet ${index + 1} freight class`, selectedText(row.freightClass) === packet.pallets[index].freight_class));
      results.push(fieldResult(`pallet ${index + 1} description`, clean(row.description?.value) === clean(packet.pallets[index].commodity_description)));
      results.push(fieldResult(`pallet ${index + 1} NMFC`, clean(row.nmfc?.value) === clean(packet.pallets[index].nmfc)));
      results.push(fieldResult(`pallet ${index + 1} do not stack`, doNotStack?.checked ?? false));
    });
    for (const [key, id] of DELIVERY_SERVICE_IDS) {
      const control = deliveryServiceControl(id);
      const expected = Boolean(packet.accessorials[key]);
      results.push(fieldResult(key, control ? control.checked === expected : !expected));
    }
    return results;
  }
}
