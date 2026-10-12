import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DsvAdapter } from "../src/adapters/dsv";
import { EchoAdapter } from "../src/adapters/echo";
import { TqlAdapter } from "../src/adapters/tql";
import { MyUnishippersAdapter } from "../src/adapters/myunishippers";
import { OdflAdapter } from "../src/adapters/odfl";
import { blockingFailures } from "../src/accessorials";
import type { CarrierAdapter, QuotePacket } from "../src/types";
import dsvFixture from "./fixtures/dsv.html?raw";
import echoFixture from "./fixtures/echoship.html?raw";
import tqlFixture from "./fixtures/tql.html?raw";
import myunishippersFixture from "./fixtures/myunishippers.html?raw";
import odflFixture from "./fixtures/odfl.html?raw";

const packet: QuotePacket = {
  schema_version: 2,
  shipment_id: "test",
  shipment_name: "Sanitized adapter contract",
  customer_references: ["ORDER-1"],
  origin: { name: "", street: "", city: "", state: "", postal_code: "80011", phone: "", email: "" },
  destination: { name: "", street: "", city: "", state: "", postal_code: "59714", phone: "", email: "" },
  accessorials: { residential: true, liftgate: true },
  commodity_description: "Cabinets",
  nmfc: "12345",
  pallets: [{ pallet: 1, type: "long", length: 96, width: 40, height: 44, weight: 1500, dimensions: "96 x 40 x 44", density_pcf: 15.34, freight_class: "70", commodity_description: "Cabinets", nmfc: "12345", stackable: false }],
  guard: "FILL_ONLY_NEVER_SUBMIT",
};

const documentListenerCleanups: Array<() => void> = [];

afterEach(() => {
  documentListenerCleanups.splice(0).forEach(cleanup => cleanup());
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{} as DOMRect]);
});

function wireFreshPostalOptions() {
  document.querySelectorAll<HTMLInputElement>("input[aria-controls], input[aria-owns]").forEach(input => {
    input.addEventListener("input", () => {
      const popup = document.getElementById(input.getAttribute("aria-controls") || input.getAttribute("aria-owns") || "");
      popup?.querySelectorAll<HTMLElement>("[role='option']").forEach(option => option.dataset.response = String(Date.now()));
    });
  });
}

function wireTqlFixture() {
  wireFreshPostalOptions();
  const modelValues = new WeakMap<HTMLElement, Record<string, number>>();
  const renderModelSummary = () => {
    const rows = Array.from(document.querySelectorAll<HTMLElement>("#add-item .commodity-row"));
    let handlingUnits = 0;
    let totalWeight = 0;
    let linearFeet = 0;
    rows.forEach((row) => {
      const values = modelValues.get(row) || {};
      const quantity = values.quantity;
      const weight = values.weight;
      const length = values.length;
      const width = values.width;
      const height = values.height;
      if ([quantity, weight, length, width, height].some(value => !Number.isFinite(value))) return;
      handlingUnits += quantity;
      totalWeight += quantity * weight;
      linearFeet += quantity * length * width / 480;
      const density = weight * 1728 / (length * width * height);
      const densityDisplay = row.querySelector<HTMLElement>("[data-testid^='input-field-group-density-'] .display-field");
      if (densityDisplay) densityDisplay.textContent = density.toFixed(2);
      const estimatedClass = row.querySelector<HTMLElement>("[data-testid^='input-field-group-estClass-'] .display-field");
      if (estimatedClass) estimatedClass.textContent = row.querySelector<HTMLSelectElement>("select[aria-label='Class']")?.selectedOptions[0]?.textContent || "100";
    });
    const setSummary = (name: string, value: number) => {
      const target = document.querySelector<HTMLElement>(`[data-summary-field='${name}'] .item-summary-value`);
      if (target) target.textContent = name === "handling-unit" ? String(value) : value.toFixed(2);
    };
    setSummary("handling-unit", handlingUnits);
    setSummary("total-unit-weight", totalWeight);
    setSummary("calculated-linear-feet", linearFeet);
  };
  const modelInputListener = (event: Event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)
      || !["quantity", "weight", "length", "width", "height"].includes(input.name)) return;
    const row = input.closest<HTMLElement>(".commodity-row");
    if (!row) return;
    const values = modelValues.get(row) || {};
    values[input.name] = Number(input.value);
    modelValues.set(row, values);
    renderModelSummary();
  };
  document.addEventListener("input", modelInputListener);
  documentListenerCleanups.push(() => document.removeEventListener("input", modelInputListener));
  const wirePostal = (inputId: string, listId: string) => {
    const input = document.getElementById(inputId) as HTMLInputElement;
    document.querySelectorAll<HTMLElement>(`#${listId} [role='option']`).forEach((option) => {
      option.addEventListener("click", () => { input.value = option.textContent || ""; });
    });
  };
  wirePostal("pickup-postal", "pickup-list");
  wirePostal("delivery-postal", "delivery-list");
  let activeCombo: HTMLElement | null = null;
  const wireCombo = (combo: HTMLElement) => combo.addEventListener("click", () => { activeCombo = combo; });
  document.querySelectorAll<HTMLElement>("[id^='input-unit-'][role='combobox'], [id^='input-class-'][role='combobox']")
    .forEach(wireCombo);
  document.querySelectorAll<HTMLElement>("[role='listbox'] [role='option']").forEach((option) => {
    option.addEventListener("click", () => {
      if (!activeCombo) return;
      const kind = activeCombo.id.includes("-unit-") ? "unit" : "class";
      activeCombo.querySelector<HTMLElement>(".k-input-value-text")!.textContent = option.textContent;
      const row = activeCombo.closest(".commodity-row")!;
      const select = row.querySelector<HTMLSelectElement>(`select[aria-label='${kind === "unit" ? "Unit" : "Class"}']`)!;
      const match = Array.from(select.options).find((candidate) => candidate.text === option.textContent);
      if (match) select.value = match.value;
    });
  });
  document.querySelector("#add-handling-unit")?.addEventListener("click", () => {
    const existing = document.querySelectorAll(".commodity-row").length;
    const clone = document.querySelector<HTMLElement>(".commodity-row")!.cloneNode(true) as HTMLElement;
    clone.dataset.row = String(existing + 1);
    clone.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
      input.id = input.id.replace(/-1$/, `-${existing + 1}`);
      if (input.type === "checkbox") input.checked = true;
      else input.value = "";
    });
    clone.querySelectorAll<HTMLElement>("[id^='input-unit-'], [id^='input-class-']").forEach((combo) => {
      combo.id = combo.id.replace(/-1$/, `-${existing + 1}`);
      if (combo.id.includes("-class-")) combo.querySelector<HTMLElement>(".k-input-value-text")!.textContent = "";
      wireCombo(combo);
    });
    clone.querySelectorAll<HTMLElement>("[data-testid^='input-field-group-density-'], [data-testid^='input-field-group-estClass-']")
      .forEach((field) => { field.dataset.testid = field.dataset.testid?.replace(/-1$/, `-${existing + 1}`); });
    document.querySelector("#add-item")!.insertBefore(clone, document.querySelector("#add-handling-unit"));
  });
}

