import subprocess
import sys
import shutil
from pathlib import Path

def main():
    print("Preparing to build MagicSort.exe...")
    
    # Verify PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    
    here = Path(__file__).parent
    
    # Define files to include
    # We include the scanner.dll and the entire gui/ folder as data
    dll_path = here / "scanner.dll"
    if not dll_path.exists():
        print("ERROR: scanner.dll not found. Please run build.py first.")
        sys.exit(1)
        
    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onefile",
        "--paths", str(here),
        "--name", "MagicSort",
        "--add-data", f"sorter.py;.",
        "--add-data", f"rules.py;.",
        "--add-data", f"{dll_path.name};.",
        "--add-data", f"gui;gui",
        "app.py"
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(here))
    
    print("\n[SUCCESS] Build complete! You can find MagicSort.exe in the 'dist' folder.")

if __name__ == "__main__":
    main()
