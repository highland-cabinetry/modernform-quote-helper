# Deployment, releases and data safety

> Modernform Quote Helper uses the same installer and update-package system as Highland's Quote Helper,
> under its own names: program folder `%LOCALAPPDATA%\Programs\Modernform Quote Helper`, settings in
> `%LOCALAPPDATA%\ModernformQuoteHelper`, ports 8030–8039, files `ModernformQuoteHelperSetup-<version>.exe` and
> `ModernformQuoteHelperUpdate-<version>.zip`. Where this document says "Quote Helper", read "Modernform Quote Helper".

Quote Helper runs on the freight workstation. GitHub is the source of truth for code; it does not
host the application. Operational data never lives in Git or in a release folder.

## Installed app (3.1 and later) — how the freight team runs Quote Helper

From 3.1, production is a normal Windows program installed from `ModernformQuoteHelperSetup-<version>.exe`.
It needs no Python, Node or Setup on the PC; only Google Chrome (for the app window and the
Build2Quote Bridge).

### What lives where

| Location | Contents | Changed by an update? |
| --- | --- | --- |
| `%LOCALAPPDATA%\Programs\Modernform Quote Helper\` | Program: `app\` (backend, built UI, `release.json`), `desktop\` (launcher, updater), `bridge\` (Build2Quote Bridge) | **Replaced** by an update package or the installer |
| `...\Modernform Quote Helper\runtime\` | Bundled Python 3.13 and pinned packages from `desktop\requirements.lock` (`runtime.json` records them) | Only by the installer |
| `...\Modernform Quote Helper\previous\`, `update-work\` | The version the last update package replaced (for rolling back); scratch space while one is applied | By update packages; removed by the installer |
| The data folder chosen at first install | `quote_helper_v2.sqlite3`, `source_files\`, `backups\` | **Never** (only the app writes to it) |
| `%LOCALAPPDATA%\ModernformQuoteHelper\config.json` | Which data folder and port to use, optional `update_folder` | Never (written once, at first install) |
| `%LOCALAPPDATA%\ModernformQuoteHelper\updates\` | Where update packages can be dropped | Never (the user puts files there) |
| `%LOCALAPPDATA%\ModernformQuoteHelper\logs\`, `run\` | `server.log`, `update.log`; the running server's port and stop token, the last update result | Rewritten by the app |

Uninstalling removes only the program folder. The data folder and `%LOCALAPPDATA%\ModernformQuoteHelper`
(settings, updates folder, logs) stay. What IT needs to know to allow it once:
[IT_APPROVAL.md](IT_APPROVAL.md).

### Installing on a PC

1. Download `ModernformQuoteHelperSetup-<version>.exe` from the repository's GitHub **Releases** page.
2. Run it. No administrator rights are needed.
3. On the **Data folder** page:
   - to keep using the existing database, choose the `data` folder inside the Quote Helper V2 folder
     (`D:\OneDrive - Highland Cabinetry 08 INC\FREIGHT\Quote Helper V2\data`);
   - for a new PC, keep the default (`%LOCALAPPDATA%\ModernformQuoteHelper\data`).
4. Close any open `Run Quote Helper V2` window, then open **Quote Helper** from the Start menu.
5. Once per PC, load the Bridge: `chrome://extensions` → Developer mode → remove any older
   "Build2Quote Bridge" → **Load unpacked** → Start menu › Quote Helper Tools › Build2Quote Bridge folder.

### Updating with an update package (normal)

