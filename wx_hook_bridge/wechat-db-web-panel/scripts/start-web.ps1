[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$WebPort = 5173,
    [ValidateRange(1024,65535)][int]$ApiPort = 8765,
    [string]$PythonPath = '',
    [string]$NodePath = ''
)
$ErrorActionPreference = 'Stop'
if ($WebPort -eq $ApiPort) { throw 'Web and API ports must differ.' }
$PanelRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonPath) { $PythonPath = Join-Path $PanelRoot '.venv\Scripts\python.exe' }
if (-not $NodePath) {
    $NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $NodePath = if ($NodeCommand) { $NodeCommand.Source } else { Join-Path $env:ProgramFiles 'nodejs\node.exe' }
}
foreach ($Required in @($PythonPath,$NodePath,(Join-Path $PanelRoot 'frontend-next\dist\index.html'),(Join-Path $PanelRoot 'frontend-next\scripts\serve-web.mjs'))) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) { throw "Missing existing local file: $Required" }
}
$RuntimeRoot = Join-Path $PanelRoot ".cache\runtime\web-$WebPort"
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$StatePath = Join-Path $RuntimeRoot 'state.json'
$BaseUrl = "http://127.0.0.1:$WebPort"

function Get-VerifiedWebState {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try { $State = Get-Content -LiteralPath $StatePath -Encoding UTF8 -Raw | ConvertFrom-Json }
    catch { return $null }
    if ($State.port -ne $WebPort -or $State.api_port -ne $ApiPort) { return $null }
    $Supervisor = Get-CimInstance Win32_Process -Filter "ProcessId=$($State.supervisor_pid)" -ErrorAction Stop
    if (-not $Supervisor) { return $null }
    if ($Supervisor.CommandLine -notmatch 'backend\.web_service_runner\b') { return $null }
    # A Windows venv redirector can own a child running the base Python executable.
    $OwnPath = $Supervisor.ExecutablePath -ieq $PythonPath
    if (-not $OwnPath) {
        $Parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($Supervisor.ParentProcessId)" -ErrorAction Stop
        if (-not $Parent -or $Parent.ExecutablePath -ine $PythonPath -or $Parent.CommandLine -notmatch 'backend\.web_service_runner\b') { return $null }
    }
    if ($Supervisor.CommandLine -notmatch ('--port\s+'+$WebPort+'(?:\s|$)')) { return $null }
    if ($Supervisor.CommandLine.IndexOf($RuntimeRoot,[StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
    $Created = ([datetimeoffset]$Supervisor.CreationDate).ToUnixTimeMilliseconds()/1000.0
    if ([Math]::Abs($Created - $State.supervisor_created_at) -gt 1) { return $null }
    return $State
}

$Existing = Get-VerifiedWebState
$Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $WebPort -ErrorAction SilentlyContinue)
if ($Listeners.Count -and -not $Existing) {
    throw "Port $WebPort is occupied by an unverified process. No service was stopped."
}
if (-not $Existing) {
    # WMI starts the same-user process outside this temporary command's process tree.
    # Break away from the WMI provider job as documented by Microsoft; no elevation.
    $Startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow=[uint16]0; CreateFlags=[uint32]16777216
    }
    $CommandLine = '"'+$PythonPath+'" -B -m backend.web_service_runner --port '+$WebPort+
        ' --api-port '+$ApiPort+' --runtime-root "'+$RuntimeRoot+'" --node "'+$NodePath+'"'
    $Created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine=$CommandLine; CurrentDirectory=$PanelRoot; ProcessStartupInformation=$Startup
    }
    if ($Created.ReturnValue -ne 0) {
        throw "Independent web supervisor creation failed (WMI code $($Created.ReturnValue)). Logs: $RuntimeRoot"
    }
}
$Ready = $false
$Deadline = (Get-Date).AddSeconds(45)
do {
    $State = Get-VerifiedWebState
    if ($State -and $State.status -in @('failed','stopped')) {
        throw "Web supervisor is $($State.status): $($State.reason). Logs: $RuntimeRoot"
    }
    if ($State -and $State.status -eq 'ready') {
        try {
            $Health = Invoke-RestMethod -Uri ($BaseUrl+'/__jqr_web_health') -TimeoutSec 2
            if (-not $Health.ok -or $Health.instance -ne $State.instance -or $Health.pid -ne $State.worker_pid) {
                throw 'Web worker identity mismatch.'
            }
            $Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $WebPort -ErrorAction Stop)
            if (@($Listeners | Where-Object {$_.OwningProcess -ne $Health.pid}).Count -or -not $Listeners.Count) {
                throw 'Web listener identity mismatch.'
            }
            $Page = Invoke-WebRequest -Uri ($BaseUrl+'/chats') -UseBasicParsing -TimeoutSec 3
            if ($Page.StatusCode -ne 200 -or $Page.Content -notmatch 'id="root"' -or $Page.Content -notmatch '/assets/.*\.js') {
                throw 'Web application HTML is unavailable.'
            }
            $Api = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/runtime/health" -TimeoutSec 3
            $Proxy = Invoke-RestMethod -Uri ($BaseUrl+'/api/runtime/health') -TimeoutSec 3
            if (-not $Api.ok -or $Proxy.instance -ne $Api.instance -or $Proxy.pid -ne $Api.pid) {
                throw 'Web API proxy does not reach the expected backend.'
            }
            $Ready = $true
            break
        } catch { $LastError = $_.Exception.Message }
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $Deadline)
if (-not $Ready) { throw "Web readiness failed: $LastError. Logs: $RuntimeRoot" }
[pscustomobject]@{
    WebUrl=($BaseUrl+'/chats'); SupervisorPid=$State.supervisor_pid; WorkerPid=$State.worker_pid
    Instance=$State.instance; RestartCount=$State.restart_count; Logs=$RuntimeRoot; ApiPid=$Api.pid
}
