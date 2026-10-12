import { clean, visible } from "./dom";
import { waitFor } from "./timing";
import type { AddressInfo } from "./types";

const committed = new WeakMap<HTMLInputElement, { zip: string; value: string }>();
const generations = new WeakMap<HTMLInputElement, number>();
const zipPattern = (zip: string) => new RegExp(`^\\s*${zip.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")}(?:-\\d{4})?\\b`);

export function containsExactZip(value: string | null | undefined, zip: string): boolean {
  return new RegExp(`(?:^|\\D)${zip}(?:-\\d{4})?(?:\\D|$)`).test(clean(value));
}

export function matchesPostalAddress(candidate: HTMLElement, address: AddressInfo): boolean {
  const text = clean(candidate.textContent).toLowerCase();
  const token = (value: string) => new RegExp(`(?:^|[^a-z])${clean(value).toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:[^a-z]|$)`);
  return containsExactZip(text, address.postal_code)
    && (!address.city || token(address.city).test(text))
    && (!address.state || token(address.state).test(text));
}

export function looksResolved(control: HTMLInputElement | null, zip: string): boolean {
  if (!control || !containsExactZip(control.value, zip)) return false;
  const record = committed.get(control);
  return record?.zip === zip && record.value === control.value;
}

function nativeCommit(control: HTMLInputElement, value: string, clearFirst = true): void {
  control.focus();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  if (clearFirst) {
    setter?.call(control, "");
    control.dispatchEvent(new InputEvent("input", { bubbles: true, data: null, inputType: "deleteContentBackward" }));
  }
  setter?.call(control, value);
  control.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: "insertText" }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function associatedOptions(control: HTMLInputElement, optionSelector: string): HTMLElement[] {
  const popupId = control.getAttribute("aria-controls") || control.getAttribute("aria-owns");
  const root = popupId ? document.getElementById(popupId) : null;
  return Array.from((root || control.closest("p-autocomplete, [data-postal-control]"))?.querySelectorAll<HTMLElement>(optionSelector) || []).filter(visible);
}

export async function selectExactPostalCode(options: {
  getControl: () => HTMLInputElement | null;
  zip: string;
  optionSelector?: string;
  getOptions?: (control: HTMLInputElement) => HTMLElement[];
  matchesOption?: (candidate: HTMLElement) => boolean;
  keyboardLookup?: boolean;
  clearFirst?: boolean;
  verifyResolved?: () => boolean;
  verifySelection?: () => boolean;
  requireCarrierVerification?: boolean;
  activeDescendantIsFresh?: boolean;
  timeoutMs?: number;
}): Promise<boolean> {
  const selector = options.optionSelector || "[role='option'], .p-autocomplete-item";
  const currentOptions = (input: HTMLInputElement) => options.getOptions
    ? options.getOptions(input).filter(visible) : associatedOptions(input, selector);
  const deadline = Date.now() + (options.timeoutMs ?? 7000);
  let control = options.getControl();
  if (!control) {
    await waitFor(() => Boolean(options.getControl()), { timeoutMs: Math.max(0, deadline - Date.now()) });
    control = options.getControl();
  }
  if (!control) return false;
  if (options.verifyResolved?.()) {
    committed.set(control, { zip: options.zip, value: control.value });
    return true;
  }
  if (looksResolved(control, options.zip)) return true;
  const generation = (generations.get(control) || 0) + 1;
  generations.set(control, generation);
  committed.delete(control);
  const old = new Set(currentOptions(control));
  const changed = new Set<Node>();
  let wake: (() => void) | undefined;
  const waitForChange = () => new Promise<void>(resolve => {
    const timer = setTimeout(() => { wake = undefined; resolve(); }, 100);
    wake = () => { clearTimeout(timer); wake = undefined; resolve(); };
  });
  const observer = new MutationObserver(records => {
    for (const record of records) {
      changed.add(record.target);
      record.addedNodes.forEach(node => changed.add(node));
    }
    wake?.();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true });
  try {
    nativeCommit(control, options.zip, options.clearFirst);
    if (options.keyboardLookup) control.dispatchEvent(new KeyboardEvent("keyup", { key: options.zip.slice(-1), bubbles: true }));
    while (Date.now() < deadline) {
      if (generations.get(control) !== generation || options.getControl() !== control || !control.isConnected) return false;
      // Some carriers commit the location asynchronously and remove the
      // suggestion list before there is an option for us to click.
      if (options.verifyResolved?.()) {
        const current = options.getControl();
        if (current) committed.set(current, { zip: options.zip, value: current.value });
        return true;
      }
      const matches = currentOptions(control).filter(candidate => options.matchesOption
        ? options.matchesOption(candidate) : zipPattern(options.zip).test(clean(candidate.textContent)));
      if (matches.length > 1) return false;
      const match = matches[0];
      const activeDescendant = options.activeDescendantIsFresh
        && Boolean(match?.id)
        && control.getAttribute("aria-activedescendant") === match?.id;
      const fresh = match && (
        !old.has(match)
        || [...changed].some(node => node === match || match.contains(node)
          || (node instanceof Element && node.contains(match) && node !== document.documentElement && node !== document.body))
        || activeDescendant
      );
      if (match && fresh) {
        match.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
        match.click();
        while (Date.now() < deadline) {
          if (generations.get(control) !== generation) return false;
          const current = options.getControl();
          // Text we typed is not evidence of a carrier selection. The carrier must
          // produce a richer location value or a verified, rebuilt location card.
          if (options.verifyResolved?.()) {
            if (current) committed.set(current, { zip: options.zip, value: current.value });
            return true;
          }
          if (current && options.verifySelection?.() && containsExactZip(current.value, options.zip)) {
            committed.set(current, { zip: options.zip, value: current.value });
            return true;
          }
          if (!options.requireCarrierVerification && current && current.isConnected
            && containsExactZip(current.value, options.zip)
            && clean(current.value) !== options.zip && current.getAttribute("aria-invalid") !== "true") {
            committed.set(current, { zip: options.zip, value: current.value });
            return true;
          }
          await waitForChange();
        }
        return false;
      }
      await waitForChange();
    }
    return false;
  } finally { observer.disconnect(); }
}
