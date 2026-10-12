import React from "react";
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PlanIssues from "./PlanIssues";
import LayerView from "./LayerView";
import { freightReady } from "./freightReady";
afterEach(cleanup);
it("shows one blocker per unpacked cabinet", () => {
  render(<PlanIssues result={{warnings:[{code:"CABINET_PACKING_BLOCKED",severity:"error",unit_ids:["csb"],message:"Not packed"}],unpacked:[{unit_id:"csb",original_sku:"CSB42",reason_code:"OVERSIZE_FOOTPRINT",reason:"Too wide"}],pallets:[]}} />);
  expect(screen.getByText("Blockers (1)")).toBeTruthy();
});
it("shows contained contents against their host", () => {
  render(<LayerView pallet={{pallet_number:1,length:48,width:40,items:[{unit_id:"host",sku:"B18",original_sku:"B18",x:0,y:0,z:0,length:20,width:20,support_ratio:1}]}} containedItems={[{unit_id:"kit",host_unit_id:"host",sku:"TRASH KIT",weight:18}]} />);
  expect(screen.getByText(/inside B18/)).toBeTruthy();
});
it("does not trust ready status when counts omit freight", () => {
  expect(freightReady({plan_status:"ready",unpacked:[],pallets:[{items:[]}],summary:{total_items:1,packed_items:1,unpacked_items:0},warnings:[]})).toBe(false);
});
