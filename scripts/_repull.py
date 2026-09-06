"""Wait for the Yahoo rate limit to lift, then re-pull any ticker whose cache is empty."""
import time, sys, subprocess, pathlib
import yfinance as yf
root = pathlib.Path(__file__).resolve().parent
log = open(root.parent / "data" / "fundamentals_pull2.log", "a")
for i in range(60):
    try:
        if len(yf.Ticker("MSFT").income_stmt.columns):
            print(f"limit lifted after {i} probes", file=log, flush=True); break
    except Exception as e:
        pass
    time.sleep(90)
subprocess.run([sys.executable, str(root / "fundamentals.py")], stdout=log, stderr=subprocess.STDOUT)
print("DONE", file=log, flush=True)
