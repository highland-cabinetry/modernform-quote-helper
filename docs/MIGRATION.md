# V2 migration — September 8, 2026

## Active installation

`D:\OneDrive - Highland Cabinetry 08 INC\FREIGHT\Quote Helper V2`

The application is running from this directory at `http://127.0.0.1:8000`, backend 2.6.0, production packing engine 2.4.0, Lab 0.1.0. The review found the old backend still serving 2.5.1; that process was stopped for cutover.

Migrated:

- Backend, frontend, Build2Quote Bridge source, tests, samples, documentation, setup/run scripts, and original lockfiles.
- Fresh `.venv` using the previous installed application dependency versions, with an editable install pointing at this directory. Setuptools/wheel were installed to register the project after the first registration attempt identified missing build tooling.
- Fresh frontend and Bridge dependencies from frozen lockfiles; both production distributions rebuilt here.
- Consistent SQLite snapshot with all 363 items, 65 Builder shipments, 15 aliases, 5 manual quotes, 37 manual quote revisions, 34 quote snapshots, and existing settings/schema records.
- All six historical database backups.
- Production Shaker master, original Shaker item list, initial V2 template, and existing inspection metadata under `reference/item-master/`.
- Audit/context records and improvement recommendations.

Old caches, temporary work, and nonportable virtual environments were not treated as application source. They were retained at the previous location; fresh dependencies were prepared here. The legacy desktop Quote Helper app was not migrated or modified.

## Compatibility and rollback

Old directory:
`D:\OneDrive - Highland Cabinetry 08 INC\FREIGHT\Quote Helper\quote_helper_v2`

Windows held that root directory open, so a whole-directory rename was unavailable. Instead:

- Old `run.ps1` and `setup.ps1` forward to the new scripts, preserving existing V2 batch shortcuts.
- Old `data` is a junction to the new `data`, so those paths resolve to one live database.
- Old `build2quote_bridge/dist` is a junction to the new built extension, preserving the Chrome unpacked-extension path.
- The former data and dist directories were retained with `.pre-migration-20260908` suffixes.
- A dated copy of original source/builds/data is preserved at `D:\OneDrive - Highland Cabinetry 08 INC\FREIGHT\Quote Helper\quote_helper_v2.pre-migration-20260908`, excluding disposable dependency/cache/tmp directories.

The original data snapshots are historical rollback material. Do not launch the archived app against them as a second active installation. If rollback becomes necessary, first snapshot the current live database so subsequent work is not lost; then restore code and runtime deliberately.

## Verification

- 110 backend tests passed; Ruff passed. One existing Starlette/TestClient dependency deprecation warning remains.
- 7 frontend tests passed; production build succeeded.
- 30 Bridge tests passed; TypeScript typecheck and build succeeded.
- All 95 original files outside intentionally updated launch/config files and the README migration notice match intake hashes. All 99 intake files match in the rollback archive.
- Final database transfer used SQLite backup and compared sorted row content digests for every table, including schema metadata. All tables matched and integrity was `ok`: `audit/migration-database-verification.json`.
- Fresh app import resolved to the new `backend/app` directory.
- Temporary cloned-database server on port 8011 was healthy and then stopped.
- Production health endpoint and browser page confirmed 2.6.0, 363 items, and no old-backend warning after cutover.
- Existing builds were rebuilt, but broker ZIP/login bugs identified in the audit were not changed or signed off.

## Daily use

- Open `http://127.0.0.1:8000` while the current service is running.
- Use `Stop Quote Helper V2.bat` to stop the background service started during migration.
- For normal subsequent launches, double-click `Run Quote Helper V2.bat`; it keeps the run window available for Ctrl+C shutdown.
- Setup remains `Setup Quote Helper V2.bat`; stop the app before setup.
- Chrome still had Bridge 2.5.1 loaded during the review. Reload **Build2Quote Bridge** manually in `chrome://extensions` to load the rebuilt 2.6.0 files through the preserved extension path. Browser security blocked automated access to that management page. No extension permissions or identity were changed.

Source migration is complete. UI/packing/broker recommendations remain proposed work in `IMPECCABLE_AUDIT.md` and `IMPROVEMENT_ROADMAP.md`.
