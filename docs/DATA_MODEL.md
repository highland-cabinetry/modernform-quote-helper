# Data model (freight schema 2)

Code: `backend/app/freight_models.py` (records), `backend/app/freight_repository.py` (tables).
Money is integer cents. Identifiers are UUIDs; human numbers (`Q-000123`, `S-000045`) come from
`freight_counters` and are never reused.

```text
Company 1─* Order 1─* OrderLine (inside the order record; revisions in order_revisions)
   │          │
   │          *─* Quote            (quote_orders; one quote may cover many orders,
   │          │                     an order may have many quotes over time)
   │          *─1 Shipment         (shipment_orders; at most one ACTIVE shipment per order)
   │                 │
   │                 1─* ShipmentExpense
   │                 1─* ShipmentRevenue       (3.2; may also exist without a shipment)
   │                 1─0..1 Allocation ─* AllocationLine (per order)
   │                 0..1 combined Quote
   ├─* UnassignedExpense (3.2: bills without a shipment)   ├─* EmailRecord (3.2: prepared emails)
   └─ settings (packing limits), catalog (items + aliases), rule set, adapters
SourceFile (hash, stored original) ──* Order       ImportBatch ──* SourceFile
AuditLog: entity_type + entity_id + action + summary + details + time
```

## Company

`code`, `name`, `active`, and `config`:

| Field | Meaning |
| --- | --- |
| `catalog` | Product catalog name. `default` is the Highland item master (legacy `item_master` table); any other name lives in `catalog_items`/`catalog_aliases`. Companies may share a catalog. |
| `rule_set` | `highland` (CSB42 footprint, trash kit, back-order resolution) or `none` (back-order resolution only). See `services/company_rules.py`. |
| `adapters` | Enabled document formats in priority order. |
| `column_mapping` | Extra header names for SKU, quantity, description, order number, packing state. |
| `text_layouts` | Regex layouts for PDFs without tables (validated on save). |
| `document_markers` | Text that identifies the company's documents. |
| `bare_number_prefix` | `INV` turns a typed `68606` into `INV68606`. |
| `default_packing_state`, `origin` | Defaults for new lines and quotes. |
| `pricing` (3.2) | `PricingRule`: `markup_percent`, `markup_fixed_cents`, `round_up_to_cents`, `minimum_cents`. Customer price = carrier × (1 + % / 100) + fixed, rounded up to the cent and then up to a multiple of `round_up_to_cents`, never below the minimum. |
| `reps` (3.2) | `SalesRep` `name`, `email`, `active`; matched to `Order.sales_rep` by name (case-insensitive) to copy reps on emails. |
| `email` (3.2) | `EmailSettings`: `signature`, `cc_rep`, `templates` (`{kind: {subject, body}}`; missing kinds use built-in defaults). |

Packing limits per company are a `PackingSettings` record (`company_settings`; the default company
keeps using the 2.x `settings` row). The default company (`HCI`) is created by the migration.

## Order

`order_number` (unique per company, case-insensitive), `references` (other identifiers; every
reference resolves to at most one order), `customer_name`, `po_number`, `order_date`, `ready_date`,
`sales_rep`, `ship_to`, `residential`, `liftgate`, `packing_state`, `notes`, `lines`, `status`,
`document_type`, `source_file_id`, `issues`, `revision`; 3.2: `customer_contact`, `customer_emails`
(up to 10, email recipients), `customer_phone`. Approving a quote adds the invoice numbers it came back
with to `references` (new revision, audit `references_added`).

Statuses: `needs_review` (import found problems), `ready`, `reference_only` (known only by number),
`shipped` (its shipment was closed), `cancelled`. `document_type = history` marks orders recreated
from pre-3.0 quote names.

Identity on import: a document is matched to an order by its own number (invoice, packing slip or
sales order number). A secondary reference it shares with an existing order (for example two invoices
for one sales order) only fills that order when it is still `reference_only`; otherwise the document
becomes its own order with the issue "Shares reference …" and status `needs_review`, so the same goods
are never quoted twice without someone checking.

Lines keep the SKU exactly as printed. Saving a correction writes a new revision, re-checks the
catalog and records the reason in the audit log. Lines of an order on a closed shipment are frozen.

## Quote

`number`, `kind` (`packed` | `measured`), `status` (`draft` | `final` | `superseded` | `void`),
`name`, `packing_result_id` or `manual_quote_id`, `order_ids`, `freight_shipment_id`,
`role` (`individual` | `combined`), `summary`, `cost`, `notes`, `legacy`.

- `summary`: pallets (standard/long), total and item weight, units, freight-ready flag, builder,
  broker rows, and per-order `order_weights` and `order_pallet_shares` (packed plans only).
- `cost`: manually recorded carrier price, customer price, carrier, broker, reference, expiry date.
  Quote Helper never reads rates from broker sites.
- A new final quote for exactly the same orders marks earlier ones `superseded`. A measured quote
  saved again is the same quote, revised, and becomes the current (final) quote again, superseding
  whatever replaced it.
- A quote is linked to a shipment as its combined quote only when it covers a subset of the
  shipment's orders; the check runs before anything is written.
