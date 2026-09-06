#!/usr/bin/env python3
"""Load the new pages in headless Chromium, fail on console errors, save screenshots.

    python scripts/shoot.py                    # all pages, night theme
    python scripts/shoot.py --themes night paper
    python scripts/shoot.py --pages /analyze/KLAC/

A page that throws in the console is broken whether or not it looks fine, and a
screenshot is the only cheap way to see that a layout collapsed. Both checks run
here so the quality gate is one command.
"""
from __future__ import annotations

import argparse
import http.server
import functools
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from an import paths  # noqa: E402

SHOTS = paths.ROOT / "data" / "shots"
DEFAULT_PAGES = ["/analyze/", "/analyze/KLAC/", "/analyze/BKNG/", "/analyze/AAPL/", "/positioning/", "/index.html"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory: Path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(Quiet, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--themes", nargs="*", default=["night"])
    ap.add_argument("--pages", nargs="*", default=DEFAULT_PAGES)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=1200)
    ap.add_argument("--mobile", action="store_true",
                    help="also render at 390px, an iPhone-width viewport")
    ap.add_argument("--full", action="store_true", default=True)
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    # The pip playwright here is newer than the chromium build the image ships, so
    # point at the installed binary rather than downloading one (there is no egress).
    chrome = None
    for cand in sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")):
        chrome = str(cand)
    launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if chrome:
        launch_kwargs["executable_path"] = chrome

    SHOTS.mkdir(parents=True, exist_ok=True)
    httpd, port = serve(paths.DASHBOARD_DIR)
    failures = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            widths = [a.width] + ([390] if a.mobile else [])
            for theme, width in [(t, w) for t in a.themes for w in widths]:
                ctx = browser.new_context(
                    viewport={"width": width, "height": a.height},
                    device_scale_factor=1,
                    is_mobile=width < 600,
                    has_touch=width < 600,
                )
                # Motion off, or a full-page screenshot catches later sections mid-fade
                # and every one of them reads as a blank gap.
                ctx.add_init_script(
                    'try { localStorage.setItem("desk-theme", "\\"%s\\""); '
                    'localStorage.setItem("desk-motion", "\\"off\\""); } catch (e) {}' % theme
                )
                page = ctx.new_page()
                errors, requests = [], []
                # Google Fonts is unreachable from this container (blocked egress), and
                # a missing favicon is not a bug. Neither is allowed to mask a real error,
                # so failures are recorded by URL and filtered explicitly.
                IGNORE = ("fonts.googleapis.com", "fonts.gstatic.com", "favicon.ico")
                page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                page.on("requestfailed", lambda r: requests.append(f"failed {r.url}"))
                page.on("response", lambda r: requests.append(f"{r.status} {r.url}") if r.status >= 400 else None)
                for route in a.pages:
                    errors.clear()
                    requests.clear()
                    url = f"http://127.0.0.1:{port}{route}"
                    page.goto(url, wait_until="networkidle")
                    page.wait_for_timeout(500)
                    # Horizontal overflow is invisible in a full-page screenshot (the
                    # image just gets wider) and breaks every layout below it. One long
                    # nowrap string is all it takes, so it is checked rather than eyeballed.
                    overflow = page.evaluate("""() => {
                      const vw = document.documentElement.clientWidth;
                      const sw = document.documentElement.scrollWidth;
                      if (sw <= vw + 2) return null;
                      const out = [];
                      document.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.right > vw + 2) out.push(
                          el.tagName + '.' + String(el.className || '').split(' ')[0] +
                          ' w=' + Math.round(r.width) + ' "' + (el.innerText||'').slice(0,50) + '"');
                      });
                      return {vw, sw, worst: out.slice(0, 3)};
                    }""")
                    if overflow:
                        errors.append(
                            f"horizontal overflow: scrollWidth {overflow['sw']} > viewport "
                            f"{overflow['vw']}; widest: {'; '.join(overflow['worst'])}")

                    suffix = f"__{theme}" + (f"__{width}" if width != a.width else "")
                    name = (route.strip("/").replace("/", "_") or "home") + suffix + ".png"
                    out = SHOTS / name
                    page.screenshot(path=str(out), full_page=a.full)
                    size = out.stat().st_size
                    text = page.evaluate("document.body.innerText.length")
                    real = [x for x in requests if not any(i in x for i in IGNORE)]
                    errors = [e for e in errors if not any(i in e for i in IGNORE)]
                    # A console "Failed to load resource" line has no URL, so pair it
                    # with the request log rather than trusting it on its own.
                    if not real:
                        errors = [e for e in errors if "Failed to load resource" not in e]
                    errors += real
                    status = "ok"
                    if errors:
                        status = "CONSOLE ERRORS"
                        failures.append((f"{route} @{width}px", theme, errors[:4]))
                    elif route == "/index.html":
                        # The original dashboard renders from data.js, which
                        # build_dashboard.py writes and which needs network access and a
                        # portfolio file. In a fresh clone data.js is empty, so a thin
                        # page here is expected and is not a regression in the new pages.
                        # It is still loaded on every run, because a console error in the
                        # page the new nav links point back to would be a real break.
                        status = "thin (data.js not built)" if text < 3000 else "ok"
                    elif size < 12_000 or text < 400:
                        status = "SUSPICIOUSLY EMPTY"
                        failures.append((f"{route} @{width}px", theme, [f"{size} bytes, {text} chars of text"]))
                    print(f"{route:<24} {theme:<7}{width:>5}px {size/1024:7.0f} KB  {text:6d} chars  {status}")
                ctx.close()
            browser.close()
    finally:
        httpd.shutdown()

    if failures:
        print("\nFAILURES")
        for route, theme, errs in failures:
            print(f"  {route} [{theme}]")
            for e in errs:
                print(f"    {e}")
        return 1
    print(f"\nall pages rendered. screenshots in {SHOTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
