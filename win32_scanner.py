"""
win32_scanner.py — Zero-compilation C-level directory scanner via Win32 ctypes.

Calls FindFirstFileW / FindNextFileW directly — equivalent speed to a compiled C DLL,
no build tools required.  Falls back silently on non-Windows platforms.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

# ─── Win32 structures ────────────────────────────────────────────────────────


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime",  wt.DWORD),
                ("dwHighDateTime", wt.DWORD)]


class WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes",   wt.DWORD),
        ("ftCreationTime",     _FILETIME),
        ("ftLastAccessTime",   _FILETIME),
        ("ftLastWriteTime",    _FILETIME),
        ("nFileSizeHigh",      wt.DWORD),
        ("nFileSizeLow",       wt.DWORD),
        ("dwReserved0",        wt.DWORD),
        ("dwReserved1",        wt.DWORD),
        ("cFileName",          ctypes.c_wchar * 260),
        ("cAlternateFileName", ctypes.c_wchar * 14),
    ]


_INVALID_HANDLE = ctypes.c_void_p(-1).value

# Attribute constants
_FILE_ATTR_DIRECTORY     = 0x00000010
_FILE_ATTR_HIDDEN        = 0x00000002
_FILE_ATTR_SYSTEM        = 0x00000004
_FILE_ATTR_READONLY      = 0x00000001
_FILE_ATTR_REPARSE_POINT = 0x00000400
_FILE_ATTR_ARCHIVE       = 0x00000020


def _init_api() -> tuple | None:
    """Return (FindFirstFileW, FindNextFileW, FindClose) or None."""
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        fff = k32.FindFirstFileW
        fff.argtypes = [wt.LPCWSTR, ctypes.POINTER(WIN32_FIND_DATAW)]
        fff.restype  = wt.HANDLE

        fnf = k32.FindNextFileW
        fnf.argtypes = [wt.HANDLE, ctypes.POINTER(WIN32_FIND_DATAW)]
        fnf.restype  = wt.BOOL

        fc = k32.FindClose
        fc.argtypes  = [wt.HANDLE]
        fc.restype   = wt.BOOL

        return fff, fnf, fc
    except OSError:
        return None


_API = _init_api()

# ─── Public scanner ──────────────────────────────────────────────────────────


def scan_win32(
    directory: Path,
    max_depth: int,
    exclude_hidden: bool = False,
    min_bytes: int = -1,
    max_bytes: int = -1,
) -> list[tuple[str, int, int, Path]]:
    """
    Scan *directory* using native Win32 FindFirstFileW.

    Returns list of (filename, size_bytes, attr_flags, full_path).

    attr_flags bits:
        0x01 = read-only  0x02 = hidden  0x04 = system
        0x08 = reparse    0x10 = archive
    """
    if _API is None:
        raise RuntimeError("win32_scanner: not on Windows")

    results: list[tuple[str, int, int, Path]] = []
    _recurse(str(directory), directory, max_depth, 0,
             exclude_hidden, min_bytes, max_bytes, results)
    return results


def is_available() -> bool:
    return _API is not None


# ─── Internal recursion ──────────────────────────────────────────────────────


def _recurse(
    dir_str: str,
    dir_path: Path,
    max_depth: int,
    current_depth: int,
    exclude_hidden: bool,
    min_bytes: int,
    max_bytes: int,
    results: list,
) -> None:
    fff, fnf, fc = _API  # type: ignore[misc]

    pattern = dir_str + "\\*"
    ffd     = WIN32_FIND_DATAW()
    handle  = fff(pattern, ctypes.byref(ffd))

    if handle == _INVALID_HANDLE:
        return

    try:
        while True:
            name  = ffd.cFileName
            attrs = ffd.dwFileAttributes

            if name not in (".", ".."):
                if exclude_hidden and (attrs & (_FILE_ATTR_HIDDEN | _FILE_ATTR_SYSTEM)):
                    pass  # skip
                elif attrs & _FILE_ATTR_DIRECTORY:
                    if max_depth == 0 or current_depth < max_depth - 1:
                        full = dir_path / name
                        _recurse(str(full), full, max_depth, current_depth + 1,
                                 exclude_hidden, min_bytes, max_bytes, results)
                else:
                    size = (ffd.nFileSizeHigh << 32) | ffd.nFileSizeLow
                    if min_bytes >= 0 and size < min_bytes:
                        pass
                    elif max_bytes >= 0 and size > max_bytes:
                        pass
                    else:
                        flags = 0
                        if attrs & _FILE_ATTR_READONLY:      flags |= 0x01
                        if attrs & _FILE_ATTR_HIDDEN:        flags |= 0x02
                        if attrs & _FILE_ATTR_SYSTEM:        flags |= 0x04
                        if attrs & _FILE_ATTR_REPARSE_POINT: flags |= 0x08
                        if attrs & _FILE_ATTR_ARCHIVE:       flags |= 0x10
                        results.append((name, size, flags, dir_path / name))

            if not fnf(handle, ctypes.byref(ffd)):
                break
    finally:
        fc(handle)
