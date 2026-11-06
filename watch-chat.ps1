[CmdletBinding()]
param(
    [switch]$FromStart,
    [switch]$IncludeOutgoing,
    [switch]$RawContent,
    [double]$Duration = 0,
    [double]$PollInterval = 0.5,
    [string]$Database = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ScriptPath = Join-Path $PSScriptRoot 'wx_hook_bridge\python\tools\watch_chat.py'
$UvPath = (Get-Command uv -ErrorAction Stop).Source
$ReceiverArgs = @('--poll-interval', $PollInterval.ToString([Globalization.CultureInfo]::InvariantCulture))
if ($FromStart) { $ReceiverArgs += '--from-start' }
if ($IncludeOutgoing) { $ReceiverArgs += '--include-outgoing' }
if ($RawContent) { $ReceiverArgs += '--raw-content' }
if ($Duration -lt 0 -or [double]::IsNaN($Duration) -or [double]::IsInfinity($Duration)) {
    throw 'Duration must be zero or a finite positive number.'
}
if ($Duration -gt 0) { $ReceiverArgs += @('--duration', $Duration.ToString([Globalization.CultureInfo]::InvariantCulture)) }
if ($Database) { $ReceiverArgs += @('--database', $Database) }
& $UvPath run --python 3.11 $ScriptPath @ReceiverArgs
exit $LASTEXITCODE
