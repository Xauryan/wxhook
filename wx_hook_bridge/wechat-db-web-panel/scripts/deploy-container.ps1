[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PanelRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$FrontendRoot = Join-Path $PanelRoot 'frontend-next'

# The native backend must stay on Windows for process/handle verification.
$Status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/chat/status' -TimeoutSec 10
if (-not $Status.bridge_connected) {
    throw 'The Windows chat API or WeChat bridge is not ready. No services were stopped.'
}
$Listeners = @(Get-NetTCPConnection -State Listen -LocalPort 5174 -ErrorAction SilentlyContinue)
$Container = docker compose --project-directory $PanelRoot ps -q web
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the task-owned Compose project.' }
if ($Listeners.Count -gt 0 -and -not $Container) {
    throw 'Port 5174 is already occupied outside this Compose service. Stop that preview explicitly before deployment.'
}

Push-Location $FrontendRoot
try {
    & pnpm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed; the existing deployment was not changed.' }
}
finally { Pop-Location }

Push-Location $PanelRoot
try {
    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose configuration.' }
    & docker compose up -d --build --wait --wait-timeout 60 web
    if ($LASTEXITCODE -ne 0) { throw 'Container deployment failed. Inspect docker compose logs web.' }
    $Check = Invoke-RestMethod -Uri 'http://127.0.0.1:5174/api/chat/status' -TimeoutSec 15
    if ($Check.account -ne $Status.account -or -not $Check.bridge_connected) {
        throw 'Container is running but current-account API acceptance did not pass.'
    }
    Write-Host 'Container preview: http://127.0.0.1:5174/chats'
    Write-Host 'The verified Windows backend and WeChat bridge remain host services.'
}
finally { Pop-Location }
