"""
sorter.py — Production-grade file sorter with multi-backend scanning.

Backend priority (fastest → slowest)
─────────────────────────────────────
  1. scanner.dll  — compiled C++ DLL with multi-threaded Win32 scan
  2. win32_scanner — ctypes Win32 FindFirstFileW (always on Windows)
  3. os.scandir   — cross-platform Python fallback

Features
────────
  • Dry-run mode (default) — no files touched
  • Move mode   (--move)   — relocate into category/subcategory tree
  • Copy mode   (--copy)   — copy instead of move
  • Filters     (--exclude-hidden, --min-size, --max-size, --ext, --exclude-ext)
  • Duplicate detection via SHA-256 (--dedup)
  • Collision-safe rename on move/copy
  • Rich structured text log with MIME types and descriptions
  • JSON log output (--json-log)
  • Summary statistics: top categories, largest files, duplicates
  • Progress bar to stderr (no external deps)
  • Configurable recursion depth

Usage
─────
    python sorter.py <directory>
    python sorter.py <directory> --move --out <dst>
    python sorter.py <directory> --copy --out <dst>
    python sorter.py <directory> --dedup --move --out <dst>
    python sorter.py <directory> --ext py ts js --depth 0

Full options: python sorter.py --help
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

from rules import EXTENSION_RULES, UNKNOWN_RULE, Rule

# ─────────────────────────── DLL loader ────────────────────────────────────

_DLL_NAME       = "scanner.dll"
_SCAN_BUF_BYTES  = 512 * 1024 * 1024   # 512 MB — handles ~2 M paths

# Pre-compiled struct for binary record header: uint64 size, uint32 flags, uint16 pathlen
import struct as _struct
_REC_HDR = _struct.Struct("<QIH")   # little-endian: 8+4+2 = 14 bytes
_REC_HDR_SIZE = _REC_HDR.size       # 14


def _load_dll() -> ctypes.CDLL | None:
    """Helper: Locates and loads scanner.dll for high-speed multi-threaded C++ directory scanning."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).parent
        
    dll_path = base_dir / _DLL_NAME
    if not dll_path.exists():
        return None
    try:
        lib = ctypes.CDLL(str(dll_path))
        lib.scan_directory.argtypes = [
            ctypes.c_char_p,    # dir (UTF-8)
            ctypes.c_int,       # max_depth
            ctypes.c_int,       # exclude_hidden
            ctypes.c_int64,     # min_bytes
            ctypes.c_int64,     # max_bytes
            ctypes.c_void_p,    # out_buf (uint8_t*)
            ctypes.c_size_t,    # buf_size
        ]
        lib.scan_directory.restype = ctypes.c_int
        lib.get_file_count.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.get_file_count.restype  = ctypes.c_int
        return lib
    except OSError:
        return None


_SCANNER_DLL: ctypes.CDLL | None = _load_dll()

# ─────────────────────────── data model ────────────────────────────────────

# Attribute flag bits (from scanner)
FLAG_READONLY  = 0x01
FLAG_HIDDEN    = 0x02
FLAG_SYSTEM    = 0x04
FLAG_REPARSE   = 0x08
FLAG_ARCHIVE   = 0x10


@dataclass(slots=True)
class FileRecord:
    path:         Path
    name:         str
    extension:    str          # raw ext without dot, or "(none)"
    size_bytes:   int
    category:     str
    subcategory:  str
    mime_type:    str
    description:  str
    attr_flags:   int = 0      # bitmask from scanner
    sha256:       str | None = None
    is_duplicate: bool = False
    destination:  Path | None = None
    error:        str | None = None

    def is_hidden(self)   -> bool: return bool(self.attr_flags & FLAG_HIDDEN)
    def is_readonly(self) -> bool: return bool(self.attr_flags & FLAG_READONLY)
    def is_system(self)   -> bool: return bool(self.attr_flags & FLAG_SYSTEM)
    def is_symlink(self)  -> bool: return bool(self.attr_flags & FLAG_REPARSE)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "path":        str(self.path),
            "extension":   self.extension,
            "size_bytes":  self.size_bytes,
            "category":    self.category,
            "subcategory": self.subcategory,
            "mime_type":   self.mime_type,
            "description": self.description,
            "attr_flags":  self.attr_flags,
            "sha256":      self.sha256,
            "is_duplicate":self.is_duplicate,
            "destination": str(self.destination) if self.destination else None,
            "error":       self.error,
        }


