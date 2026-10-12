# Quote Helper — implementation and release status

## 3.0.0 (October 9, 2026) — built, not yet released

Branch `feature/quote-helper-3`. Production still serves **2.10.0** (`releases/2.10.0`); 2.10.0's source is
preserved on `preserve/2.10.0-worktree`. 3.0.0 adds the freight domain (companies, orders, quotes,
shipments, shared expenses and allocations, audit history), a multi-company document import system,
catalog maintenance with preview-then-apply Excel import, the redesigned app shell and pages, release
tooling (backup, preview, cut, switch/rollback) and CI. The packing engine (stack planner 3.2) and
broker autofill are unchanged.

Verification: [TEST_RESULTS.md](TEST_RESULTS.md). Remaining gates before switching: Setup on the
workstation (Vite build + Vitest + Bridge checks), a Preview session with real recent documents, then
cut and switch per [DEPLOYMENT.md](DEPLOYMENT.md). Limitations: [LIMITATIONS.md](LIMITATIONS.md).

---

## Earlier releases

Updated September 24, 2026. Production release **2.8.0**, production engine **2.4.4**, Packing Lab **0.2.0**. The normal launcher selects `releases/2.8.0` and production port 8000 serves the packaged 2.8.0 frontend and backend. The live database was backed up and verified before cutover; see [the 2.8.0 release record](2.8.0_RELEASE_GATE.md). The previous 2.7.0 release remains available for rollback.

The current packing and Builder changes passed 192 Python and 29 frontend tests. Bridge 2.8.0 passed 70 tests, TypeScript checking, and build. The packaged release passed an isolated startup and UI asset check before cutover; live health, UI assets, catalog count, and database integrity passed afterward. Physical warehouse review remains required before treating estimated layouts as shipping instructions. Old Dominion's carrier account-linkage gate still prevents a final live fill check.

## Historical 2.8.0 development review

Updated September 23, 2026. Development release **2.8.0**, production engine **2.4.4**, Packing Lab **0.2.0**.

Release 2.8.0 implements assembled-cabinet short-pallet-first planning, including a sanitized INV4405-shaped regression; resilient DSV autocomplete and Bridge lifecycle recovery; explicit broker-handoff replacement; and Old Dominion as the fifth autofill-only broker. ODFL pauses for manual account selection, fills Outbound shipment data, leaves NMFC/subclass and overlength behavior alone, and never submits the form.

Carrier handoffs remain immutable and memory-only, and now expire after fifteen minutes so manual carrier account selection and normal sign-in returns can complete before autofill resumes.

Automated Python (183 tests), frontend (22 tests), and extension (70 tests) suites, TypeScript checking, production builds, Python lint, release synchronization, and whitespace checks passed at that review. DSV and TQL passed signed-in fill-only checks; TQL's independent width-versus-height readback remained a manual-review field. Old Dominion's final signed-in check was blocked by the carrier's account-linkage update gate and was not recorded as a live pass. See `BUILD2QUOTE_VERIFICATION.md` for the sanitized evidence. Production deployment was pending at that time; Lab promotion remains unapproved. No database migration was required, and no rate submission or booking was performed.

## Historical 2.7.0 review record

The following 2.7.0 review record is retained as historical context. Its test counts, staged paths, and rollout steps do not describe the current 2.8.0 working tree.

**Milestone:** engine **2.4.2** added packing diagnosis and proven fixes. See [the accuracy diagnosis report](PACKING_ACCURACY_DIAGNOSIS.md): 16/20 valid quick results, no regressions against previously valid baselines, and a complete CASE-06 refinement.

Updated September 8, 2026. Review release **2.7.0**, production engine **2.4.1**, Packing Lab **0.2.0**.

Implementation was available for local review. Production deployment and Lab promotion were not approved by the gates at that time.

## Review the application

