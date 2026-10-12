export type Broker = "echoship" | "tql" | "dsv" | "myunishippers" | "odfl";

export interface AddressInfo {
  name: string; street: string; city: string; state: string; postal_code: string; phone: string; email: string;
}

export interface PalletRow {
  pallet: number; type: string; length: number; width: number; height: number; weight: number; dimensions: string;
  density_pcf: number; freight_class: string; commodity_description: string; nmfc: string; stackable: boolean;
}

/** Delivery services (backend/app/models.py Accessorials). Residential and limited access are exclusive. */
export interface Accessorials {
  residential?: boolean;
  liftgate?: boolean;
  appointment_delivery?: boolean;
  delivery_call_ahead?: boolean;
  limited_access?: boolean;
  inside_delivery?: boolean;
  [service: string]: boolean | undefined;
}

export interface QuotePacket {
  shipment_id: string;
  schema_version: number;
  shipment_name: string;
  customer_references: string[];
  origin: AddressInfo;
  destination: AddressInfo;
  accessorials: Accessorials;
  commodity_description: string;
  nmfc: string;
  pallets: PalletRow[];
  guard: string;
}

export interface BridgeSession {
  request_id: string;
  token: string;
  bridge_origin: string;
  broker: Broker;
  expires_at: string;
}
/**
 * One filled or verified field. `manual` marks a note for the person rather than a fill failure:
 * "set" = a requested service the Bridge does not select on this page, "check" = a value the
 * carrier does not let the Bridge read back. Notes keep `ok: false` so every surface shows them,
 * but they never trigger a refill, repair, or a longer wait.
 */
export interface FieldResult { field: string; ok: boolean; message: string; manual?: "set" | "check"; }

export interface CarrierAdapter {
  broker: Broker;
  detect(): boolean;
  isLoginPage(): boolean;
  discoverFields(): FieldResult[];
  fill(packet: QuotePacket): Promise<FieldResult[]>;
  verify(packet: QuotePacket): FieldResult[];
  formRoot(): Element | null;
  manualPrerequisite?(): FieldResult | null;
  /** Accessorial keys this adapter fills and verifies on the carrier page. */
  readonly supportedAccessorials?: readonly string[];
}