@dataclass
class SortResult:
    records:        list[FileRecord]            = field(default_factory=list)
    total_files:    int                         = 0
    total_bytes:    int                         = 0
    by_category:    dict[str, int]              = field(default_factory=dict)
    by_subcategory: dict[tuple[str, str], int]  = field(default_factory=dict)
    duplicates:     int                         = 0
    errors:         list[str]                   = field(default_factory=list)
    elapsed_seconds: float                      = 0.0
    scanner_backend: str                        = "unknown"

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, rec: FileRecord) -> None:
        with self._lock:
            self.records.append(rec)
            self.total_files += 1
            self.total_bytes += rec.size_bytes
            self.by_category[rec.category] = self.by_category.get(rec.category, 0) + 1
            key = (rec.category, rec.subcategory)
            self.by_subcategory[key] = self.by_subcategory.get(key, 0) + 1
            if rec.is_duplicate:
                self.duplicates += 1
            if rec.error:
                self.errors.append(rec.error)

    @property
    def top_categories(self) -> list[tuple[str, int]]:
        return sorted(self.by_category.items(), key=lambda x: x[1], reverse=True)[:10]

    @property
    def largest_files(self) -> list[FileRecord]:
        return sorted(self.records, key=lambda r: r.size_bytes, reverse=True)[:10]

# ─────────────────────────── classification ────────────────────────────────


def _classify(name: str, size_bytes: int, attr_flags: int, full_path) -> FileRecord:
    """Helper: Looks up a file's extension in rules.py and maps it to a category (Images, Code, etc.) in O(1) time."""
    ext_raw = ""
    dot = name.rfind(".")
    if dot > 0:
        ext_raw = name[dot + 1:]
    ext = ext_raw.lower() if ext_raw else name.lower()

    rule: Rule = EXTENSION_RULES.get(ext, UNKNOWN_RULE)

    return FileRecord(
        path=Path(full_path) if isinstance(full_path, str) else full_path,
        name=name,
        extension=ext_raw or "(none)",
        size_bytes=size_bytes,
        category=rule.category,
        subcategory=rule.subcategory,
        mime_type=rule.mime_type,
        description=rule.description,
        attr_flags=attr_flags,
    )


# ─────────────────────────── scanning backends ─────────────────────────────


def _scan_dll(
    source: Path, max_depth: int,
    exclude_hidden: bool, min_bytes: int, max_bytes: int,
) -> tuple[list[tuple[str, int, int, Path]], str]:
    """Backend 1: Calls C++ scanner.dll to scan the directory using lock-free work-stealing threads."""
    count = _SCANNER_DLL.get_file_count(         # type: ignore[union-attr]
        str(source).encode("utf-8"),
        ctypes.c_int(max_depth),
        ctypes.c_int(1 if exclude_hidden else 0),
    )
    if count < 0:
        raise RuntimeError("scanner.dll returned error counting files")
    if count == 0:
        return [], "C++ DLL (parallel Win32)"

    buf_size = max(4 + (count + 100) * 2048, 65536)
    buf   = ctypes.create_string_buffer(buf_size)
    scan_count = _SCANNER_DLL.scan_directory(    # type: ignore[union-attr]
        str(source).encode("utf-8"),
        ctypes.c_int(max_depth),
        ctypes.c_int(1 if exclude_hidden else 0),
        ctypes.c_int64(min_bytes),
        ctypes.c_int64(max_bytes),
        ctypes.c_void_p(ctypes.addressof(buf)),
        ctypes.c_size_t(buf_size),
    )
    if scan_count < 0:
        raise RuntimeError("scanner.dll returned error")
    if scan_count == 0:
        return [], "C++ DLL (parallel Win32)"

    # Parse binary: [4B count] + N * [8B size | 4B flags | 2B pathlen | path]
    raw = buf.raw
    offset = 4   # skip the 4-byte count header (we already have it from return value)

    results: list[tuple[str, int, int, Path]] = []
    results_append = results.append  # local binding — avoids repeated attr lookup
    unpack = _REC_HDR.unpack_from
    hdr_sz = _REC_HDR_SIZE

    for _ in range(scan_count):
        size, flags, pathlen = unpack(raw, offset)
        offset += hdr_sz
        path_str = raw[offset:offset + pathlen].decode("utf-8", errors="replace")
        offset += pathlen
        # Extract filename without constructing a full Path object yet
        sep = path_str.rfind("\\")   # Windows paths
        if sep == -1:
            sep = path_str.rfind("/")
        name = path_str[sep + 1:] if sep >= 0 else path_str
        results_append((name, size, flags, Path(path_str)))   # defer Path() construction

    return results, "C++ DLL (parallel Win32)"