function wireDsvFixture() {
  wireFreshPostalOptions();
  let activePostal: HTMLInputElement | null = null;
  const addPostalPanel = () => {
    if (document.getElementById("dsv-postal-overlay") || activePostal?.closest(".row")?.querySelector(".dsv-postal-marker")) return;
    const panel = document.createElement("div");
    panel.id = "dsv-postal-overlay";
    panel.className = "p-autocomplete-panel";
    panel.setAttribute("role", "listbox");
    panel.innerHTML = '<button type="button" role="option">80011 Aurora CO</button><button type="button" role="option">59714 Belgrade MT</button>';
    document.body.append(panel);
    wirePostalPanel(panel);
  };
  const wirePostalPanel = (panel: HTMLElement) => panel.querySelectorAll<HTMLElement>("[role='option']").forEach(option => {
    option.addEventListener("click", () => {
      const text = option.textContent?.trim() || "";
      if (!/^\d{5}\b/.test(text) || !activePostal) return;
      activePostal.value = text;
      activePostal.classList.add("ng-valid");
      activePostal.classList.remove("ng-invalid");
      activePostal.setAttribute("aria-invalid", "false");
      const marker = document.createElement("span");
      marker.className = "dsv-postal-marker";
      marker.innerHTML = `<i class="pi pi-compass"></i> Postal Code: ${text}`;
      activePostal.closest(".row")?.querySelector(".dsv-postal-marker")?.remove();
      activePostal.closest(".row")?.append(marker);
      panel.remove();
    });
  });
  document.querySelectorAll<HTMLInputElement>("input[role='searchbox']").forEach((input) => {
    input.addEventListener("input", () => {
      activePostal = input;
      document.querySelectorAll<HTMLElement>("#dsv-postal-overlay [role='option']")
        .forEach(option => option.dataset.response = String(Date.now()));
    });
    input.addEventListener("input", () => {
      const hadPanel = Boolean(document.getElementById("dsv-postal-overlay"));
      if (!hadPanel && input.dataset.noOptions !== "true") setTimeout(addPostalPanel, 0);
    });
  });
  let activeDropdown: HTMLElement | null = null;
  const wireDropdown = (dropdown: HTMLElement) => dropdown.addEventListener("click", () => { activeDropdown = dropdown; });
  document.querySelectorAll<HTMLElement>(".p-dropdown").forEach(wireDropdown);
  document.querySelectorAll<HTMLElement>("[role='option']").forEach((option) => {
    option.addEventListener("click", () => {
      const text = option.textContent?.trim() || "";
      if (/^\d{5}\b/.test(text) && activePostal) activePostal.value = text;
      if (activeDropdown && !/^\d{5}\b/.test(text)) {
        activeDropdown.querySelector<HTMLElement>(".p-dropdown-label")!.textContent = text;
      }
    });
  });
  const initialPanel = document.getElementById("dsv-postal-overlay");
  if (initialPanel) {
    initialPanel.querySelectorAll<HTMLElement>("[role='option']").forEach(option => {
      option.addEventListener("click", () => {
        const text = option.textContent?.trim() || "";
        if (!/^\d{5}\b/.test(text) || !activePostal) return;
        activePostal.value = text;
        activePostal.classList.add("ng-valid");
        activePostal.classList.remove("ng-invalid");
        activePostal.setAttribute("aria-invalid", "false");
        const marker = document.createElement("span");
        marker.className = "dsv-postal-marker";
        marker.innerHTML = `<i class="pi pi-compass"></i> Postal Code: ${text}`;
        activePostal.closest(".row")?.querySelector(".dsv-postal-marker")?.remove();
        activePostal.closest(".row")?.append(marker);
        initialPanel.remove();
      });
    });
  }
  document.querySelector("#dsv-add-item")?.addEventListener("click", () => {
    const clone = document.querySelector<HTMLElement>(".dsv-goods-row")!.cloneNode(true) as HTMLElement;
    clone.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
      if (input.type === "checkbox") input.checked = true;
      else input.value = "";
    });
    clone.querySelectorAll<HTMLElement>(".p-dropdown-label").forEach((label) => { label.textContent = ""; });
    clone.querySelectorAll<HTMLElement>(".p-dropdown").forEach(wireDropdown);
    document.querySelector("#dsv-goods")!.append(clone);
  });
}

function addDsvLocalPostalOptions(inputId: string, options: Array<{ text: string; zip: string }>) {
  const input = document.getElementById(inputId) as HTMLInputElement;
  const wrapper = document.createElement("p-autocomplete");
  input.parentElement!.insertBefore(wrapper, input);
  wrapper.append(input);
  const list = document.createElement("ul");
  list.className = "p-autocomplete-items";
  wrapper.append(list);
  for (const optionData of options) {
    const option = document.createElement("li");
    option.setAttribute("role", "option");
    option.textContent = optionData.text;
    option.addEventListener("click", () => {
      input.value = optionData.zip;
      input.classList.remove("ng-valid");
      input.classList.remove("ng-invalid");
      input.removeAttribute("aria-invalid");
      const marker = document.createElement("span");
      marker.innerHTML = `<i class="pi pi-compass"></i> Postal Code: ${optionData.zip}`;
      input.closest(".row")?.querySelector(".dsv-postal-marker")?.remove();
      input.closest(".row")?.append(marker);
      list.remove();
    });
    list.append(option);
  }
}

function wireEchoFixture() {
  wireFreshPostalOptions();
  document.querySelectorAll<HTMLElement>("[role='option']").forEach(option => {
    option.addEventListener("click", () => {
      const destination = document.querySelector<HTMLInputElement>("input[name='locationTypeaheadDestination']");
      if (destination && /^\d{5}\b/.test(option.textContent?.trim() || "")) destination.value = option.textContent?.trim() || "";
    });
  });
  document.querySelector("#add-new-quote-item-btn")?.addEventListener("click", () => {
    const rows = document.querySelectorAll(".commodity");
    const suffix = rows.length + 3;
    const clone = rows[0].cloneNode(true) as HTMLElement;
    clone.querySelectorAll<HTMLInputElement>("input").forEach((input) => {
      input.id = input.id.replace(/-3$/, `-${suffix}`);
      if (input.type === "checkbox") input.checked = false;
      else input.value = "";
    });
    clone.querySelectorAll<HTMLSelectElement>("select").forEach((select) => {
      select.id = select.id.replace(/-3$/, `-${suffix}`);
      select.selectedIndex = 0;
    });
    rows[0].parentElement!.insertBefore(clone, document.querySelector("#add-new-quote-item-btn"));
  });
}

function wireMyUnishippersFixture() {
  const destinationPostal = document.querySelector<HTMLInputElement>("#my-destination-postal")!;
  const resolveDestination = () => {
    const destination = document.querySelector<HTMLElement>(".destination-address")!;
    destination.querySelector<HTMLElement>("[formcontrolname='state']")!.textContent = "Montana";
    destination.querySelector<HTMLInputElement>("[formcontrolname='city']")!.value = "BELGRADE";
  };
  destinationPostal.addEventListener("input", resolveDestination);
  destinationPostal.addEventListener("blur", resolveDestination);

  let activeSelect: HTMLElement | null = null;
  const wireSelect = (select: HTMLElement) => select.addEventListener("click", () => { activeSelect = select; });
  document.querySelectorAll<HTMLElement>("mat-select").forEach(wireSelect);
  document.querySelectorAll<HTMLElement>("#my-options [role='option']").forEach((option) => {
    option.addEventListener("click", () => {
      if (activeSelect) activeSelect.textContent = option.textContent;
    });
  });
  document.querySelector("#my-add-handling-unit")?.addEventListener("click", () => {
    const clone = document.querySelector<HTMLElement>(".handling-unit")!.cloneNode(true) as HTMLElement;
    clone.querySelectorAll<HTMLInputElement>("input").forEach((input) => { input.value = ""; });
    clone.querySelectorAll<HTMLElement>("mat-select").forEach((select) => {
      select.textContent = select.getAttribute("formcontrolname") === "handlingUnitTypes" ? "Pallet" : "";
      if (select.getAttribute("formcontrolname") === "stackable") select.textContent = "Yes";
      wireSelect(select);
    });
    document.querySelector("#my-handling-units")!.append(clone);
  });
}

