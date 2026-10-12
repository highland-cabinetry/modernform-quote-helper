import { waitFor } from "./timing";
import type { FieldResult, PalletRow } from "./types";

export function clean(value: unknown): string { return String(value ?? "").replace(/\s+/g, " ").trim(); }

export function visible(element: Element | null): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  const style = getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}

export function inputByNames(names: string[]): HTMLInputElement | HTMLSelectElement | null {
  const lowered = names.map((name) => name.toLowerCase());
  const controls = Array.from(document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select"));
  for (const control of controls) {
    if (!visible(control)) continue;
    const label = control.labels ? Array.from(control.labels).map((item) => item.textContent).join(" ") : "";
    const fieldText = clean(control.closest("mat-form-field, [class*='form-field'], [class*='field']")?.textContent);
    const text = clean([label, fieldText, control.name, control.id, control.getAttribute("formcontrolname"), control.getAttribute("placeholder"), control.getAttribute("aria-label")].join(" ")).toLowerCase();
    if (lowered.some((name) => text.includes(name))) return control;
  }
  return null;
}

export function setValue(control: HTMLInputElement | HTMLSelectElement | null, value: string | number): boolean {
  if (!control) return false;
  const next = String(value ?? "");
  if (control instanceof HTMLSelectElement) {
    const option = Array.from(control.options).find((item) => clean(item.text).toLowerCase().includes(next.toLowerCase()) || item.value === next);
    if (!option) return false;
    control.value = option.value;
  } else {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(control, next);
  }
  for (const type of ["input", "change", "blur"]) control.dispatchEvent(new Event(type, { bubbles: true }));
  if (control instanceof HTMLSelectElement) {
    const selected = clean(control.selectedOptions[0]?.textContent).toLowerCase();
    return control.value === next || selected.includes(clean(next).toLowerCase());
  }
  return clean(control.value).toLowerCase().includes(clean(next).toLowerCase());
}

export function fieldResult(field: string, ok: boolean, message = ""): FieldResult {
  return { field, ok, message: message || (ok ? "Filled and verified" : "Field not found or rejected") };
}

export function clickAddItem(): boolean {
  const button = Array.from(document.querySelectorAll<HTMLElement>("button, [role='button']")).find((item) => {
    const text = clean(item.textContent).toLowerCase();
    return visible(item) && /add (?:handling unit|item|commodity|goods)/.test(text);
  });
  button?.click();
  return Boolean(button);
}

export function candidateItemRoots(): HTMLElement[] {
  const roots = Array.from(document.querySelectorAll<HTMLElement>("tql-card, form, tr, .card, [class*='commodity'], [class*='handling'], [class*='goods']"))
    .filter((root) => visible(root) && /weight|dimensions|length|commodity/i.test(clean(root.textContent)));
  return roots.filter((root) => !roots.some((other) => (
    other !== root && root.contains(other) && other.querySelectorAll("input, select").length >= 4
  )));
}

export function controlIn(root: HTMLElement, names: string[]): HTMLInputElement | HTMLSelectElement | null {
  const lowered = names.map((name) => name.toLowerCase());
  for (const control of Array.from(root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("input, select"))) {
    const label = control.labels ? Array.from(control.labels).map((item) => item.textContent).join(" ") : "";
    const fieldText = clean(control.closest("mat-form-field, [class*='form-field'], [class*='field']")?.textContent);
    const text = clean([label, fieldText, control.name, control.id, control.getAttribute("formcontrolname"), control.getAttribute("placeholder"), control.getAttribute("aria-label")].join(" ")).toLowerCase();
    if (lowered.some((name) => text.includes(name))) return control;
  }
  return null;
}

export function checkboxByNames(names: string[], root: ParentNode = document): HTMLInputElement | null {
  const lowered = names.map((name) => name.toLowerCase());
  for (const control of Array.from(root.querySelectorAll<HTMLInputElement>("input[type='checkbox'], input[type='radio']"))) {
    const label = control.labels ? Array.from(control.labels).map((item) => item.textContent).join(" ") : "";
    const parentText = clean(control.closest("label, mat-checkbox, [class*='checkbox']")?.textContent);
    const text = clean([label, parentText, control.name, control.id, control.getAttribute("aria-label")].join(" ")).toLowerCase();
    if (lowered.some((name) => text.includes(name))) return control;
  }
  return null;
}

export function setChecked(control: HTMLInputElement | null, checked: boolean): boolean {
  if (!control) return false;
  if (control.checked !== checked) control.click();
  // Native checkbox activation already emits input/change. Repeating change
  // can toggle framework-backed services back off after they were selected.
  return control.checked === checked;
}

export async function setChoiceIn(root: ParentNode, names: string[], value: string): Promise<boolean> {
  const native = controlIn(root as HTMLElement, names);
  if (native && setValue(native, value)) return true;
  const lowered = names.map((name) => name.toLowerCase());
  const trigger = Array.from(root.querySelectorAll<HTMLElement>("button, [role='combobox']")).find((element) => {
    const labelled = element.getAttribute("aria-label") || element.getAttribute("name") || element.getAttribute("formcontrolname") || element.id;
    const parentText = clean(element.closest("label, [class*='field'], [class*='select']")?.textContent);
    const text = clean(`${labelled} ${parentText}`).toLowerCase();
    return visible(element) && lowered.some((name) => text.includes(name));
  });
  if (!trigger) return false;
  trigger.click();
  let option: HTMLElement | undefined;
  // Wait for the option list the click opens instead of a fixed 200 ms pause.
  await waitFor(() => {
    option = Array.from(document.querySelectorAll<HTMLElement>("[role='option'], option, li, [class*='option']")).find((element) => (
      visible(element) && clean(element.textContent).toLowerCase().includes(value.toLowerCase())
    ));
    return Boolean(option);
  }, { timeoutMs: 1500 });
  option?.click();
  return Boolean(option);
}

export function choiceTextIn(root: ParentNode, names: string[]): string {
  const native = controlIn(root as HTMLElement, names);
  if (native) return clean(
    native.value || (native instanceof HTMLSelectElement ? native.selectedOptions[0]?.textContent : ""),
  );
  const lowered = names.map((name) => name.toLowerCase());
  const choice = Array.from(root.querySelectorAll<HTMLElement>("[role='combobox'], mat-select")).find((element) => {
    const labelled = clean([
      element.getAttribute("aria-label"), element.getAttribute("name"), element.getAttribute("formcontrolname"),
      element.id, element.closest("mat-form-field, [class*='field']")?.textContent,
    ].join(" ")).toLowerCase();
    return lowered.some((name) => labelled.includes(name));
  });
  return clean(choice?.textContent);
}

export async function selectVisibleOption(value: string): Promise<boolean> {
  let option: HTMLElement | undefined;
  await waitFor(() => {
    option = Array.from(document.querySelectorAll<HTMLElement>("[role='option'], li, [class*='option']")).find((element) => (
      visible(element) && clean(element.textContent).includes(value)
    ));
    return Boolean(option);
  }, { timeoutMs: 1500 });
  option?.click();
  return Boolean(option);
}

export async function fillPalletRows(pallets: PalletRow[], description: string): Promise<FieldResult[]> {
  let roots = candidateItemRoots();
  while (roots.length < pallets.length) {
    const before = roots.length;
    if (!clickAddItem()) break;
    // Wait for the new row (not a fixed 500 ms); stop adding when the carrier does not create one.
    if (!await waitFor(() => candidateItemRoots().length > before, { timeoutMs: 3000 })) break;
    roots = candidateItemRoots();
  }
  const results: FieldResult[] = [];
  pallets.forEach((pallet, index) => {
    const root = roots[index];
    if (!root) { results.push(fieldResult(`pallet ${index + 1}`, false, "Handling-unit row was not created")); return; }
    const values: Array<[string, HTMLInputElement | HTMLSelectElement | null, string | number]> = [
      ["quantity", controlIn(root, ["quantity", "qty", "number of units"]), 1],
      ["weight", controlIn(root, ["total weight", "weight"]), Math.round(pallet.weight)],
      ["length", controlIn(root, ["length"]), pallet.length],
      ["width", controlIn(root, ["width"]), pallet.width],
      ["height", controlIn(root, ["height"]), pallet.height],
      ["description", controlIn(root, ["description", "commodity"]), description],
    ];
    for (const [name, control, value] of values) results.push(fieldResult(`pallet ${index + 1} ${name}`, setValue(control, value)));
  });
  return results;
}