def _scan_win32(
    source: Path, max_depth: int,
    exclude_hidden: bool, min_bytes: int, max_bytes: int,
) -> tuple[list[tuple[str, int, int, Path]], str]:
    """Backend 2: Windows ctypes scanning using native FindFirstFileW (no compiler needed)."""
    from win32_scanner import scan_win32 as _sw32
    results = _sw32(source, max_depth, exclude_hidden, min_bytes, max_bytes)
    return results, "Win32 ctypes (FindFirstFileW)"


def _scan_python(
    source: Path, max_depth: int,
    exclude_hidden: bool, min_bytes: int, max_bytes: int,
) -> tuple[list[tuple[str, int, int, Path]], str]:
    """Backend 3: Pure-Python fallback scanning using standard os.scandir."""
    results: list[tuple[str, int, int, Path]] = []

    def _recurse(directory: Path, depth: int) -> None:
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if max_depth == 0 or depth < max_depth - 1:
                            _recurse(Path(entry.path), depth + 1)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        size = st.st_size
                        if min_bytes >= 0 and size < min_bytes: continue
                        if max_bytes >= 0 and size > max_bytes: continue
                        results.append((entry.name, size, 0, Path(entry.path)))
        except PermissionError as exc:
            print(f"[WARN] {exc}", file=sys.stderr)

    _recurse(source, 0)
    return results, "Python (os.scandir)"


def _pick_backend(
    source: Path, max_depth: int,
    exclude_hidden: bool, min_bytes: int, max_bytes: int,
) -> tuple[list[tuple[str, int, int, Path]], str]:
    """Manager: Automatically selects the fastest available scanning backend (C++ DLL -> Win32 -> Python)."""
    if _SCANNER_DLL is not None:
        return _scan_dll(source, max_depth, exclude_hidden, min_bytes, max_bytes)
    if platform.system() == "Windows":
        try:
            return _scan_win32(source, max_depth, exclude_hidden, min_bytes, max_bytes)
        except Exception:
            pass
    return _scan_python(source, max_depth, exclude_hidden, min_bytes, max_bytes)