Each release publishes `ModernformQuoteHelperUpdate-<version>.zip` next to the installer. It holds only
`app\`, `bridge\` and `desktop\` and `update-manifest.json` (version, the runtime it needs, build
commit, SHA-256 of every file), optionally signed (`update-manifest.sig`). It never contains the
runtime or any `.exe`/`.dll`/`.pyd`/script file, so applying it runs no installer and gives
SmartScreen, AppLocker and antivirus nothing new to evaluate: IT approves the program once
([IT_APPROVAL.md](IT_APPROVAL.md)).

1. Put the zip in the updates folder (Start menu › Quote Helper Tools › **Updates folder**, i.e.
   `%LOCALAPPDATA%\ModernformQuoteHelper\updates`) or in the shared folder named by `update_folder` in
   `config.json` (for example a OneDrive or network folder; set it at install with
   `/UPDATEFOLDER="<path>"`).
2. Quote Helper checks when it opens and every 30 minutes, and shows "Quote Helper <version> is
   ready to install". Click **Install now**: the window shows "Updating…" and reloads on the new
   version. (Or use Start menu › Quote Helper Tools › **Check for updates** or **Install update
   file**, which asks for the zip.)

What happens (`desktop/desktop_update.py`, run by `launcher.pyw --apply-update <zip>` from a copy of
the launcher, because `desktop\` itself is replaced):

1. The package is checked while the old version keeps running: the signature (when a key is
   pinned), that it is newer than the installed version (`--reinstall` allows the same one), that
   the installed runtime has the Python version and exact package versions it needs, and every
   file's SHA-256. A package that fails any check changes nothing.
2. It is unpacked into `update-work\incoming` and checked again on disk.
3. The server is stopped (as Stop Quote Helper does; database writes finish first).
4. `app\`, `bridge\`, `desktop\` are swapped one at a time with renames, each recorded in
   `update-work\journal.json` before it happens. If a rename fails (a file in use), the renames
   already made are undone and the old version starts again. If the PC stops half-way, the next
   start of Quote Helper undoes them. The replaced folders become `previous\`.
5. The new version starts. Its first start makes the verified database backup
   (`backups\quote-helper-v2-before-update-<version>-<time>.sqlite3`, newest ten kept). If it does
   not start, the previous version is put back and started.

The result is in `%LOCALAPPDATA%\ModernformQuoteHelper\run\update-result.json` (the app window shows
it after a failed attempt) and `logs\server.log`. Restart Chrome afterwards so the Bridge reloads.

If a release changes the bundled Python or a package in `desktop\requirements.lock`, its update
package is refused with "needs the full installer": run `ModernformQuoteHelperSetup-<version>.exe` instead.

Version order: numbers compare numerically, and a suffix marks a later build of the same version
(`3.1.0-ci.2` and `3.1.0-hotfix.1` are newer than `3.1.0`); Quote Helper does not publish pre-releases.

### Updating with the installer

Run the newer `ModernformQuoteHelperSetup-<version>.exe` (needed for a new runtime; always allowed). It:

1. stops the running Quote Helper (it finishes any database write first);
2. deletes the old program files, including `previous\` and `update-work\`, and installs the new
   ones (no stale code is left behind);
3. leaves the data folder and `config.json` untouched (the data-folder page is not shown again).

On its first start, the new version makes a verified backup, as above, before it changes anything,
then applies any additive schema changes. Restart Chrome afterwards so the Bridge reloads. Saved
drafts and UI choices stay, because the app window keeps the same address
(`http://127.0.0.1:8000`) and Chrome profile.

### Rolling back