function wireOdflFixture() {
  const locations = {
    "80011": { city: "Aurora", state: "CO" },
    "59714": { city: "Belgrade", state: "MT" },
    "80022": { city: "Commerce City", state: "CO" },
    "59106": { city: "Billings", state: "MT" },
  } as const;
  const commitCarrierLocation = (control: HTMLInputElement, zip: string, details: { city: string; state: string }) => {
    const origin = control.id.includes("origin");
    const prefix = origin ? "ltl_origin" : "ltl_dest";
    const cityId = origin ? "ltl_origin_city" : "ltl_destination_city";
    const countryId = origin ? "ltl_origin_country" : "ltl_destination_country";
    const postal = document.getElementById(`${prefix}Zipcode`) as HTMLInputElement;
    const state = document.getElementById(`${prefix}State`) as HTMLInputElement;
    const city = document.getElementById(cityId) as HTMLSelectElement;
    const country = document.getElementById(countryId) as HTMLSelectElement;
    const cityOption = document.createElement("option");
    cityOption.value = details.city.toUpperCase(); cityOption.textContent = details.city;
    postal.value = zip;
    city.replaceChildren(cityOption); city.value = cityOption.value;
    state.value = details.state;
    country.value = "US";
  };
  document.querySelectorAll<HTMLSelectElement>("[id^='ltl_class']").forEach((control) => {
    control.addEventListener("click", () => {
      if (control.querySelector("option[value='100']")) return;
      const option = document.createElement("option");
      option.value = "100";
      option.textContent = "Class 100";
      control.append(option);
    });
  });
  document.querySelectorAll<HTMLInputElement>(".js-autocomplete").forEach((control) => {
    control.addEventListener("input", () => {
      document.querySelectorAll(".pac-container").forEach((candidate) => candidate.remove());
      if (control.dataset.noSuggestions === "true") return;
      const details = locations[control.value as keyof typeof locations];
      if (!details) return;
      const panel = document.createElement("div");
      panel.className = "pac-container";
      const option = document.createElement("div");
      option.className = "pac-item";
      option.textContent = `${control.value} ${details.city} ${details.state} USA`;
      option.addEventListener("click", () => {
        const origin = control.id.includes("origin");
        const prefix = origin ? "ltl_origin" : "ltl_dest";
        const cityId = origin ? "ltl_origin_city" : "ltl_destination_city";
        const countryId = origin ? "ltl_origin_country" : "ltl_destination_country";
        const postal = document.getElementById(`${prefix}Zipcode`) as HTMLInputElement;
        const state = document.getElementById(`${prefix}State`) as HTMLInputElement;
        const city = document.getElementById(cityId) as HTMLSelectElement;
        const country = document.getElementById(countryId) as HTMLSelectElement;
        if (!postal || !state || !city || !country) { panel.remove(); return; }
        postal.value = control.value;
        control.value = `${control.value} ${details.city}, ${details.state}, USA`;
        city.dataset.lookupCity = details.city;
        city.dataset.lookupState = details.state;
        city.addEventListener("click", () => {
          const cityOption = document.createElement("option");
          cityOption.value = details.city.toUpperCase(); cityOption.textContent = details.city;
          city.replaceChildren(cityOption); city.value = cityOption.value;
          state.value = details.state;
          country.value = "US";
        }, { once: true });
        panel.remove();
      });
      panel.append(option);
      document.body.append(panel);
    });
    control.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || control.dataset.noSuggestions !== "true" || control.dataset.neverCommits === "true") return;
      if (control.dataset.requestSubmit === "true") control.form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      const zip = control.value;
      const details = locations[zip as keyof typeof locations];
      if (!details) return;
      setTimeout(() => commitCarrierLocation(control, zip, details), Number(control.dataset.commitDelayMs || 80));
    });
  });

  const wireAdd = (buttonSelector: string, rowSelector: string, containerSelector: string) => {
    document.querySelector(buttonSelector)?.addEventListener("click", () => {
      const rows = document.querySelectorAll<HTMLElement>(rowSelector);
      const clone = rows[0].cloneNode(true) as HTMLElement;
      const suffix = rows.length;
      clone.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select").forEach((control) => {
        control.id = control.id.replace(/\d+$/, String(suffix));
        if (control instanceof HTMLSelectElement) control.selectedIndex = 0;
        else control.value = "";
      });
      document.querySelector(containerSelector)?.append(clone);
    });
  };
  wireAdd(".js-addwtclass-fields", ".odfl-commodity-row", ".js-weight-section");
  wireAdd(".js-add-dimensions-fields", ".odfl-dimension-row", ".js-dimensions-section");
}

describe.each([
  ["echoship.com", echoFixture, () => new EchoAdapter()],
  ["trax.tqlapp.com", tqlFixture, () => new TqlAdapter()],
  ["mytms.us.dsv.com", dsvFixture, () => new DsvAdapter()],
  ["www.myunishippers.com", myunishippersFixture, () => new MyUnishippersAdapter()],
] as const)("%s adapter", (hostname, fixture, createAdapter) => {
  it("detects, fills, and verifies the sanitized form without submitting", async () => {
    const path = hostname.includes("tql")
      ? "/quote-details"
      : hostname.includes("dsv") ? "/dsvweb/dashboard/quote" : "/quote";
    vi.stubGlobal("location", { href: `https://${hostname}${path}`, hostname });
    document.body.innerHTML = fixture;
    if (hostname.includes("tql")) wireTqlFixture();
    if (hostname.includes("dsv")) wireDsvFixture();
    if (hostname.includes("echo")) wireEchoFixture();
    if (hostname.includes("myunishippers")) wireMyUnishippersFixture();
    const adapter: CarrierAdapter = createAdapter();
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit");

    expect(adapter.detect()).toBe(true);
    expect(adapter.discoverFields().every((field) => field.ok)).toBe(true);
    expect((await adapter.fill(packet)).every((field) => field.ok)).toBe(true);
    expect(adapter.verify(packet).every((field) => field.ok)).toBe(true);
    expect(submit).not.toHaveBeenCalled();
  }, 15000);
});

