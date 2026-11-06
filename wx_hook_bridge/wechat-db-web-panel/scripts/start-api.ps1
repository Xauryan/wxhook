[CmdletBinding()]
param(
    [string]$DataRepositoryRoot = '',
    [string]$PythonPath = '',
    [int]$ApiPort = 8765
)

$ErrorActionPreference = 'Stop'
$PanelRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$DataRoot = if ($DataRepositoryRoot) {
    (Resolve-Path -LiteralPath $DataRepositoryRoot).Path
} else {
    (Resolve-Path -LiteralPath (Join-Path $PanelRoot '..')).Path
}
if ($ApiPort -lt 1 -or $ApiPort -gt 65535) { throw 'Invalid API port.' }
if (Get-NetTCPConnection -State Listen -LocalPort $ApiPort -ErrorAction SilentlyContinue) {
    throw "Port $ApiPort is already occupied. No existing service was stopped."
}
if (-not $PythonPath) {
    $VenvPython = Join-Path $PanelRoot '.venv\Scripts\python.exe'
    $PythonPath = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { (Get-Command python -ErrorAction Stop).Source }
}
$Environment = @{
    WECHAT_DB_ROOT = Join-Path $DataRoot 'runtime\db_decrypt\all'
    WECHAT_DECRYPTION_REPORT = Join-Path $DataRoot 'runtime\db_decrypt\decryption_report.json'
    WECHAT_FTS_EXTENSION = Join-Path $DataRoot 'dist\weixin_fts_tokenizer.dll'
    WECHAT_ANALYTICS_CACHE = Join-Path $PanelRoot '.cache\analytics.sqlite'
    WECHAT_CHAT_ONLY = '1'
}
foreach ($Required in @($PythonPath, (Join-Path $DataRoot 'python\tools\wechat_db_decrypt.py'))) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Required existing local path missing: $Required" }
}
# Foreground API only; leave the existing frontend container and WeChat untouched.
$Previous = @{}
foreach ($Name in $Environment.Keys) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    [Environment]::SetEnvironmentVariable($Name, $Environment[$Name], 'Process')
}
Push-Location $PanelRoot
try {
    & $PythonPath -m uvicorn backend.app.main:app --host 127.0.0.1 --port $ApiPort --lifespan on
    if ($LASTEXITCODE -ne 0) { throw "API exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
    foreach ($Name in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], 'Process')
    }
}
