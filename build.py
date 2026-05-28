"""
build.py — One-shot build script for scanner.dll.

Searches for a MinGW g++ compiler and compiles scanner.cpp → scanner.dll.
Run this once after cloning, or when scanner.cpp changes.

Usage:
    python build.py [--compiler <path>]  # auto-detect if not specified
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_CANDIDATES = [
    r"C:\msys64\ucrt64\bin\g++.exe",
    r"C:\msys64\mingw64\bin\g++.exe",
    r"C:\msys64\clang64\bin\g++.exe",
    r"C:\tools\mingw64\bin\g++.exe",
    r"C:\mingw64\bin\g++.exe",
]

_FLAGS = [
    "-O3", "-std=c++17", "-shared",
    "-static-libgcc", "-static-libstdc++",
    "-static", "-lpthread", "-lkernel32",
]


def find_compiler() -> str | None:
    import shutil
    # Try PATH first
    if shutil.which("g++"):
        return "g++"
    for path in _CANDIDATES:
        if Path(path).exists():
            return path
    return None


def build(compiler: str, src: Path, out: Path) -> bool:
    cmd = [compiler, str(src), "-o", str(out)] + _FLAGS
    print(f"[build] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[build] FAILED:\n{result.stderr}", file=sys.stderr)
        return False
    size = out.stat().st_size
    print(f"[build] OK -> {out}  ({size:,} bytes)")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Build scanner.dll from scanner.cpp")
    p.add_argument("--compiler", default=None, help="Path to g++ (auto-detect if omitted)")
    args = p.parse_args()

    here = Path(__file__).parent
    src  = here / "scanner.cpp"
    out  = here / "scanner.dll"

    if not src.exists():
        sys.exit(f"[build] scanner.cpp not found at {src}")

    compiler = args.compiler or find_compiler()
    if not compiler:
        print("[build] No g++ compiler found. scanner.dll will not be built.", file=sys.stderr)
        print("[build] Install MinGW via: winget install MSYS2.MSYS2", file=sys.stderr)
        print("[build] Then run: pacman -S mingw-w64-ucrt-x86_64-gcc", file=sys.stderr)
        sys.exit(1)

    success = build(compiler, src, out)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
