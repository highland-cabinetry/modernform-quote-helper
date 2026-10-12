$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    # Resolve the compatibility junction to keep one runtime and database.
    $RootItem = Get-Item -LiteralPath $ProjectRoot
    if ($RootItem.LinkType -eq 'Junction') {
        $ProjectRoot = @($RootItem.Target)[0]
    }
    $RunningServer = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*uvicorn app.main:app*" -and
        $_.CommandLine -like "*$ProjectRoot*"
    } | Select-Object -First 1
    if ($RunningServer) {
        throw (
            "Quote Helper V2 is currently running. Close its Run window or press Ctrl+C there, " +
            "then start setup again."
        )
    }
    $RuntimePython = "C:\Users\Freight\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (-not (Test-Path -LiteralPath $RuntimePython)) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "Python 3.11, 3.12, 3.13, or 3.14 is required but was not found."
        }
        $RuntimePython = $PythonCommand.Source
    }

    $BundledNodeDir = "C:\Users\Freight\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
    $BundledPnpm = "C:\Users\Freight\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
    if (Test-Path -LiteralPath (Join-Path $BundledNodeDir "node.exe")) {
        $env:PATH = "$BundledNodeDir;$env:PATH"
    }
    $PnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
    $Pnpm = if ($PnpmCommand) { $PnpmCommand.Source } elseif (Test-Path -LiteralPath $BundledPnpm) { $BundledPnpm } else { $null }
    if (-not $Pnpm) {
        throw "Node.js and pnpm are required. Install Node.js and pnpm, then run setup again."
    }

    $VirtualEnvironment = Join-Path $ProjectRoot ".venv"
    $VenvPython = Join-Path $VirtualEnvironment "Scripts\python.exe"
    Write-Host ""
    Write-Host "Setting up Freight Quote Helper V2..." -ForegroundColor Cyan
    Write-Host "This can take several minutes the first time." -ForegroundColor DarkGray

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "[1/5] Creating the private Python environment..."
        & $RuntimePython -m venv $VirtualEnvironment
        if ($LASTEXITCODE -ne 0) { throw "Python could not create the V2 environment." }
    } else {
        Write-Host "[1/5] Existing Python environment found."
    }

    Write-Host "[2/5] Installing backend components..."
    & $VenvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "pip could not prepare the backend build tools." }
    $EditableTarget = "$ProjectRoot[dev]"
    & $VenvPython -m pip install --no-build-isolation -e $EditableTarget
    if ($LASTEXITCODE -ne 0) { throw "Backend installation failed." }

    Write-Host "[3/5] Installing warehouse interface components..."
    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        & $Pnpm install
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
        Write-Host "[4/5] Building the warehouse interface..."
        & $Pnpm build
        if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
    } finally {
        Pop-Location
    }

    Write-Host "[5/5] Building Build2Quote Bridge..."
    Push-Location (Join-Path $ProjectRoot "build2quote_bridge")
    try {
        & $Pnpm install
        if ($LASTEXITCODE -ne 0) { throw "Build2Quote Bridge dependency installation failed." }
        & $Pnpm build
        if ($LASTEXITCODE -ne 0) { throw "Build2Quote Bridge production build failed." }
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "Quote Helper V2 is ready." -ForegroundColor Green
    Write-Host "Double-click 'Run Quote Helper V2.bat' to start it." -ForegroundColor White
    Write-Host "For broker autofill, load build2quote_bridge\dist once from chrome://extensions." -ForegroundColor DarkGray
} catch {
    Write-Host ""
    Write-Host "Quote Helper V2 setup failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Use 'Setup Quote Helper V2.bat' so this message remains visible." -ForegroundColor Yellow
    exit 1
}
