# Freight workflow (3.2): quote → approval → shipment → actuals → profit

This is the contract the 3.2 backend implements and the UI uses. Stored models are in
`backend/app/freight_models.py` (search "3.2"); tables in `freight_repository.py` (schema 2).
Services: `services/quote_workflow.py` (quotes, approval, actuals, revenue, bills), `services/comparison.py`,
`services/emails.py`, `services/attention.py`, `services/profit_reports.py`; routes in `api_workflow.py`.
Money is integer cents. Every change is written to the audit log.

## 1. Quoting an order (each quote = one "time quoted")

An order can be quoted many times. Every non-void `Quote` linked to the order is one entry in its
quote history, newest first. For each quote the history shows:

- number, date, kind (pallet plan / measured / external), record status, and **customer status**
  (`not_sent` → `sent` → `approved` | `declined` | `expired`);
- **delivery terms** (`Quote.terms`): ZIP/city/state, location type (commercial / residential /
  limited access), liftgate, appointment, call ahead, inside delivery;
- pallets, dimensions and weight (`Quote.summary`);
- **price options** (`Quote.options`): one row per broker/carrier page with source (echo,
  myunishippers, dsv, tql, odfl, other), carrier, service, transit days, price, broker reference,
  expiry; and which option was used (`selected_option_id`);
- the customer price (`cost.customer_price_cents`), from the company pricing rule unless typed by
  hand (`price_manual`).

Rules:

- `terms` is captured automatically when a quote is recorded from a pallet plan or measured quote
  that has shipping info (`DeliveryTerms.from_shipping`). Until the quote is approved, re-saving the
  measured quote or changing the plan's shipping info (`PUT /api/shipments/{id}/shipping-info`)
  re-captures them; notes typed on the quote's terms are kept. Terms can be edited by hand at any
  time (after approval too, audited). Quotes recorded before 3.2 have no stored terms; the history
  shows them from the source plan (`terms_source: "source"`, otherwise `"recorded"` or `null`).
- Option `source` is normalised to a lowercase slug (`" TQL "` → `tql`, empty → `other`);
  `source_label` defaults to the display name (Echo, MyUniShippers, DSV, TQL, ODFL, Other) or the
  upper-cased slug. At most 30 options per quote. `POST …/options` with `"select": true` also selects it.
- Selecting an option sets `cost.amount_cents`, `cost.carrier`, `cost.broker` (= option
  `source_label`), `cost.reference`, `cost.expires_on` from it, and — unless `price_manual` —
  `cost.customer_price_cents` = `company.config.pricing.price(option.price_cents)`. Editing the
  selected option re-applies this; removing it clears the selection. Clearing the selection clears
  those cost fields (keeps a manual customer price).
- Pricing: carrier × (1 + markup % / 100) + fixed markup, rounded **up** to the cent, then up to a
  multiple of `round_up_to_cents` (0 = to the cent), never below `minimum_cents`.
- Changing the pricing rule never rewrites existing quotes (only a new selection or option edit prices again).
- `quoted_count` for an order = number of its quotes that are not `void` and have a customer price,
  were sent, or have any customer answer. Drafts without a price are listed but not counted
  (`counted: false`, `time_quoted: null`; counted ones are numbered 1 = first time).
- Void quotes cannot be priced, edited or answered (409).
- The order list (`GET /orders`) carries `quoted_count` (same rule) and `latest_customer_status` (the
  customer status of the newest counted quote, `null` when none) on every row, from the same query as
  `quote_ids`.

## 2. Customer answer

| Action | Allowed from | Result |
| --- | --- | --- |
| sent | `not_sent`, `sent` | `sent`, `sent_at = now` (sending again resets the clock) |
| approve | any but void; again when already approved | see below |
| decline | `not_sent`, `sent`, `expired`, `declined`; `approved` only when its shipment is cancelled | `declined`, `responded_at`, note |
| expire | `not_sent`, `sent`, `expired` | `expired`, `responded_at`, note |
| reopen | `declined`, `expired`; `approved` only when its shipment is cancelled | `sent` (keeps `sent_at`), `responded_at = null` |

Preparing a quote email never changes the status; the UI calls `/sent` when the user confirms.

