[CmdletBinding()]
param(
    [int]$ApiPort = 8765,
    [int]$WebPort = 5174,
    [string]$DataRepositoryRoot = "",
    [switch]$EnableBackgroundServices,
    [string]$PythonPath = "",
    [switch]$NoBrowser,
    [int]$ExitAfterSeconds = 0
)

$ErrorActionPreference = "Stop"
$PanelRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepositoryRoot = (Resolve-Path (Join-Path $PanelRoot "..")).Path
$FrontendRoot = Join-Path $PanelRoot "frontend-next"
if ($DataRepositoryRoot) {
    $RepositoryRoot = (Resolve-Path -LiteralPath $DataRepositoryRoot).Path
}
$ArtifactsRoot = Join-Path $PanelRoot "artifacts\logs"
$VenvPython = Join-Path $PanelRoot ".venv\Scripts\python.exe"

if (-not $PythonPath) {
    if (Test-Path -LiteralPath $VenvPython) {
        $PythonPath = $VenvPython
    }
    else {
        $PythonPath = (Get-Command python -ErrorAction Stop).Source
    }
}

$NodePath = (Get-Command node.exe -ErrorAction Stop).Source
$ViteCli = Join-Path $FrontendRoot "node_modules\vite\bin\vite.js"
$DatabaseRoot = Join-Path $RepositoryRoot "runtime\db_decrypt\all"
$ReportPath = Join-Path $RepositoryRoot "runtime\db_decrypt\decryption_report.json"
$FtsExtension = Join-Path $RepositoryRoot "dist\weixin_fts_tokenizer.dll"
$CachePath = Join-Path $PanelRoot ".cache\analytics.sqlite"

# A first-run user creates the snapshot from the UI. FTS availability is
# reported by diagnostics instead of preventing the application from starting.
foreach ($RequiredPath in @($FrontendRoot, $ViteCli)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required local path does not exist: $RequiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $ArtifactsRoot | Out-Null
$env:WECHAT_DB_ROOT = $DatabaseRoot
$env:WECHAT_DECRYPTION_REPORT = $ReportPath
$env:WECHAT_ANALYTICS_CACHE = $CachePath
$env:WECHAT_FTS_EXTENSION = $FtsExtension
if (-not $env:WEIXIN_DLL_PATH) {
    $env:WEIXIN_DLL_PATH = "D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll"
}
$env:VITE_API_PROXY = "http://127.0.0.1:$ApiPort"
$env:WECHAT_CHAT_ONLY = $(if ($EnableBackgroundServices) { '0' } else { '1' })

function Test-HttpEndpoint {
    param([string]$Uri)
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
        return $Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

$ApiListeners = @(Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue)
$WebListeners = @(Get-NetTCPConnection -State Listen -LocalPort $WebPort -ErrorAction SilentlyContinue)
if ($ApiListeners.Count -gt 0 -or $WebListeners.Count -gt 0) {
    $ApiReady = $ApiListeners.Count -gt 0 -and (Test-HttpEndpoint "http://127.0.0.1:$ApiPort/api/health")
    $WebReady = $WebListeners.Count -gt 0 -and (Test-HttpEndpoint "http://127.0.0.1:$WebPort/")
    if ($ApiReady -and $WebReady) {
        $ApiOwners = ($ApiListeners.OwningProcess | Sort-Object -Unique) -join ","
        $WebOwners = ($WebListeners.OwningProcess | Sort-Object -Unique) -join ","
        Write-Host "Panel is already running."
        Write-Host "Web: http://127.0.0.1:$WebPort/ (PID $WebOwners)"
        Write-Host "API: http://127.0.0.1:$ApiPort/api (PID $ApiOwners)"
        if (-not $NoBrowser) {
            Start-Process "http://127.0.0.1:$WebPort/"
        }
        return
    }

    $BusyPorts = @()
    if ($ApiListeners.Count -gt 0) { $BusyPorts += "$ApiPort (PID $(($ApiListeners.OwningProcess | Sort-Object -Unique) -join ','))" }
    if ($WebListeners.Count -gt 0) { $BusyPorts += "$WebPort (PID $(($WebListeners.OwningProcess | Sort-Object -Unique) -join ','))" }
    throw "Panel cannot start because these ports are already occupied: $($BusyPorts -join '; ')"
}

$ApiOut = Join-Path $ArtifactsRoot "api.out.log"
$ApiErr = Join-Path $ArtifactsRoot "api.err.log"
$WebOut = Join-Path $ArtifactsRoot "web.out.log"
$WebErr = Join-Path $ArtifactsRoot "web.err.log"
Remove-Item -Force -ErrorAction SilentlyContinue $ApiOut, $ApiErr, $WebOut, $WebErr

$ApiProcess = $null
$WebProcess = $null

function Wait-HttpReady {
    param([string]$Uri, [int]$TimeoutSeconds)
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) {
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 400
        }
    } while ([DateTime]::UtcNow -lt $Deadline)
    throw "Timed out waiting for $Uri"
}

try {
    $ApiProcess = Start-Process -FilePath $PythonPath `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", $ApiPort, "--lifespan", "on") `
        -WorkingDirectory $PanelRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $ApiOut -RedirectStandardError $ApiErr

    Wait-HttpReady -Uri "http://127.0.0.1:$ApiPort/api/health" -TimeoutSeconds 180

    $WebProcess = Start-Process -FilePath $NodePath `
        -ArgumentList @($ViteCli, "--host", "127.0.0.1", "--port", $WebPort) `
        -WorkingDirectory $FrontendRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $WebOut -RedirectStandardError $WebErr

    Wait-HttpReady -Uri "http://127.0.0.1:$WebPort/" -TimeoutSeconds 90

    Write-Host "Web: http://127.0.0.1:$WebPort/"
    Write-Host "API: http://127.0.0.1:$ApiPort/api"
    Write-Host "Logs: $ArtifactsRoot"
    Write-Host "Press Ctrl+C to stop both services."

    if (-not $NoBrowser) {
        Start-Process "http://127.0.0.1:$WebPort/"
    }

    $StartedAt = [DateTime]::UtcNow
    while (-not $ApiProcess.HasExited -and -not $WebProcess.HasExited) {
        Start-Sleep -Seconds 1
        $ApiProcess.Refresh()
        $WebProcess.Refresh()
        if ($ExitAfterSeconds -gt 0 -and ([DateTime]::UtcNow - $StartedAt).TotalSeconds -ge $ExitAfterSeconds) {
            break
        }
    }

    if ($ApiProcess.HasExited) {
        throw "API process exited with code $($ApiProcess.ExitCode). See $ApiErr"
    }
    if ($WebProcess.HasExited) {
        throw "Web process exited with code $($WebProcess.ExitCode). See $WebErr"
    }
}
finally {
    foreach ($Process in @($WebProcess, $ApiProcess)) {
        if ($null -ne $Process) {
            try {
                if (-not $Process.HasExited) {
                    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
                    $null = $Process.WaitForExit(5000)
                }
            }
            catch {
                Write-Warning "Could not stop process $($Process.Id): $($_.Exception.Message)"
            }
        }
    }
}
