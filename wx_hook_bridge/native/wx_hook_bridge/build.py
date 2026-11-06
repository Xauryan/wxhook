from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


NATIVE_ROOT = Path(__file__).resolve().parent
BUILD_PS1 = NATIVE_ROOT / "build.ps1"
VERIFY_BOUNDARIES = NATIVE_ROOT / "verify_boundaries.py"


def build(
    *,
    out_file: Path | None = None,
    syntax_only: bool = False,
) -> int:
    verify = subprocess.run([sys.executable, str(VERIFY_BOUNDARIES)])
    if verify.returncode != 0:
        return verify.returncode
    cmd = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BUILD_PS1),
    ]
    if out_file is not None:
        cmd.extend(["-OutFile", str(out_file)])
    if syntax_only:
        cmd.append("-SyntaxOnly")
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the refactored wx_hook_bridge native DLL.")
    parser.add_argument("--out-file", type=Path, default=None, help="DLL output path. Defaults to dist/wx_hook_bridge.dll.")
    parser.add_argument("--syntax-only", action="store_true", help="Compile to an object file without linking a DLL.")
    args = parser.parse_args(argv)
    return build(out_file=args.out_file, syntax_only=args.syntax_only)


if __name__ == "__main__":
    sys.exit(main())
