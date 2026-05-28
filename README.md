# MagicSort PRO 🚀

A high-performance directory file sorter featuring a compiled parallel C++ scanning core, native Win32 ctypes fallback, and a rich Web GUI dashboard. 

---

## 🎨 Web GUI Dashboard
MagicSort PRO includes a premium, responsive Web GUI console.
* To launch the Web GUI:
  ```powershell
  python app.py
  ```
  This starts the backend API server and automatically opens your web browser to `http://localhost:8000`.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  Scan Layer                                     │
│    1. scanner.dll  ← Compiled C++ (Parallel)    │
│    2. win32_scanner ← Win32 ctypes (Native)     │
│    3. os.scandir   ← Cross-platform fallback    │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Python Layer                                   │
│    • Classification (300+ extension rules, O(1))│
│    • Filtering      (Extension, size, hidden)   │
│    • Deduplication  (Parallel SHA-256 hashing)  │
│    • Move / Copy    (Threaded disk I/O)         │
└─────────────────────────────────────────────────┘
```

---

## ⚡ Performance

Highly optimized to handle massive directories with low overhead:

| Files  | Backend Scan Engine    | Scan Time | Throughput      |
|--------|------------------------|-----------|-----------------|
| 50,000 | C++ DLL (Parallel)     | ~0.3s     | ~160,000 files/s|
| 50,000 | Win32 ctypes (Native)  | ~0.5s     | ~95,000 files/s |
| 50,000 | Python os.scandir      | ~2.0s     | ~25,000 files/s |

---

## ⚙️ Requirements

- Python 3.12+
- No external Python package dependencies required!
- *(Optional)* MinGW `g++` to compile the C++ DLL for maximum scanning speed.

---

## 🛠️ Compilation & Packaging

### Compile scanner.dll:
```powershell
python build.py
```

### Build standalone executable:
To build a standalone single-file executable (`dist/MagicSort.exe`):
```powershell
python package.py
```

---

## 💻 CLI Usage Examples

### 1. Dry Run (Analyze only, no files modified)
```powershell
python sorter.py C:\Users\name\Downloads
```

### 2. Move files into organized category folders
```powershell
python sorter.py C:\messy_dir --move --out C:\organized_dir
```

### 3. Copy files and filter by extensions recursively (depth = unlimited)
```powershell
python sorter.py C:\source_dir --copy --out C:\dest_dir --ext py ts js --depth 0
```

### 4. Scan and flag duplicate files using SHA-256 (no files moved)
```powershell
python sorter.py C:\images --dedup
```

---

## 📂 Output Folder Structure
Sorted files land directly in their corresponding category folders:
```
<destination_root>/
  Images/         # .jpg, .png, .gif, .svg, .heic ...
  Videos/         # .mp4, .mkv, .mov, .avi ...
  Audio/          # .mp3, .wav, .flac, .aac ...
  Documents/      # .pdf, .docx, .xlsx, .txt ...
  Code/           # .py, .js, .ts, .cpp, .html, .css ...
  Data/           # .json, .csv, .xml, .yaml, .db ...
  Archives/       # .zip, .rar, .tar, .7z ...
  Executables/    # .exe, .msi, .apk, .app ...
  Fonts/          # .ttf, .otf, .woff ...
  Dev/            # .gitignore, .lock, makefiles ...
  Uncategorized/  # Unknown extensions
```

---

## 🧪 Testing

Run the comprehensive unit test suite to verify stability:
```powershell
pip install pytest
python -m pytest test_sorter.py -v
```

---

## 🤝 Credits & Attribution

This project's core logic, performance architecture, and debugging were entirely driven and overseen by me, while the implementation details, standard boilerplates, and syntax refinements were generated with the assistance of Claude and Gemini.
