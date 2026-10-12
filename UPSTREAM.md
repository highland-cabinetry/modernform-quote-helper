# Upstream: Highland Cabinetry Quote Helper

Modernform Quote Helper started from `highland-cabinetry/freight-manager` at commit `91a9437`
(Quote Helper 3.2.0, merge of PR #4). The code is shared; the differences are kept small so later
upstream fixes (packing engine, validator, importer, Bridge, installer) can be brought over.

## What differs

| Area | Where |
| --- | --- |
| Edition defaults: company (ModernForm, rule set, document formats, ship-from), starter catalog | `backend/app/edition.py`, `backend/app/seed/modernform_seed.json`, `release.json` (`"edition": "modernform"`) |
| ModernForm PDF reader | `backend/app/services/adapters/modernform.py` |
| Product rules: door-style prefixes, shortened codes, named trash kits | `services/company_rules.py` (`modernform`), `services/sku_matching.py` |
| Packing slip + invoice of one order are the same goods | `services/order_import.py` (`same_goods_allowing_short_codes`) |
| *Assemble* column on packing slips | `services/imports.py` (header alias) |
| Pallet engine 3.3.0: several PC/OC pantry pairs stacked on one long deck ("stacked" mode); a nearly empty last pallet spread over the others | `backend/app/packing/stack_planner.py` (`place_pair_batch`, `_absorb_runt`), `docs/STACK_PLANNER.md` |
| *Get quotes* for all brokers opens every broker that can quote; one that cannot (Old Dominion's 96 in pallet limit) is reported instead of blocking the rest | `services/broker_handoff.py` (`prepare_many(skipped=...)`), `main.py` prepare-all |
| Product catalog in the simple item-list workbook (Cabinet Colors + Item References); imports change only the workbook's columns; Cabinet colors list in settings | `services/reports.py` (`simple_catalog_xlsx`), `services/catalog.py`, `models.py` (`CabinetColor`), `pages/Catalog.jsx` |
| Hidden freight workflow (shipments, approvals, emails, reps, dashboard, reports) | `frontend/src/edition.js` (`FEATURES.freightWorkflow`) |
| Removed: Freight Manager workbook import | `api_fm_import.py`, `services/freight_manager_import.py` and their tests/docs |
| Separate install identity: name, AppId, program/settings folders, ports 8030–8039, update package names and product, Bridge name and extension key/ID | `desktop/`, `desktop/installer/`, `build2quote_bridge/manifest.json`, `BUILD2QUOTE_EXTENSION_ID` in `backend/app/main.py` and `frontend/src/build2quote.js` |

The engine 3.3 changes and the "Get quotes" change would help Highland's app too. On Highland's 70
distinct saved requests the 3.3 engine never plans more pallets or errors than 3.2 (9 plans change, each
to a smaller deck mix, fewer load boards or a lower maximum height). The ModernForm matching rules only
apply with the `modernform` rule set.

## Bringing over an upstream change

```bash
git remote add upstream https://github.com/highland-cabinetry/freight-manager.git
git fetch upstream
git diff 91a9437 upstream/main -- backend/app/packing > /tmp/packing.patch   # pick the paths you want
git apply -3 /tmp/packing.patch
```

Then run the full test suite, update `UPSTREAM.md` with the new upstream commit, and release.
Never copy upstream's identity values (AppId, `APP_ID`/`PRODUCT`, ports, Bridge key and extension ID).
