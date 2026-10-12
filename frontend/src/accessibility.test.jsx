import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import useModalFocus from "./useModalFocus";
import LayerView from "./LayerView";

afterEach(cleanup);
function Dialog({ dismiss }) {
  useModalFocus(true, dismiss);
  return <main><button>Background</button><section role="dialog" aria-modal="true" aria-label="Resolve item"><button>First action</button><button>Last action</button></section></main>;
}
it("contains modal keyboard focus and dismisses with Escape", () => {
  const dismiss = vi.fn(); render(<Dialog dismiss={dismiss} />);
  expect(document.activeElement).toBe(screen.getByText("First action"));
  expect(screen.getByText("Background").inert).toBe(true);
  fireEvent.keyDown(document.activeElement, {key:"Tab",shiftKey:true});
  expect(document.activeElement).toBe(screen.getByText("Last action"));
  fireEvent.keyDown(document.activeElement, {key:"Tab"});
  expect(document.activeElement).toBe(screen.getByText("First action"));
  fireEvent.keyDown(document.activeElement, {key:"Escape"});
  expect(dismiss).toHaveBeenCalledOnce();
});
it("exposes all cartons as buttons and synchronizes the selected layer", () => {
  const select = vi.fn();
  const item = {unit_id:"unit-1", original_sku:"B30", x:0,y:0,z:3,length:10,width:10,height:2,support_ratio:.8};
  render(<LayerView pallet={{pallet_number:1,length:48,width:40,items:[item]}} selectedId="unit-1" onSelect={select} />);
  fireEvent.click(screen.getByText("Layer view and keyboard carton selection"));
  const button = screen.getByRole("button", {name:/B30/});
  expect(button.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(button);
  expect(select).toHaveBeenCalledWith(item);
});

it("keeps focus and Escape in the top panel when dialogs are stacked", async () => {
  const { Drawer, Modal } = await import("./components/Common");
  const closeDrawer = vi.fn(); const closeModal = vi.fn();
  render(<Drawer open title="Order" onClose={closeDrawer}><button>Drawer action</button>
    <Modal open title="Quote" onClose={closeModal}><button>Modal action</button></Modal></Drawer>);
  expect(document.activeElement.textContent).not.toBe("Drawer action");
  fireEvent.keyDown(screen.getByText("Modal action"), { key: "Escape" });
  expect(closeModal).toHaveBeenCalledOnce();
  expect(closeDrawer).not.toHaveBeenCalled();
});
