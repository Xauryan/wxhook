param(
    [string]$OutFile = "",
    [switch]$SyntaxOnly
)

$ErrorActionPreference = "Stop"
$NativeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $NativeRoot "..\..")).Path
$IncludeDir = Join-Path $NativeRoot "src\include"
$VerifyScript = Join-Path $NativeRoot "verify_boundaries.py"
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    & py -3 $VerifyScript
} else {
    & python $VerifyScript
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$Sources = @(
    (Join-Path $NativeRoot "src\wx_hook_bridge.c"),
    (Join-Path $NativeRoot "src\modules\state.c"),
    (Join-Path $NativeRoot "src\modules\paths.c"),
    (Join-Path $NativeRoot "src\modules\memory.c"),
    (Join-Path $NativeRoot "src\modules\file_io.c"),
    (Join-Path $NativeRoot "src\modules\text.c"),
    (Join-Path $NativeRoot "src\modules\account_info.c"),
    (Join-Path $NativeRoot "src\modules\protocol.c"),
    (Join-Path $NativeRoot "src\modules\send_runtime.c"),
    (Join-Path $NativeRoot "src\modules\wechat_string.c"),
    (Join-Path $NativeRoot "src\modules\friend_accept.c"),
    (Join-Path $NativeRoot "src\modules\contact_lookup.c"),
    (Join-Path $NativeRoot "src\modules\revoke_protocol.c"),
    (Join-Path $NativeRoot "src\modules\debug_log.c"),
    (Join-Path $NativeRoot "src\modules\forward_protocol.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_runtime.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_status.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_trace.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_closed_loop.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_control.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_sentinels.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_receive_route.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_noclick.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_cold.c"),
    (Join-Path $NativeRoot "src\modules\hongbao_hooks.c"),
    (Join-Path $NativeRoot "src\modules\anti_revoke.c"),
    (Join-Path $NativeRoot "src\modules\protocol_capture.c"),
    (Join-Path $NativeRoot "src\modules\a8key_task.c"),
    (Join-Path $NativeRoot "src\modules\h5ext_transfer.c"),
    (Join-Path $NativeRoot "src\modules\finder_ops.c"),
    (Join-Path $NativeRoot "src\modules\pay_transfer.c"),
    (Join-Path $NativeRoot "src\modules\open_url.c"),
    (Join-Path $NativeRoot "src\modules\browser_events.c"),
    (Join-Path $NativeRoot "src\modules\sns_timeline.c"),
    (Join-Path $NativeRoot "src\modules\sns_publish.c"),
    (Join-Path $NativeRoot "src\modules\sns_comment.c"),
    (Join-Path $NativeRoot "src\modules\worker_control.c"),
    (Join-Path $NativeRoot "src\modules\voice_probe.c"),
    (Join-Path $NativeRoot "src\modules\voice_transcribe.c"),
    (Join-Path $NativeRoot "src\modules\media_ops.c"),
    (Join-Path $NativeRoot "src\modules\chatroom_member.c"),
    (Join-Path $NativeRoot "src\modules\revoke_notice_event.c"),
    (Join-Path $NativeRoot "src\modules\revoke_hooks.c"),
    (Join-Path $NativeRoot "src\modules\room_contacts.c"),
    (Join-Path $NativeRoot "src\modules\forward_builders.c"),
    (Join-Path $NativeRoot "src\modules\forward_retained.c"),
    (Join-Path $NativeRoot "src\modules\forward_send.c"),
    (Join-Path $NativeRoot "src\modules\forward_hooks.c"),
    (Join-Path $NativeRoot "src\modules\send_api.c"),
    (Join-Path $NativeRoot "src\modules\card_send.c"),
    (Join-Path $NativeRoot "src\modules\outbox_dispatch.c"),
    (Join-Path $NativeRoot "src\modules\http_api.c"),
    (Join-Path $NativeRoot "src\modules\http_server.c"),
    (Join-Path $NativeRoot "src\modules\revoke_forward_cache.c"),
    (Join-Path $NativeRoot "src\modules\realtime_relay.c"),
    (Join-Path $NativeRoot "src\modules\receive_hooks.c"),
    (Join-Path $NativeRoot "src\modules\lifecycle_exports.c"),
    (Join-Path $NativeRoot "src\third_party\mongoose.c")
)
$ObjDir = Join-Path $NativeRoot "build"
New-Item -ItemType Directory -Force -Path $ObjDir | Out-Null

if (-not $OutFile) {
    $OutFile = Join-Path $RepoRoot "dist\wx_hook_bridge.dll"
}
$OutFile = [System.IO.Path]::GetFullPath($OutFile)
New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($OutFile)) | Out-Null

$candidates = @(
    "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
    "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
$vcvars = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $vcvars) {
    foreach ($root in @("C:\Program Files\Microsoft Visual Studio", "C:\Program Files (x86)\Microsoft Visual Studio")) {
        if (Test-Path $root) {
            $vcvars = Get-ChildItem -Path $root -Recurse -Filter vcvars64.bat -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
            if ($vcvars) { break }
        }
    }
}
if (-not $vcvars) {
    throw "vcvars64.bat not found; install VS 2022 C++ Build Tools."
}

$objStem = [System.IO.Path]::GetFileNameWithoutExtension($OutFile)
if ($SyntaxOnly) {
    $objStem = "$objStem.syntax"
}
$ObjWorkDir = Join-Path $ObjDir ("obj_" + $objStem + "_" + $PID)
New-Item -ItemType Directory -Force -Path $ObjWorkDir | Out-Null

$objects = @()
foreach ($source in $Sources) {
    $obj = Join-Path $ObjWorkDir ([System.IO.Path]::GetFileNameWithoutExtension($source) + ".obj")
    $objects += $obj
    $compile = "cl /c /utf-8 /O2 /GS- /nologo /I:`"$IncludeDir`" /Fo:`"$obj`" `"$source`""
    $cmd = "call `"$vcvars`" >nul 2>&1 && cd /d `"$NativeRoot`" && $compile"
    & cmd /c $cmd
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not $SyntaxOnly) {
    $quotedObjects = ($objects | ForEach-Object { "`"$_`"" }) -join " "
    $link = "link /DLL /NOLOGO /OUT:`"$OutFile`" $quotedObjects kernel32.lib user32.lib winhttp.lib ws2_32.lib"
    $cmd = "call `"$vcvars`" >nul 2>&1 && cd /d `"$NativeRoot`" && $link"
    & cmd /c $cmd
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
if (-not $SyntaxOnly) {
    Write-Host "built: $OutFile"
}