- Review application: http://127.0.0.1:8011 — separate database, visible review banner.
- Review database: `tmp/implementation-review.sqlite3`.
- Staged frontend: `tmp/frontend-review`.
- Staged extension: `tmp/bridge-review`.
- Frozen warehouse evidence and review form: `docs/warehouse-cases/review.html`.
- Rollback database and previous frontend/extension: `tmp/rollback-2.6.0`; `manifest.json` records SHA-256 hashes and SQLite integrity verification.
- Local Git baseline: `52a58f8`. Customer documents, runtime databases, frozen cases, and generated builds are excluded from Git.

The review server is a temporary local process. Its URL remains available while that process runs. The extension needs version 2.7.0 loaded in Chrome before its release checks; this was not done automatically. Never request rates during these checks.

## Release 1: implemented

- One backend conversion supplies canonical broker rows to preview responses, review UI, packets and PDF/XLSX exports. Warehouse geometry retains original precision; broker dimensions ceiling to inches, gross weights round half-up to pounds, and density uses transmitted values. Overhang, deck height and tare are included.
- Four handoffs validate one shipment, persist all snapshots in one SQLite transaction, then publish sessions together. Failed persistence leaves the previous sessions intact.
- Bridge ZIP lookup scopes suggestions to the associated current control, rejects duplicates/stale suggestions, and verifies commitment. DSV/Echo stop downstream filling after unresolved locations. Tabs are tracked before storage; rollback attempts storage cleanup, every created tab and focus recovery independently. Existing ordering, login recovery, expiry and autofill-only behavior are retained.
- Builder has a task sequence, collapsed advanced limits, distinct blockers/actions/notes and carton inspection. Open all brokers is primary.
- Versioned IndexedDB recovery for Builder and Quick Quote retains incomplete inputs and matching/placement decisions. Restore/Discard, undo/redo, conflicting-tab detection and storage-error reporting are separate from saved shipments. Tokens and file binaries are excluded.
- Saved-load reopening restores the original request as well as the result. Changed geometry makes a result stale and blocks quoting/export until rebuilding.
- Inputs have accessible names; modal focus is trapped/restored with Escape handling; keyboard carton selection synchronizes 2D/3D; narrow navigation wraps and tables scroll. The viewer and Three.js load lazily and render on demand.
- `release.json` is the release source. `scripts/sync_release.py --check` verifies generated manifests/package versions; engine versions remain separate.

Gate status: automated measurement, recovery, accessibility and failure checks passed. **All four live brokers still require fill-only verification after reloading the installed extension.** Fixture success is not evidence that current broker pages work.

## Release 2: implemented with performance gate open

- Thirty immutable private cases: 20 development, 10 held out. Shared order references are grouped to prevent cross-split leakage. Frozen cases include request/settings/master/aliases/profiles and SHA-256 identity. Historical output is explicitly not warehouse approval.
- Review form records reviewer, pallet types/count, measured envelope/gross weight, tolerance, packaging, alternatives and correction reasons. Approval requires actual measurements. `handling-review-priorities.json` starts with SM8, TF3x96 and TK8 and lists development-case SKU frequencies. No handling rules were loosened.
- Isolated subprocess jobs support start/status/cancel/refine/accept. Quick search returns the first complete independently validated candidate; refinement runs at most 60 seconds and retains the incumbent. Applying refinement is explicit.
- Immutable input fingerprints cover engine, item data, aliases, profiles, quantities, geometry and settings. Contacts/names do not repack. Acceptance rechecks current inputs inside the save transaction. Cached candidates use the same complete fingerprint; jobs/cache are bounded and ephemeral.
- Timing includes queue/startup in job elapsed time, matching, strategy work, candidate generation, collision/support feasibility, and physical/final validation. Phase timers are inclusive boundaries and must not be added together as exclusive time.
- Quick mode defers the expensive redundant-pallet and triplet rebalancing passes to refinement. Synchronous endpoints and full refinement retain them.

Measured on this Windows workstation with the same 20 development cases and 10-second process budgets:

| Measure | Before quick-path change | Quick path |
| --- | ---: | ---: |
| Cases with complete valid candidate | 7/20 | 14/20 |
| p50 elapsed including failures | 10.015 s | 2.063 s |
| p95 elapsed including failures | 10.032 s | 10.031 s |

