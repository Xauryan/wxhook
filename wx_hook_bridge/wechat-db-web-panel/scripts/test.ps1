[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$PanelRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $PanelRoot "frontend"
$VenvPython = Join-Path $PanelRoot ".venv\Scripts\python.exe"

if (-not $PythonPath) {
    $PythonPath = if (Test-Path -LiteralPath $VenvPython) {
        $VenvPython
    }
    else {
        (Get-Command python -ErrorAction Stop).Source
    }
}
$NpmPath = (Get-Command npm.cmd -ErrorAction Stop).Source

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Write-Host "`n== $Label =="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $PanelRoot
try {
    Invoke-Checked "pytest" { & $PythonPath -m pytest }
    Invoke-Checked "Vitest" { & $NpmPath --prefix $FrontendRoot run test }
    Invoke-Checked "Frontend build" { & $NpmPath --prefix $FrontendRoot run build }
    if (-not $SkipBrowser) {
        $env:PLAYWRIGHT_PYTHON = $PythonPath
        Invoke-Checked "Playwright e2e" { & $NpmPath --prefix $FrontendRoot run test:e2e }
        Invoke-Checked "Playwright visual" { & $NpmPath --prefix $FrontendRoot run test:visual }
    }
}
finally {
    Pop-Location
}