describe("rounded carrier values", () => {
  const decimalPacket: QuotePacket = {
    ...packet,
    pallets: [
      { ...packet.pallets[0], weight: 512.4, height: 64.6 },
      { ...packet.pallets[0], pallet: 2, freight_class: "92.5", weight: 607.22, height: 55.2 },
    ],
  };

  it("fills EchoShip with whole-pound weights and whole-inch freight dimensions", async () => {
    vi.stubGlobal("location", { href: "https://echoship.com/new-quote/ltl", hostname: "echoship.com" });
    document.body.innerHTML = echoFixture;
    wireEchoFixture();
    const submit = document.querySelector<HTMLButtonElement>("#submit-new-quote-btn")!;
    const clicked = vi.fn();
    submit.addEventListener("click", clicked);
    const adapter = new EchoAdapter();

    const fillResults = await adapter.fill(decimalPacket);
    expect(fillResults.filter((field) => !field.ok)).toEqual([]);
    expect(adapter.verify(decimalPacket).every((field) => field.ok)).toBe(true);
    const rows = document.querySelectorAll<HTMLElement>(".commodity");
    expect(rows[0].querySelector<HTMLInputElement>("input[name='weight']")?.value).toBe("512");
    expect(rows[0].querySelector<HTMLInputElement>("input[name='height']")?.value).toBe("65");
    expect(rows[1].querySelector<HTMLInputElement>("input[name='weight']")?.value).toBe("607");
    expect(clicked).not.toHaveBeenCalled();
  }, 15000);

  it("fills DSV with rounded values and remains idempotent", async () => {
    vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
    document.body.innerHTML = dsvFixture;
    wireDsvFixture();
    const getRates = document.querySelector<HTMLButtonElement>("#dsv-get-rates")!;
    const clicked = vi.fn();
    getRates.addEventListener("click", clicked);
    const adapter = new DsvAdapter();

    expect((await adapter.fill(decimalPacket)).every((field) => field.ok)).toBe(true);
    expect(adapter.verify(decimalPacket).every((field) => field.ok)).toBe(true);
    expect(document.querySelectorAll(".dsv-goods-row")).toHaveLength(2);
    const rows = document.querySelectorAll<HTMLElement>(".dsv-goods-row");
    expect(rows[0].querySelector<HTMLInputElement>("p-inputnumber[formcontrolname='weight'] input")?.value).toBe("512");
    expect(rows[0].querySelector<HTMLInputElement>("input[formcontrolname='height']")?.value).toBe("65");
    expect(rows[1].querySelector<HTMLInputElement>("p-inputnumber[formcontrolname='weight'] input")?.value).toBe("607");
    expect((await adapter.fill(decimalPacket)).every((field) => field.ok)).toBe(true);
    expect(document.querySelectorAll(".dsv-goods-row")).toHaveLength(2);
    expect(clicked).not.toHaveBeenCalled();
  }, 20000);

  it("fills MyUnishippers with whole-pound weights and whole-inch freight dimensions", async () => {
    vi.stubGlobal("location", { href: "https://www.myunishippers.com/ship/ltl", hostname: "www.myunishippers.com" });
    document.body.innerHTML = myunishippersFixture;
    wireMyUnishippersFixture();
    const getRates = Array.from(document.querySelectorAll<HTMLButtonElement>("button"))
      .find((button) => button.textContent?.includes("Get Rates"))!;
    const clicked = vi.fn();
    getRates.addEventListener("click", clicked);
    const adapter = new MyUnishippersAdapter();
    const onePallet = { ...decimalPacket, pallets: [decimalPacket.pallets[0]] };

    const fillResults = await adapter.fill(onePallet);
    expect(fillResults.filter((field) => !field.ok)).toEqual([]);
    expect(adapter.verify(onePallet).every((field) => field.ok)).toBe(true);
    expect(document.querySelector<HTMLInputElement>("input[formcontrolname='weight']")?.value).toBe("512");
    expect(document.querySelector<HTMLInputElement>("input[formcontrolname='height']")?.value).toBe("65");
    expect(clicked).not.toHaveBeenCalled();
  }, 15000);

  it("creates one verified MyUnishippers handling unit per pallet", async () => {
    vi.stubGlobal("location", { href: "https://www.myunishippers.com/ship/ltl", hostname: "www.myunishippers.com" });
    document.body.innerHTML = myunishippersFixture;
    wireMyUnishippersFixture();
    const adapter = new MyUnishippersAdapter();
    const originMismatchPacket = {
      ...decimalPacket,
      origin: { ...decimalPacket.origin, postal_code: "80022" },
    };

    const fillResults = await adapter.fill(originMismatchPacket);
    expect(fillResults.filter((field) => !field.ok)).toEqual([]);
    expect(document.querySelectorAll(".handling-unit")).toHaveLength(2);
    expect(adapter.verify(originMismatchPacket).every((field) => field.ok)).toBe(true);
    expect(document.querySelector<HTMLInputElement>(".origin-address mat-form-field input")?.value).toBe("80022");
    const rows = document.querySelectorAll<HTMLElement>(".handling-unit");
    expect(rows[0].querySelector<HTMLInputElement>("[formcontrolname='weight']")?.value).toBe("512");
    expect(rows[1].querySelector<HTMLInputElement>("[formcontrolname='weight']")?.value).toBe("607");
  }, 15000);
});

