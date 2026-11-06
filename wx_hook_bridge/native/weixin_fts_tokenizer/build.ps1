param(
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$BuildDir = Join-Path $ScriptRoot "build"
$HeaderDir = Join-Path $BuildDir "sqlite-amalgamation-3530400"
$ZipFile = Join-Path $BuildDir "sqlite-amalgamation-3530400.zip"
$SourceFile = Join-Path $ScriptRoot "weixin_fts_tokenizer.c"

if (-not $OutFile) {
    $OutFile = Join-Path $RepoRoot "dist\weixin_fts_tokenizer.dll"
}
$OutFile = [IO.Path]::GetFullPath($OutFile)

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null

if (-not (Test-Path (Join-Path $HeaderDir "sqlite3ext.h"))) {
    if (-not (Test-Path $ZipFile)) {
        Invoke-WebRequest `
            -Uri "https://www.sqlite.org/2026/sqlite-amalgamation-3530400.zip" `
            -OutFile $ZipFile
    }
    Expand-Archive -LiteralPath $ZipFile -DestinationPath $BuildDir -Force
}

$VcVarsCandidates = @(
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
$VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path -LiteralPath $VsWhere) {
    $Installation = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -eq 0 -and $Installation) {
        $VcVarsCandidates = @((Join-Path $Installation "VC\Auxiliary\Build\vcvars64.bat")) + $VcVarsCandidates
    }
}
$VcVars = $VcVarsCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $VcVars) {
    throw "vcvars64.bat was not found"
}

$ObjectFile = Join-Path $BuildDir "weixin_fts_tokenizer.obj"
$ImportLibrary = Join-Path $BuildDir "weixin_fts_tokenizer.lib"
$Command = @"
call "$VcVars" >nul 2>&1 && cd /d "$BuildDir" && cl /nologo /LD /O2 /W4 /utf-8 /I"$HeaderDir" /Fo:"$ObjectFile" "$SourceFile" /Fe:"$OutFile" /link /NOLOGO /IMPLIB:"$ImportLibrary" kernel32.lib
"@

& cmd.exe /d /s /c $Command
if ($LASTEXITCODE -ne 0) {
    throw "MSVC build failed with exit code $LASTEXITCODE"
}

Write-Output $OutFile
