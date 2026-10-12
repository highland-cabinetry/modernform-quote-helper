# Packing engine evaluation

> **Superseded October 2026.** The production engine is now the stack planner
> (`StackPlannerPackingEngine` 3.0). `SafetyFirstPackingEngine`, the warehouse-layered strategies,
> the Packing Lab and the py3dbp adapter were removed. See [STACK_PLANNER.md](STACK_PLANNER.md)
> for the design, the validator changes and the benchmark against warehouse-measured pallets.
> The record below is kept for history.

Evaluation date: 2026-08-03

## Decision

Use the internal `PackingEngine` interface with `SafetyFirstPackingEngine` as the production implementation. Keep a `Py3dbpPackingEngine` adapter boundary for future benchmarking, but do not allow an external library to bypass the V2 rules validator.

The warehouse constraints are harder than geometric bin packing. A valid plan must also express upright-only cartons, no-upside-down handling, fragile exposure, maximum layers, top-load limits, sufficient surface support, pallet tare, manual position locks, and an explainable reason for every unplaced unit.

## Libraries considered

### py3dbp / enzoruiz/3dbinpacking

Strengths:

- small Python API with bins, items, maximum weight, rotations, and decimal precision;
- deterministic enough to use as a baseline benchmark;
- MIT license and broad familiarity.

Gaps for this application:

- the [published py3dbp package](https://pypi.org/project/py3dbp/) is old and its public model does not express the cabinet-specific support, top-load, fragile, or manual-lock rules;
- its [repository usage model](https://github.com/enzoruiz/3dbinpacking) focuses on geometric fit and bin/item distribution;
- placements would still need a separate safety pass and repacking when validation fails.

Conclusion: useful only behind an adapter for comparison, not as the trusted production engine.

### jerry800416/3D-bin-packing improved fork

Strengths:

- adds gravity/floating correction, load-bearing priority, up/down handling, stability checks, configurable support ratio, and multi-bin distribution;
- closest open-source fit to the requested warehouse behavior. The [fork documents its added stability and support rules](https://github.com/jerry800416/3D-bin-packing).

Gaps:

- repository-oriented rather than a versioned, pinned PyPI dependency;
- load bearing is primarily a packing priority, not the full cumulative top-load accounting needed here;
- still lacks shipment source lineage, per-SKU layer limits, manual locked placements, and V2's exact validation/report contract.

Conclusion: valuable algorithm reference, but vendoring a lightly maintained fork would increase operational risk. Its useful ideas—fixing floating placements and checking support ratio—are implemented explicitly in V2 and revalidated independently.

### py3dbc

Strengths:

- more recently released: [PyPI lists version 1.0.2 from February 2026](https://pypi.org/project/py3dbc/);
- models center of gravity, stack weight, placement strategies, and stability constraints.

Gaps:

- designed for maritime container slots and ship physics, including bays, tiers, metacentric height, reefer power, and hazmat separation;
- its discrete ship model is not a substitute for continuous carton placement on 48 × 40 and 96 × 40 pallet decks.

Conclusion: actively relevant to logistics but the wrong physical problem and rule vocabulary for cabinet palletization.

## Why the internal heuristic is safer here

The V2 engine owns a narrow, testable data contract. It expands every requested quantity into a deterministic unit; enumerates only allowed rotations; places heavy and load-bearing cartons early; checks bounds, weight, collision, support, layers, fragile exposure, and top-load limits before accepting a point; then runs a separate validator over the completed plan. Candidate scoring strongly penalizes unpacked units and rule errors before pallet count or cube use.

This does not prove an optimal minimum-pallet solution. Three-dimensional bin packing is combinatorial, and the engine intentionally searches a bounded set of explainable strategies. A failed placement means “no safe position found by this heuristic,” not “no mathematically possible position exists.” That distinction is surfaced in the unpacked reason and is why human review remains part of release.

## Replacement path

Any future engine must implement `PackingEngine.pack()` and return the same `PackingResult` schema. Before becoming selectable in production, it must:

1. map every unit without loss;
2. preserve original SKU and packing-slip sources;
3. pass `RulesValidator` with zero errors;
4. pass the synthetic and private warehouse calibration suites;
5. provide deterministic results or record its random seed;
6. retain manual locked placements or reject them with a specific reason.

## V1 comparison and bounded refinement check

Evaluation update: September 23, 2026.

A private, ignored manifest paired 30 completed freight packing slips with usable pallet photos. Twenty cases were assigned to development and ten to holdout. Photo-derived dimensions remain provisional ranges with confidence labels; they are not warehouse measurements or shipping approval. Twenty-one cases could be resolved completely against the current catalog. The other nine remain input-blocked and were not treated as packing failures.

The V1-inspired whole-order refinement produced rank parity on the seven candidate-bearing held-out cases (COMP-23 and COMP-25 through COMP-30). COMP-21 was catalog-resolved but physically blocked under both baseline and refined null-seed searches. COMP-22 and COMP-24 were input-blocked by unresolved units. Development comparisons also produced rank parity on COMP-09 through COMP-13 and COMP-15 through COMP-16 where a candidate completed; COMP-06 was physically blocked and COMP-07 timed out before a seed. No photo estimate was promoted to a measured result.

Three development cases were also sent through V1 using the identical V2-resolved unit geometry. V1 and V2 each used all physical weight and did not use V1's automatic height relief. V1 used one pallet where V2 used two on COMP-11 and COMP-20, and both used one pallet on COMP-13. Reconstructing the two V1 one-pallet layouts under the V2 validator explained the apparent advantage:

- COMP-11 had nine units below V2's 75% support requirement, including support ratios from 0% to 69%, plus height-gap dunnage warnings. V1 also replaced several long-accessory units with a synthetic bundled object, which does not preserve V2's unit lineage contract.
- COMP-20 had six units below the support requirement, including five at 0%, plus height-gap dunnage warnings.
- V1 inserted a synthetic 96 × 40 × 0.25-inch load spreader after every layer. Those sheets were neither order items nor documented and approved warehouse dunnage in the comparison inputs.

The warehouse later confirmed that rigid 96 × 40 × 0.5-inch boards are used only when needed, usually once and at most twice per pallet. This geometry is provisional and for quoting or estimation only: board weight and load-bearing capacity are unverified. It therefore does not authorize V1's every-layer spreaders, a V2 support-rule change, a shipping-ready plan, or omission of board weight from a freight-ready broker packet. A future comparison-only Lab scenario may model the board after those missing physical properties are documented.

The refinement therefore remains gated: it did not improve a validated representative or held-out case, and the V1 one-pallet layouts are not admissible V2 plans. The whole-order search is disabled by default in the production engine and must be enabled explicitly for comparison tests. Future work can target an explicit, measured, and warehouse-reviewed dunnage or load-spreader rule while preserving unit lineage and independent validation.

## September 2026 warehouse photo guidance

Five private warehouse photos supplied for the mixed 65-unit shipment review are kept outside Git. Their visible order markings and warehouse context are not fixture data. The anonymized geometry and handling-rule fixture is `tests/fixtures/mixed_65_unit_warehouse.json`; it preserves 65 units across 24 product groups and the saved pallet settings, but uses synthetic source references. It is a reproducible packing case, not evidence of an approved shipping build.

| Photo | Observed arrangement | Confidence and limit |
| --- | --- | --- |
| 1 | Two adjacent columns of flat cabinet cartons, including narrow base cartons, are stacked densely rather than leaving the deck center open. | High confidence in the visible pattern; pallet footprint and load capacity are not measured. |
| 2 | Long cartons form multiple rows on a long deck, with a large sheet visible above the stack. | High confidence in the row pattern; the sheet's strength and weight are unknown. |
| 3 | Narrow base cartons form a paired lower section under wider cabinet cartons. | High confidence in the visible pairing; exact support overlap cannot be recovered from the photo. |
| 4 | Cabinet cartons form close side-by-side vertical stacks, with similar footprints repeated across layers. | High confidence in the column pattern; load-bearing limits are unknown. |
| 5 | The warehouse says W3018 or W1830 cartons can be arranged three per layer; the visible lower layers are staggered/interleaved. | High confidence in the warehouse-reported count; per-carton coordinates and support ratios are not established by pixels. Current W1830 master length permits three end-to-end within 96 inches, and current W3018 length likewise permits a three-carton long-deck row. |

The photos establish arrangement ideas only. Fixture dimensions and weights come from the saved item-master geometry, not measurements taken from image pixels. These examples justify rotation-aware row and layer search when catalog geometry fits. They do not change the independent validator, permit a new edge-standing orientation, or establish provisional sheet capacity.

An independent quick-worker run on the sanitized 65-unit fixture produced **one long and one standard pallet in 1.97 seconds** on the final search revision. All 65 units reconciled, with zero independent validator errors and zero accounting errors. The long pallet held 54 units (40.61-inch cargo stack, 1,402.46 lb gross); the standard held 11 units (62.84-inch cargo stack, 949.38 lb gross) under the saved 66-inch cargo limit. The historical saved shipment had one long plus two standard pallets; a controlled run of the pre-improvement code on this sanitized fixture produced two long pallets. The new result improves both comparisons. Physical warehouse review is still required before treating the arrangement as a shipping instruction. The review-only estimate path remains separate from freight export and broker handoff.

The geometry-derived targeted search is bounded to at most 80 expanded units and runs only after a complete, independently valid base candidate exists. A paired 10-second held-out quick-worker comparison initially found one apparent availability loss on CASE-25, whose 119 units put its base search near the deadline. The final revision skips targeted search for that case; a helper-invocation trap confirmed the skip, and it can still time out during base packing around 10 seconds. CASE-24 retained a six-pallet candidate with the same rank under both paths. The other held-out snapshots were either input-blocked or timed out under both paths in this bounded comparison. These time-limited outcomes do not establish shipping approval or optimality.
