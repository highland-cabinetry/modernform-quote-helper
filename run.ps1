param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    # Old V2 shortcuts may enter through the migration junction.
    $RootItem = Get-Item -LiteralPath $ProjectRoot
    if ($RootItem.LinkType -eq 'Junction') {
        $ProjectRoot = @($RootItem.Target)[0]
    }
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $AppRoot = $ProjectRoot
    $DeploymentPath = Join-Path $ProjectRoot 'deployment.json'
    if (Test-Path -LiteralPath $DeploymentPath) {
        $Deployment = Get-Content -LiteralPath $DeploymentPath -Raw | ConvertFrom-Json
        $AppRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Deployment.release_path))
        $ReleaseRoot = (Join-Path $ProjectRoot 'releases') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $AppRoot.StartsWith($ReleaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'The deployment path must identify a build inside the releases folder.'
        }
    }
    $Backend = Join-Path $AppRoot "backend"
    # Packing workers are separate Python processes: make them import the selected
    # release's code, not the workspace copy registered by the editable install.
    $env:PYTHONPATH = $Backend
    $Frontend = Join-Path $AppRoot "frontend\dist\index.html"
    $env:QUOTE_HELPER_V2_DATABASE = Join-Path $ProjectRoot 'data\quote_helper_v2.sqlite3'
    $env:QUOTE_HELPER_FRONTEND_DIST = Join-Path $AppRoot 'frontend\dist'
    $env:QUOTE_HELPER_REVIEW_MODE = '0'

    $RunningServer = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*uvicorn app.main:app*" -and
        $_.CommandLine -like "*$ProjectRoot*" -and
        $_.CommandLine -match '--port\s+800[0-9](?:\s|$)'
    } | Select-Object -First 1
    if ($RunningServer) {
        throw (
            "Quote Helper V2 is already running. Use 'Stop Quote Helper V2.bat' or press Ctrl+C in its Run window, " +
            "then start it again so updated backend code is loaded."
        )
    }

    if (-not (Test-Path -LiteralPath $Python)) {
        throw "V2 is not installed yet. Double-click 'Setup Quote Helper V2.bat' first."
    }
    if (-not (Test-Path -LiteralPath $Frontend)) {
        throw "The warehouse interface has not been built. Double-click 'Setup Quote Helper V2.bat' first."
    }

    & $Python -c "import fastapi, uvicorn, openpyxl, pdfplumber, reportlab"
    if ($LASTEXITCODE -ne 0) {
        throw "The V2 Python environment is incomplete. Run 'Setup Quote Helper V2.bat' again."
    }

    $Port = $null
    foreach ($CandidatePort in 8000..8009) {
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
        throw "Ports 8000 through 8009 are already in use. Close another local server and try again."
    }

    $AppUrl = "http://127.0.0.1:$Port"
    Write-Host ""
    Write-Host "Freight Quote Helper V2 is starting..." -ForegroundColor Cyan
    Write-Host "Local address: $AppUrl" -ForegroundColor White
    Write-Host "Keep this window open while using Quote Helper." -ForegroundColor Yellow
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
        throw "The local server stopped with exit code $LASTEXITCODE."
    }
} catch {
    Write-Host ""
    Write-Host "Quote Helper V2 could not start." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Use 'Run Quote Helper V2.bat' so this message remains visible." -ForegroundColor Yellow
    exit 1
}
