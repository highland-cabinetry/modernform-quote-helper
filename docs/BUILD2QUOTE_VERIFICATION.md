# Build2Quote live verification

Updated September 23, 2026. This report records fill-only checks against signed-in carrier forms. No rate request, booking, tender, or scheduling action was performed.

## Synthetic shipment

Checks used an isolated review database and a synthetic one-pallet manual quote: origin ZIP 80022 (Commerce City, CO), destination ZIP 59106 (Billings, MT), 48 × 40 × 23 inches, 224 pounds, freight class 100, NMFC 79300, and appointment delivery. The operational shipment database was not changed.

## Reproduced failures

- **DSV:** the origin autocomplete displayed six local suggestions for ZIP 80022, but the Bridge did not see the nested carrier list, did not commit Commerce City, and stopped before the destination and commodity rows.
- **TQL:** the Bridge reported a verified fill after login return while the five Kendo numeric editors appeared empty. The original verifier trusted transient editor values and did not verify TQL's carrier-owned handling-unit summaries after the row settled. Later manual-keyboard testing established that the current TQL page natively keeps those editor DOM values blank even when its model accepts the numbers.
- **Old Dominion:** account selection correctly remained manual. After selection, the destination ZIP lookup needed the City control to be opened before Billings, MT, and USA materialized. The existing adapter then cleared NMFC, left freight class unset, and selected Trade Show Pickup instead of Scheduled Appointment Delivery because it trusted a colliding carrier value before the visible label. The original five-minute handoff also expired during the first manual account-selection attempt.

## Implemented safeguards

- DSV scopes and recognizes the carrier's nested autocomplete list and commits the exact city/state match.
- TQL waits for the complete form to settle and verifies the carrier model through stable handling-unit, total-weight, linear-foot, density, and estimated-class summaries. It reports manual review where the carrier does not expose independent width-versus-height readback.
- Old Dominion preserves NMFC/subclass state, resolves lazy ZIP/city controls, waits for dynamic freight-class options, and matches optional services by visible carrier label. Account selection remains manual and changeable.
- Handoff packets remain immutable and memory-only. Their expiry is fifteen minutes so manual account selection and ordinary sign-in returns can complete.

## Post-build live findings

The first rebuilt 2.8.0 Bridge was reloaded manually and checked again with the same isolated shipment.

- **DSV:** both ZIPs committed to the correct carrier chips, including Commerce City for 80022. The live committed inputs expose neither Angular `ng-valid` nor `ng-invalid` classes, so the Bridge remained in Filling even though the exact ZIP chips were present and no suggestion panel remained. The verifier was corrected to use the carrier chip, exact ZIP, absence of invalid state, and closed popup together.
- **TQL:** pickup, delivery, service, class, NMFC, commodity, and reference values settled correctly. TQL's current numeric controls keep their visible `input.value` empty even when a user enters values with genuine keyboard events. Carrier-owned summary fields retain the model values instead: the synthetic row reported handling unit 1, total weight 224.00, calculated linear feet 4.00, density 8.76, and estimated class 100 after six seconds. Changing height changed density; changing length changed density, linear feet, and estimated class. These summaries verify the carrier model but cannot independently prove width versus height because both contribute to volume; that distinction remains a manual-review field.
- **Old Dominion:** the session-bound prepared tab correctly paused for manual account selection and did not alter the selected account. The carrier accepted destination ZIP 59106 through its own Enter path but populated the hidden committed ZIP, Billings, MT, and USA more than twenty seconds later without displaying a Google Places suggestion popup. The original lookup deadline ended before that carrier-owned commit, so no commodity or service field was touched. The adapter must wait for the delayed exact carrier state while preserving the rule against fabricating hidden ZIP or location fields.

No carrier rate or Submit button was used during these checks.

## Final release-candidate checks

The final 2.8.0 Bridge candidate was rebuilt and manually reloaded before a new synthetic handoff was prepared.