describe("live release regressions", () => {
  it("fills DSV from its local autocomplete when a ZIP has several city suggestions", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
      document.body.innerHTML = dsvFixture;
      document.getElementById("dsv-postal-overlay")?.remove();
      wireDsvFixture();
      const pickup = document.getElementById("dsv-pickup") as HTMLInputElement;
      const delivery = document.getElementById("dsv-delivery") as HTMLInputElement;
      pickup.dataset.noOptions = "true";
      delivery.dataset.noOptions = "true";
      addDsvLocalPostalOptions("dsv-pickup", [
        { text: "80022  (COMMERCE CITY, CO US)", zip: "80022" },
        { text: "80022  (ADAMS CITY, CO US)", zip: "80022" },
        { text: "80022  (DERBY, CO US)", zip: "80022" },
        { text: "80022  (ENO, CO US)", zip: "80022" },
        { text: "80022  (IRONDALE, CO US)", zip: "80022" },
        { text: "80022  (SAPP BROS DENVER, CO US)", zip: "80022" },
      ]);
      addDsvLocalPostalOptions("dsv-delivery", [
        { text: "59106  (BILLINGS, MT US)", zip: "59106" },
      ]);
      const livePacket: QuotePacket = {
        ...packet,
        origin: { ...packet.origin, city: "Commerce City", state: "CO", postal_code: "80022" },
        destination: { ...packet.destination, city: "Billings", state: "MT", postal_code: "59106" },
      };
      const adapter = new DsvAdapter();
      pickup.value = "80022";
      pickup.classList.add("ng-valid");
      const pendingMarker = document.createElement("span");
      pendingMarker.innerHTML = '<i class="pi pi-compass"></i> Postal Code: 80022';
      pickup.closest(".row")?.append(pendingMarker);
      expect(adapter.verify(livePacket).find(field => field.field === "pickup postal code verification")?.ok).toBe(false);
      pendingMarker.remove();
      pickup.value = "";
      pickup.classList.remove("ng-valid");

      const filling = adapter.fill(livePacket);
      await vi.runAllTimersAsync();
      const results = await filling;

      expect(results.filter(field => !field.ok)).toEqual([]);
      expect(adapter.verify(livePacket).filter(field => !field.ok)).toEqual([]);
      expect(pickup.value).toBe("80022");
      expect(delivery.value).toBe("59106");
      expect(document.querySelector<HTMLInputElement>("input[formcontrolname='length']")?.value).toBe("96");
      expect(document.querySelector<HTMLInputElement>("input[formcontrolname='description']")?.value).toBe("Cabinets");
      expect(document.querySelector<HTMLInputElement>("input[value='DELIVERY_RESIDENTIAL']")?.checked).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  }, 15000);

  it("uses DSV's body-level autocomplete panel and accepts a committed ZIP on retry", async () => {
    vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
    document.body.innerHTML = dsvFixture;
    wireDsvFixture();
    const adapter = new DsvAdapter();

    expect(document.getElementById("dsv-pickup")?.hasAttribute("aria-controls")).toBe(false);
    expect((await adapter.fill(packet)).every(field => field.ok)).toBe(true);
    expect(document.querySelector("#dsv-pickup-row .pi-compass")?.parentElement?.textContent).toContain("Postal Code: 80011");
    expect(document.getElementById("dsv-postal-overlay")).toBeNull();
    expect((await adapter.fill(packet)).every(field => field.ok)).toBe(true);
  }, 20000);

  it("accepts DSV's asynchronous carrier commit after the body panel is removed", async () => {
    vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
    document.body.innerHTML = dsvFixture;
    wireDsvFixture();
    const pickup = document.getElementById("dsv-pickup") as HTMLInputElement;
    pickup.dataset.noOptions = "true";
    pickup.addEventListener("input", () => {
      document.getElementById("dsv-postal-overlay")?.remove();
      setTimeout(() => {
        pickup.value = "80011 Aurora CO";
        pickup.classList.add("ng-valid");
        const marker = document.createElement("span");
        marker.innerHTML = '<i class="pi pi-compass"></i> Postal Code: 80011';
        pickup.closest(".row")?.append(marker);
      }, 25);
    });
    const result = await new DsvAdapter().fill(packet);
    expect(result.every(field => field.ok)).toBe(true);
  }, 20000);

  it("rejects DSV raw ZIP text, invalid state, and an open body panel", () => {
    vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
    document.body.innerHTML = dsvFixture;
    wireDsvFixture();
    const pickup = document.getElementById("dsv-pickup") as HTMLInputElement;
    const pickupResult = () => new DsvAdapter().verify(packet).find(field => field.field === "pickup postal code verification")?.ok;

    expect(pickupResult()).toBe(false);
    pickup.value = "80011 Aurora CO";
    pickup.classList.add("ng-valid");
    const marker = document.createElement("span");
    marker.innerHTML = '<i class="pi pi-compass"></i> Postal Code: 80011';
    pickup.closest(".row")?.append(marker);
    expect(pickupResult()).toBe(false);
    pickup.classList.add("ng-invalid");
    document.getElementById("dsv-postal-overlay")?.remove();
    expect(pickupResult()).toBe(false);
    pickup.classList.remove("ng-invalid");
    expect(pickupResult()).toBe(true);
  });

  it("does not fill downstream DSV fields when a postal control is unresolved", async () => {
    vi.stubGlobal("location", { href: "https://mytms.us.dsv.com/dsvweb/dashboard/quote", hostname: "mytms.us.dsv.com" });
    document.body.innerHTML = dsvFixture;
    wireDsvFixture();
    const pickup = document.getElementById("dsv-pickup") as HTMLInputElement;
    pickup.addEventListener("input", () => pickup.remove());

    const result = await new DsvAdapter().fill(packet);

    expect(result.find(field => field.field === "pickup postal code")?.ok).toBe(false);
    expect(document.querySelector<HTMLInputElement>("input[value='DELIVERY_RESIDENTIAL']")?.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>("input[formcontrolname='description']")?.value).toBe("");
  }, 20000);

  it("applies delivery services after asynchronous destination resolution clears them", async () => {
    document.body.innerHTML = myunishippersFixture + '<input type="checkbox" id="appointmentDelivery-input">';
    wireMyUnishippersFixture();
    document.getElementById("my-destination-postal")!.addEventListener("input", () => {
      const city = document.querySelector<HTMLInputElement>(".destination-address input[formcontrolname='city']")!;
      city.value = "";
      setTimeout(() => {
        city.value = "BELGRADE";
        (document.getElementById("appointmentDelivery-input") as HTMLInputElement).checked = false;
      }, 200);
    });
    const adapter = new MyUnishippersAdapter();
    const requested = { ...packet, accessorials: { ...packet.accessorials, appointment_delivery: true } };
    expect((await adapter.fill(requested)).filter(field => !field.ok)).toEqual([]);
    expect(adapter.verify(requested).filter(field => !field.ok)).toEqual([]);
  });

  it("dismisses reopened TQL favorites without selecting an ambiguous saved address", async () => {
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const pickup = document.getElementById("pickup-postal") as HTMLInputElement;
    pickup.value = "80011 - Aurora, CO, USA";
    pickup.setAttribute("aria-expanded", "true");
    pickup.addEventListener("keydown", event => {
      if (event.key === "Escape") pickup.setAttribute("aria-expanded", "false");
    });
    const adapter = new TqlAdapter();
    expect((await adapter.fill(packet)).filter(field => !field.ok)).toEqual([]);
    expect(pickup.value).toBe("80011 - Aurora, CO, USA");
    expect(pickup.getAttribute("aria-expanded")).toBe("false");
    expect(adapter.verify(packet).filter(field => !field.ok)).toEqual([]);
  }, 15000);

  it("selects TQL's active Postal Code option from Kendo address and Favorites results", async () => {
    document.body.innerHTML = tqlFixture;
    const deliveryList = document.getElementById("delivery-list")!;
    deliveryList.innerHTML = `
      <li id="delivery-postal-option" role="option" aria-selected="false" class="k-list-item k-focus">
        <div><span>59106 - Billings, MT, USA</span><div><div role="button" class="k-chip k-chip-solid-success"><span class="k-chip-content">Postal Code</span></div></div></div>
      </li>
      <li id="delivery-address-option" role="option">
        <div><span>Shipping Office 59106 - Billings, MT, USA</span><div><div role="button" class="k-chip"><span class="k-chip-content">Saved Address</span></div></div></div>
      </li>
      <li id="delivery-favorite-option" role="option">
        <div><span>80022 - Commerce City, CO, USA</span><div><div role="button" class="k-chip"><span class="k-chip-content">Favorites</span></div></div></div>
      </li>`;
    const delivery = document.getElementById("delivery-postal") as HTMLInputElement;
    delivery.value = "59106";
    delivery.setAttribute("aria-expanded", "true");
    delivery.setAttribute("aria-activedescendant", "delivery-postal-option");
    wireTqlFixture();
    const livePacket: QuotePacket = {
      ...packet,
      destination: { ...packet.destination, city: "Billings", state: "MT", postal_code: "59106" },
    };
    const adapter = new TqlAdapter();

    const result = await adapter.fill(livePacket);

    expect(result.filter(field => !field.ok)).toEqual([]);
    expect(delivery.value).toContain("59106 - Billings, MT, USA");
    expect(delivery.value).toContain("Postal Code");
    expect(adapter.verify(livePacket).filter(field => !field.ok)).toEqual([]);
  }, 15000);

  it("verifies TQL's carrier model when Kendo renders its native blank numeric editors", async () => {
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const adapter = new TqlAdapter();
    const height = document.querySelector<HTMLInputElement>("input[name='height']")!;
    height.addEventListener("blur", () => {
      setTimeout(() => {
        document.querySelectorAll<HTMLInputElement>(
          "input[name='quantity'], input[name='weight'], input[name='length'], input[name='width'], input[name='height']",
        ).forEach(control => {
          const replacement = control.cloneNode(true) as HTMLInputElement;
          replacement.value = "";
          control.replaceWith(replacement);
        });
      }, 200);
    }, { once: true });

    const result = await adapter.fill(packet);

    expect(result.find(field => field.field === "numeric values persisted")?.ok).toBe(true);
    expect(result.find(field => field.field === "pallet 1 dimensions manual review required")?.ok).toBe(false);
    expect(Array.from(document.querySelectorAll<HTMLInputElement>(
      "input[name='quantity'], input[name='weight'], input[name='length'], input[name='width'], input[name='height']",
    )).every(control => control.value === "")).toBe(true);
    expect(document.querySelector("[data-summary-field='total-unit-weight'] .item-summary-value")?.textContent).toBe("1500.00");
    expect(adapter.verify(packet).filter(field => !field.ok).map(field => field.field))
      .toEqual(["pallet 1 dimensions manual review required"]);
  }, 15000);

  it("verifies TQL model summaries after repeated late Kendo control replacements", async () => {
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const adapter = new TqlAdapter();
    let clears = 0;
    const clearCurrentEditors = () => {
      clears += 1;
      document.querySelectorAll<HTMLInputElement>(
        "input[name='quantity'], input[name='weight'], input[name='length'], input[name='width'], input[name='height']",
      ).forEach(control => {
        const replacement = control.cloneNode(true) as HTMLInputElement;
        replacement.value = "";
        control.replaceWith(replacement);
      });
    };
    const replaceClearedEditors = (event: Event) => {
      const control = event.target;
      if (!(control instanceof HTMLInputElement) || control.name !== "height" || clears || scheduled) return;
      scheduled = true;
      // The first replacement lands inside the verification window and restarts its quiet period;
      // the second comes after the fill, where the content script's durability monitor verifies.
      setTimeout(clearCurrentEditors, 700);
      setTimeout(clearCurrentEditors, 2600);
    };
    let scheduled = false;
    document.addEventListener("blur", replaceClearedEditors, true);
    documentListenerCleanups.push(() => document.removeEventListener("blur", replaceClearedEditors, true));

    const started = Date.now();
    const results = await adapter.fill(packet);

    expect(results.filter(field => !field.ok).map(field => field.field))
      .toEqual(["pallet 1 dimensions manual review required"]);
    expect(results.find(field => field.field.endsWith("manual review required"))?.manual).toBe("check");
    // No fixed 5.2 s window any more: the fill returns once the model held and TRAX went quiet.
    expect(clears).toBe(1);
    expect(Date.now() - started).toBeLessThan(5000);
    await vi.waitFor(() => expect(clears).toBe(2), { timeout: 5000, interval: 50 });
    expect(adapter.verify(packet).filter(field => !field.ok).map(field => field.field))
      .toEqual(["pallet 1 dimensions manual review required"]);
    expect(Array.from(document.querySelectorAll<HTMLInputElement>(
      "input[name='quantity'], input[name='weight'], input[name='length'], input[name='width'], input[name='height']",
    )).every(control => control.value === "")).toBe(true);
    expect(document.querySelector("[data-summary-field='calculated-linear-feet'] .item-summary-value")?.textContent).toBe("8.00");
  }, 30000);

  it("fails closed when replaced TQL numeric controls reject restored values", async () => {
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const adapter = new TqlAdapter();
    let rejectRestores = false;
    const rejectRestoredInput = (event: Event) => {
      const control = event.target;
      if (rejectRestores && control instanceof HTMLInputElement
        && ["quantity", "weight", "length", "width", "height"].includes(control.name)) {
        control.value = "";
      }
    };
    document.addEventListener("input", rejectRestoredInput, true);
    documentListenerCleanups.push(() => document.removeEventListener("input", rejectRestoredInput, true));
    const height = document.querySelector<HTMLInputElement>("input[name='height']")!;
    const invalidateSummaryAfterReplacement = () => {
      setTimeout(() => {
        document.querySelectorAll<HTMLInputElement>(
          "input[name='quantity'], input[name='weight'], input[name='length'], input[name='width'], input[name='height']",
        ).forEach(control => {
          const replacement = control.cloneNode(true) as HTMLInputElement;
          replacement.value = "";
          control.replaceWith(replacement);
        });
        rejectRestores = true;
        const totalWeight = document.querySelector<HTMLElement>("[data-summary-field='total-unit-weight'] .item-summary-value");
        if (totalWeight) totalWeight.textContent = "0.00";
        const density = document.querySelector<HTMLElement>("[data-testid^='input-field-group-density-'] .display-field");
        if (density) density.textContent = "0.00";
        // Inside the shortened verification window (it was 1.8 s with the fixed 5.2 s window).
      }, 300);
    };
    height.addEventListener("blur", invalidateSummaryAfterReplacement, { once: true });
    documentListenerCleanups.push(() => height.removeEventListener("blur", invalidateSummaryAfterReplacement));

    const results = await adapter.fill(packet);

    expect(results.find(field => field.field === "numeric values persisted")?.ok).toBe(false);
    expect(adapter.verify(packet).filter(field => /pallet 1 (quantity|weight|length|width|height) verification/.test(field.field)))
      .toHaveLength(2);
  }, 20000);

  it("verifies a TQL replacement control containing a committed location, but rejects raw ZIP text", async () => {
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const adapter = new TqlAdapter();
    await adapter.fill(packet);
    const old = document.getElementById("pickup-postal") as HTMLInputElement;
    const replacement = old.cloneNode(true) as HTMLInputElement;
    replacement.value = "80011 - Aurora, CO, USA";
    replacement.setAttribute("aria-expanded", "true");
    old.replaceWith(replacement);
    expect(adapter.verify(packet).find(field => field.field === "pickup postal code verification")?.ok).toBe(true);
    replacement.setAttribute("aria-invalid", "true");
    expect(adapter.verify(packet).find(field => field.field === "pickup postal code verification")?.ok).toBe(false);
    replacement.removeAttribute("aria-invalid");
    replacement.value = "80011";
    expect(adapter.verify(packet).find(field => field.field === "pickup postal code verification")?.ok).toBe(false);
  }, 15000);
  it("applies and verifies appointment and call-ahead, including manual changes", async () => {
    document.body.innerHTML = myunishippersFixture + '<input type="checkbox" id="appointmentDelivery-input"><input type="checkbox" id="notifyBeforeDelivery-input">';
    wireMyUnishippersFixture();
    const adapter = new MyUnishippersAdapter();
    const requested = { ...packet, accessorials: { ...packet.accessorials, appointment_delivery: true, delivery_call_ahead: true } };
    expect((await adapter.fill(requested)).filter(field => !field.ok)).toEqual([]);
    expect(adapter.verify(requested).filter(field => !field.ok)).toEqual([]);
    (document.getElementById("appointmentDelivery-input") as HTMLInputElement).checked = false;
    expect(adapter.verify(requested).find(field => field.field === "appointment_delivery")?.ok).toBe(false);
    document.getElementById("notifyBeforeDelivery-input")!.remove();
    expect(adapter.verify(requested).find(field => field.field === "delivery_call_ahead")?.ok).toBe(false);
  });

  it("does not use the origin input when Echo destination is missing", () => {
    document.body.innerHTML = '<div id="ltl-quote-origin-entry"><input placeholder="Search by ZIP or Saved Location"></div>';
    expect(new EchoAdapter().discoverFields().find(field => field.field === "destination postal code")?.ok).toBe(false);
  });
});

