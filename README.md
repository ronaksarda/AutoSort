# MagicSort PRO

MagicSort PRO is a Windows-focused file organization tool that sorts directories into categorized folders. It combines a compiled C++ scanning backend, a native Win32 fallback, and a Python-driven sorting layer. The project also includes a browser-based GUI for interactive directory browsing and sorting.

## Overview

Key capabilities:
- Multi-backend directory scanning with a compiled C++ DLL, Win32 native scanning, and Python fallback
- File classification by extension with detailed category and subcategory metadata
- Dry-run analysis mode that does not modify files by default
- Move and copy modes for file organization
- Duplicate detection via SHA-256 hashing
- Web GUI interface served from `app.py`
- Packaging support for a standalone executable with `package.py`

## Repository Structure

- `app.py` - Web GUI server and API entry point
- `sorter.py` - Core sorting and scanning logic
- `build.py` - Builds `scanner.dll` from `scanner.cpp`
- `package.py` - Builds a single-file executable using PyInstaller
- `rules.py` - Extension classification rules and category metadata
- `scanner.cpp` - C++ directory scanning implementation
- `gui/` - Web interface assets
- `test_sorter.py` - Unit test coverage for sorting logic

## Requirements

- Python 3.12 or newer
- Windows platform for the native scanner backends
- Optional: MinGW `g++` to build `scanner.dll` for best scan performance

The Python components do not require external package dependencies for normal operation.

## Installation

1. Clone the repository.
2. Ensure Python 3.12+ is installed.
3. Optionally install MinGW if you want to compile `scanner.dll`.

## Building the Scanner Backend

To compile the native scanning library from `scanner.cpp`:

```powershell
python build.py
```

If `build.py` cannot find `g++`, install MinGW and rerun the command.

## Running the Web GUI

Start the web interface with:

```powershell
python app.py
```

The server listens on port `8000` and serves the GUI from the `gui/` directory. It uses a background thread to process requests and coordinate directory selection dialogs.

## CLI Usage

Basic analysis without moving files:

```powershell
python sorter.py C:\path\to\directory
```

Move files into organized folders:

```powershell
python sorter.py C:\path\to\directory --move --out C:\organized
```

Copy files instead of moving them:

```powershell
python sorter.py C:\path\to\directory --copy --out C:\organized
```

Find duplicates without modifying files:

```powershell
python sorter.py C:\path\to\directory --dedup
```

Use the help command to view all available options:

```powershell
python sorter.py --help
```

## Packaging as a Standalone Executable

To build a single-file executable using PyInstaller:

```powershell
python package.py
```

The output executable is created in the `dist/` folder.

## Testing

Run the unit tests to verify functionality:

```powershell
python -m pytest test_sorter.py -v
```

## Notes

- `sorter.py` implements a prioritized scanning strategy: compiled DLL first, native Win32 scan second, and Python `os.scandir` as a fallback.
- The project is intended for Windows environments, but the Python fallback improves portability for non-native scanning situations.
- The GUI uses local browser-based controls and communicates with `app.py` through HTTP endpoints.
