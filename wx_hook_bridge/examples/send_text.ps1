# Write a send_text outbox command and wait for the bridge to clear it / write a result.
# Usage (from repository root, after inject is running):
#   powershell -ExecutionPolicy Bypass -File examples\send_text.ps1 -To filehelper -Content "hello from bridge"
param(
    [Parameter(Mandatory = $true)]
    [string]$To,
    [Parameter(Mandatory = $true)]
    [string]$Content,
    [int]$TimeoutSec = 20
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Outbox = Join-Path $RepoRoot "bridge\outbox\next.txt"
$Control = Join-Path $RepoRoot "bridge\control"
New-Item -ItemType Directory -Force -Path (Split-Path $Outbox) | Out-Null
New-Item -ItemType Directory -Force -Path $Control | Out-Null

$seq = [string]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
# Format: seq \t type \t wxid \t content
# type 1 = send_text
$line = "$seq`t1`t$To`t$Content"
# UTF-8 without BOM (native reader treats file as raw bytes / UTF-8 text)
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Outbox, $line, $utf8)
Write-Host "wrote outbox: $Outbox"
Write-Host "line: $line"

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$resultPath = Join-Path $Control "result_$seq.json"
while ((Get-Date) -lt $deadline) {
    if (-not (Test-Path $Outbox) -or ((Get-Content $Outbox -Raw -ErrorAction SilentlyContinue) -eq "")) {
        Write-Host "outbox cleared (command consumed)"
    }
    if (Test-Path $resultPath) {
        Write-Host "result:"
        Get-Content $resultPath -Raw
        exit 0
    }
    $status = Join-Path $Control "status.txt"
    if (Test-Path $status) {
        $s = Get-Content $status -Raw -ErrorAction SilentlyContinue
        if ($s) { Write-Host "status: $($s.Trim())" }
    }
    Start-Sleep -Milliseconds 300
}

Write-Host "timeout waiting for result_$seq.json"
Write-Host "check: bridge\control\status.txt and that inject_bridge.py is still running"
exit 1
