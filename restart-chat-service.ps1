[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$ApiPort = 8765,
    [ValidateRange(1024,65535)][int]$WebPort = 5173
)
$ErrorActionPreference = 'Stop'
$PanelRoot = Join-Path $PSScriptRoot 'wx_hook_bridge\wechat-db-web-panel'
$RepositoryRoot = Split-Path -Parent $PanelRoot
$PythonPath = Join-Path $PanelRoot '.venv\Scripts\python.exe'
$DllPath = 'C:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll'
$WebStartScript = Join-Path $PanelRoot 'scripts\start-web.ps1'
if ($WebPort -eq $ApiPort) { throw 'Web and API ports must differ.' }
foreach ($Required in @($PythonPath,$DllPath,$WebStartScript,(Join-Path $PanelRoot 'frontend-next\dist\index.html'),(Join-Path $RepositoryRoot 'runtime\db_decrypt\decryption_report.json'))) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Missing required path: $Required" }
}
function Get-ApiListeners {
    @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $ApiPort })
}
$Listeners = @(Get-ApiListeners)
$Owners = @($Listeners.OwningProcess | Select-Object -Unique)
if ($Owners.Count -gt 1) { throw 'Multiple port owners found; no service was stopped.' }
$PortPattern = '--port\s+' + $ApiPort + '(?:\s|$)'
$ProgramPattern = '(?:uvicorn\s+backend\.app\.main:app|backend\.service_runner\b)'
$PythonProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop)
$OwnedBackends = @()
foreach ($Candidate in $PythonProcesses) {
    if ($Candidate.CommandLine -notmatch $ProgramPattern -or $Candidate.CommandLine -notmatch $PortPattern) { continue }
    $CandidateParent = @($PythonProcesses | Where-Object { $_.ProcessId -eq $Candidate.ParentProcessId }) | Select-Object -First 1
    $OwnPath = $Candidate.ExecutablePath -ieq $PythonPath
    $ParentPath = $CandidateParent -and $CandidateParent.ExecutablePath -ieq $PythonPath -and $CandidateParent.CommandLine -match $ProgramPattern -and $CandidateParent.CommandLine -match $PortPattern
    if ($OwnPath -or $ParentPath) { $OwnedBackends += $Candidate }
}
$OwnedIds = @($OwnedBackends.ProcessId)
if ($Owners.Count -eq 1 -and $OwnedIds -notcontains $Owners[0]) { throw 'Port owner is not the verified project backend; no service was stopped.' }
# A failed Windows accept loop can leave the importer alive after its socket closes.
# Stop only exact project/command/port matches, including that non-listening backend.
foreach ($Candidate in @($OwnedBackends | Sort-Object @{Expression={ if ($_.CommandLine -match 'backend\.service_runner') { 0 } else { 1 } }}, @{Expression={ $_.ExecutablePath -ieq $PythonPath }}, ProcessId)) {
    $BackendProcess = Get-Process -Id $Candidate.ProcessId -ErrorAction SilentlyContinue
    if ($BackendProcess) {
        try { Stop-Process -InputObject $BackendProcess -Force -Confirm:$false -ErrorAction Stop }
        catch { if (-not $BackendProcess.HasExited) { throw } }
    }
}
for ($Wait = 0; $Wait -lt 30; $Wait++) {
    if (-not (Get-ApiListeners)) { break }
    Start-Sleep -Milliseconds 100
}
if (Get-ApiListeners) { throw 'Backend port did not become free.' }
$Environment = @{
    PYTHONUTF8='1'; PYTHONIOENCODING='utf-8'; PYTHONDONTWRITEBYTECODE='1'
    WECHAT_CHAT_ONLY='1'; WECHAT_FRIEND_AUTOMATION_ENABLED='0'; WEIXIN_DLL_PATH=$DllPath
    WECHAT_DB_ROOT=(Join-Path $RepositoryRoot 'runtime\db_decrypt\all')
    WECHAT_DECRYPTION_REPORT=(Join-Path $RepositoryRoot 'runtime\db_decrypt\decryption_report.json')
    WECHAT_FTS_EXTENSION=(Join-Path $RepositoryRoot 'dist\weixin_fts_tokenizer.dll')
    WECHAT_ANALYTICS_CACHE=(Join-Path $PanelRoot '.cache\analytics.sqlite')
}
$Previous = @{}
$LogRoot = Join-Path $env:TEMP ('jqr-api-restart-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
try {
    foreach ($Name in $Environment.Keys) {
        $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name,'Process')
        [Environment]::SetEnvironmentVariable($Name,$Environment[$Name],'Process')
    }
    $Started = Start-Process -FilePath $PythonPath -ArgumentList @('-m','backend.service_runner','--port',[string]$ApiPort,'--log-root',$LogRoot) -WorkingDirectory $PanelRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogRoot 'runner.out.log') -RedirectStandardError (Join-Path $LogRoot 'runner.err.log') -PassThru
} finally {
    foreach ($Name in $Previous.Keys) { [Environment]::SetEnvironmentVariable($Name,$Previous[$Name],'Process') }
}
$BaseUrl = 'http://127.0.0.1:' + $ApiPort
$Ready = $false
for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
    try { $Docs = Invoke-WebRequest -Uri ($BaseUrl + '/api/docs') -UseBasicParsing -TimeoutSec 2; $Ready = $Docs.StatusCode -eq 200; if ($Ready) { break } } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $Ready) { throw "Backend startup failed. Read logs in $LogRoot" }
