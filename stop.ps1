$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootItem = Get-Item -LiteralPath $ProjectRoot
if ($RootItem.LinkType -eq 'Junction') { $ProjectRoot = @($RootItem.Target)[0] }
$RunningServers = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -like '*uvicorn app.main:app*' -and
    $_.CommandLine -like "*$ProjectRoot*" -and
    $_.CommandLine -match '--port\s+800[0-9](?:\s|$)'
}
foreach ($Server in $RunningServers) {
    Stop-Process -Id $Server.ProcessId -ErrorAction SilentlyContinue
}
Write-Host 'Quote Helper V2 stopped. Saved shipments remain in the local database.'