After an update package: Start menu › Quote Helper Tools › **Go back to the previous version**
(`launcher.pyw --rollback-update`). It swaps the program folders with `previous\` the same safe way;
running it again returns to the newer version. After an installer update, install the previous
version's installer over the new one. Schema changes are additive, so an older version can open a
database a newer one has used. If a future change ever cannot be read by older versions, it raises
`DATABASE_COMPATIBILITY` in `backend/app/database.py`; older versions then refuse to open the
database with an explanation instead of misreading it. To return to the database exactly as it was
before an update, stop Quote Helper and copy the matching `before-update` backup over
`quote_helper_v2.sqlite3` (keep the current file first).

### Signing

Both kinds of signing are optional. Without the secrets below, the workflow builds unsigned files
and passes.

**Installer (Authenticode).** Lets IT allow every future installer with one publisher rule, and
removes SmartScreen's "unknown publisher" warning once the certificate has reputation. The
workflow signs `ModernformQuoteHelperSetup-<version>.exe` and its uninstaller while Inno Setup builds them
(`SignTool=` and `SignedUninstaller=yes`, enabled by `/DSign` and `/Sqhsign=...` on ISCC). Add
**one** of these sets of repository secrets (Settings › Secrets and variables › Actions):

- (a) A code-signing certificate file (from a certificate authority, or one IT issues if all PCs
  trust IT's CA): `CODE_SIGNING_PFX_BASE64` (the `.pfx` as base64:
  `[Convert]::ToBase64String([IO.File]::ReadAllBytes('cert.pfx'))`) and `CODE_SIGNING_PFX_PASSWORD`.
  It is imported into the runner's certificate store, used by thumbprint and timestamped by
  DigiCert. Since 2023 public CAs issue code-signing keys only on hardware tokens or cloud HSMs, so
  a `.pfx` usually means an internal (IT) certificate.
- (b) Azure Artifact Signing (formerly Trusted Signing; Microsoft's signing service, about
  $10/month, needs an organization identity validation): `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
  `AZURE_CLIENT_SECRET` (an app registration with the "Artifact Signing Certificate Profile
  Signer" role on the account), `AZURE_SIGNING_ENDPOINT` (for example
  `https://eus.codesigning.azure.net`), `AZURE_SIGNING_ACCOUNT` and `AZURE_SIGNING_PROFILE`. The
  workflow downloads Microsoft's signtool plug-in (`Microsoft.ArtifactSigning.Client`, pinned by
  `ARTIFACT_SIGNING_CLIENT_VERSION`) and signs through it. The official
  `azure/artifact-signing-action` signs files after they are built, which cannot reach the
  uninstaller inside the installer, so it is not used.

On every run, the test-only installer is signed with a throwaway self-signed certificate, and the
test checks that both it and the installed uninstaller carry that signature. This proves the
signing settings without real secrets.

**Update packages (Ed25519).** Lets installed copies refuse an update package that did not come
from our builds (for example a changed zip in a shared folder). Create a key pair once with
`python scripts/build_desktop.py --generate-signing-key` and store the private key as the
repository secret `UPDATE_SIGNING_KEY` (never in the repository). Builds made with the secret write
the public key to `desktop\update-signing-key.pub` (so the installer and every update package pin
it) and sign `update-manifest.json`. An installed copy with a pinned key accepts only packages
signed with it. A copy without one (installed from an unsigned build) accepts signed and unsigned
packages, and is then pinned by the first signed package it installs. Trade-off: unsigned packages
are protected only by their checksums, which catch damage but not a deliberate change by someone
who can write to the update folder. Anyone who can write to the user's program folder can change
the program either way, so signing protects the delivery path, not a compromised PC. Losing the
private key means installed copies can no longer be updated with packages (the installer still
works and pins the new key). The Python runtime itself is never in a package; its files keep the
Python Software Foundation's own signatures.

### Publishing a release

1. Merge the work into `main` (CI green).
2. Set the version in `release.json`, run `python scripts\sync_release.py`, commit.
3. Tag the commit `v<version>` and push the tag — or, without a tag: GitHub › Actions › **Desktop installer** ›
   **Run workflow** on `main` with **Publish** ticked (the workflow creates the tag and the release).
4. The **Desktop installer** workflow (`.github/workflows/desktop.yml`) builds the installer and the
   update package on Windows, runs the backend tests on the exact bundled runtime, installs it,
   applies an update package (from the launcher and from the app window) and rolls it back,
   upgrades with the installer over a running copy, checks that data, settings and the pre-update
   backups are right and that no executable changed, uninstalls it, and then publishes
   `ModernformQuoteHelperSetup-<version>.exe`, `ModernformQuoteHelperUpdate-<version>.zip` and
   `ModernformQuoteHelperRuntime-<version>-SHA256.txt` on the GitHub release. Pull requests and `feature/**`
   pushes run the same checks and attach the files as build artifacts.

