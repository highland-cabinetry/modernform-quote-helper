# Preview the WORKSPACE build of Quote Helper V2 against a copy of the live database.
#
# Usage: double-click 'Preview Quote Helper.bat', or from PowerShell in the project folder:
#     .\scripts\preview.ps1 [-NoBrowser]
#
# - Copies data\quote_helper_v2.sqlite3 with the SQLite backup API (scripts\backup_database.py)
#   to tmp\preview\quote_helper_v2-preview.sqlite3. Each start replaces the previous preview copy.
#   The live database is opened read-only and is never served by the preview.
# - Serves the workspace backend\app and frontend\dist (not the release selected in deployment.json)
#   on the first free port from 8010 to 8019, in review mode (QUOTE_HELPER_REVIEW_MODE=1).
# - Changes made in the preview are discarded at the next preview start.
# - Production (Run Quote Helper V2.bat, ports 8000-8009) can run at the same time.
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
    $RootItem = Get-Item -LiteralPath $ProjectRoot
    if ($RootItem.LinkType -eq 'Junction') {
        $ProjectRoot = @($RootItem.Target)[0]
    }
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $Backend = Join-Path $ProjectRoot "backend"
    $FrontendDist = Join-Path $ProjectRoot "frontend\dist"
    $LiveDatabase = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "data\quote_helper_v2.sqlite3"))
    $PreviewDir = Join-Path $ProjectRoot "tmp\preview"
    $PreviewDatabase = [System.IO.Path]::GetFullPath((Join-Path $PreviewDir "quote_helper_v2-preview.sqlite3"))

    if ($PreviewDatabase -ieq $LiveDatabase) {
        throw "The preview database path resolves to the live database. Preview stopped."
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "V2 is not installed yet. Double-click 'Setup Quote Helper V2.bat' first."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDist "index.html"))) {
        throw "The workspace interface has not been built (frontend\dist\index.html is missing). Double-click 'Setup Quote Helper V2.bat' first."
    }

    $RunningPreview = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*uvicorn app.main:app*" -and
        $_.CommandLine -like "*$ProjectRoot*" -and
        $_.CommandLine -match '--port\s+801[0-9](?:\s|$)'
    } | Select-Object -First 1
    if ($RunningPreview) {
        throw "A preview is already running. Close its window (or press Ctrl+C there) and start the preview again."
    }

    & $Python -c "import fastapi, uvicorn, openpyxl, pdfplumber, reportlab"
    if ($LASTEXITCODE -ne 0) {
        throw "The V2 Python environment is incomplete. Run 'Setup Quote Helper V2.bat' again."
    }

    # Fresh copy every start: remove the previous preview database and its companions.
    New-Item -ItemType Directory -Force -Path $PreviewDir | Out-Null
    foreach ($Suffix in @("", "-wal", "-shm", "-journal")) {
        $Old = "$PreviewDatabase$Suffix"
        if (Test-Path -LiteralPath $Old) { Remove-Item -LiteralPath $Old -Force }
    }
    $PreviewBackups = Join-Path $PreviewDir "backups"
    if (Test-Path -LiteralPath $PreviewBackups) { Remove-Item -LiteralPath $PreviewBackups -Recurse -Force }

    if (Test-Path -LiteralPath $LiveDatabase) {
        Write-Host "Copying the live database for the preview..." -ForegroundColor DarkGray
        $BackupScript = Join-Path $ProjectRoot "scripts\backup_database.py"
        $Copied = & $Python $BackupScript --database $LiveDatabase --output $PreviewDatabase
        if ($LASTEXITCODE -ne 0) {
            throw "The live database could not be copied for the preview. Nothing was changed."
        }
        Write-Host "Preview database: $Copied" -ForegroundColor DarkGray
    } else {
        Write-Host "No live database found; the preview starts with an empty database." -ForegroundColor Yellow
    }

    $Port = $null
    foreach ($CandidatePort in 8010..8019) {
        $Listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            $CandidatePort
        )
        try {
            $Listener.Start()
            $Port = $CandidatePort
            break
        } catch {
            continue
        } finally {
            $Listener.Stop()
        }
    }
    if ($null -eq $Port) {
        throw "Ports 8010 through 8019 are already in use. Close another preview or local server and try again."
    }

    $env:QUOTE_HELPER_V2_DATABASE = $PreviewDatabase
    $env:QUOTE_HELPER_FRONTEND_DIST = $FrontendDist
    $env:QUOTE_HELPER_REVIEW_MODE = '1'
    # Packing worker subprocesses import "app" too; make them use the same workspace backend.
    $env:PYTHONPATH = $Backend

    $AppUrl = "http://127.0.0.1:$Port"
    Write-Host ""
    Write-Host "Quote Helper V2 PREVIEW (workspace build, copied data) is starting..." -ForegroundColor Cyan
    Write-Host "Preview address: $AppUrl" -ForegroundColor White
    Write-Host "Changes made here are NOT saved to the live database." -ForegroundColor Yellow
    Write-Host "Press Ctrl+C here when you are finished." -ForegroundColor DarkGray
    Write-Host ""

    if (-not $NoBrowser) {
        Start-Job -ScriptBlock {
            param($HealthUrl, $BrowserUrl)
            for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
                try {
                    Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1 | Out-Null
                    Start-Process $BrowserUrl
                    return
                } catch {
                    Start-Sleep -Seconds 1
                }
            }
        } -ArgumentList "$AppUrl/api/health", $AppUrl | Out-Null
    }

    & $Python -m uvicorn app.main:app --app-dir $Backend --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "The preview server stopped with exit code $LASTEXITCODE."
    }
} catch {
    Write-Host ""
    Write-Host "Quote Helper V2 preview could not start." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Use 'Preview Quote Helper.bat' so this message remains visible." -ForegroundColor Yellow
    exit 1
}
