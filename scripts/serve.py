"""Serve the dashboard and keep it fresh.

    python scripts/serve.py            then open http://localhost:8765

Rebuilds dashboard/data.js every REFRESH_MINUTES during US market hours (9:30 to 16:00 Eastern,
Monday to Friday) and once at start. The page polls for the new snapshot and reloads its numbers
without a full page refresh.
"""
import subprocess, sys, threading, time, datetime as dt, http.server, functools, os
from zoneinfo import ZoneInfo
from config import DASHBOARD_DIR, ROOT

PORT = 8765
REFRESH_MINUTES = 15
ET = ZoneInfo("America/New_York")


def market_open(now=None):
    now = now or dt.datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt.time(9, 30) <= t <= dt.time(16, 5)


def rebuild():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dashboard.py")], capture_output=True, text=True)
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] rebuild: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-200:]}", flush=True)


def loop():
    rebuild()
    while True:
        time.sleep(REFRESH_MINUTES * 60)
        if market_open():
            rebuild()
        else:
            print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] market closed, skipping rebuild", flush=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    handler = functools.partial(Quiet, directory=str(DASHBOARD_DIR))
    print(f"dashboard at http://localhost:{PORT}  (rebuilds every {REFRESH_MINUTES} min while the market is open)")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