- **Approve**: requires a selected option or a customer price (422) and at least one order (422).
  Invoice numbers are canonicalised (`canonical_reference(.., bare_number_prefix)`, invalid → 422):
  `invoice_numbers` are added to every order of the quote, `order_invoices` `{order_id: [..]}` to that
  order only (must be one of the quote's orders). Refused with 409, before anything is written, if an
  invoice number already belongs to a different order. Then the shipment, in the same transaction:
  - if all the quote's orders are on one active shipment that is not locked → the quote becomes its
    combined quote (the previous combined quote, if `final`, becomes `superseded`); the carrier/broker
    are filled from the selected option only when empty; the charge follows when `charge_source=quote`;
  - if they are on a locked (closed) shipment → 409 "Reopen …"; spread over several shipments, or some
    on a shipment and some not → 409 naming each order's shipment;
  - else create a shipment (status `planning`) for exactly the quote's orders (cancelled order → 409),
    combined quote = this quote, destination from `terms` (the orders' address when the ZIP matches),
    `customer_charge_cents` = customer price, `charge_source=quote`, carrier/broker from the selected option.
  The quote gets `role=combined` and `freight_shipment_id`; its record `status` is unchanged.
  Approving an approved quote again is allowed (an invoice that came later): it adds the references and
  keeps the shipment and the first `responded_at`.
  Other quotes of the same orders that are `sent`/`not_sent` stay as they are (history).
- An approved quote's price/options/terms can still be corrected; each change is audited. A change of
  the customer price updates its shipment's `customer_charge_cents` while the quote is the combined
  quote, `charge_source == "quote"` and the shipment is not closed/cancelled (closed books keep their
  charge; it is resolved again when the shipment is reopened and saved). The same applies to the 3.0
  `PUT /quotes/{id}` cost editor and to linking a priced quote as combined quote.

## 3. Shipment

Status flow `planning → booked → in_transit → delivered → closed` (and `cancelled`); existing lock
rules stay (closed/cancelled are locked; reopen needs a reason). New fields: `purpose`,
`expected_delivery_date`, `charge_source` + `rep_charge_cents` + `backcharge_cents`, `actual`.

- **Actual measurements** (`PUT /freight-shipments/{id}/actual`): pallets (L×W×H in, weight lb,
  quantity), measured_on, source (warehouse / carrier_bill / bol / other), billed weight/class,
  notes. Allowed until closed (409 when closed/cancelled). An empty body or `DELETE …/actual` clears them.
- **Customer charge**: resolved by `charge_source` whenever the shipment is created or saved:
  `backcharge` → backcharge (422 when missing), `rep_assigned` → rep charge (422 when missing),
  `quote` → combined quote's customer price (the stored charge is kept while that quote has no price),
  `manual` → typed value, `none` → 0. With `quote` the typed `customer_charge_cents` is ignored when
  the quote has a price, so the UI shows it read-only.
- **Expenses** (carrier bills): kinds linehaul, fuel, accessorial, redelivery, reweigh, storage,
  adjustment (may be negative), other; `category` keeps a free label.
- **Unassigned expenses**: bills without a shipment (company level); can later be assigned to a
  shipment (moved with the same id and data into `shipment_expenses`, clears the allocation, audited
  on both) or voided (reason required). Assigning needs an open shipment of the same company.
- **Extra revenue** (`ShipmentRevenue`): accessorial re-bills, claim recoveries, other; may be
  unassigned (claims not yet matched) and assigned later. Locked shipments refuse new/void revenue.

## 4. Comparison (`GET /freight-shipments/{id}` → `comparison`)

The reference quote is the customer-approved quote of this shipment (newest answer), else the combined
quote — a plan built for the shipment after approval becomes the combined quote but does not replace
what the customer accepted.

```
quoted:   quote_id, number, pallets, total_weight, rows           (reference quote summary)
actual:   pallets, total_weight, billed_weight, billed_class, weight, rows, source, measured_on, notes
weight_variance_lb / weight_variance_percent: actual weight − quoted weight
          (actual weight = measured total when pallets were measured, else billed weight)
cost:     expected_cents (reference quote cost = selected option price), actual_cents (non-void
          expenses, null without bills), variance_cents, variance_percent
revenue:  customer_charge_cents, extra_cents, total_cents (null when nothing is known), charge_source
profit_cents: revenue − actual cost (or − expected cost when no bills yet: profit_estimated = true)
margin_percent: profit / revenue
flags:    weight_over (actual > quoted by > 5% or by > 100 lb), pallets_changed, cost_over (actual > expected)
```

The 3.0 keys (`separate_estimates_cents`, `separate_estimates_partial_cents`, `combined_estimate_cents`,
`actual_cents`, `customer_charge_cents`) are unchanged. The view also carries `revenue` (entries) and
`revenue_total_cents`.

## 5. Prepared emails (no sending from the app)

Kinds: `quote`, `follow_up`, `ready`, `shipped`, `delivered`. `POST /emails/draft` with
`{kind, quote_id | shipment_id}` (exactly one) returns `{to, cc, subject, body, mailto, …}`.
Recipients: the orders' `customer_emails` (deduplicated case-insensitively); CC the orders' sales reps'
emails (active reps in `company.config.reps`, matched by name case-insensitively) when `email.cc_rep`,
unless already a recipient. Templates: `company.config.email.templates[kind]` (an empty subject or body
falls back to the built-in default) or built-in defaults; placeholders `{orders}` `{invoices}`
`{customer}` `{contact}` `{price}` `{terms}` `{zip}` `{city}` `{pallets}` `{weight}` `{carrier}` `{pro}`
`{pickup_date}` `{delivery_date}` `{expected_delivery}` `{expires}` `{rep}` `{signature}` `{quote_number}`
`{shipment_number}`; unknown placeholders are left as typed (no `format()` evaluation). `[[ … ]]` marks
an optional segment, dropped when a placeholder inside it is empty (e.g. `[[ Valid until {expires}.]]`).
Formatting: money `$1,234.56`, weight `1,234`, dates `Tue Oct 20, 2026`, terms labels joined with
", ", `{contact}` falls back to the customer name; spaces before punctuation and empty `()` are tidied.
`POST /emails/log` records that the user opened it (EmailRecord + an `email_prepared` audit entry on the
entity). `mailto` uses `%20`-style encoding with CRLF line breaks and is capped at 1,900 characters (body
truncated with a note, `mailto_truncated: true`; the UI also offers "Copy").

## 6. Needs attention (`GET /attention?company_id=`)

Returns `{items, counts: {kind: n}, total, generated_at}`; items
`{kind, severity, title, detail, entity_type, entity_id, number, age_days}` sorted danger → warn → info,
then oldest first. The dashboard carries `attention` (counts, total, first 10 items).

- `quote_unanswered`: sent ≥ 3 days ago, no answer (warn), ≥ 14 days (danger, suggest expire);
- `quote_expiring`: open (`not_sent`/`sent`) quote whose price (`cost.expires_on`, set from the selected
  option) expires today or within 2 days;
- `approved_not_booked`: shipment whose reference quote is approved, still `planning` 1+ day after approval;
- `pickup_today`: booked with pickup today/tomorrow (info);
- `overdue_delivery`: in transit past expected delivery date (danger after 2 days);
- `missing_cost`: delivered/closed with no expense;
- `cost_over_quote`: actual cost > expected by > 5%;
- `missing_actuals`: delivered/closed without actual measurements (info);
- `unassigned_expense`: one item when unassigned bills exist (count and total).

Superseded and void quotes, and quotes/shipments imported from the Freight Manager workbook
(`external_source` set) are not reported; missing-cost/actuals/cost-over only look at shipments
delivered in the last 90 days.

## 7. Reports (`GET /reports/summary?company_id=&from=&to=&group=month|rep|carrier|purpose|customer&exclude_sister=`)

Per group: shipments, revenue (customer charges + extra revenue), cost (expenses), net, margin %,
quotes, quotes sent, approved, declined, conversion % (approved / sent), loss shipments (net < 0),
shipments without cost. Excludes cancelled. `exclude_sister=true` is `purpose != sister`.

- A shipment's date is its pickup date, else delivery date, else creation day; quotes count by creation day.
- Group keys: month `YYYY-MM`; rep / customer from the first order with a value; carrier = shipment
  carrier (quotes: selected carrier); purpose (quotes: their shipment's purpose, else `standard`);
  empty keys are `(none)`. Months sort ascending, other groups by revenue.
- Quotes: void and legacy-backfilled quotes are ignored, superseded ones count only when answered;
  "sent" = sent or answered.
- `totals` adds `unassigned_expense_cents`, `unassigned_revenue_cents` (dated by incurred/received date,
  else creation) and `net_with_unassigned_cents`.
- `/reports/summary.csv`: the same rows plus totals and the unassigned lines.

`GET /freight-records` (Freight Data) keeps its 3.0 fields and adds purpose, charge source, customer,
rep, reference quote number/status, expected cost, revenue, cost, net, margin, expected vs actual
weight and pallets, `weight_over`.

## 8. API (all under `/api`, JSON, errors 404/409/422 as elsewhere)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/orders/{id}/quote-history` | quotes (full), `quoted_count`, latest terms, approved quote id |
| GET | `/quotes/{id}/detail` | one quote as a history entry |
| PUT | `/quotes/{id}/terms` | DeliveryTerms |
| POST | `/quotes/{id}/options` | add QuoteOptionInput (+ `select`) → quote |
| PUT | `/quotes/{id}/options/{option_id}` | edit option |
| DELETE | `/quotes/{id}/options/{option_id}` | remove option (clears selection if selected) |
| POST | `/quotes/{id}/select` | `{option_id \| null}` → apply pricing |
| PUT | `/quotes/{id}/customer-price` | `{cents \| null}`; null → back to pricing rule |
| POST | `/quotes/{id}/sent` · `/approve` · `/decline` · `/expire` · `/reopen` | customer answer (`{note}`); approve body `{invoice_numbers: [], order_invoices: {}, note}` returns `{quote, shipment, shipment_created}` |
| POST | `/quotes/pricing-preview` | `{company_id, carrier_cents, pricing?}` → customer price |
| PUT/DELETE | `/freight-shipments/{id}/actual` | ActualMeasurements / clear |
| POST/GET | `/freight-shipments/{id}/revenue`, `/revenue/{rid}/void` | extra revenue |
| GET/POST | `/revenue`, `/revenue/{id}/assign` `{shipment_id}`, `/revenue/{id}/void` | revenue without a shipment |
| GET/POST | `/unassigned-expenses`, `/{id}/assign` `{shipment_id}`, `/{id}/void` | bills without a shipment |
| POST | `/emails/draft`, `/emails/log` | prepared emails |
| GET | `/emails?entity_type=&entity_id=&company_id=`, `/emails/templates?company_id=` | email log; templates, defaults and placeholders |
| GET | `/attention` | needs-attention list |
| GET | `/reports/summary`, `/reports/summary.csv` | profit reports |
| GET/PUT | `/companies/{id}` (existing) | pricing rule, reps, email templates live in `config` |
