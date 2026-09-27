"""Start the local CRM if needed, then open the user's default browser."""
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

root = Path(__file__).resolve().parent

# This installation has moved to the VPS; keep the local database as a backup.
online_marker = root / '.runtime' / 'online-url.txt'
if online_marker.exists():
    webbrowser.open(online_marker.read_text(encoding='utf-8').strip())
    raise SystemExit(0)

def running():
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=1):
            return True
    except OSError:
        return False

if not running():
    with (root / 'server.log').open('a', encoding='utf-8') as log:
        subprocess.Popen([sys.executable, str(root / 'app.py')], cwd=root,
                         stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(40):
        if running():
            break
        time.sleep(0.25)

if running():
    webbrowser.open('http://127.0.0.1:8765/')
else:
    raise RuntimeError('CRM did not start. See server.log.')
