import { expect, it, vi } from "vitest";
import { setChecked } from "../src/dom";

it("emits one native change per checkbox transition and none on an idempotent retry", () => {
  document.body.innerHTML = '<input type="checkbox">';
  const input = document.querySelector("input")!;
  const changed = vi.fn();
  input.addEventListener("change", changed);
  expect(setChecked(input, true)).toBe(true);
  expect(changed).toHaveBeenCalledTimes(1);
  expect(setChecked(input, true)).toBe(true);
  expect(changed).toHaveBeenCalledTimes(1);
  expect(setChecked(input, false)).toBe(true);
  expect(changed).toHaveBeenCalledTimes(2);
});
