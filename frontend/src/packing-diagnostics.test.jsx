import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PackingDiagnostics from "./PackingDiagnostics";

afterEach(cleanup);
it("distinguishes an input blocker and exposes its review action", () => {
  const inspect = vi.fn();
  const blocker = {sku:"W4242",reason_code:"OVERSIZE_FOOTPRINT",message:"Carton exceeds pallet width.",suggested_correction:"Verify dimensions and allowed orientations."};
  render(<PackingDiagnostics job={{outcome_category:"physically_blocked",termination_reason:"preflight",blockers:[blocker]}} onInspect={inspect} />);
  expect(screen.getByRole("heading",{name:"Review carton constraints"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Review W4242"}));
  expect(inspect).toHaveBeenCalledWith(blocker);
  expect(screen.queryByText(/try a longer refinement/)).toBeNull();
});
it("explains timeout without calling the shipment physically impossible", () => {
  render(<PackingDiagnostics job={{outcome_category:"timed_out"}} />);
  expect(screen.getByRole("heading",{name:"Search time limit reached"})).toBeTruthy();
  expect(screen.getByText(/reviewed plan is unchanged/)).toBeTruthy();
});