These are elapsed percentiles, **not successful-result latency claims**. CASE-04, CASE-07 and CASE-16 ended without a complete valid candidate; CASE-06, CASE-08 and CASE-17 timed out. Quick mode can initially use more pallets; refinement is needed for consolidation. The first-valid-result target is not met for the whole benchmark. Results were captured before the final telemetry-only instrumentation and gross-weight precision regression test; they are developmental measurements, not a signed-off release benchmark.

The 300 ms measured-pallet preview target still needs an agreed end-to-end browser/workstation benchmark. Quick Quote cancels superseded preview requests, but its existing 250 ms debounce is included in perceived latency. Warehouse agreement cannot be measured until reviews exist. No held-out cases were used for search tuning.

## Release 3: Lab-only implementation

- Valid seeds retain original-precision geometry and required actions. Refinement cannot replace an incumbent with a worse-ranked result.
- Complete independently validated candidates rank by pallet count, envelope volume, required actions, support, then balance; equal ranks retain the incumbent deterministically.
- Candidate generation includes mixed accessory/cabinet batches, reviewed base panels, existing multiple support heights, and bounded two-batch lookahead retaining three promising partial states. Safety validation continues to govern every accepted placement.
- Synchronized 2D layer/carton inspection shows deck boundaries, carton identity and support percentage; the inspector and issue lists expose handling/rejection details. It is not a detailed drawing of individual contact polygons.
- `scripts/check_packing_promotion.py` evaluates exact-snapshot approvals, complete quantity/physical validity, retained-baseline regression, recorded envelope tolerance and 9/10 held-out pallet-count agreement. It does not change the engine.

Gate status: blocked by missing warehouse reviews/comparisons. The Builder continues to use the warehouse engine. An opt-in Lab engine is deliberately not exposed until this gate passes; default promotion additionally requires review of the comparison report. More extensive affected-layer rebuilding/search breadth should be driven by the six incomplete development cases and approved warehouse evidence.

## Validation completed

- Backend: full suite **117 tests passed**, then the seven release-reliability tests passed again after phase telemetry was added.
- Frontend: **15 tests passed**; staged Vite build passed.
- Extension: **37 tests passed**, TypeScript checks and staged extension build passed.
- Python lint and release synchronization check passed.
- Browser review on separate data: draft restores B30 after reload, narrow 390 px layout has no page overflow, all inspected Builder fields named, desktop packing produces a validated candidate. Automated modal and keyboard selection tests passed.
- Rollback SQLite `integrity_check` passed. No new database schema was required: canonical rows are computed and jobs are ephemeral, so no artificial schema migration was introduced. Existing compatibility migrations remain unchanged. Future schema changes still require numbered additive migrations and verified backups.

Existing Starlette/httpx deprecation warning remains; it does not fail the tests. Browser fixtures cover important failure classes but cannot reproduce every carrier deployment, account/login state or extension storage failure on the installed browser.

## Complete the rollout

1. Reload/load the staged 2.7.0 Bridge in Chrome and exercise the actual review-app Open all brokers path. Verify the four locations and transmitted rows, adjacent tab order, focus, login recovery and five-minute expiry. Confirm no rate request is submitted. Record results and fix any site-specific failures before replacing the deployed builds.
2. Review the 30 frozen cases with warehouse staff. Record handling approvals explicitly; retain strict defaults for everything unapproved. Save the form's downloaded review JSON beside the private cases.
3. Resolve incomplete development cases, then rerun measured p50/p95 and preview latency on the agreed workstation. Keep the held-out cases sealed from tuning.
4. Run held-out comparisons against their exact snapshots and feed comparison files plus approved review JSON to `check_packing_promotion.py`. Review the report before any default-engine change.
5. Deploy each gated release with a fresh verified SQLite backup and previous build, then reload the extension and verify the live local app. Stop the normal server before replacing its build. Do not copy the development database over the active database.

Runtime relocation outside OneDrive and a standalone installer remain deferred as requested.