describe("TQL asynchronous handling units", () => {
  it("creates missing pallet rows once and remains idempotent on retry", async () => {
    vi.stubGlobal("location", { href: "https://trax.tqlapp.com/quote-details", hostname: "trax.tqlapp.com" });
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const adapter = new TqlAdapter();
    const secondPacket: QuotePacket = {
      ...packet,
      pallets: [
        { ...packet.pallets[0], weight: 1500.49, height: 44.2 },
        { ...packet.pallets[0], pallet: 2, freight_class: "92.5", weight: 900.6, height: 44.8 },
      ],
    };

    const initialFill = await adapter.fill(secondPacket);
    expect(initialFill.filter(field => !field.ok), `initial fill failures: ${JSON.stringify(initialFill.filter(field => !field.ok))}`)
      .toEqual([]);
    expect(document.querySelectorAll("#add-item .commodity-row")).toHaveLength(2);
    expect(adapter.verify(secondPacket).every((field) => field.ok)).toBe(true);
    expect(document.querySelector<HTMLInputElement>("#shipmentDate")?.value).toBe("8/26/2026");
    expect(document.querySelector<HTMLInputElement>("#delivery-residential")?.checked).toBe(true);
    expect(document.querySelector<HTMLInputElement>("#delivery-liftgate")?.checked).toBe(true);
    const rows = document.querySelectorAll<HTMLElement>("#add-item .commodity-row");
    expect(rows[0].querySelector<HTMLInputElement>("input[name='weight']")?.value).toBe("1500");
    expect(rows[0].querySelector<HTMLInputElement>("input[name='height']")?.value).toBe("45");
    expect(rows[1].querySelector<HTMLInputElement>("input[name='weight']")?.value).toBe("901");
    expect(rows[1].querySelector<HTMLInputElement>("input[name='height']")?.value).toBe("45");

    const retryFill = await adapter.fill(secondPacket);
    expect(retryFill.filter(field => !field.ok), `retry fill failures: ${JSON.stringify(retryFill.filter(field => !field.ok))}`)
      .toEqual([]);
    expect(document.querySelectorAll("#add-item .commodity-row")).toHaveLength(2);
    expect(adapter.verify(secondPacket).every((field) => field.ok)).toBe(true);
  }, 30000);

  it("fails closed when a required TQL control changes", async () => {
    vi.stubGlobal("location", { href: "https://trax.tqlapp.com/quote-details", hostname: "trax.tqlapp.com" });
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    document.querySelector("[id^='input-class-']")?.remove();
    const getQuote = document.querySelector<HTMLButtonElement>("#get-quote")!;
    const clicked = vi.fn();
    getQuote.addEventListener("click", clicked);
    const adapter = new TqlAdapter();

    expect(adapter.discoverFields().some((field) => !field.ok)).toBe(true);
    expect((await adapter.fill(packet)).some((field) => !field.ok)).toBe(true);
    expect(clicked).not.toHaveBeenCalled();
  }, 15000);
});

