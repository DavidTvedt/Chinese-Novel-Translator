"""Build script — creates a standalone .exe for the Wuxia Translator.

This script is the single supported way to build the EXE.
It builds via `WuxiaTranslator.spec` so spec-only packaging fixes (like bundling
`tiktoken_ext` encodings) are always included.

Usage:
    python build_exe.py
    python build_exe.py --clean
    python build_exe.py --no-clean

Output:
    dist/WuxiaTranslator.exe
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build dist/WuxiaTranslator.exe via the spec file")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean PyInstaller cache and remove temporary files before building",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clean before building (faster incremental builds)",
    )
    parser.add_argument(
        "--noconfirm",
        action="store_true",
        help="Overwrite output directory without asking (recommended for CI)",
    )
    args = parser.parse_args(argv)

    spec = ROOT / "WuxiaTranslator.spec"
    if not spec.exists():
        print(f"ERROR: Spec file not found: {spec}")
        return 2

    # Default behavior: clean unless explicitly disabled.
    do_clean = True
    if args.no_clean:
        do_clean = False
    if args.clean:
        do_clean = True

    cmd: list[str] = [sys.executable, "-m", "PyInstaller", str(spec)]
    if args.noconfirm:
        cmd.append("--noconfirm")
    if do_clean:
        cmd.append("--clean")

    print("=" * 50)
    print("  Building WuxiaTranslator.exe")
    print("=" * 50)
    print(f"Spec: {spec}")
    print(f"Python: {sys.executable}")
    print()
    print("Command:")
    print(" ".join(cmd))
    print()

    try:
        result = subprocess.run(cmd, cwd=str(ROOT))
    except FileNotFoundError as e:
        print(f"\nBuild failed: {e}")
        return 1

    out_exe = ROOT / "dist" / "WuxiaTranslator.exe"
    if result.returncode == 0:
        print()
        print("=" * 50)
        print("  Build succeeded!")
        print(f"  Output: {out_exe}")
        print("=" * 50)
        return 0

    print(f"\nBuild failed with exit code {result.returncode}")
    print("If this says 'No module named PyInstaller', install it in this Python:")
    print(f"  {sys.executable} -m pip install pyinstaller")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
