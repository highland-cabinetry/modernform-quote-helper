# Product catalog: source of truth and Excel round trip

## Decision

**The application database is the source of truth for product data; Excel is the bulk-editing and
exchange format.**

Why: the planner, matcher and validator already read the catalog from SQLite (363 items, imported
from `reference/item-master/quote-helper-v2-shaker-item-master.xlsx`), and every quote must use the
same validated data. Keeping a workbook as the master would mean re-importing it before every quote
and would lose per-item history. Making the app the master keeps one validated copy, records every
change, and still lets the team work in Excel when that is faster.

## Workflows

| Task | Where |
| --- | --- |
| Find a product, fix one value, add a product | Product Catalog → search → open the product → Save |
| See what is incomplete or suspicious | Product Catalog → Data quality, or the "Missing sizes" / "Other issues" filters |
| Edit many products in Excel | Export Excel → edit → Import & export → Choose file to preview → Apply |
| Mark warehouse handling reviewed | Open the product → Save & mark rules reviewed |
| Teach an alternate SKU | Open the product → Alternate SKU, or choose the product for an unknown SKU while quoting |
| Retire a product | Open the product → Deactivate (saved quotes keep their data; it can be reactivated) |
| Start over from a new workbook | Import & export → Replace the whole item master (guarded, backed up) |

## Import rules

- Rows are matched by SKU (case-insensitive). The preview classifies every row as **new**,
  **changed** (with each field's old → new value), **unchanged**, **invalid** (with the reason) or
  **conflict** (the SKU appears twice in the file, or its manufacturer SKU already belongs to another
  product).
- Applying writes all new and changed rows in one transaction, or nothing. Invalid and conflicting
  rows are skipped and listed. Products missing from the file are left as they are (an import never
  deletes).
- The exported workbook (`Item Master` sheet, one column per field) re-imports with no changes, which
  is covered by tests. Older templates with plain `Length/Width/Height` import as flatpack sizes.
- Each changed product gets one history entry with the changed fields and the file name.

## What the fields mean

- **Flatpack / assembled carton L × W × H** — shipping-carton sizes the planner packs. At least one
  complete set is required; a line in the other packing state cannot be planned and is reported.
- **Cabinet size (reference only)** — the manufacturer's cabinet dimensions. Never used for packing.
- **Pallet sizes, tare and weight limits** — company packing settings, not product data.
- **Handling rules** — stackable, fragile, upright, orientations, stack layers, top load, bundling,
  edge-standing, overhang approval. Changing any of them marks the product "not reviewed" until the
  warehouse confirms it.

## Other companies

A company can use the Highland item master (`default`) or its own named catalog. Named catalogs have
the same editor, import/export and alternate SKUs, and never mix with the Highland data.