describe("ODFL fill-only adapter", () => {
  beforeEach(() => {
    vi.stubGlobal("location", {
      href: "https://www.odfl.com/us/en/tools/freight-shipping-rate-estimate/ltl-rate-estimate.html",
      hostname: "www.odfl.com",
    });
    document.body.innerHTML = odflFixture;
    wireOdflFixture();
  });

  it("requires a manually selected account and detects only the exact rate-estimate page", async () => {
    const adapter = new OdflAdapter();
    expect(adapter.detect()).toBe(true);
    document.querySelector(".js-account-section")!.innerHTML = "<button type='button'>Select Account</button>";
    expect(adapter.manualPrerequisite().ok).toBe(false);
    expect((await adapter.fill(packet))).toEqual([expect.objectContaining({ field: "ODFL account selection", ok: false })]);
    vi.stubGlobal("location", { href: "https://www.odfl.com/us/en/tools/freight-shipping-rate-estimate/", hostname: "www.odfl.com" });
    expect(adapter.detect()).toBe(false);
  });

  it("waits for ODFL's delayed carrier commit when the Places popup is absent", async () => {
    const adapter = new OdflAdapter();
    document.querySelector<HTMLInputElement>("#ltl_origin_address")!.value = "80011 Aurora, CO, USA";
    document.querySelector<HTMLInputElement>("#ltl_originZipcode")!.value = "80011";
    const originCity = document.querySelector<HTMLSelectElement>("#ltl_origin_city")!;
    originCity.innerHTML = "<option value='AURORA'>Aurora</option>";
    originCity.value = "AURORA";
    document.querySelector<HTMLInputElement>("#ltl_originState")!.value = "CO";
    document.querySelector<HTMLSelectElement>("#ltl_origin_country")!.value = "US";
    const destination = document.querySelector<HTMLInputElement>("#ltl_destination_address")!;
    destination.dataset.noSuggestions = "true";
    destination.dataset.commitDelayMs = "100";
    destination.dataset.requestSubmit = "true";
    const submit = vi.fn();
    document.querySelector("form")!.addEventListener("submit", submit);

    expect((await adapter.fill(packet)).filter((field) => !field.ok)).toEqual([]);
    expect(adapter.verify(packet).filter((field) => !field.ok)).toEqual([]);
    expect(document.querySelectorAll(".pac-container, .pac-item")).toHaveLength(0);
    expect(document.querySelector<HTMLInputElement>("#ltl_destZipcode")?.value).toBe("59714");
    expect(document.querySelector<HTMLSelectElement>("#ltl_destination_city")?.selectedOptions[0]?.textContent).toBe("Belgrade");
    expect(document.querySelector<HTMLInputElement>("#ltl_destState")?.value).toBe("MT");
    expect(document.querySelector<HTMLSelectElement>("#ltl_destination_country")?.value).toBe("US");
    expect(submit).not.toHaveBeenCalled();
  }, 15000);

  it("fails clearly and leaves all downstream fields untouched when ODFL never commits the ZIP", async () => {
    const adapter = new OdflAdapter();
    document.querySelector<HTMLInputElement>("#ltl_origin_address")!.value = "80011 Aurora, CO, USA";
    document.querySelector<HTMLInputElement>("#ltl_originZipcode")!.value = "80011";
    const originCity = document.querySelector<HTMLSelectElement>("#ltl_origin_city")!;
    originCity.innerHTML = "<option value='AURORA'>Aurora</option>";
    originCity.value = "AURORA";
    document.querySelector<HTMLInputElement>("#ltl_originState")!.value = "CO";
    document.querySelector<HTMLSelectElement>("#ltl_origin_country")!.value = "US";
    const destination = document.querySelector<HTMLInputElement>("#ltl_destination_address")!;
    destination.dataset.noSuggestions = "true";
    destination.dataset.neverCommits = "true";
    destination.dataset.requestSubmit = "true";
    const submit = vi.fn();
    document.querySelector("form")!.addEventListener("submit", submit);

    vi.useFakeTimers();
    try {
      const failed = expect(adapter.fill(packet)).rejects.toThrow(/ODFL could not confirm the destination ZIP/i);
      await vi.advanceTimersByTimeAsync(36000);
      await failed;
    } finally {
      vi.useRealTimers();
    }
    expect(document.querySelector<HTMLInputElement>("#ltl_destination_address")?.value).toBe("59714");
    expect(document.querySelector<HTMLInputElement>("#ltl_destZipcode")?.value).toBe("");
    expect(document.querySelector<HTMLSelectElement>("#ltl_destination_city")?.selectedIndex).toBe(0);
    expect(document.querySelector<HTMLInputElement>("#ltl_weight0")?.value).toBe("");
    expect(document.querySelector<HTMLInputElement>("#item-9")?.checked).toBe(false);
    expect(submit).not.toHaveBeenCalled();
  });

  it("fills committed locations, outbound pallets, and four delivery services idempotently without restricted actions", async () => {
    const adapter = new OdflAdapter();
    const selectedAccountText = document.querySelector(".js-selected-account")?.textContent;
    const submit = document.getElementById("form-button-1624145924") as HTMLButtonElement;
    const overlength = document.querySelector<HTMLInputElement>("input[value='OVL']")!;
    const submitClick = vi.fn(); const overlengthClick = vi.fn();
    submit.addEventListener("click", submitClick);
    overlength.addEventListener("click", overlengthClick);
    const twoPallets: QuotePacket = {
      ...packet,
      origin: { ...packet.origin, postal_code: "80022" },
      destination: { ...packet.destination, postal_code: "59106" },
      accessorials: { residential: true, liftgate: true, appointment_delivery: true, delivery_call_ahead: true },
      pallets: [
        { ...packet.pallets[0], freight_class: "100", weight: 512.4, height: 44.2 },
        { ...packet.pallets[0], pallet: 2, freight_class: "92.5", weight: 607.6, height: 55.1 },
      ],
    };

    expect((await adapter.fill(twoPallets)).filter((field) => !field.ok)).toEqual([]);
    expect(adapter.verify(twoPallets).filter((field) => !field.ok)).toEqual([]);
    expect(document.querySelectorAll(".odfl-commodity-row")).toHaveLength(2);
    expect(document.querySelectorAll(".odfl-dimension-row")).toHaveLength(2);
    expect(document.querySelector<HTMLInputElement>("#ltl_weight0")?.value).toBe("512");
    expect(document.querySelector<HTMLInputElement>("#ltl_height1")?.value).toBe("56");
    expect(document.querySelector<HTMLInputElement>("#ltl_unitCount1")?.value).toBe("1");
    expect(document.querySelector<HTMLInputElement>("#ltl_nmfc0")?.value).toBe("12345");
    expect(document.querySelector<HTMLInputElement>("#ltl_sub0")?.value).toBe("stale");
    expect(document.querySelector<HTMLInputElement>("#item-13")?.checked).toBe(true);
    expect(document.querySelector<HTMLInputElement>("#item-12")?.checked).toBe(false);
    expect(document.querySelector(".js-selected-account")?.textContent).toBe(selectedAccountText);
    expect((await adapter.fill(twoPallets)).filter((field) => !field.ok)).toEqual([]);
    expect(document.querySelectorAll(".odfl-commodity-row")).toHaveLength(2);
    expect(submitClick).not.toHaveBeenCalled();
    expect(overlengthClick).not.toHaveBeenCalled();
    expect(document.querySelector<HTMLInputElement>("input[name='overlengthradio']:checked")).toBeNull();
  }, 30000);

  it("fails closed on uncommitted locations and excess rows while ignoring ODFL overlength controls", async () => {
    const adapter = new OdflAdapter();
    document.getElementById("ltl_originZipcode")!.remove();
    await expect(adapter.fill(packet)).rejects.toThrow(/ODFL could not confirm the origin ZIP/i);
    expect(document.querySelector<HTMLInputElement>("input[value='RDC']")?.checked).toBe(false);

    document.body.innerHTML = odflFixture;
    wireOdflFixture();
    const overlengthPacket = { ...packet, pallets: [{ ...packet.pallets[0], length: 97 }] };
    expect((await adapter.fill(overlengthPacket)).filter((field) => !field.ok)).toEqual([]);
    expect(adapter.verify(overlengthPacket).filter((field) => !field.ok)).toEqual([]);
    expect(document.querySelector<HTMLInputElement>("input[value='OVL']")?.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>("input[name='overlengthradio']:checked")).toBeNull();

    document.body.innerHTML = odflFixture;
    wireOdflFixture();
    document.querySelector<HTMLButtonElement>(".js-addwtclass-fields")!.click();
    const excess = await adapter.fill(packet);
    expect(excess.find((field) => field.field === "commodity row count")?.ok).toBe(false);
  }, 30000);
});