- **DSV passed the live fill-only check.** The Bridge reported `Filled and verified`; both exact carrier ZIP chips were committed, delivery appointment was selected, and the one commodity row contained quantity 1, 48 × 40 × 23 inches, 224 pounds, class 100, and NMFC 79300.
- **TQL passed the final live fill-only check.** Its delivery control reused a Kendo option node, identified the exact postal result through `aria-activedescendant`, and nested the result type in a `Postal Code` chip alongside saved-address and Favorites entries. The adapter now prefers that exact postal result, excludes saved-address chips, and still requires the committed city/state after selection. After reload, the Bridge reported `Filled and verified`; pickup 80022 resolved to Commerce City, CO, destination 59106 resolved to Billings, MT, appointment delivery was selected, and class 100, NMFC 79300, commodity description, and shipment reference were exact. After 42 seconds the carrier summaries showed handling unit 1, total weight 224.00, linear feet 4.00, density 8.76, and estimated class 100. TQL's width-versus-height distinction remains a manual-review item because the native Kendo editors stay visually blank and the summaries do not expose those dimensions independently.
- **Old Dominion is blocked by the carrier account gate.** After normal sign-in on the session-bound prepared tab, Old Dominion replaced the account list with: `Your username requires an account linkage update. Please email Internet.CustomerService@odfl.com, and we will be able to assist you promptly.` No workaround or email was attempted. The earlier signed-in attempt confirmed manual account selection and exact carrier-owned origin/destination resolution, but the final candidate could not reach commodity and service fields. This is not recorded as a live pass.

Automated release checks passed: Bridge 70/70 tests, TypeScript checking, and production build; frontend 22/22 tests and production build; full Python suite (183 tests), Ruff, release synchronization, and whitespace checks. No rate request or Submit action was used.

## 3.2 one-pass broker run

**Why pages needed manual tabbing.** Broker tabs opened in the background. Chrome clamps timers in hidden tabs (at least one second per wait, less often later) and pauses animation frames, so the carriers' Kendo, PrimeNG, Angular and Google Places controls did not render dropdowns, and every short Bridge wait took a second or more until the person looked at the tab.

**What happens now.** After **Send** in Quote Helper the Bridge opens all broker tabs at once (the pages load in parallel) and the background service worker runs them in one pass:

- Each broker tab gets a visible turn in order: the Bridge activates it, and the page fills and verifies while visible. The run moves on when the tab reports *ready*, *review*, *needs you* (sign-in, ODFL account choice) or *stopped*, or after the turn limit (45 s; one 30 s revisit for a page still filling). Pages keep filling in the background between turns, so most are finished when their turn comes. The on-page indicator shows "Filling 2 of 5…".
- A page that failed while it was hidden (and not because of a person's edit) retries once when it becomes visible.
- After the pass, pages that need the person are shown one at a time with "Needs you: sign in or choose the account here". There is no time limit; once the person signs in, the page fills while visible and the run continues with the next page that needs them. A page that comes back from sign-in in the background gets fresh turns.
- At the end the Bridge shows the first page that needs the person, otherwise the first ready page.
- The run does not fight the person. Activating a broker tab yourself pauses the run on that tab ("Bridge paused while you use this tab"); it resumes when you return to Quote Helper, switch to another Chrome window or to a non-broker tab, or when the page you signed in on finishes. Switching to another application only stops the run from raising its window. When you work in another window, the run still activates tabs in the broker window but no longer brings that window to the front.
- The run state is kept in `chrome.storage.session`; a suspended service worker continues on the next tab event, page report or `chrome.alarms` wake-up. A run ends after 20 minutes at the latest (handoffs expire after 15).

**Broker window.** The broker tabs open in one dedicated normal Chrome window, reused while it stays open, instead of next to Quote Helper. The installed Quote Helper is a Chrome app window, which cannot hold tabs (Chrome would put them in whichever browser window was used last), and rotating tabs inside the person's own browsing window would disturb unrelated work. A separate window can be in front while the pages fill, so the active broker page is really visible; a page in a covered or minimized window counts as hidden to Chrome. If Chrome cannot create the window, the Bridge falls back to tabs beside Quote Helper.

**Manifest.** `alarms` was added so a suspended service worker wakes for the next turn deadline. `chrome.windows` needs no permission.

**Accessorials.** The packet's `limited_access` and `inside_delivery` flags pass through to the Bridge. No adapter has a control for either that is identified in a fixture or a live check, so none is filled, and no selector was guessed. When either is requested the page still finishes; the indicator, the event message and the first event fields say "Set manually: Limited access" (or "Inside delivery"), and Quote Helper's broker chip shows the page as needing attention. EchoShip and TQL treat the location type as one choice, so with limited access they leave it untouched for the manual Limited access selection instead of forcing Business or Commercial. EchoShip now opens its "Delivery Accessorials" panel whenever a requested control is not rendered (appointment delivery, which defaults to on, used to fail when neither residential nor liftgate was requested) and never clicks the toggle when everything is already there, so a retry does not close the panel.

**Manual notes.** A field can be a note rather than a failure: "set" for a service to choose manually, "check" for a value the carrier does not let the Bridge read back (TQL's blank length, width and height editors). Notes stay visible ("Ready for review: Filled and verified. Set manually: …") but never trigger a refill, repair or a longer wait. Before this change TQL's dimension note counted as a failure, so every TQL fill waited out the full 10 s verification, refilled once and waited another 10 s.

**Automatic Bridge reload.** The installer replaces the Bridge folder, but Chrome keeps running the old code until the extension is reloaded. Quote Helper's `ensureBridgeCurrent(expectedVersion)` pings the Bridge; if it is older it sends `BUILD2QUOTE_RELOAD` once, waits for the Bridge to come back and pings again. The Bridge accepts the request only from the local Quote Helper page, answers before reloading, refuses while a run or an unexpired handoff is active, and refuses a second reload of the same running version within two minutes; Quote Helper also asks at most once per page load and not again for the same versions within ten minutes. Bridges from before this change do not know the message: load the new Bridge once per PC in `chrome://extensions`; later updates reload it automatically.

## 3.2 timing changes

All values below need a live fill-only check on each broker site before release; the fixture tests prove the logic, not carrier behavior. Every verification step remains: values are read back, the stable window still requires an uninterrupted hold, refills stay limited to one and structural repairs to three, and the durability monitor keeps verifying after a page reports ready.

| Where | Old | New | Reason |
| --- | --- | --- | --- |
| Hidden broker tabs (all) | Timer waits clamped to ≥1 s while hidden; animation frames paused | Visible turn per tab (one-pass run); short pauses (≤1 s) keep time with a MessageChannel loop while hidden; condition waits wake on DOM mutations | Main cause of manual tabbing |
| Content script: wait for the carrier form | Mutation or 1.2 s timer per check, 45 s limit | Wakes on every mutation, 400 ms fallback poll, 45 s limit | Same limit, faster pickup |
| Post-fill stable verification (`durability.ts`) | 1.5 s stable window, 75 ms timer poll, 10 s timeout always used up while anything failed (twice with the refill) | 0.8 s stable window, wakes on mutations; returns once the same failures have persisted 3 s; 10 s cap kept; manual notes never block | The window only needs to outlast carrier re-renders; a page that is not converging gains nothing from 7 more seconds |
| TQL dimension note | Counted as a failure: +10 s wait, a full second fill, +10 s wait on every TQL fill | Non-blocking "check" note, still shown as "Check manually: pallet N dimensions" | Largest single TQL saving |
| TQL verified carrier model | 5.2 s minimum stable window, 5.8 s timeout, 0.4 s failure grace | 1.0 s stable, then 0.6 s with no DOM change in the quote form (a late Kendo control replacement restarts it), at most 3 s of continuous success; 6 s timeout; 0.4 s grace kept | The fixed window guarded against late re-renders; waiting for the form to go quiet covers them directly, and the durability monitor checks again afterwards |
| TQL settle loop | 22 s deadline, ≤2 numeric restores | 12 s deadline, ≤2 numeric restores | Matches the shorter verification windows |
| TQL Kendo numeric editors | Fixed 150 ms after input, after a replaced control and after blur (2–3 per field, 5 fields per pallet) | Until the quote form has had 50 ms without DOM changes, at most 150 ms | Waits for the re-render instead of a fixed pause |
| TQL location stability, combo selection, `setAndConfirm` | 0.5 s stable within 10 s; 3.5 s; 15 ms + ≤0.6 s | Unchanged (mutation-woken) | Already condition waits |
| Postal autocomplete (`autocomplete.ts`) | 30 ms timer poll for the control | Mutation-woken wait; 7 s / 45 s deadlines and the 100 ms wake unchanged | — |
| Generic choice and option lists (`dom.ts`) | Fixed 200 ms / 250 ms after opening | Wait for the option, ≤1.5 s | Condition wait |
| Generic and EchoShip added rows | Fixed 500 ms per added row | Wait for the new row, ≤3 s; stop adding when none appears | Condition wait |
| EchoShip origin card cleared | Fixed 300 ms | Wait for the origin input, ≤2 s | Condition wait |
| EchoShip accessorial panel | Fixed 150 ms after opening (only for residential or liftgate) | Opened whenever a needed control is missing; wait for the controls, ≤1.5 s | Condition wait; appointment fix |
| MyUnishippers | 600 ms stable city/state within 10 s; 100 ms before services; 75 ms after ZIPs | 600 ms stable kept; 100 ms pause removed; 75 ms kept (throttle-resistant) | The stable location window already covers the services |
| DSV | 35 ms per numeric field, 75 ms per row, 300 ms after the last row | 35 ms per field kept (throttle-resistant); 75 ms and 300 ms removed | The post-fill stable verification waits for the values to hold |
| Old Dominion | 35 s location commit, 2 s suggestion grace, 4 s rows, 1.5 s class list, 40 ms end pause | All limits kept (mutation-woken); 40 ms end pause removed | Carrier-owned commits stay generous |
| Health monitor, repair backoff | 3 s interval, 260 ms debounce, 250/650/1200 ms backoff | Unchanged (backoff throttle-resistant) | — |

## 3.2 live checks still required

Run each with a synthetic shipment and an isolated database; fill only, no rate request or Submit.

- **All brokers:** Send to all five. Confirm the broker window comes to the front, each tab is shown in order with "Filling n of 5…", sign-in pages wait with "Needs you…" and resume after sign-in, clicking a broker tab pauses the run, returning to Quote Helper resumes it without raising the broker window, and the run ends on the first page that needs you, else the first ready page. Repeat with Quote Helper covering the broker window to see how much a covered window slows the background pages.
- **TQL:** the shortened verified-model window. Watch whether TRAX resets its summaries after the page reports ready; the durability monitor must then show Review required or repair. Check the 50 ms Kendo quiet period against the numeric editors.
- **EchoShip:** a shipment with appointment delivery only (no residential or liftgate) and a retry with the accessorial panel open; limited access leaves the location type alone.
- **DSV and MyUnishippers:** the removed per-row and end pauses with two or more pallets.
- **Old Dominion:** account selection through the "Needs you" step, then the fill.
- **Limited access and inside delivery:** each broker shows "Set manually" and Quote Helper's chip shows needing attention.
- **Bridge update:** install a newer Quote Helper over a running one with the new Bridge already loaded once; the Bridge must reload by itself on the next Quote Helper start without a Chrome restart.

## Release boundary

Production deployment and carrier rate submission remain outside this verification. A successful local or live fill does not establish carrier acceptance, warehouse approval, or a freight rate.
