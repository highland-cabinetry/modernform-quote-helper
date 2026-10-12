# Stack planner — production packing engine 3.3 (Modernform Quote Helper)

## 3.3 changes (Modernform Quote Helper 1.0)

- **Stacked pantry/OC pairs** (planner mode `stacked`, tried next to `bottom` and `top` whenever an order
  has two or more PC/OC BOX + FFD pairs). Before, each pair needed its own long pallet: a BOX may never
  rest on an FFD, so after one pair the deck was closed by a load board. Now the BOX cartons of one
  family are stacked, their FFD cartons go on top of them (FFD on FFD of the same family is allowed),
  and every FFD stays on the pallet of its BOX. The number of pairs per deck is limited by weight,
  height and the reviewed top-load limit of the FFDs (e.g. PC309024FFD carries at most 100 lb, so three
  per column). A deck whose top FFDs cannot carry a load board plus cartons is closed ("sealed").
  ModernForm invoice 4210 (8 × PC309024 + 6 × PC309624 pairs) went from 14 pantry pallets to 4.
- **Runt pallet absorbed.** When the plan ends with a nearly empty pallet (≤ 12 cartons and ≤ 25 % of its
  weight limit), its cartons are set on the lowest supported spots of the other pallets; kept only if
  every carton fits and the validator finds no new error.
- Invoice 4210 overall: 14 long pallets with three top-load errors (3.2) → 6 pallets, no errors (3.3).
- Highland's 70 distinct saved requests (1,200 / 1,600 lb): never more pallets or errors than 3.2;
  9 plans changed, each to a smaller deck mix, fewer load boards or a lower maximum height.

# Stack planner — production packing engine 3.2

October 2026. This engine replaces the `warehouse-layered-safety-first` engine (2.4.x), the
CP-SAT Packing Lab and the refinement worker. Code: `backend/app/packing/stack_planner.py`.
Shared plan/result code: `backend/app/packing/common.py`. Independent checks:
`backend/app/packing/validator.py`.

## Why it was replaced

The old engine treated every carton as a general 3D bin-packing problem. Its strict carton-on-carton
support rules, zero overhang and lack of a pallet-mix search produced more pallets than the warehouse
ships. On the 13 orders that have both a saved Builder plan and warehouse-measured pallets
(Quick Quote), the warehouse used **22 pallets** and the old engine planned **34**.

## How it builds a plan

