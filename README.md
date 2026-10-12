# Modernform Quote Helper 1.0

Modernform Quote Helper turns ModernForm Cabinets & Closet invoices and packing slips into auditable pallet
plans and LTL freight quotes. It reads the PDF, matches every line to the product catalog, plans the
pallets (sizes, weights, heights, freight class per pallet) and fills the broker quote forms in Chrome
with the Modernform Build2Quote Bridge (TQL, DSV and Old Dominion; EchoShip and MyUnishippers are
turned off in `edition.py` / `edition.js`).

It is a local Windows application (FastAPI + React + SQLite), installed with
`ModernformQuoteHelperSetup-<version>.exe` and updated later with `ModernformQuoteHelperUpdate-<version>.zip`
update packages. It is a downsized edition of Highland Cabinetry's Quote Helper 3.2 and shares its pallet
engine, validator, importer and Bridge ([UPSTREAM.md](UPSTREAM.md)).

## What's in the app

| Page | Use it to |
| --- | --- |
| **New Quote** | *Pallet plan from items*: drop invoices or packing slips (several PDFs, or a ZIP), review the lines, build and review the pallet plan, then *Get quotes* to open and fill every broker page. *Measured pallets*: enter pallets the warehouse measured and quote those. Every accepted plan or measured quote is saved in Quote History. |
| **Orders** | Every imported order. Correct imported lines (each save is a revision with a reason) and select orders to quote together or measure. |
| **Quote History** | Every quote with its delivery terms, ZIP, pallets and the broker prices you record; reopen the pallet plan or measurements. |
| **Product Catalog** | The shipping-carton sizes and weights the planner uses, in the simple item-list layout (In House Item SKU, Manufacturer Item SKU, Manufacturer Dimensions, Unit Weight (lb), Flatpack L/W/H, Assembled L/W/H, Category, Description) plus the **Cabinet colors** list (color name → item prefix, e.g. Natural Shaker → `NS-`). *Export Excel* gives that workbook; edit it and import it back (only those columns change) or use it to replace the whole list with your own products. |
| **Company & Import** | Document formats, the product rule set, pallet limits and ship-from address, and the customer pricing rule. |

Not included (Highland-only): shipments and freight costs, customer approval workflow, prepared emails,
sales reps, dashboard and Freight Data reports, the Freight Manager workbook import.

## ModernForm documents

Both QuickBooks layouts are read by the *ModernForm invoice / packing slip PDF* format:

- **Invoice** (`i4210_1.pdf`): invoice number, S.O. number, Ship To, ship date, outside sales rep, the
  *Assembly* box (Yes → assembled) and the item table. Prices are never read.
- **Packing slip** (`4210-P_Rev_1.pdf`): invoice number, Ship To, sales rep, ship date and the item table
  with its *Assemble* column (Yes/X → that line is assembled).

Product rules (*ModernForm Cabinets & Closet* rule set, Company & Import):

- Cabinet-color / door-style prefixes are dropped when matching: `NS-3DB30` → `3DB30`, `WS-Sample Door` →
  `Sample door`. List your colors in Product Catalog › Cabinet colors (any `XX-` prefix also works when the
  rest is exactly one catalog SKU). The printed code stays on the order.
- Packing slips shorten long codes (`NS-PC309024...`). A shortened code is matched only when one catalog
  product starts with it and the line's description names it (*Box Only* → `PC309024BOX`, *Face Frame and
  Door* → `PC309024FFD`); otherwise the line is held for review. These matches are marked "verify".
- `Trash Kit Shaker` and other named trash kits use the trash-kit carton (18 × 14 × 3 in, 18 lb).
- Importing the invoice after the packing slip of the same order is not a conflict: the order keeps the
  complete item codes from the invoice.
- Unknown products never get invented sizes; they block a freight-ready plan until resolved.

## Install and update

- **Install**: run `ModernformQuoteHelperSetup-<version>.exe` (no administrator rights; Google Chrome
  needed). Choose a data folder; the database, imported documents and backups live there.
- **First start** fills the product catalog with the standard cabinet line's carton sizes and weights
  and sets the ship-from address to 1520 Luna Rd Suite 140, Carrollton, TX 75006. Existing data is never
  overwritten.
- **Bridge** (once per PC): `chrome://extensions` → Developer mode → *Load unpacked* → Start menu ›
  Modernform Quote Helper Tools › Build2Quote Bridge folder. Sign in to each broker site yourself; the
  Bridge only fills forms and never books.
- **Update**: put `ModernformQuoteHelperUpdate-<version>.zip` in the updates folder (Start menu ›
  Modernform Quote Helper Tools › Updates folder) and click *Install now* in the app. Only the app's own
  files change; the data folder and settings are kept and the database is backed up first. *Go back to the
  previous version* undoes an update. Details: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- It can run next to Highland's Quote Helper on the same PC: separate program folder, settings,
  ports (8030–8039), Bridge extension and update packages.

## Releasing a new version

1. Change the code, then set the new version in `release.json` and run `python scripts/sync_release.py`.
2. Push to `main`. CI runs the backend tests (Ubuntu, Windows and the bundled runtime), the frontend and
   Bridge tests, builds the installer and the update package, and test-installs, updates, rolls back,
   upgrades and uninstalls them on a clean Windows runner.
3. Push a tag `v<version>` (or run *Desktop installer* manually with *publish*). The GitHub release gets
   `ModernformQuoteHelperSetup-<version>.exe`, `ModernformQuoteHelperUpdate-<version>.zip` and the runtime
   hash list. Send existing users the update zip; new users the installer.

Optional repository secrets: `UPDATE_SIGNING_KEY` (installed copies then accept only update packages
signed with it; set it before the first release you hand out) and code-signing certificates
([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#signing)).

Catalog additions can ship with an update: add the products to `backend/app/seed/modernform_seed.json` and
raise its `seed_version`; existing installations then get the new SKUs (only SKUs they don't have).

## Development

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --port 8030
cd frontend; pnpm install; pnpm dev        # http://127.0.0.1:5173
.\.venv\Scripts\python.exe -m pytest     # tests/test_modernform.py: ModernForm documents end to end
```

The shared regression tests run with the upstream defaults (`QUOTE_HELPER_EDITION=highland`, set in
`tests/conftest.py`); `tests/test_modernform.py` switches to the Modernform edition. The edition's
defaults are in `backend/app/edition.py` and `frontend/src/edition.js`.

## Safety model

Pallet plans keep collision, bounds, support, orientation, top-load, weight/tare and total-height checks
plus an independent validator; unpacked or unmatched items block a freight-ready plan. Build2Quote is
autofill-only: no credentials, no rate scraping, no booking. See [docs/STACK_PLANNER.md](docs/STACK_PLANNER.md)
and [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