# ─────────────────────────── deduplication ─────────────────────────────────


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    """Helper: Calculates SHA-256 hash of a file for duplicate detection."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk_data := f.read(chunk):
                h.update(chunk_data)
    except OSError:
        return ""
    return h.hexdigest()


def _mark_duplicates(records: list[FileRecord], workers: int) -> None:
    """Manager: Computes SHA-256 hashes of all scanned files in parallel and flags duplicates."""
    seen: dict[str, str] = {}   # sha256 → first file name
    lock  = threading.Lock()

    def _hash(rec: FileRecord) -> tuple[FileRecord, str]:
        return rec, _sha256(rec.path)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec, digest in pool.map(_hash, records):
            if not digest:
                continue
            rec.sha256 = digest
            with lock:
                if digest in seen:
                    rec.is_duplicate = True
                else:
                    seen[digest] = rec.name


# ─────────────────────────── move / copy ───────────────────────────────────


def _safe_dest(dest_dir: Path, name: str) -> Path:
    """Helper: Avoids overwriting existing files by appending a counter suffix (e.g. file__1.txt)."""
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{stem}__{counter}{suffix}"
        counter += 1
    return dest


def _find_matching_dir(existing_dirs: list[Path], ext: str, sub: str, cat: str) -> Path | None:
    """Finds any existing directory matching the extension, subcategory, or category name case-insensitively."""
    ext_lower, sub_lower, cat_lower = ext.lower(), sub.lower(), cat.lower()
    for d in existing_dirs:
        if d.name.lower() == ext_lower: return d
    for d in existing_dirs:
        if d.name.lower() == sub_lower: return d
    for d in existing_dirs:
        if d.name.lower() == cat_lower: return d
    return None


def _resolve_case_in_list(existing_dirs: list[Path], parent: Path, name: str) -> Path:
    """Resolves the casing of a directory segment using the existing directories list."""
    name_lower = name.lower()
    for d in existing_dirs:
        if d.parent == parent and d.name.lower() == name_lower:
            return d
    return parent / name


def _apply_file(rec: FileRecord, root_out: Path, copy: bool, existing_dirs: list[Path], dirs_lock: threading.Lock) -> None:
    """Action: Copies or moves a file into a matching existing directory or the default category/subcategory folder."""
    with dirs_lock:
        matched = _find_matching_dir(existing_dirs, rec.extension, rec.subcategory, rec.category)
        if matched:
            dest_dir = matched
        else:
            category_dir = _resolve_case_in_list(existing_dirs, root_out, rec.category)
            dest_dir = _resolve_case_in_list(existing_dirs, category_dir, rec.subcategory)
            if category_dir not in existing_dirs:
                existing_dirs.append(category_dir)
            if dest_dir not in existing_dirs:
                existing_dirs.append(dest_dir)

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _safe_dest(dest_dir, rec.name)
    try:
        shutil.copy2(str(rec.path), dest) if copy else shutil.move(str(rec.path), dest)
        rec.destination = dest
    except OSError as exc:
        rec.error = f"{'COPY' if copy else 'MOVE'} FAILED [{rec.path}] → {dest}: {exc}"


# ─────────────────────────── progress bar ──────────────────────────────────


class _Progress:
    """Thread-safe progress bar. Only renders when stderr is a TTY."""

    _IS_TTY: bool = sys.stderr.isatty()

    def __init__(self, total: int, width: int = 40) -> None:
        self.total  = max(total, 1)
        self.width  = width
        self._done  = 0
        self._lock  = threading.Lock()

    def advance(self, n: int = 1) -> None:
        with self._lock:
            self._done = min(self._done + n, self.total)
            if self._IS_TTY:
                self._draw()

    def _draw(self) -> None:
        pct    = self._done / self.total
        filled = int(self.width * pct)
        # ASCII-safe bar: # for filled, . for empty
        bar    = "#" * filled + "." * (self.width - filled)
        print(f"\r  [{bar}] {self._done:,}/{self.total:,} ({pct*100:.1f}%)",
              end="", file=sys.stderr, flush=True)

    def done(self) -> None:
        with self._lock:
            self._done = self.total
            if self._IS_TTY:
                self._draw()
                print(file=sys.stderr)   # newline after bar



# ─────────────────────────── core runner ───────────────────────────────────


def sort_directory(
    source: Path,
    output_root: Path | None,
    mode: str,            # "dry", "move", "copy"
    workers: int,
    max_depth: int,
    dedup: bool,
    exclude_hidden: bool,
    min_bytes: int,
    max_bytes: int,
    ext_filter: set[str] | None,
    ext_exclude: set[str] | None,
) -> SortResult:
    """Main Orchestrator: Coordinates scanning, classification, hashing, sorting, and stats calculation."""
    result  = SortResult()
    t_start = datetime.now()

    # ── 1. Scan ────────────────────────────────────────────────────────────
    raw_entries, backend = _pick_backend(
        source, max_depth, exclude_hidden, min_bytes, max_bytes
    )
    result.scanner_backend = backend

    # ── 2. Extension filter ────────────────────────────────────────────────
    if ext_filter or ext_exclude:
        filtered = []
        for name, size, flags, path in raw_entries:
            ext = Path(name).suffix.lstrip(".").lower()
            if ext_filter  and ext not in ext_filter:  continue
            if ext_exclude and ext in ext_exclude:      continue
            filtered.append((name, size, flags, path))
        raw_entries = filtered

    # ── 3. Classify (pure dict lookup, no I/O) ─────────────────────────────
    records = [_classify(name, size, flags, path)
               for name, size, flags, path in raw_entries]

    # ── 4. Dedup (optional, parallel SHA-256) ─────────────────────────────
    if dedup and records:
        print(f"[sorter] Hashing {len(records):,} files for deduplication...",
              file=sys.stderr)
        _mark_duplicates(records, workers)

    # ── 5. Move / Copy (optional, parallel I/O) ───────────────────────────
    if mode in ("move", "copy") and output_root and records:
        copy = mode == "copy"
        prog = _Progress(len(records))
        print(f"[sorter] {'Copying' if copy else 'Moving'} {len(records):,} files...",
              file=sys.stderr)

        existing_dirs = []
        dirs_lock = threading.Lock()
        if output_root.exists():
            try:
                for root, dirs, _ in os.walk(output_root):
                    for d in dirs:
                        existing_dirs.append(Path(root) / d)
            except OSError:
                pass

        def _apply(rec: FileRecord) -> FileRecord:
            _apply_file(rec, output_root, copy, existing_dirs, dirs_lock)
            prog.advance()
            return rec

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_apply, records))   # consume iterator to run all
        prog.done()

    # ── 6. Aggregate (bulk, no per-record locking needed here) ─────────────
    # records list is fully built; no concurrent writers at this point.
    for rec in records:
        result.records.append(rec)
        result.total_files += 1
        result.total_bytes += rec.size_bytes
        result.by_category[rec.category] = result.by_category.get(rec.category, 0) + 1
        key = (rec.category, rec.subcategory)
        result.by_subcategory[key] = result.by_subcategory.get(key, 0) + 1
        if rec.is_duplicate:
            result.duplicates += 1
        if rec.error:
            result.errors.append(rec.error)

    result.elapsed_seconds = (datetime.now() - t_start).total_seconds()
    result.records.sort(key=lambda r: (r.category, r.subcategory, r.name.lower()))
    return result


# ─────────────────────────── formatting helpers ─────────────────────────────


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _flag_str(flags: int) -> str:
    parts = []
    if flags & FLAG_READONLY: parts.append("RO")
    if flags & FLAG_HIDDEN:   parts.append("HID")
    if flags & FLAG_SYSTEM:   parts.append("SYS")
    if flags & FLAG_REPARSE:  parts.append("SYM")
    if flags & FLAG_ARCHIVE:  parts.append("ARC")
    return ",".join(parts) if parts else "-"


# ─────────────────────────── log writers ───────────────────────────────────


def generate_text_log(result: SortResult, source: Path, mode: str) -> str:
    import io
    f = io.StringIO()
    THICK = "=" * 90
    THIN  = "-" * 90

    w = lambda s="": f.write(s + "\n")  # noqa: E731

    # ── Header ───────────────────────────────────────────────────────────────
    w(THICK)
    w("  FILE SORT REPORT")
    w(f"  Generated   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"  Source      : {source}")
    w(f"  Scan Engine : {result.scanner_backend}")
    w(f"  Mode        : {mode.upper()}")
    w(f"  Elapsed     : {result.elapsed_seconds:.3f}s")
    w(f"  Throughput  : {result.total_files / max(result.elapsed_seconds, 1e-9):,.0f} files/sec")
    w(f"  Total Files : {result.total_files:,}")
    w(f"  Total Size  : {_human_bytes(result.total_bytes)}")
    if result.duplicates:
        w(f"  Duplicates  : {result.duplicates:,}")
    if result.errors:
        w(f"  Errors      : {len(result.errors):,}")
    w(THICK)
    w()

    # ── Category summary ────────────────────────────────────────────────
    w("  CATEGORY SUMMARY")
    w(THIN)
    w(f"  {'Category':<22} {'Subcategory':<22} {'Files':>8}  {'% of Total':>10}")
    w(THIN)
    for (cat, sub), count in sorted(result.by_subcategory.items()):
        pct = count / max(result.total_files, 1) * 100
        w(f"  {cat:<22} {sub:<22} {count:>8,}  {pct:>9.1f}%")
    w(THIN)
    w(f"  {'TOTAL':<45} {result.total_files:>8,}  {'100.0%':>10}")
    w(THICK)
    w()

    # ── Top 10 categories by file count ─────────────────────────────────
    w("  TOP CATEGORIES")
    w(THIN)
    for rank, (cat, count) in enumerate(result.top_categories, 1):
        bar_len = int(40 * count / max(result.total_files, 1))
        bar = "#" * bar_len
        w(f"  {rank:>2}. {cat:<20} {bar:<40}  {count:,}")
    w(THICK)
    w()

    # ── Largest files ───────────────────────────────────────────────────
    if any(r.size_bytes > 0 for r in result.records):
        w("  LARGEST FILES")
        w(THIN)
        for rank, rec in enumerate(result.largest_files, 1):
            w(f"  {rank:>2}. {_human_bytes(rec.size_bytes):>10}  {rec.name}")
        w(THICK)
        w()

    # ── Duplicates ──────────────────────────────────────────────────────
    dups = [r for r in result.records if r.is_duplicate]
    if dups:
        w("  DUPLICATE FILES")
        w(THIN)
        w(f"  {'SHA-256':<64}  {'Filename'}")
        w(THIN)
        for rec in dups:
            w(f"  {rec.sha256 or '?':<64}  {rec.name}")
        w(THICK)
        w()

    # ── Per-file records ────────────────────────────────────────────────
    w("  FILE RECORDS")
    w(THIN)
    w(f"  {'#':<7} {'Category':<18} {'Subcategory':<18} {'Flags':<12} {'Size':>9}  {'Filename'}")
    w(THIN)
    for idx, rec in enumerate(result.records, 1):
        dup_mark = " [DUP]" if rec.is_duplicate else ""
        extra = ""
        if rec.destination:
            extra = f"  →  {rec.destination}"
        elif rec.error:
            extra = f"  !!  {rec.error}"
        w(f"  {idx:<7,} {rec.category:<18} {rec.subcategory:<18} "
          f"{_flag_str(rec.attr_flags):<12} {_human_bytes(rec.size_bytes):>9}  "
          f"{rec.name}{dup_mark}{extra}")
    w(THIN)

    # ── Errors ──────────────────────────────────────────────────────────
    if result.errors:
        w()
        w("  ERRORS")
        w(THIN)
        for err in result.errors:
            w(f"  [ERR] {err}")
        w(THIN)

    w()
    w(THICK)
    w("  END OF REPORT")
    w(THICK)
    
    return f.getvalue()

def write_text_log(result: SortResult, log_path: Path, source: Path, mode: str) -> None:
    text = generate_text_log(result, source, mode)
    with log_path.open("w", encoding="utf-8") as f:
        f.write(text)


def write_json_log(result: SortResult, log_path: Path, source: Path, mode: str) -> None:
    payload = {
        "generated_at":  datetime.now().isoformat(),
        "source":        str(source),
        "scan_engine":   result.scanner_backend,
        "mode":          mode,
        "elapsed_sec":   result.elapsed_seconds,
        "throughput":    result.total_files / max(result.elapsed_seconds, 1e-9),
        "total_files":   result.total_files,
        "total_bytes":   result.total_bytes,
        "duplicates":    result.duplicates,
        "errors":        result.errors,
        "category_summary": {
            f"{cat}/{sub}": count
            for (cat, sub), count in sorted(result.by_subcategory.items())
        },
        "records": [r.to_dict() for r in result.records],
    }
    with log_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ─────────────────────────── CLI ───────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sorter",
        description="High-performance file sorter: C++ scan + Python classify + rich log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Dry-run (no files moved):
    python sorter.py C:\\Users\\me\\Downloads

  Move into sorted tree:
    python sorter.py C:\\messy --move --out C:\\sorted

  Copy only Python and TypeScript files recursively:
    python sorter.py C:\\projects --copy --out C:\\code --ext py ts --depth 0

  Find duplicates without moving:
    python sorter.py C:\\photos --dedup

  Generate JSON log:
    python sorter.py C:\\downloads --json-log --log report.json
""",
    )

    p.add_argument("directory",     help="Directory to scan")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--move", action="store_true", help="Move files into sorted sub-folders")
    mode.add_argument("--copy", action="store_true", help="Copy files into sorted sub-folders")

    p.add_argument("--out",         default=None, metavar="PATH",
                   help="Output root (required with --move or --copy)")
    p.add_argument("--log",         default=None, metavar="PATH",
                   help="Log file path (default: auto-named in cwd)")
    p.add_argument("--json-log",    action="store_true",
                   help="Write log as JSON instead of plain text")
    p.add_argument("--workers",     type=int,
                   default=min(32, (os.cpu_count() or 4) * 2),
                   metavar="N",
                   help="Thread pool size (default: CPU×2, max 32)")
    p.add_argument("--depth",       type=int, default=1, metavar="N",
                   help="Scan depth: 1=top-level, 0=unlimited (default: 1)")
    p.add_argument("--dedup",       action="store_true",
                   help="Hash all files and flag duplicates (slower)")
    p.add_argument("--exclude-hidden", action="store_true",
                   help="Skip hidden and system files")
    p.add_argument("--min-size",    type=int, default=-1, metavar="BYTES",
                   help="Minimum file size in bytes")
    p.add_argument("--max-size",    type=int, default=-1, metavar="BYTES",
                   help="Maximum file size in bytes")
    p.add_argument("--ext",         nargs="+", default=None, metavar="EXT",
                   help="Only include these extensions (e.g. --ext py js ts)")
    p.add_argument("--exclude-ext", nargs="+", default=None, metavar="EXT",
                   help="Exclude these extensions")
    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    source = Path(args.directory).resolve()
    if not source.is_dir():
        parser.error(f"Not a directory: {source}")

    mode = "dry"
    if args.move: mode = "move"
    if args.copy: mode = "copy"

    output_root: Path | None = None
    if mode in ("move", "copy"):
        if not args.out:
            parser.error("--move / --copy requires --out <destination>")
        output_root = Path(args.out).resolve()
        output_root.mkdir(parents=True, exist_ok=True)

    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext_log = ".json" if args.json_log else ".txt"
    log_path = Path(args.log) if args.log else None

    ext_filter  = {e.lower() for e in args.ext}          if args.ext         else None
    ext_exclude = {e.lower() for e in args.exclude_ext}  if args.exclude_ext else None

    # Determine which backend will be used
    if _SCANNER_DLL is not None:
        backend_label = "C++ DLL (parallel Win32)"
    elif platform.system() == "Windows":
        backend_label = "Win32 ctypes (FindFirstFileW)"
    else:
        backend_label = "Python (os.scandir)"

    print(f"[sorter] Engine   : {backend_label}")
    print(f"[sorter] Source   : {source}")
    print(f"[sorter] Mode     : {mode.upper()}")
    print(f"[sorter] Depth    : {'unlimited' if args.depth == 0 else args.depth}")
    print(f"[sorter] Workers  : {args.workers}")

    result = sort_directory(
        source=source,
        output_root=output_root,
        mode=mode,
        workers=args.workers,
        max_depth=args.depth,
        dedup=args.dedup,
        exclude_hidden=args.exclude_hidden,
        min_bytes=args.min_size,
        max_bytes=args.max_size,
        ext_filter=ext_filter,
        ext_exclude=ext_exclude,
    )

    throughput = result.total_files / max(result.elapsed_seconds, 1e-9)
    print(f"[sorter] Done     : {result.total_files:,} files in {result.elapsed_seconds:.3f}s  ({throughput:,.0f} files/sec)")
    if log_path:
        print(f"[sorter] Log      : {log_path}")

    # Write or print log
    if args.json_log:
        if log_path:
            write_json_log(result, log_path, source, mode)
        else:
            # We skip printing JSON to stdout if no log requested to keep it clean,
            # but usually json implies they want data. If they want json to stdout,
            # they can use --json-log --log - (left as exercise)
            pass
    else:
        text_log = generate_text_log(result, source, mode)
        if log_path:
            with log_path.open("w", encoding="utf-8") as f:
                f.write(text_log)
        else:
            print("\n" + text_log)

    if result.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
