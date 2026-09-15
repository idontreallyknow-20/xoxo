"""Assemble dashboard/ into a bundle that can be published as a Claude artifact.

An artifact is one page plus supporting files, with two constraints the site as
built for Vercel and serve.py does not meet:

  * at most 255 files per version. dashboard/ has ~350, because every ticker gets
    its own analyze/<TICKER>/index.html. The bundle drops those 152 copies and
    serves every ticker from one analyze/index.html?t=<TICKER>.
  * no directory routing. A link to "paper/" is not served as paper/index.html,
    so every directory link is rewritten to name the file.

Reads:  dashboard/ (never modified).
Writes: dashboard/_artifact/ (gitignored, regenerable) and prints the file list.

    python scripts/build_artifact.py

Every rewrite is asserted to match the expected number of times, so a change to
the dashboard that this script no longer understands fails the build instead of
publishing broken links.
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard"
OUT = SRC / "_artifact"


def rewrite(text, rules, where):
    """Apply (pattern, replacement, expected count) rules; fail on a count mismatch."""
    for pat, rep, n in rules:
        text, k = re.subn(pat, rep, text)
        if k != n:
            sys.exit(f"{where}: expected {n} match(es) of {pat!r}, found {k}")
    return text


def page_body(html):
    """Strip the document wrapper: the artifact host wraps the page in its own
    doctype, html, head and body, so only head content and body content go in."""
    m = re.search(r"<head>(.*?)</head>\s*<body>(.*?)</body>", html, re.S)
    if not m:
        sys.exit("index.html: could not find head and body")
    head, body = m.group(1), m.group(2)
    head = re.sub(r'<meta (charset|name="viewport")[^>]*>\n?', "", head)
    return head.strip() + "\n" + body.strip() + "\n"


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "assets").mkdir()
    (OUT / "analyze" / "compare").mkdir(parents=True)

    files = []

    def put(rel, text):
        p = OUT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        files.append(rel)

    def copy(rel):
        p = OUT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC / rel, p)
        files.append(rel)

    # The page. Directory links name their index file.
    index = (SRC / "index.html").read_text(encoding="utf-8")
    index = rewrite(index, [
        (r'href="analyze/"', 'href="analyze/index.html"', 1),
        (r'href="positioning/"', 'href="positioning/index.html"', 1),
        (r'href="paper/"', 'href="paper/index.html"', 1),
    ], "index.html")
    (OUT / "index.html").write_text(page_body(index), encoding="utf-8")

    # Shared chrome: the three tab links built from `depth`.
    put("assets/desk-common.js", rewrite((SRC / "assets/desk-common.js").read_text(encoding="utf-8"), [
        (r'depth \+ "analyze/"', 'depth + "analyze/index.html"', 1),
        (r'depth \+ "positioning/"', 'depth + "positioning/index.html"', 1),
        (r'depth \+ "paper/"', 'depth + "paper/index.html"', 1),
    ], "desk-common.js"))

    # Front page: ticker links.
    put("assets/home.js", rewrite((SRC / "assets/home.js").read_text(encoding="utf-8"), [
        (r'analyze/\$\{encodeURIComponent\((\w+)\.ticker\)\}/', r'analyze/index.html?t=${encodeURIComponent(\1.ticker)}', 4),
    ], "home.js"))

    # Analyse index: lives at analyze/index.html, depth "../".
    put("assets/analyze-index.js", rewrite((SRC / "assets/analyze-index.js").read_text(encoding="utf-8"), [
        (r'href="\$\{esc\(x\.ticker\)\}/"', 'href="index.html?t=${esc(x.ticker)}"', 1),
        (r'href="compare/"', 'href="compare/index.html"', 1),
        (r'href="\.\./positioning/"', 'href="../positioning/index.html"', 1),
    ], "analyze-index.js"))

    # Ticker page: now served from analyze/index.html, so one level shallower.
    put("assets/analyze.js", rewrite((SRC / "assets/analyze.js").read_text(encoding="utf-8"), [
        (r'href="\.\./\$\{esc\(t\)\}/"', 'href="index.html?t=${esc(t)}"', 1),
        (r'href="\.\./\.\./positioning/"', 'href="../positioning/index.html"', 1),
        (r'D\.chrome\("Analyse", "\.\./\.\./"\)', 'D.chrome("Analyse", "../")', 1),
        (r'href="\.\./compare/\?t=', 'href="compare/index.html?t=', 1),
        (r'fetch\(`\.\./\.\./analysis/', 'fetch(`../analysis/', 1),
    ], "analyze.js"))

    # Compare: stays at analyze/compare/index.html, depth "../../".
    put("assets/compare.js", rewrite((SRC / "assets/compare.js").read_text(encoding="utf-8"), [
        (r'href="\.\./\$\{esc\(r\.identity\.ticker\)\}/"', 'href="../index.html?t=${esc(r.identity.ticker)}"', 2),
        (r'href="\.\./\$\{esc\(t\)\}/"', 'href="../index.html?t=${esc(t)}"', 1),
        (r'href="\.\./"', 'href="../index.html"', 1),
    ], "compare.js"))

    put("assets/positioning.js", rewrite((SRC / "assets/positioning.js").read_text(encoding="utf-8"), [
        (r'href="\.\./analyze/\$\{esc\((\w+)\.ticker\)\}/"', r'href="../analyze/index.html?t=${esc(\1.ticker)}"', 6),
    ], "positioning.js"))

    for rel in ["assets/desk.css", "assets/motion.js", "assets/paper.js", "data.js",
                "paper/index.html", "positioning/index.html", "analyze/compare/index.html"]:
        copy(rel)

    # One analyse page for every ticker: ?t=AAPL renders the ticker, no query renders the index.
    analyze_index = (SRC / "analyze/index.html").read_text(encoding="utf-8")
    chooser = (
        '<script>\n'
        '  (function () {\n'
        '    var t = new URLSearchParams(location.search).get("t");\n'
        '    if (t) { window.DESK_TICKER = t.toUpperCase(); document.title = window.DESK_TICKER + " \\u00b7 Desk"; }\n'
        '    else { window.DESK_INDEX = true; }\n'
        '    document.write(\'<script src="../assets/\' + (t ? "analyze.js" : "analyze-index.js") + \'"><\\/script>\');\n'
        '  })();\n'
        '</script>'
    )
    analyze_index = rewrite(analyze_index, [
        (r'<script>window\.DESK_INDEX = true;</script>\n', "", 1),
        (r'<script src="\.\./assets/analyze-index\.js"></script>', lambda m: chooser, 1),
    ], "analyze/index.html")
    put("analyze/index.html", analyze_index)

    for p in sorted(SRC.glob("*.json")):
        copy(p.name)
    for p in sorted((SRC / "analysis").rglob("*.json")):
        copy(str(p.relative_to(SRC)))

    if len(files) > 255:
        sys.exit(f"{len(files)} supporting files, the artifact limit is 255")
    (OUT / "files.json").write_text(json.dumps({f: f for f in files}, indent=1), encoding="utf-8")
    return files


if __name__ == "__main__":
    files = build()
    total = sum((OUT / f).stat().st_size for f in files) + (OUT / "index.html").stat().st_size
    print(f"{OUT.relative_to(ROOT)}: page + {len(files)} supporting files, {total / 1e6:.1f} MB")
