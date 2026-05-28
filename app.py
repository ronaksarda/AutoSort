"""
app.py — MagicSort Web GUI Server.

Runs a multi-threaded HTTP server that hosts the web interface and exposes APIs
for browsing folders and sorting directories. Coordinates between the web front-end
and the C++/Python backend.
"""

import http.server
import socketserver
import json
import urllib.parse
import webbrowser
import threading
import os
import sys
import queue
from pathlib import Path
from datetime import datetime

# Handle sys._MEIPASS for PyInstaller single-file executables
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    sys.path.insert(0, sys._MEIPASS)

from sorter import sort_directory, generate_text_log, write_json_log, write_text_log

PORT = 8000

# Locate GUI assets directory
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    GUI_DIR = Path(sys._MEIPASS) / "gui"
else:
    GUI_DIR = Path(__file__).parent / "gui"

# Thread-safe queues for communication between the HTTP server thread and main Tkinter thread
gui_request_queue = queue.Queue()
gui_response_queue = queue.Queue()


class MagicSortHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that serves GUI files and processes browser/sorting APIs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(GUI_DIR), **kwargs)

    def do_GET(self):
        """Handles HTTP GET requests, routing '/api/browse' to the main thread's folder dialog."""
        if self.path == '/api/browse':
            gui_request_queue.put("browse")
            folder_path = gui_response_queue.get()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"path": folder_path}).encode('utf-8'))
        else:
            super().do_GET()

    def do_POST(self):
        """Handles HTTP POST requests, routing '/api/sort' to run the directory organizer."""
        if self.path == '/api/sort':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                source = Path(payload.get('source', '')).resolve()
                if not source.is_dir():
                    self._send_error(400, f"Source directory does not exist: {source}")
                    return
 
                is_dry_run = payload.get('is_dry_run', True)
                out_path_str = payload.get('out', '')
                
                mode = "dry"
                output_root = None
                
                if not is_dry_run:
                    if not out_path_str:
                        self._send_error(400, "Destination folder is required when sorting.")
                        return
                    output_root = Path(out_path_str).resolve()
                    output_root.mkdir(parents=True, exist_ok=True)
                    mode = "copy" if payload.get('copy_mode', False) else "move"
                
                dedup = payload.get('dedup', False)
                workers = min(32, (os.cpu_count() or 4) * 2)
                
                # Execute the main sorter orchestrator
                result = sort_directory(
                    source=source,
                    output_root=output_root,
                    mode=mode,
                    workers=workers,
                    max_depth=1,
                    dedup=dedup,
                    exclude_hidden=False,
                    min_bytes=-1,
                    max_bytes=-1,
                    ext_filter=None,
                    ext_exclude=None
                )
                
                raw_log = generate_text_log(result, source, mode)
                response_data = {
                    "total_files": result.total_files,
                    "elapsed_sec": result.elapsed_seconds,
                    "throughput": result.total_files / max(result.elapsed_seconds, 1e-9),
                    "by_category": result.by_category
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "data": response_data, "raw_log": raw_log}).encode('utf-8'))
                
            except Exception as e:
                self._send_error(500, str(e))
        else:
            self.send_error(404, "Not Found")

    def _send_error(self, code, message):
        """Sends a JSON formatted HTTP error response."""
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "error", "error": message}).encode('utf-8'))

    def log_message(self, format, *args):
        """Overridden to silence standard request log prints in the terminal."""
        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded server to handle concurrent API requests without freezing the Web UI."""
    daemon_threads = True


def browse_folder_via_ps() -> str:
    """Fallback: Launches a PowerShell dialog to let the user select a folder."""
    import subprocess
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$f.Description = 'Select Directory'; "
        "$f.ShowNewFolderButton = $true; "
        "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"
    ]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        return res.stdout.strip()
    except Exception as e:
        print("PowerShell dialog fallback failed:", e)
        return ""


def start_server():
    """Starts the ThreadingHTTPServer on the background thread."""
    with ThreadingHTTPServer(("", PORT), MagicSortHandler) as httpd:
        print(f"MagicSort Web GUI running at http://localhost:{PORT}")
        httpd.serve_forever()


def handle_next_gui_request(timeout=0.1) -> bool:
    """Pulls and executes the next pending GUI request (like browse dialogs) thread-safely."""
    try:
        try:
            task = gui_request_queue.get(timeout=timeout)
        except queue.Empty:
            return True
        
        folder_path = ""
        try:
            if task == "browse":
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()
                    root.lift()
                    root.attributes('-topmost', True)
                    folder_path = filedialog.askdirectory(title="Select Directory")
                    root.destroy()
                except Exception as tk_err:
                    print("Tkinter dialog failed, falling back to PowerShell:", tk_err)
                    folder_path = browse_folder_via_ps()
        finally:
            gui_response_queue.put(folder_path)
    except Exception as loop_err:
        print("Error in main loop task execution:", loop_err)
    return True


if __name__ == "__main__":
    # Start HTTP GUI server in a daemon thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    # Automatically open local browser page
    print("Opening browser...")
    webbrowser.open(f"http://localhost:{PORT}")
    
    # Process GUI requests sequentially on the main thread (required for OS window dialogs)
    try:
        while True:
            handle_next_gui_request()
    except KeyboardInterrupt:
        print("\nShutting down MagicSort...")
        sys.exit(0)
