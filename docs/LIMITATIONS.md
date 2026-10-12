# Known limitations and next steps (3.2.0)

Ordered by what would most affect daily use or data correctness.

**3.2 additions**
- **Bridge changes need a live check.** One-pass filling (the Bridge switches broker tabs itself) and the
  shorter waits were tested against saved copies of the broker pages, not the live sites. Follow
  "3.2 live checks" in `BUILD2QUOTE_VERIFICATION.md` the first time, fill-only, before relying on it.
- **Limited access and inside delivery are not ticked on broker pages yet.** The Bridge passes them and
  shows "Set manually: …" on each broker tab; the page controls still need to be identified on the live
  sites before they can be filled.
- **Broker prices are typed in.** By design the Bridge never reads rates from broker pages (autofill
  only); the quote drawer makes entering them quick.
- **Emails are prepared, not sent.** They open in Outlook (mailto) for review; very long emails may be
  cut off by Outlook, so the window also offers *Copy*.
- **Margin in reports counts shipments without a bill at full revenue** until their bill is entered;
  the reports flag them ("no bill") and the ledger marks their profit as estimated.

1. **Not yet run on the workstation.** CI runs the backend on Windows plus Vitest and the Vite build,
   but `preview.ps1` and the `run.ps1` change have not been executed on the workstation (see
   `TEST_RESULTS.md`). Run Setup, then Preview, before cutting 3.0.0.
2. **Real Highland PDFs not yet re-tested through the new import path.** The adapter reuses the 2.x PDF
   reader, but it was tested with synthetic PDFs. Import a sample of recent invoices, packing slips and
   sales orders in Preview and compare line counts with the documents.
3. **No OCR.** Scanned PDFs without a text layer are reported as unsupported. Adding OCR needs a local
   engine (for example Tesseract) installed on the workstation.
4. **Freight Manager is not migrated.** Historical freight costs, customer charges, claims and the
   customer-email workflow remain in the workbook. Plan, field mapping and the decisions needed:
   `FREIGHT_MANAGER_MIGRATION.md` (decisions D1–D8). Notable model gaps for that import: re-shipments
   (back orders/warranty) of an order already on a closed shipment, external IDs, expenses without a
   shipment, carriers/brokers/reps as reference tables.
5. **Carrier prices are entered by hand.** By design Build2Quote never reads rates. Quote-versus-actual
   reporting is only as complete as the prices recorded on quotes.
6. **Orders recreated from 2.x history are reference-only.** Quote names like `SO77767 SO77769 ASMBLD`
   were turned into orders without lines. They are labelled "From history" and hidden from the Current
   order view; their quotes keep the original pallet data.
7. **Single workstation, single user.** SQLite on OneDrive suits one active writer. Audit entries record
   the actor as "freight-manager"; there are no user accounts. Moving to a shared server would need
   authentication and a server database (the repository layer is the only place that would change).
8. **Per-company rules beyond the two rule sets need code.** Prefixes, columns and text layouts are
   configuration; a company with genuinely new product exceptions needs a rule set in
   `company_rules.py`/`product_exceptions.py` with tests. The packing engine's CSB42 placement is keyed by
   the SKU `CSB42` in any catalog.
9. **Packing settings always keep Highland's colour prefixes** (`WHS`, `ESS`, …) in the ignored-prefix
   list, inherited from 2.x validation; harmless for other companies unless their SKUs start with them.
10. **Very large tables render in full.** Lists of a few thousand rows are fast; tens of thousands would
    need paging in the API and UI.
11. **The Bridge is an unpacked extension.** The installed app keeps it at a fixed path
    (`...\Modernform Quote Helper\bridge`) that updates replace, but Chrome loads the new files only after
    a restart or a reload in `chrome://extensions`. Loading it is a one-time manual step per PC.
12. **Physical validation.** Unchanged from 2.x: plans are heuristics; the warehouse must verify loads.
13. **Installer is unsigned until a certificate is added.** The workflow signs the installer and
    uninstaller when code-signing secrets exist (a `.pfx`, or Azure Artifact Signing; see
    `DEPLOYMENT.md`, "Signing"), but none are configured yet. Until then SmartScreen may warn on the
    first install ("More info" → "Run anyway") and IT allows the installer by hash
    ([IT_APPROVAL.md](IT_APPROVAL.md)). Later versions arrive as update packages, which add no
    executable and need no new approval; only releases that change the bundled Python need the
    installer again. Update packages are unsigned until the `UPDATE_SIGNING_KEY` secret is set (each
    file is still checked against its SHA-256).
14. **Updates are not downloaded automatically.** The repository is private and PCs hold no
    credentials, so someone copies `ModernformQuoteHelperUpdate-<version>.zip` from the GitHub release into
    the updates folder or a shared `update_folder`. Quote Helper then finds it (at start and every
    30 minutes) and installs it on request.
15. **The background server keeps running after the window is closed** until it is stopped from the
    Start menu, the PC restarts or the user signs out.
