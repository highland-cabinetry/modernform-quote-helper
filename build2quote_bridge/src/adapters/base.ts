import { fieldResult, fillPalletRows, inputByNames, setValue } from "../dom";
import type { Broker, CarrierAdapter, FieldResult, QuotePacket } from "../types";

export abstract class BaseAdapter implements CarrierAdapter {
  abstract broker: Broker;
  abstract detect(): boolean;
  formRoot(): Element | null { return document.querySelector("form") || document.body; }
  isLoginPage(): boolean { return /login|sign.?in|b2clogin|microsoftonline/i.test(location.href) || Boolean(document.querySelector("input[type='password']")); }
  protected pickupZip() { return inputByNames(["pickup postal", "pickup zip", "origin postal", "origin zip", "from zip"]); }
  protected deliveryZip() { return inputByNames(["delivery postal", "delivery zip", "destination postal", "destination zip", "to zip"]); }
  discoverFields(): FieldResult[] {
    return [fieldResult("pickup postal code", Boolean(this.pickupZip())), fieldResult("delivery postal code", Boolean(this.deliveryZip()))];
  }
  async fill(packet: QuotePacket): Promise<FieldResult[]> {
    const results = [
      fieldResult("pickup postal code", setValue(this.pickupZip(), packet.origin.postal_code)),
      fieldResult("delivery postal code", setValue(this.deliveryZip(), packet.destination.postal_code)),
    ];
    results.push(...await fillPalletRows(packet.pallets, packet.commodity_description));
    return results;
  }
  verify(packet: QuotePacket): FieldResult[] {
    const pickup = this.pickupZip(); const delivery = this.deliveryZip();
    return [
      fieldResult("pickup postal code", pickup?.value.includes(packet.origin.postal_code) ?? false),
      fieldResult("delivery postal code", delivery?.value.includes(packet.destination.postal_code) ?? false),
    ];
  }
}