1. **Classify cartons.** Cartons that cannot fit a 48 × 40 deck (with overhang) need a 96 × 40 deck:
   panels, pantry/OC boxes and their door cartons, and trim. Trim and moldings (≤ 8" wide) ride in a
   strip along one side of the long deck. Everything else is a pile carton.
2. **Long items.** These go either at the bottom as a platform or on top as a cap. The
   `long_items_position` setting chooses; `auto` tries both and keeps the better plan. A PC/OC BOX and
   its FFD always share a pallet, with the FFD above the BOX.
3. **Load boards.** A rigid 96 × 40 × 0.5 in board is laid over the platform when its top is not a
   flat surface for the piles. A second board may carry long items on top of the cabinet stacks. The
   limit is two per pallet. Board weight (`load_board_weight`, default 35 lb, confirmed and adjustable) is added to
   the pallet tare. Boards never enter quantity accounting.
4. **Piles.** Two build styles are planned and the better plan is kept (fewest pallets, then the
   smallest deck, then the lowest and most even heights):
   - *Classic*: largest footprints first become pile bases across the deck (a short look-ahead picks
     the orientation that leaves room for the next cartons), smaller cartons stack on top.
   - *Tiled* (3.1): the carton type that tiles the free deck best is laid first as an even, centred
     grid of pile bases (for example two 25" vanity cartons side by side on a 48" deck, using up to
     the allowed overhang) instead of one 31" carton that leaves a strip nothing fits. Cartons stay
     inside their own pile on the first pass; cartons that only fit by hanging over a neighbouring
     pile wait and then bridge level piles at the end. Flatpack cabinet cartons may stand on edge,
     like books, in deck strips too narrow for a flat pile whenever that keeps the load lower
     (`flatpack_cartons_on_edge`, on by default). Door/face-frame cartons, accessories and assembled
     cabinets never stand on edge, and standing cartons get a strap/wrap warning.

   In both styles narrow cartons of equal thickness are paired side by side, accessories ride on top
   of piles, and face-frame/door (FFD) cartons go last, on top, and may stack on other FFDs.
5. **Pallet count and mix.** Counts are tried from a weight/volume lower bound upward. For each
   count, long/standard mixes are tried smallest deck first (so a 48 × 40 wins whenever it holds the
   order), and one common height target is binary-searched so pallets come out balanced. Mixes whose
   decks cannot carry the order's weight or volume are skipped. The first mix whose plan passes the
   independent validator with zero errors is used.

   Pallets fill one after another. When a 96 × 40 runs into its weight limit (3.2), the same mix is
   also filled with the 48 × 40 decks first and the better plan is kept. Filling the long deck
   first can otherwise leave the 48 × 40 with an awkward remainder that only piles up tall. Long
   pallets are always listed first.
6. **Locks and rotation preferences.** Rotation preferences are tried first when the carton fits that
   way. Exact position locks are applied after planning and are kept only when the validator finds no
   new error; otherwise the carton stays automatically packed and the outcome says why.

## Two pallet builders (3.2)

Builder → Warehouse limits has a **Pallet builder** choice. Both use the same deck building,
validator and quantity accounting. They differ only in the pallet weight limits and in what the
mix search optimizes.

| | Standard LTL pallets (default) | Dedicated truck (bulk order) |
| --- | --- | --- |
| Use for | LTL quotes | Bulk orders on a dedicated box truck or dry van |
| Optimizes | Fewest pallets, then smallest deck | Least truck floor, then fewest pallets |
| 48 × 40 max weight | 1,200 lb (`standard_pallet.max_total_weight`) | 2,400 lb (`truckload_standard_max_weight`) |
| 96 × 40 max weight | 1,600 lb (`long_pallet.max_total_weight`) | 1,600 lb (`truckload_long_max_weight`) |

The request field is `optimizer` (`standard` or `truckload`). It is saved with the shipment and
reopens with it.

**Truck floor model.** Pallets ride two across and are never stacked on each other. 96 × 40 pallets
run lengthwise in two 40 in lanes, so a pair uses 96 in of floor. An odd 96 × 40 leaves a 96 in lane
beside it that holds two 48 × 40 pallets. The remaining 48 × 40 pallets go in pairs turned sideways
(48 + 48 in across, 40 in of floor). This assumes about 96 in of inside width. Plan review, the Excel
summary and `truck_load` in the result show the floor length and the smallest truck that holds it
by floor and payload: 26 ft box truck (300 in, 10,000 lb) or 53 ft dry van (624 in, 44,000 lb).

**Search.** The dedicated-truck builder starts from the sequential-fill plan and then tries mixes
whose floor is shorter, including up to four more pallets than the LTL builder would use. A plan
with more but heavier 48 × 40 pallets often uses less floor than fewer 96 × 40 pallets. The whole
search (both build styles) stops after 22 seconds and keeps the best clean plan found; the job
runner's limit is 30 seconds.

## Settings (Builder → Warehouse limits)

| Setting | Default | Meaning |
| --- | --- | --- |
| `flatpack_max_total_height` | 66 in | Freight height incl. deck for flatpack-only pallets |
| `assembled_max_total_height` | 80 in | Used when a pallet carries any assembled carton |
| `general_overhang_per_side` | 2 in | Used only when needed; always reported in load dimensions |
| `long_items_position` | auto | `bottom`, `top`, or best of both |
| `flatpack_cartons_on_edge` | on | Stand flatpack cabinet cartons on edge to fill narrow deck strips |
| `load_board_weight` | 35 lb | Confirmed by the freight team; adjustable in Warehouse limits |
| `max_load_boards_per_pallet` | 2 | Warehouse: "usually once, at most twice" |
| `minimum_support_ratio` | saved value | Bottom contact required for every carton |
| Standard / long max weight | 1,200 / 1,600 lb | Standard LTL builder (set October 8, 2026) |
| `truckload_standard_max_weight` | 2,400 lb | Dedicated-truck builder, 48 × 40 |
| `truckload_long_max_weight` | 1,600 lb | Dedicated-truck builder, 96 × 40 |

Tare comes from the existing pallet settings. Tare stays at 45 lb (standard)
and 80 lb (long), confirmed October 8, 2026, even though measured actuals run about 5–20 lb higher.

## Validator changes made with this engine

- **Overhang.** Any carton may overhang by `general_overhang_per_side`. Item-specific approvals and
  the CSB42 rule still apply on top of that.
- **Top load.** Catalog top-load values that were never warehouse-reviewed (the 1,000 lb default is
  exceeded by every measured full pallet) raise one `TOP_LOAD_UNREVIEWED` warning per pallet.
  Reviewed limits are still errors.
- **Door cartons.** FFD cartons may stack on any other FFD carton regardless of style or order
  (SO77223 shipped 13 mixed-style door cartons on one 48 × 40).
- **Load boards.** Boards take part in support, collision and load-path checks. A rigid board may
  rest on fragile cartons because it spreads the load.
- **Contact.** A carton resting on a closer surface (for example a load board) no longer counts as
  also bearing on what lies under that surface.

Collision, bounds, height, weight, upright orientation, BOX/FFD pairing, CSB42 geometry, stack-layer
and quantity-accounting checks are unchanged.

## Benchmark against what the warehouse shipped

`scripts/benchmark_warehouse_actuals.py` pairs saved Builder orders with Quick Quote measurements that
have the same order number(s) and re-plans each order with the current engine. It copies the database
to a temporary file first and never writes to it.

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_warehouse_actuals.py
.\.venv\Scripts\python.exe scripts\benchmark_warehouse_actuals.py --state SO77724=flatpack --state SO76982=flatpack
```

Options: `--max-weights 1200,1600` replaces the saved pallet weight limits, and
`--optimizer truckload` plans with the dedicated-truck builder. Each run also reports truck floor
length (pallets two across) for what shipped and for the plan.

Result on the October 8, 2026 database (14 orders; SO77724 and SO76982 were saved as assembled but
shipped flatpack, so their state is corrected with `--state`):

| | Warehouse | Old engine 2.4.4 | 3.1, saved limits\* | 3.2, 1,200 / 1,600 lb |
| --- | ---: | ---: | ---: | ---: |
| Pallets | 24 | 34 (13 orders)\*\* | 21 | 23 |
| Exact pallet-count match | — | 6 / 13\*\* | 12 / 14 | 12 / 14 |
| Slowest order | — | 10–60 s budget | 0.7 s | 1.4 s |

\* Saved requests carry 2,500 lb pallet limits. \*\* SO78016 was only ever planned with the new engine.

With 1,200 / 1,600 lb limits, SO78016 plans a 96 × 40 at 40.1" / 1,598 lb plus a 48 × 40 at 46.5" /
936 lb (warehouse: 34" / 1,271 lb and 48" / 1,089 lb). Before the standard-first fill it was a
48 × 40 piled to 64.8". INV4405 no longer leaves a 19" runt: it plans 96 × 40 at 72" plus two
48 × 40 at 78" and 72".

Across the 77 distinct saved Builder requests, with 1,200 / 1,600 lb limits:

| | Pallets | Truck floor | Slowest |
| --- | ---: | ---: | ---: |
| 3.1, LTL | 212 | 956 ft | 11 s |
| 3.2, Standard LTL builder | 210 | 939 ft | 8 s |
| 3.2, Dedicated truck builder | 226 | 859 ft | 23 s (search limit) |

3.2 is never worse than 3.1 on pallet count. The dedicated-truck builder never needs more floor
than the LTL builder and needs less on 19 of the 77 requests. It uses more pallets because heavier
48 × 40 pallets pack the truck tighter than 96 × 40 pallets. The same five requests have validator
errors and 14 units stay unpacked in every version; those come from the saved inputs, not from 3.2.

The two count mismatches use fewer pallets than the warehouse did:

- **INV67300** plans 4 long pallets where the warehouse used 2 long + 4 standard. The plan puts
  cabinets on a load board over the assembled pantry boxes. The warehouse confirmed it does this, so
  this is a valid alternative build.
- **SO78016**, with the saved 2,500 lb limits, fits on one long pallet at 60.6" and 2,488 lb. With
  1,200 / 1,600 lb limits it matches the warehouse's two pallets.

Known gaps:

- Flatpack heights can run a few inches above measured pallets (SO77523: 37.4" planned, 35"
  measured; it was 44" in 3.0).
- On very large orders (about 280 units) the dedicated-truck search reaches its 22-second limit and
  keeps the shortest floor found so far, which may not be the shortest possible.
- Benchmark weights use each saved request's tare (45/80 lb).

Add every measured shipment as a Quick Quote with the order number in its name. That grows the
benchmark automatically.
