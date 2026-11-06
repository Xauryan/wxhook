from __future__ import annotations

import re
import sys
from pathlib import Path


NATIVE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = NATIVE_ROOT / "src"
CHECK_PATHS = [
    SRC_ROOT / "include",
    SRC_ROOT / "modules",
    SRC_ROOT / "wx_hook_bridge.c",
    NATIVE_ROOT / "build.ps1",
    NATIVE_ROOT / "build.py",
]

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("deprecated wx_bridge_state.h reference", re.compile(r"wx_bridge_state\.h")),
    ("legacy global g_weixin", re.compile(r"\bg_weixin\b")),
    ("legacy global g_instance", re.compile(r"\bg_instance\b")),
    ("legacy global g_self_wxid", re.compile(r"\bg_self_wxid\b")),
    ("source-order C aggregation include", re.compile(r'#include\s+["<].*\.c[">]')),
    ("layer include", re.compile(r'#include\s+"layers/')),
    ("public global HINSTANCE", re.compile(r"extern\s+HINSTANCE\b")),
    ("public global HMODULE", re.compile(r"extern\s+HMODULE\b")),
    ("public char global", re.compile(r"extern\s+char\s+g_")),
    ("public volatile global", re.compile(r"extern\s+volatile\b")),
    ("public g_ global", re.compile(r"extern\s+.*\bg_")),
]

INTERNAL_INCLUDE_RULES: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    (
        "hongbao runtime internal include outside hongbao modules",
        re.compile(r'#include\s+"wx_bridge_hongbao_runtime_internal\.h"'),
        re.compile(r"^src/modules/hongbao_.*\.c$|^src/modules/wx_bridge_hongbao_runtime_internal\.h$"),
    ),
]

MODULE_RULES: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    (
        "forward retained state must stay out of revoke cache module",
        re.compile(r"\b(RetainedSI|RetainedModel|si_cache|model_cache|SI_CACHE_SIZE|MODEL_CACHE_SIZE|find_retained_|retain_send_item|retain_model|wx_bridge_forward_retained\.h)\b"),
        re.compile(r"^src/modules/revoke_forward_cache\.c$"),
    ),
    (
        "revoke hook state must stay out of receive hook module",
        re.compile(r"\b(RevokeEntryHookState|RevokeDispatchHookState|g_revoke_entry|g_revoke_dispatch|WX_ORIG_REVOKE_DISPATCH|install_revoke_entry|install_revoke_dispatch|restore_revoke_dispatch|revoke_entry_hook_installed|revoke_dispatch_hook_installed|WX_OFF_REVOKE_DISPATCH|WX_OFF_REVOKE_HANDLER)\b"),
        re.compile(r"^src/modules/receive_hooks\.c$"),
    ),
    (
        "revoke real hook state must stay out of lifecycle exports",
        re.compile(r"\b(g_revoke_real|WX_ORIG_REVOKE_REAL|handle_revoke_real|build_revoke_real|WX_OFF_REVOKE_REAL|resolve_revoke_payload|find_cached_msg_snapshot|find_recent_cached_revocable_msg_snapshot)\b"),
        re.compile(r"^src/modules/lifecycle_exports\.c$"),
    ),
]

PUBLIC_HEADER_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("public CachedMsg implementation record", re.compile(r"\bCachedMsg\b")),
    ("public forward hook body prototype", re.compile(r"\bforward_(dispatch|submit|ui_entry)_hook\b")),
    ("public forward runtime reset hook body", re.compile(r"\bforward_reset_runtime_patch_state\b")),
]

PUBLIC_HEADER_ALLOWLIST = {
    "wx_bridge_forward_entrypoints.h",
}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in CHECK_PATHS:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in {".c", ".h", ".ps1", ".py"}
            )
    return files


def verify_build_manifest(failures: list[str]) -> None:
    build_ps1 = NATIVE_ROOT / "build.ps1"
    if not build_ps1.is_file():
        failures.append("build.ps1: missing native build manifest")
        return
    text = build_ps1.read_text(encoding="utf-8", errors="replace")
    listed = {
        Path(match.group(1).replace("\\", "/")).as_posix()
        for match in re.finditer(r'Join-Path\s+\$NativeRoot\s+"([^"]+\.c)"', text)
    }
    expected = {"src/wx_hook_bridge.c"}
    expected.update(
        path.relative_to(NATIVE_ROOT).as_posix()
        for path in (SRC_ROOT / "modules").glob("*.c")
    )
    third_party = SRC_ROOT / "third_party"
    if third_party.is_dir():
        expected.update(
            path.relative_to(NATIVE_ROOT).as_posix()
            for path in third_party.glob("*.c")
        )
    for missing in sorted(expected - listed):
        failures.append(f"build.ps1: source missing from $Sources: {missing}")
    for stale in sorted(listed - expected):
        failures.append(f"build.ps1: stale/nonexistent $Sources entry: {stale}")


def verify_hongbao_automation_disabled(failures: list[str]) -> None:
    # Now default enabled; removed old disabled checks
    pass


def main() -> int:
    failures: list[str] = []
    layers_dir = SRC_ROOT / "layers"
    state_header = SRC_ROOT / "include" / "wx_bridge_state.h"
    if layers_dir.exists():
        failures.append(f"{layers_dir.relative_to(NATIVE_ROOT)}: legacy layers directory must not exist")
    if state_header.exists():
        failures.append(f"{state_header.relative_to(NATIVE_ROOT)}: deprecated global-state header must not exist")

    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(NATIVE_ROOT)
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in RULES:
                if pattern.search(line):
                    failures.append(f"{rel}:{lineno}: {name}: {line.strip()}")
            rel_posix = rel.as_posix()
            for name, pattern, path_pattern in MODULE_RULES:
                if path_pattern.search(rel_posix) and pattern.search(line):
                    failures.append(f"{rel}:{lineno}: {name}: {line.strip()}")
            for name, pattern, allowed_path in INTERNAL_INCLUDE_RULES:
                if pattern.search(line) and not allowed_path.search(rel_posix):
                    failures.append(f"{rel}:{lineno}: {name}: {line.strip()}")
            if path.parent == SRC_ROOT / "include" and path.name not in PUBLIC_HEADER_ALLOWLIST:
                for name, pattern in PUBLIC_HEADER_RULES:
                    if pattern.search(line):
                        failures.append(f"{rel}:{lineno}: {name}: {line.strip()}")

    verify_build_manifest(failures)
    verify_hongbao_automation_disabled(failures)

    if failures:
        print("native boundary verification failed:", file=sys.stderr)
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
