"""Desktop entry point: start the local library once, then open the browser."""
import ctypes
import msvcrt
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8765/'


def ready():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL, timeout=2) as response:
            return b'DesignSpark' in response.read(4096)
    except OSError:
        return False


def main():
    runtime = ROOT / 'work' / 'desktop'
    runtime.mkdir(parents=True, exist_ok=True)
    # A file lock prevents rapid double-clicks from starting multiple servers.
    with (runtime / 'launcher.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        acquired = False
        for _ in range(120):
            try:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError:
                if ready():
                    break
                time.sleep(1)
        if not acquired and not ready():
            raise RuntimeError('Another launcher is still starting. Please try again shortly.')
        try:
            if not ready():
                with (runtime / 'server.log').open('ab') as log:
                    process = subprocess.Popen(
                        [sys.executable, '-B', str(ROOT / 'library_server.py')],
                        cwd=str(ROOT), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                for _ in range(120):
                    if ready():
                        break
                    if process.poll() is not None:
                        raise RuntimeError('Library service could not start. Check work/desktop/server.log.')
                    time.sleep(1)
                else:
                    raise RuntimeError('Library startup timed out. Check work/desktop/server.log.')
            if '--check-only' not in sys.argv:
                webbrowser.open(URL, new=2)
        finally:
            if acquired:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'DesignSpark', 0x10)
        sys.exit(1)
