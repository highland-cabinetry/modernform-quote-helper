# Quote Helper: information for IT

Quote Helper is a freight-quoting program for Highland Cabinetry's freight team. It runs only on
the PC it is installed on and opens in a Google Chrome app window. This page explains what it
installs and how to approve it **once**, so later updates need no new approval.

## What is installed, and where

The installer, `ModernformQuoteHelperSetup-<version>.exe` (Inno Setup), installs **for the current user
only**. It does not need administrator rights and writes nothing to Program Files, the registry's
machine hive or Windows services.

| Location | Contents |
| --- | --- |
| `%LOCALAPPDATA%\Programs\Modernform Quote Helper\runtime\` | The official Windows embeddable Python 3.13 from python.org and pinned Python packages. `python.exe`, `pythonw.exe` and `python313.dll` are signed by the **Python Software Foundation**. Package binaries (`.pyd`, `.dll`) come from PyPI wheels. |
| `...\Modernform Quote Helper\app\`, `desktop\`, `bridge\` | Quote Helper's own files: Python source (`.py`), the web interface (`.html`, `.js`, `.css`), images and JSON. `bridge\` is a Chrome extension the user loads once with *Load unpacked* (Chrome's Developer mode; a Chrome policy that blocks developer-mode extensions also blocks it). |
| `...\Modernform Quote Helper\unins000.exe` | The uninstaller. |
| `...\Modernform Quote Helper\previous\`, `update-work\` | Created by updates: the version an update replaced (for going back), and scratch space. |
| `%LOCALAPPDATA%\ModernformQuoteHelper\` | `config.json` (settings), `logs\`, `run\`, `updates\` (where update files are placed). |
| The data folder chosen at install | The database (SQLite), imported documents and backups. |

The Start-menu shortcut runs `runtime\pythonw.exe desktop\launcher.pyw`. Every Quote Helper
process is `pythonw.exe` (or `python.exe`) from the `runtime` folder, plus Chrome.

Each release also publishes `ModernformQuoteHelperRuntime-<version>-SHA256.txt`: the SHA-256 of every
`.exe`, `.dll` and `.pyd` in `runtime\`. These files do not change between releases unless the
Python version or a package version changes, which happens rarely and is stated in the release.

## Network use

- Quote Helper listens on **127.0.0.1 only** (port 8000, or the next free one up to 8009). It is
  not reachable from other computers.
- It makes **no outbound connections**: no update downloads, no telemetry, no licence checks.
- The Build2Quote Bridge Chrome extension fills in freight-broker websites in the user's own Chrome
  session. It does not store passwords and never submits or books anything by itself.

## Approving it once

**If the installer is code-signed** (the release notes say so; Windows shows "Verified
publisher"):

1. Allow the installer and uninstaller with a **publisher rule** for our signing certificate.
2. Allow the runtime with a **publisher rule** for the Python Software Foundation (product
   "Python", files `python.exe` and `pythonw.exe`).

**If the installer is not signed:**

1. Allow `ModernformQuoteHelperSetup-<version>.exe` once, by its **file hash** (SHA-256 published with the
   release). SmartScreen shows "Windows protected your PC" the first time; choose *More info* →
   *Run anyway*.
2. Allow the runtime with a **publisher rule** for the Python Software Foundation, as above. A
   **path rule** for `%LOCALAPPDATA%\Programs\Modernform Quote Helper\runtime\*` also works, but it
   is weaker because the user can write to that folder.

If AppLocker **DLL rules** are enforced (uncommon), also allow the `.pyd`/`.dll` files in
`runtime\` by hash (from the SHA-256 list) or by a path rule. These hashes carry over from release
to release while the runtime is unchanged.

## Later updates: no new approval

Later versions are delivered as an **update package**, `ModernformQuoteHelperUpdate-<version>.zip`. The
user places it in the updates folder (or a shared folder IT configures) and clicks *Install now*
in Quote Helper. Then:

- **No installer runs** and no administrator rights are used. Quote Helper itself replaces its
  `app\`, `desktop\` and `bridge\` folders. The approved, PSF-signed `python.exe`/`pythonw.exe` run
  the new code.
- The package **cannot contain executables or scripts Windows runs by itself**: Quote Helper
  refuses any package with `.exe`, `.dll`, `.pyd`, `.msi`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.lnk` and
  similar files, and any package that would change the `runtime\` folder. So there is nothing new
  for SmartScreen, AppLocker or antivirus to approve.
- Every file is checked against the SHA-256 in the package's manifest before anything is replaced.
  If our update signing key is configured, the package must also carry a valid signature from it.
- The data folder and `config.json` are never touched. The database is backed up before the new
  version first opens it. The previous version is kept, and the user can go back with one click
  (Start menu › Quote Helper Tools › Go back to the previous version).

The full installer is needed again only when a release changes the bundled Python runtime. Quote
Helper says so itself ("needs the full installer"). With a publisher rule for our certificate,
that installer is already allowed. With hash rules, approve the new installer's hash.

## Optional: a shared update folder

To have every PC pick up updates from one place, install with
`ModernformQuoteHelperSetup-<version>.exe /UPDATEFOLDER="\\server\share\Quote Helper updates"`. Or add
`"update_folder": "<path>"` to `%LOCALAPPDATA%\ModernformQuoteHelper\config.json`. Users need only
read access to it.

## Removing it

Settings › Apps › Quote Helper › Uninstall removes the program folder. The data folder and
`%LOCALAPPDATA%\ModernformQuoteHelper` are left in place on purpose. Delete them by hand if the data
is no longer needed.
