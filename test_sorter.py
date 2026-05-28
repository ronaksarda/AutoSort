"""
test_sorter.py — Comprehensive test suite for the MagicSort project.
Run with: python -m pytest test_sorter.py -v
"""

import os
import json
import pytest
import shutil
import ctypes
import queue
import sys
from pathlib import Path

from rules import EXTENSION_RULES, Rule
from sorter import (
    FileRecord,
    SortResult,
    _classify,
    _human_bytes,
    _flag_str,
    _safe_dest,
    _scan_python,
    sort_directory,
    write_text_log,
    write_json_log,
    _SCANNER_DLL
)

def _touch(path: Path, content: bytes = b""):
    """Helper: Creates a dummy file with optional content."""
    path.write_bytes(content)

def _make_files(tmp_path: Path, names: list[str], content: bytes = b""):
    """Helper: Creates multiple dummy files in a temp folder."""
    for name in names:
        _touch(tmp_path / name, content)


class TestRules:
    """Validates the extensions and rules mapping file."""
    def test_extension_rules_exist(self):
        assert len(EXTENSION_RULES) > 200
        for ext, rule in EXTENSION_RULES.items():
            assert isinstance(rule, Rule)
            assert rule.category
            assert rule.subcategory


class TestClassify:
    """Validates that files are classified correctly based on extensions."""
    def test_classify_known_extensions(self, tmp_path):
        rec1 = _classify("photo.jpg", 1024, 0, tmp_path / "photo.jpg")
        assert rec1.category == "Images"
        assert rec1.subcategory == "Photos"
        assert rec1.extension == "jpg"

        rec2 = _classify("script.py", 500, 0, tmp_path / "script.py")
        assert rec2.category == "Code"
        assert rec2.subcategory == "Python"

    def test_classify_unknown_extension(self, tmp_path):
        rec = _classify("alien.xyzzy", 0, 0, tmp_path / "alien.xyzzy")
        assert rec.category == "Uncategorized"
        assert rec.subcategory == "Unknown"


class TestFormatting:
    """Validates byte and attribute formatting helpers."""
    def test_human_bytes(self):
        assert _human_bytes(0) == "0.0 B"
        assert _human_bytes(1024) == "1.0 KB"
        assert _human_bytes(1048576) == "1.0 MB"

    def test_flag_str(self):
        assert _flag_str(0) == "-"
        assert "HID" in _flag_str(0x02)  # Hidden flag


class TestDeduplication:
    """Validates parallel SHA-256 duplicate detection."""
    def test_duplicate_marking(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _touch(src / "file1.txt", b"duplicate content")
        _touch(src / "file2.txt", b"duplicate content")
        _touch(src / "unique.txt", b"different content")

        res = sort_directory(
            source=src,
            output_root=None,
            mode="dry",
            workers=2,
            max_depth=1,
            dedup=True,
            exclude_hidden=False,
            min_bytes=-1,
            max_bytes=-1,
            ext_filter=None,
            ext_exclude=None
        )

        duplicates = [r for r in res.records if r.is_duplicate]
        assert len(duplicates) == 1
        assert res.duplicates == 1


class TestDirectorySorting:
    """Validates full directory move, copy, and folder reuse operations."""
    def test_dry_run_does_not_touch_disk(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        _touch(src / "photo.jpg", b"image data")

        res = sort_directory(src, dst, "dry", workers=1, max_depth=1, dedup=False,
                             exclude_hidden=False, min_bytes=-1, max_bytes=-1,
                             ext_filter=None, ext_exclude=None)
        assert res.total_files == 1
        assert not (dst / "Images" / "Photos" / "photo.jpg").exists()

    def test_move_relocates_files(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        _touch(src / "photo.jpg", b"image data")

        res = sort_directory(src, dst, "move", workers=1, max_depth=1, dedup=False,
                             exclude_hidden=False, min_bytes=-1, max_bytes=-1,
                             ext_filter=None, ext_exclude=None)
        assert res.total_files == 1
        assert (dst / "Images" / "Photos" / "photo.jpg").exists()
        assert not (src / "photo.jpg").exists()

    def test_case_insensitive_folder_reuse(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        # Pre-create lowercase category/subcategory folder "images/photos"
        (dst / "images").mkdir()
        (dst / "images" / "photos").mkdir()
        _touch(src / "photo.jpg", b"image data")

        res = sort_directory(src, dst, "copy", workers=1, max_depth=1, dedup=False,
                             exclude_hidden=False, min_bytes=-1, max_bytes=-1,
                             ext_filter=None, ext_exclude=None)
        
        dest = res.records[0].destination
        assert dest is not None
        assert dest.parent.name == "photos"
        assert dest.parent.parent.name == "images"

    def test_extension_folder_reuse(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        
        # Pre-create folder matching extension name at top-level
        (dst / "py").mkdir()
        _touch(src / "script.py", b"print(1)")

        res = sort_directory(src, dst, "copy", workers=1, max_depth=1, dedup=False,
                             exclude_hidden=False, min_bytes=-1, max_bytes=-1,
                             ext_filter=None, ext_exclude=None)
        
        dest = res.records[0].destination
        assert dest is not None
        assert dest.parent.name == "py"


class TestAppGuiRequest:
    """Validates GUI thread request safety and exception resilience."""
    def test_handle_next_gui_request_resilience(self, monkeypatch):
        import app

        # Clear request/response queues
        while not app.gui_request_queue.empty():
            app.gui_request_queue.get_nowait()
        while not app.gui_response_queue.empty():
            app.gui_response_queue.get_nowait()

        # Queue a browse request
        app.gui_request_queue.put("browse")

        # Mock folder browser failure to test try/finally safety
        def mock_ps_fail():
            raise RuntimeError("Simulated PowerShell error")

        monkeypatch.setattr(app, "browse_folder_via_ps", mock_ps_fail)

        # Mock tkinter failure
        import sys
        import types
        mock_tk = types.ModuleType("tkinter")
        mock_tk.Tk = lambda: mock_ps_fail()
        sys.modules["tkinter"] = mock_tk

        # Run loop iteration
        app.handle_next_gui_request(timeout=0.01)

        # Should put an empty string instead of blocking the queue indefinitely
        try:
            res = app.gui_response_queue.get(timeout=0.2)
            assert res == ""
        except queue.Empty:
            pytest.fail("gui_response_queue blocked, leading to a GUI hang")