$CurrentListeners = @(Get-ApiListeners)
if ($CurrentListeners.Count -eq 0) { throw 'Started backend does not own a listening port.' }
foreach ($CurrentOwnerId in @($CurrentListeners.OwningProcess | Select-Object -Unique)) {
    $CurrentOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$CurrentOwnerId"
    $Ancestor = $CurrentOwner
    $BelongsToStarted = $false
    for ($Depth = 0; $Depth -lt 8 -and $Ancestor; $Depth++) {
        if ($Ancestor.ProcessId -eq $Started.Id) { $BelongsToStarted = $true; break }
        $Ancestor = Get-CimInstance Win32_Process -Filter "ProcessId=$($Ancestor.ParentProcessId)" -ErrorAction Stop
    }
    if (-not $BelongsToStarted) { throw 'API port is owned by a different process; restart verification failed.' }
}
$Status = Invoke-RestMethod -Uri ($BaseUrl + '/api/chat/status') -TimeoutSec 20
for ($VerifyAttempt = 0; $VerifyAttempt -lt 40; $VerifyAttempt++) {
    if ($Status.account -and $Status.bridge_connected -and $Status.contacts_verified -and -not $Status.contacts_syncing -and -not $Status.contacts_error) { break }
    Start-Sleep -Milliseconds 500
    $Status = Invoke-RestMethod -Uri ($BaseUrl + '/api/chat/status') -TimeoutSec 20
}
if (-not $Status.contacts_verified -or $Status.contacts_error) { throw ('API started, but active WeChat source verification failed: ' + $Status.contacts_error + '. Logs: ' + $LogRoot) }
if ($Status.account -and $Status.bridge_connected -and $Status.send_enabled) {
    $Web = & $WebStartScript -ApiPort $ApiPort -WebPort $WebPort -PythonPath $PythonPath
    Write-Host "API restarted. Parent PID: $($Started.Id). Logs: $LogRoot"
    Write-Host "Web page verified: $($Web.WebUrl)"
    Write-Host "Web supervisor PID: $($Web.SupervisorPid). Worker PID: $($Web.WorkerPid). Logs: $($Web.Logs)"
    Write-Host 'API, web application and API proxy verified. No message was sent by this restart script.'
    Write-Host 'Active WeChat source and send gate verified. WeChat and its bridge were not stopped.'
} else {
    throw 'API started, but the current WeChat bridge or verified send gate is not ready.'
}