Keep `PYTHON_EMBED_VERSION` (workflow) and `desktop\requirements.lock` unchanged unless there is a
reason to change them. While they are unchanged, every release's runtime is byte-identical (IT's
approval carries over) and update packages can be used. Changing them makes the next release
installer-only.

### Starting, stopping and troubleshooting

- The Start-menu shortcut runs `runtime\pythonw.exe desktop\launcher.pyw`: it starts the server in
  the background if it is not already running and opens a Chrome app window. Closing the window
  leaves the server running; **Quote Helper Tools › Stop Quote Helper** stops it.
- If Chrome is missing it opens in Edge (no broker autofill). If port 8000 is taken by the old
  `Run Quote Helper V2` window, it says so instead of starting a second copy.
- Problems at start are shown in a message box; details are in
  `%LOCALAPPDATA%\ModernformQuoteHelper\logs\server.log`.
- `runtime\python.exe desktop\launcher.pyw --status` prints the running version, port and data folder,
  the previous version kept for rollback and the last update result; `--check-updates` prints what
  the update folders hold and whether it can be installed.
- An update that could not be installed says why in the app window and in
  `%LOCALAPPDATA%\ModernformQuoteHelper\run\update-result.json`; the version that was running is
  still in place. Output of an update started from the window is in `logs\update.log`.

## Workstation release folders (development, and production before 3.1)

The sections below describe the source checkout in the Quote Helper V2 folder. It remains the place
for development and for **Preview** (a copy of the live database). Once the installed app is in use,
`Run Quote Helper V2.bat`, `cut_release.py` and `switch_release.py` are no longer needed for
production; they are kept for rolling back to 3.0 or earlier.

### What lives where

| Path | Contents | In Git | In a release |
| --- | --- | --- | --- |
| `backend/`, `frontend/src/`, `build2quote_bridge/src/`, `tests/`, `scripts/`, `docs/` | Source | Yes | backend/app only |
| `frontend/dist/`, `build2quote_bridge/dist/` | Builds made by Setup | No | Yes |
| `releases/<version>/` | Frozen builds the launcher serves | No | — |
| `deployment.json`, `deployment.previous.json` | Which release runs, and the previous one | No | — |
| `data/quote_helper_v2.sqlite3` | **Live database**: catalog, orders, quotes, shipments, expenses, settings | No | No |
| `data/backups/` | Automatic and manual verified backups | No | No |
| `data/source_files/` | Original imported documents (by SHA-256) | No | No |
| `reference/` | Catalog workbooks | No | No |

Deployments replace only `releases/<version>` and `deployment.json`. Nothing in the release process
writes to `data/`. A new release migrates the database additively on its first start, after writing
a verified backup; older releases keep working against the migrated database.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `QUOTE_HELPER_V2_DATABASE` | `data\quote_helper_v2.sqlite3` | Database file. `run.ps1` sets it to the live file; `preview.ps1` to a copy. |
| `QUOTE_HELPER_FRONTEND_DIST` | `frontend\dist` | UI build to serve. |
| `QUOTE_HELPER_REVIEW_MODE` | `0` | `1` shows the "Preview build" banner. |
| `PYTHONPATH` | — | Set by the launchers to the backend being served, so packing workers import the same code. |

No secrets or credentials are used. Broker sign-ins stay in Chrome.

### First-time installation

1. Install Python 3.12 or 3.13 and Node.js 22 with pnpm.
2. Clone the repository into `D:\OneDrive - Highland Cabinetry 08 INC\FREIGHT\Quote Helper V2`
   (or keep the existing folder; see "Updating the existing installation").
