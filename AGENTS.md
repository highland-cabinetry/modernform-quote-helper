# Modernform Quote Helper

Local Windows FastAPI + React/Three.js + SQLite application: a downsized edition of Highland Cabinetry's Quote Helper 3.2 for ModernForm Cabinets & Closet. Read `UPSTREAM.md` first: what differs from upstream and how to port upstream changes. Edition defaults live in `backend/app/edition.py` and `frontend/src/edition.js`; never copy upstream identity values (AppId, APP_ID/PRODUCT, ports, Bridge key/ID). Explicit user instructions take priority.

## Code and documentation map
- API/contracts/storage: `backend/app/main.py`, `models.py`, `database.py`, and `workflow_repository.py`.
- Freight domain (3.0): `freight_models.py`, `freight_repository.py` (additive schema + migration), `api_freight.py`, `services/freight.py` (quotes, shipments, expenses, allocation), `services/order_import.py` and `services/adapters/` (document adapters), `services/company_rules.py` (rule sets), `services/catalog.py`. Read `docs/DATA_MODEL.md` before changing these.
- Imports, matching, reports, and broker packets: `backend/app/services/`.
- Production packing and independent validation: `backend/app/packing/stack_planner.py` (engine), `common.py` (unit expansion and result assembly), and `validator.py`. Read `docs/STACK_PLANNER.md` before changing packing; score changes with `scripts/benchmark_warehouse_actuals.py`.
- UI: `frontend/src/` — `shell/AppShell.jsx` (navigation, routing), `pages/` (one file per page), `App.jsx` + `QuoteWorkflows.jsx` (quote workspaces), `components/`; broker extension: `build2quote_bridge/src/`; Python regression tests: `tests/` (`tests/e2e/` browser test, opt-in with `QH_E2E=1`).
- Releases: `docs/DEPLOYMENT.md`. Production is the Windows installer (3.1+): `desktop/` (launcher, background server, Inno Setup script, pinned runtime packages in `requirements.lock`), built and test-installed by `.github/workflows/desktop.yml` via `scripts/build_desktop.py`; publish by pushing a `v<version>` tag. Older release folders: `cut_release.py`, `switch_release.py`; `preview.ps1` for previews. Never point development or tests at `data/quote_helper_v2.sqlite3`; use Preview or a copy.
- Stored records (`Company`, `Order`, `Quote`, `FreightShipment`, `ShipmentExpense`, `ItemMaster`, nested config) ignore unknown fields so an older version can read what a newer one wrote; keep API input classes strict. Freight workflow contract: `docs/WORKFLOW.md`.
- Updates must never break existing data: schema changes stay additive. A change older versions cannot read must raise `DATABASE_COMPATIBILITY` in `backend/app/database.py` (older versions then refuse to open the database instead of misreading it). Never write inside the installed program folder; data lives in the configured data folder.
- Read `README.md` for setup and user behavior; `docs/ARCHITECTURE.md` for boundary changes; `docs/STACK_PLANNER.md` and `docs/PACKING_ENGINE_EVALUATION.md` for packing changes; `docs/IMPLEMENTATION_STATUS.md` for validation/release gates; `docs/MIGRATION.md` for paths/launchers. `PROJECT_CONTEXT.md` contains historical observations: verify versions and Git state against current files.
- Avoid scanning `data/`, `reference/`, `releases/`, `tmp/`, virtual environments, dependency folders, and generated distributions unless the task requires them.

## Commands
Run from the project root in PowerShell using the existing environment; do not reinstall dependencies for ordinary edits.
- Backend development: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload`.
- Focused Python check: `.\.venv\Scripts\python.exe -m pytest tests/<relevant_test>.py`; full Python suite: `.\.venv\Scripts\python.exe -m pytest` when warranted.
- Python lint: `.\.venv\Scripts\python.exe -m ruff check backend tests` (or changed files).
- In `frontend/`: `pnpm dev`, `pnpm test <relevant_test>`, `pnpm build`.
- In `build2quote_bridge/`: `pnpm test <relevant_test>`, `pnpm typecheck`, `pnpm build`.
- Build affected frontend/extension distributions when preparing runnable assets or a release; edit source rather than generated `dist/` files. Use `git diff --check` for patch hygiene.
- `setup.ps1` is installation/update work; stop the running app before setup. `run.ps1` launches the installed app. Do not interrupt a live workstation session merely to verify documentation.

## Integrity and external effects
- Preserve original SKUs, order/source references, quantity accounting, and review gates for fuzzy/unmatched items. Never silently discard physical products.
- Packing must preserve collision, bounds, support, orientation, top-load, weight/tare, and total-height checks plus the independent validator. Unpacked feasible matched cabinets block freight-ready output. Invalid locks must fall back safely.
- Quick Quote measurements already contain gross weight: never add tare again or apply Builder limits. Broker packets snapshot the source revision.
- Do not infer trim or edge-standing permissions. General overhang is the `general_overhang_per_side` setting; item-specific approvals stay separate.
- Build2Quote is autofill-only: no credentials entry, rate scraping, new rate storage, automatic rate requests, booking, tendering, or scheduling. Preserve historical hidden rates and manual confirmation boundaries. Keep all pallets, ZIPs, services, references, and class information; preserve documented rounding rules.
- Treat `data/quote_helper_v2.sqlite3`, saved shipments, settings, learned aliases, reference workbooks, and backups as operational data. Use synthetic isolated fixtures; never reset or migrate live data as a test. Production changes and external writes require applicable user authorization.
- Regression coverage must follow the affected boundary: packing/validator/quantity accounting, API/storage, UI, or broker adapters. Preserve documented release and warehouse validation gates; passing a local test does not establish physical shipping safety or live broker behavior.

## Efficient workflow

- Use one agent unless the user explicitly requests delegation for this task.
- Search with `rg`, then read relevant code, callers, and tests. Avoid whole-repository reads, generated assets, caches, archives, and operational data unless needed.
- Load supporting docs only for the task they govern; reuse unchanged context and batch independent reads.
- Use focused checks for changed behavior. Documentation-only and predictable cosmetic edits do not automatically require smoke tests, browser sessions, builds, or full suites. Preserve all required integrity and release gates below.
- Stop repeating checks once relevant checks pass unless new changes or evidence justify another run. Report what ran, what did not, and why.
- Keep handoffs to goal, changed paths, decisions, checks, risks, and next action. Respect the selected model/effort; these instructions do not configure them.
