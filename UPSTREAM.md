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
| Pallet engine 3.3.0: several PC/OC pantry pairs stacked on one long deck ("stacked" mode) | `backend/app/packing/stack_planner.py` (`place_pair_batch`) |
| Hidden freight workflow (shipments, approvals, emails, reps, dashboard, reports) | `frontend/src/edition.js` (`FEATURES.freightWorkflow`) |
| Removed: Freight Manager workbook import | `api_fm_import.py`, `services/freight_manager_import.py` and their tests/docs |
| Separate install identity: name, AppId, program/settings folders, ports 8030–8039, update package names and product, Bridge name and extension key/ID | `desktop/`, `desktop/installer/`, `build2quote_bridge/manifest.json`, `BUILD2QUOTE_EXTENSION_ID` in `backend/app/main.py` and `frontend/src/build2quote.js` |

The pallet-engine change and the shortened-code matching would help Highland's app too; they are
written so the upstream behaviour is unchanged unless the new mode or rule set is used.

## Bringing over an upstream change

```bash
git remote add upstream https://github.com/highland-cabinetry/freight-manager.git
git fetch upstream
git diff 91a9437 upstream/main -- backend/app/packing > /tmp/packing.patch   # pick the paths you want
git apply -3 /tmp/packing.patch
```

Then run the full test suite, update `UPSTREAM.md` with the new upstream commit, and release.
Never copy upstream's identity values (AppId, `APP_ID`/`PRODUCT`, ports, Bridge key and extension ID).