3. Double-click `Setup Quote Helper V2.bat`. It creates `.venv`, installs the backend in editable
   mode with its dev tools, installs and builds the UI and the Build2Quote Bridge.
4. Cut the first release and select it (below), then double-click `Run Quote Helper V2.bat`.

### Normal update procedure

Run each step from the project folder in PowerShell (`.\.venv\Scripts\python.exe` is shortened to
`python` here).

1. **Get the code**: `git fetch` and `git checkout` the branch or tag to release (normally `main`).
2. **Stop the app**: `Stop Quote Helper V2.bat` (Setup refuses to run while it is open).
3. **Build**: `Setup Quote Helper V2.bat`. This does not change what production serves.
4. **Back up**: `python scripts\backup_database.py --keep 20`.
5. **Preview**: `Preview Quote Helper.bat`. It copies the live database into
   `tmp\preview\quote_helper_v2-preview.sqlite3` and serves the *workspace* build on port 8010–8019
   with a "Preview build" banner. Check the pages you rely on; nothing you do there reaches live data.
6. **Cut the release**: `python scripts\cut_release.py`. It copies `backend\app`, both dists and
   `release.json` into `releases\<version>` with a SHA-256 manifest, and refuses if the release exists,
   a build is missing, or versions drift. `python scripts\cut_release.py --verify <version>` re-checks it.
7. **Switch**: `python scripts\switch_release.py --to <version>` (refuses while the app is running;
   keeps the previous selection in `deployment.previous.json`).
8. **Start**: `Run Quote Helper V2.bat`. Check `http://127.0.0.1:8000/api/health` shows the new version.
9. **Bridge**: if `build2quote_bridge` changed, reload "Build2Quote Bridge" in `chrome://extensions`.

### Health check and rollback

- `GET /api/health` returns status, app version, engine version and item count.
- Rollback: stop the app, `python scripts\switch_release.py --rollback` (or `--to <version>`), start
  it again. The database is not rolled back; the older release runs against the additively migrated
  database, which is supported. If the database itself must be restored, stop the app and copy a
  backup from `data\backups\` over `data\quote_helper_v2.sqlite3` (keep the current file first).
- `python scripts\switch_release.py --status` lists releases and the current/previous selection.

### Updating the existing installation to 3.0

The 3.0 work is on branch `feature/quote-helper-3`; 2.10.0 is preserved on
`preserve/2.10.0-worktree` (and `master`). Production keeps serving `releases\2.10.0` until you
switch. To go live with 3.0: steps 2–9 above with version 3.0.0. On first start 3.0 writes
`data\backups\quote-helper-v2-before-freight-domain-*.sqlite3`, creates the new tables and the
default company `HCI`, and records the existing saved plans and measured quotes as quotes.

## GitHub workflow

- `main` is production-ready; substantial work happens on `feature/*` branches merged by pull request.
- CI (`.github/workflows/ci.yml`) runs on pushes and pull requests: backend tests and Ruff on Ubuntu
  and Windows, UI tests and build, Bridge tests/typecheck/build, and the release-version check.
- The Desktop installer workflow builds and test-installs the installer and the update package on
  every pull request and `feature/**` push, and publishes them when a `v<version>` tag is pushed.
- Signing keys and certificates live only in repository secrets (see "Signing"), never in the
  repository.
- Do not commit `data/`, `reference/`, `releases/`, `deployment*.json`, `tmp/`, `build/`, `.venv/` or builds
  (all ignored).

## Backups

- Automatic: before a new version first opens the database (`quote-helper-v2-before-update-*`, newest
  ten kept), before every schema migration and every "Replace all data"
  (`quote-helper-v2-before-*.sqlite3`; replacement backups are pruned to the newest ten).
- Manual: `scripts\backup_database.py` (`quote-helper-v2-manual-*.sqlite3`, `--keep N` prunes only these).
- `data\` is inside OneDrive; OneDrive versioning is a second copy, not a substitute for the above.