describe("delivery services and new accessorials", () => {
  /** EchoShip renders appointment and call-ahead only inside its "Delivery Accessorials" panel. */
  function wireEchoAccessorialPanel() {
    let clicks = 0;
    document.querySelector("#da-dropdown")!.addEventListener("click", () => {
      clicks += 1;
      const open = document.getElementById("ltl-quote-destination-appointment-required");
      if (open) {
        open.remove();
        document.getElementById("ltl-quote-destination-notify-prior-to-delivery")?.remove();
        return;
      }
      const panel = document.createElement("div");
      panel.innerHTML = `
        <div id="ltl-quote-destination-appointment-required"><label>Appointment Required<input type="checkbox"></label></div>
        <div id="ltl-quote-destination-notify-prior-to-delivery"><label>Notify Prior to Delivery<input type="checkbox"></label></div>`;
      document.querySelector("#da-dropdown")!.after(...Array.from(panel.children));
    });
    return () => clicks;
  }

  it("opens EchoShip's accessorial panel for a requested appointment without residential or liftgate", async () => {
    vi.stubGlobal("location", { href: "https://echoship.com/new-quote/ltl", hostname: "echoship.com" });
    document.body.innerHTML = echoFixture;
    wireEchoFixture();
    const clicks = wireEchoAccessorialPanel();
    const appointmentOnly: QuotePacket = {
      ...packet,
      accessorials: { residential: false, liftgate: false, appointment_delivery: true, delivery_call_ahead: false },
    };
    const adapter = new EchoAdapter();

    expect((await adapter.fill(appointmentOnly)).filter(field => !field.ok)).toEqual([]);
    expect(clicks()).toBe(1);
    expect(document.querySelector<HTMLInputElement>("#ltl-quote-destination-appointment-required input")?.checked).toBe(true);
    expect(adapter.verify(appointmentOnly).filter(field => !field.ok)).toEqual([]);
    // A retry with the panel already open must not toggle it closed.
    expect((await adapter.fill(appointmentOnly)).filter(field => !field.ok)).toEqual([]);
    expect(clicks()).toBe(1);
    expect(adapter.verify(appointmentOnly).filter(field => !field.ok)).toEqual([]);
  }, 15000);

  it("leaves EchoShip's location type for a manual Limited access choice", async () => {
    vi.stubGlobal("location", { href: "https://echoship.com/new-quote/ltl", hostname: "echoship.com" });
    document.body.innerHTML = echoFixture;
    wireEchoFixture();
    const locationType = document.querySelector<HTMLSelectElement>("#locationType")!;
    locationType.value = "2";
    const limited: QuotePacket = {
      ...packet,
      accessorials: { residential: false, liftgate: true, limited_access: true, inside_delivery: true },
    };
    const adapter = new EchoAdapter();

    const results = await adapter.fill(limited);
    expect(results.filter(field => !field.ok)).toEqual([]);
    expect(results.find(field => field.field === "destination location type")?.message).toMatch(/Limited access/);
    expect(locationType.value).toBe("2");
    expect(adapter.verify(limited).filter(field => !field.ok)).toEqual([]);
    // No adapter claims the new services, so the content script reports them as notes.
    expect(adapter.supportedAccessorials).toBeUndefined();
  }, 15000);

  it("leaves TQL's location type for a manual Limited access choice", async () => {
    vi.stubGlobal("location", { href: "https://trax.tqlapp.com/quote-details", hostname: "trax.tqlapp.com" });
    document.body.innerHTML = tqlFixture;
    wireTqlFixture();
    const limited: QuotePacket = { ...packet, accessorials: { residential: false, liftgate: false, limited_access: true } };
    const adapter = new TqlAdapter();

    const results = await adapter.fill(limited);
    expect(blockingFailures(results)).toEqual([]);
    expect(document.querySelector<HTMLInputElement>("#delivery-commercial")?.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>("#delivery-residential")?.checked).toBe(false);
    expect(blockingFailures(adapter.verify(limited))).toEqual([]);
  }, 15000);
});