- 3.2 (see `docs/WORKFLOW.md`): `terms` (`DeliveryTerms`: ZIP/city/state, `location_type`
  commercial/residential/limited_access, liftgate, appointment, call_ahead, inside_delivery, notes),
  `options` (`QuoteOption` per broker/carrier page: id, source, source_label, carrier, service,
  transit_days, price_cents, reference, expires_on, notes, recorded_at), `selected_option_id`,
  `price_manual`, `customer_status` (`not_sent`/`sent`/`approved`/`declined`/`expired`), `sent_at`,
  `responded_at`, `response_note`, `external_source`/`external_ref` (importer keys). Kind `external` is a
  quote priced without a plan. The selected option fills `cost`; `cost.customer_price_cents` comes from
  the pricing rule unless `price_manual`.

## Shipment

`number`, `name`, `status` (`planning`, `booked`, `in_transit`, `delivered`, `closed`,
`cancelled`), `carrier`, `broker`, `pro_number`, `bol_number`, `pickup_date`, `delivery_date`,
`destination`, `customer_charge_cents`, `notes`, `order_ids`, `combined_quote_id`; 3.2: `purpose`
(standard, back_order, warranty, damage_warranty, correction, transfer, sister), `expected_delivery_date`,
`charge_source` (quote, rep_assigned, backcharge, manual, none) with `rep_charge_cents` and
`backcharge_cents` (the source decides `customer_charge_cents`, see WORKFLOW §3), `actual`
(`ActualMeasurements`: pallets `[{length, width, height, weight, quantity, note}]`, measured_on, source,
billed_weight, billed_class, notes), `external_source`/`external_ref`.

Invariants enforced in one transaction: all orders belong to the shipment's company; an order is on
at most one shipment whose status is not `cancelled`; `closed` and `cancelled` shipments are locked
(no order, expense, allocation or combined-quote changes) until reopened with a reason. Closing a
shipment marks its orders `shipped`; reopening returns them to `ready` (or `reference_only` when they
have no lines). The combined quote of a locked shipment cannot change orders or status; notes and cost
can still be recorded. Cancelled orders cannot be added to a shipment. A void combined quote is
ignored. "Separate estimates" add each order's current single-order quote once.

## Shipment expense and allocation

Expense: `amount_cents` (negative only for kind `adjustment`), `kind`, `vendor`,
`invoice_reference`, `incurred_on`, `notes`, `voided`, `void_reason`. Expenses are voided, never
deleted.

Allocation: `method`, `total_cents`, `basis_source`, `lines[]` (`order_id`, `basis`, `share`,
`amount_cents`, `rounding_adjustment_cents`). Splits use the largest-remainder method on cents, so
the lines always sum to `total_cents`. Weight and pallet bases come from the combined packed quote
when it covers every order, otherwise from each order's own single-order quote; if neither exists the
method is refused with an explanation. Recording or voiding an expense, or changing the orders,
clears the allocation (logged as `allocation_cleared`), so a split of an old total is never shown as
current; it must be recalculated.

## 3.2 money and messages (schema 2 tables)

| Record | Table | Notes |
| --- | --- | --- |
| `ShipmentExpense` | `shipment_expenses` | Kinds linehaul, fuel, accessorial, redelivery, reweigh, storage, adjustment (only kind that may be negative), other; free `category` label; `external_*` importer keys. |
| `UnassignedExpense` | `unassigned_expenses` | Same fields plus `company_id`; assigning moves it (same id and data) into `shipment_expenses` and deletes the unassigned row in one transaction; voided ones stay with a reason. |
| `ShipmentRevenue` | `shipment_revenue` | Extra revenue: `amount_cents` (> 0), kind accessorial_rebill / claim_recovery / other, payer, reference, received_on, notes, `shipment_id` (null = not yet matched), voided + reason. |
| `EmailRecord` | `email_log` | A prepared email the user opened: kind, entity (quote/shipment/order), to, cc, subject, time. |

Profit per shipment: revenue = customer charge + non-void extra revenue; cost = non-void expenses;
net = revenue − cost (estimated with the expected cost before any bill); margin = net / revenue.
Reports, Freight Data and the needs-attention list read these with one query per table
(`workflow_*_rows`, `expense_sums_by_shipment`, `revenue_sums_by_shipment`, `unassigned_money_rows` in
the repository's "3.2 workflow helpers" section); quote, shipment and order rows are reused while the
table's fingerprint (row count, latest `updated_at`, payload bytes) is unchanged.

## Version records

`app_meta` (`last_app_version`, `database_compatibility`) and `app_versions` (one row per version that
opened the database, with the backup made first). Before a version opens a database last opened by a
different version, it writes a verified backup `backups/quote-helper-v2-before-update-<version>-*.sqlite3`
(newest ten kept). `database_compatibility` only rises for a change older versions cannot read; a
version whose `DATABASE_COMPATIBILITY` is lower refuses to open the database.

## Audit log

Written for imports, corrections, status changes, quote creation/revision/supersede/void, shipment
creation/changes/reopen, expenses, allocations, catalog edits and imports, alias changes and company
settings; 3.2 adds quote `terms_updated`, `option_added/updated/removed/selected`, `price_set`, `sent`,
`approved`, `declined`, `expired`, `reopened`, `email_prepared`; shipment `actuals_recorded/cleared`,
`charge_updated`, `revenue_recorded/assigned/voided`, `expense_assigned`; order `references_added`;
entities `unassigned_expense` (`recorded`, `assigned`, `voided`) and `revenue` (unassigned entries). Read per entity (`GET /api/audit`) and shown as History in the UI.
