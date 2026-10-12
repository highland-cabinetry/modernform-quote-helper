# Quote Helper architecture (3.0)

Quote Helper is a local Windows application: a FastAPI backend with a SQLite database and a
React single-page UI, launched from the workstation. 3.0 keeps the 2.x packing engine and broker
autofill unchanged and adds a freight domain (companies, orders, quotes, shipments, expenses),
a configurable multi-company import system, catalog maintenance, and a redesigned interface.

## How 3.0 was arrived at

The October 2026 inspection found:

| Area | Finding | Decision |
| --- | --- | --- |
| V1 (`FREIGHT\Quote Helper`, Tk desktop app) | Older planner, PDF parsing for Highland documents, order-header extraction (P.O., dates, rep, ship-to), ZIP uploads, cross-order address/assembly alerts. No longer the active app. | Not rebuilt. Its header extraction, ZIP support and cross-order alerts were ported into 3.0. Left untouched on disk. |
| V2 (`FREIGHT\Quote Helper V2`) | Active install. Stack planner 3.2 with independent validator, 160 passing tests, live data (363 items, 106 saved plans, 97 measured quotes). UI already split into components. 102 uncommitted 2.10.0 changes, no Git remote. | Base for 3.0. The uncommitted work was committed first as `Record 2.10.0 working tree` (branch `preserve/2.10.0-worktree`). |
| Product rules | CSB42 footprint, trash kit, back-order placeholders already centralised in `product_exceptions.py`; prefixes in packing settings. | Kept; rule *sets* added so each company chooses whether they apply. |
| Release model | `run.ps1` serves `releases\<version>` chosen by `deployment.json`; the database lives in `data\`. Packing workers imported the workspace code, not the release. | Kept; worker import path fixed; preview, cut, switch and backup scripts added. |
| Freight Manager workbook | VBA application, 579 shipment rows holding quote, shipment, customer charge and actual cost together. | Inventoried (`FREIGHT_MANAGER_INSPECTION.md`); migration planned (`FREIGHT_MANAGER_MIGRATION.md`); not migrated. |

## Components

```text
React UI (frontend/src)
  shell/AppShell.jsx        navigation, company switch, toasts, routing (#/path?query)
  pages/                    Dashboard, NewQuote, Orders, QuoteHistory, Shipments,
                            Catalog, Settings (Company & Import), FreightData
  App.jsx + QuoteWorkflows  the pallet-plan and measured-pallet workspaces (2.x, extended)
  components/               DataTable, Drawer, OrderDrawer, ImportPanel, …
          │ JSON / multipart
          ▼
FastAPI (backend/app)
  main.py                   2.x endpoints: packing jobs, manual quotes, broker handoffs, exports
  api_freight.py            3.0 endpoints: companies, imports, orders, quotes, shipments,
                            expenses, allocation, catalog, freight records, dashboard, audit
  services/
    adapters/               document adapters (highland_pdf, text_layout, table) + registry
    order_import.py         batch import, dedupe, originals, conflicts, dry run
    company_rules.py        which product exceptions apply to which company
    product_exceptions.py   the exceptions themselves (CSB42, trash kit, back order)
    sku_matching.py         exact → alias → fuzzy matching (rule-set aware)
    freight.py              quotes, shipments, expenses, allocation, views, history backfill
    catalog.py              validation report, import preview/apply, export
    shipments.py            packing request orchestration (company catalog aware)
  packing/                  stack planner 3.2 + independent validator (unchanged logic)
  database.py               Repository (2.x tables)  ─┐
  workflow_repository.py    manual quotes, snapshots ─┼─ one SQLite file
  freight_repository.py     3.0 tables, migration     ─┘
```

The packing engine knows nothing about companies, orders or the UI: it receives combined catalog
items and settings and returns a `PackingResult`. Company rules act before it (which catalog,
which prefixes, which product exceptions); quotes and shipments act after it (recording results).

## Freight domain

See [DATA_MODEL.md](DATA_MODEL.md) for fields and invariants. In short:

- **Order** — one customer order with its packing-slip lines and header fields, per company.
  Identified by `order_number` plus `references` (invoice/SO/PO). Corrections create revisions.
- **Quote** — a freight estimate for one or more orders: a packed plan (from the stack planner)
  or measured pallets. Recorded automatically whenever a plan is accepted or a measured quote is
  saved; keeps a snapshot of pallets, weights and per-order weight/pallet shares.
- **Shipment** — the physical load. One or more orders, quoted together or separately. An order is
  on at most one active shipment. Optional combined quote.
- **Shipment expense** — money actually recorded for the shipment, never per order.
- **Allocation** — an analytical split of the expense total across the shipment's orders (equal,
  weight, pallets, manual) that always reconciles to the cent.
- **Audit log** — every change to orders, quotes, shipments, expenses, catalog items and companies.

Packed plans attribute cartons back to orders through the line source reference, which the
workspace builds as `"<order number> · <file, page, table>"`.

## Import pipeline

1. Files (PDF, CSV, TSV, XLSX, or ZIPs of them) are hashed (SHA-256). A file already imported for
   the company is reported as a duplicate and creates nothing.
2. Each enabled adapter scores the file; the most confident one parses it. PDFs are read once and
   shared between adapters. Scanned PDFs (no text layer) and damaged files are reported, not guessed.
3. The adapter returns parsed orders: header fields, lines, and ignored rows with reasons.
4. Lines are checked against the company catalog so unknown SKUs surface at import.
5. In one transaction: new orders are created; reference-only orders are filled; existing orders
   with identical lines are reported; existing orders with different lines are reported as
   conflicts with the new lines attached (the stored order is never overwritten silently).
6. Originals are stored under `data/source_files/<hash prefix>/` and can be downloaded.

Adding a company format is configuration first (column names, text layouts with regular
expressions, document markers) and code second (a new adapter class in `services/adapters/`
registered in `ADAPTERS`, with its own tests). `POST /api/imports/test` runs adapters on sample
files without storing anything.

## Persistence and migration

- SQLite in WAL mode with foreign keys, a 30-second busy timeout and explicit indexes.
- The 3.0 schema is created by `FreightRepository.initialize_freight()` on start-up after a verified
  online backup (`data/backups/quote-helper-v2-before-freight-domain-*.sqlite3`). It is additive:
  no legacy table is altered, nothing new is written into legacy payloads, and no foreign key points
  at legacy tables. A 2.10.0 release still runs against a migrated database (tested).
- On each start, plans and measured quotes saved without a quote record (by 2.x or by an older
  release running in parallel) get one (`legacy_backfill`); orders recreated this way are marked
  "From history".
- `Replace all data` (catalog cut-over) still resets saved pallet plans and packing settings, as in
  2.x, but keeps orders, quotes, shipments, expenses and their history; quotes keep their snapshots.

## Coordinate contract (unchanged)

All dimensions and coordinates use inches; all weights use pounds. Backend X is pallet length,
Y is width, Z is height above the deck. `freight_height = deck_height + stack_height`;
`total_weight = pallet_tare + item_weight`. The Three.js viewer maps X/Y/Z to scene X/depth/vertical.

## Manual rotation and exact placement (unchanged)

Rotation buttons send a deterministic `unit_id` plus an LWH/WLH preference; preferences never lock
coordinates. Exact X/Y/Z placement is separate; invalid or stale locks are rejected in
`override_outcomes` and returned to automatic packing, never removing the carton. Before a result
is freight-ready, every matched, dimension-complete cabinet must be packed; otherwise the plan is
`blocked`, which prevents reports and broker handoff.

## Processes and releases

`run.ps1` starts uvicorn from `releases\<version>\backend` and sets `PYTHONPATH` to it, so packing
workers (separate processes) import the same release. The workspace (`backend\`, `frontend\`) is
for development and preview only. See [DEPLOYMENT.md](DEPLOYMENT.md).
