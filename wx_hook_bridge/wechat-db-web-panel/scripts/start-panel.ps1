[CmdletBinding()]
param(
    [int]$ApiPort = 8765,
    [int]$WebPort = 5173
)

$ErrorActionPreference = "Stop"

$PanelRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepositoryRoot = (Resolve-Path (Join-Path $PanelRoot "..")).Path
$FrontendRoot = Join-Path $PanelRoot "frontend-next"
$LogsRoot = Join-Path $PanelRoot "artifacts\logs"

$VenvPython = Join-Path $PanelRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $VenvPython) {
    $PythonPath = $VenvPython
}
else {
    $PythonPath = (Get-Command python -ErrorAction Stop).Source
}

$NodePath = (Get-Command node.exe -ErrorAction Stop).Source
$ViteCli = Join-Path $FrontendRoot "node_modules\vite\bin\vite.js"

$DatabaseRoot = Join-Path $RepositoryRoot "runtime\db_decrypt\all"
$ReportPath = Join-Path $RepositoryRoot "runtime\db_decrypt\decryption_report.json"
$FtsExtension = Join-Path $RepositoryRoot "dist\weixin_fts_tokenizer.dll"
$CachePath = Join-Path $PanelRoot ".cache\analytics.sqlite"

foreach ($RequiredPath in @($ViteCli)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required local path does not exist: $RequiredPath"
    }
}

New-Item -ItemType Directory -Force -Path $LogsRoot | Out-Null

$env:WECHAT_DB_ROOT = $DatabaseRoot
$env:WECHAT_DECRYPTION_REPORT = $ReportPath
$env:WECHAT_ANALYTICS_CACHE = $CachePath
$env:WECHAT_FTS_EXTENSION = $FtsExtension
if (-not $env:WEIXIN_DLL_PATH) {
    $env:WEIXIN_DLL_PATH = "D:\Program Files\Tencent\Weixin\4.1.9.23\Weixin.dll"
}
$env:VITE_API_PROXY = "http://127.0.0.1:$ApiPort"

# Refuse to start a second copy on the same ports.
foreach ($Port in @($ApiPort, $WebPort)) {
    $Busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($Busy) {
        throw "Port $Port is already in use by PID $($Busy.OwningProcess). Stop it first or pick another port."
    }
}

$ApiOut = Join-Path $LogsRoot "api.out.log"
$ApiErr = Join-Path $LogsRoot "api.err.log"
$WebOut = Join-Path $LogsRoot "web.out.log"
$WebErr = Join-Path $LogsRoot "web.err.log"

$Api = Start-Process -FilePath $PythonPath `
    -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort") `
    -WorkingDirectory $PanelRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $ApiOut -RedirectStandardError $ApiErr

$Web = & (Join-Path $PSScriptRoot 'start-web.ps1') -WebPort $WebPort -ApiPort $ApiPort -PythonPath $PythonPath -NodePath $NodePath

Write-Output "API_PID=$($Api.Id)"
Write-Output "WEB_PID=$($Web.WorkerPid)"
Write-Output "WEB_SUPERVISOR_PID=$($Web.SupervisorPid)"
Write-Output "Web=http://127.0.0.1:$WebPort/analysis/overview"
Write-Output "API=http://127.0.0.1:$ApiPort/api"
Write-Output "Logs=$LogsRoot"
