"""
gen_dummy.py — Generate N dummy files in a directory for benchmarking.

Usage:
    python gen_dummy.py <target_dir> [--count 50000] [--seed 42]
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

from rules import EXTENSION_RULES

# EXTENSION_RULES values are Rule dataclass instances; we only need the keys for extension names
_ALL_EXTENSIONS = list(EXTENSION_RULES.keys()) + ["xyz", "abc", "unknown", "dat", "tmp"]

_ADJECTIVES = ["quick", "lazy", "sleepy", "noisy", "bright", "dark", "cold", "warm",
               "wild", "calm", "fresh", "stale", "giant", "tiny", "old", "new"]
_NOUNS      = ["fox", "dog", "cat", "bird", "tree", "rock", "star", "moon",
               "cloud", "river", "mountain", "valley", "ocean", "desert", "city", "town"]


def _random_name(rng: random.Random) -> str:
    adj   = rng.choice(_ADJECTIVES)
    noun  = rng.choice(_NOUNS)
    num   = rng.randint(1, 9999)
    ext   = rng.choice(_ALL_EXTENSIONS)
    return f"{adj}_{noun}_{num:04d}.{ext}"


def generate(target: Path, count: int, seed: int) -> None:
    target.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    print(f"[gen] Creating {count:,} dummy files in {target} …", flush=True)

    # Write in batches to reduce syscall overhead
    batch_size = 5_000
    created = 0

    for batch_start in range(0, count, batch_size):
        batch = min(batch_size, count - batch_start)
        names = [_random_name(rng) for _ in range(batch)]
        for name in names:
            (target / name).write_bytes(b"")  # zero-byte file
        created += batch
        print(f"  {created:,} / {count:,}", end="\r", flush=True)

    print(f"\n[gen] Done — {created:,} files created.")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate dummy files for benchmarking the sorter.")
    p.add_argument("target_dir", help="Directory to fill with dummy files")
    p.add_argument("--count", type=int, default=50_000, help="Number of files to create (default: 50,000)")
    p.add_argument("--seed",  type=int, default=42,     help="Random seed for reproducibility")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    generate(Path(args.target_dir).resolve(), args.count, args.seed)


if __name__ == "__main__":
    main()
