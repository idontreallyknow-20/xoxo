"""Every link the new pages emit has to resolve to a file that exists.

The pages are static directories served by anything that understands an index
file, so a link is right or wrong on disk and can be checked without a browser.
The bug that prompted this: ``chrome()`` took a ``depth`` argument, used it for
the brand and the two new tabs, and hard-coded ``../../`` for the six front-page
anchors. From ``/analyze/`` and ``/positioning/``, whose depth is ``../``, those
six pointed one directory above the site root.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
COMMON = DASH / "assets" / "desk-common.js"

# Where each page sits, and the depth it passes to chrome().
PAGES = {
    "dashboard/analyze/index.html": "../",
    "dashboard/positioning/index.html": "../",
    "dashboard/paper/index.html": "../",
    "dashboard/analyze/KLAC/index.html": "../../",
    "dashboard/analyze/compare/index.html": "../../",
}


def chrome_hrefs(depth: str):
    """Call the real ``chrome()`` in node and read the hrefs out of what it returns.

    Re-implementing the nav table in Python would test the copy, not the code. node
    is on the box (the build already uses it to syntax-check the scripts); if it is
    not, the test says so rather than passing quietly.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available to evaluate desk-common.js")
    script = (
        "global.window = {}; global.localStorage = undefined;\n"
        "global.document = { documentElement: { dataset: {} } };\n"
        + COMMON.read_text()
        + "\nconsole.log(JSON.stringify("
        + "(window.Desk.chrome('Analyse', %r).match(/href=\"[^\"]*\"/g) || [])"
        % depth
        + ".map(function (h) { return h.slice(6, -1); })));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    hrefs = json.loads(out.stdout)
    assert len(hrefs) >= 10, hrefs  # brand plus nine tabs
    return hrefs


@pytest.mark.parametrize("page,depth", sorted(PAGES.items()))
def test_every_nav_link_resolves_from_the_page_that_emits_it(page, depth):
    here = (ROOT / page).parent
    assert here.is_dir(), page
    for href in chrome_hrefs(depth):
        target = href.split("#")[0]
        if target.endswith("/"):
            target += "index.html"
        resolved = (here / target).resolve()
        assert resolved.exists(), f"{page}: {href} -> {resolved} does not exist"
        assert DASH.resolve() in resolved.parents or resolved == DASH.resolve(), \
            f"{page}: {href} escapes the site root"


def test_chrome_builds_every_link_from_depth():
    """No literal ``../`` may survive in the nav table: it is right for exactly one
    of the three page depths and wrong for the others."""
    src = COMMON.read_text()
    m = re.search(r"function chrome\(active, depth\) \{(.*?)\n  \}", src, re.S)
    assert "../" not in m.group(1), "chrome() still hard-codes a relative path"


def test_the_quarter_marker_fits_the_column_it_goes_in():
    """`.ledger .m` is one line capped at 27ch with `overflow: hidden`, so anything
    longer is unreadable with no way to get it back. The quarter label runs to 148
    characters and was clipped on thirteen of the sixteen deep pages. This runs the
    real split from analyze.js over every label the build produces.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available to evaluate analyze.js")
    src = (DASH / "assets" / "analyze.js").read_text()
    parts = []
    for name in ("Q_SPLIT", "qMark", "qCaveat"):
        m = re.search(r"^  (?:const %s = .*?;|function %s\(.*?^  \})" % (name, name),
                      src, re.S | re.M)
        assert m, f"{name} not found in analyze.js"
        parts.append(m.group(0))

    labels = []
    for f in sorted((DASH / "analysis").glob("*.json")):
        rec = json.loads(f.read_text())
        q = ((rec.get("what_changed") or {}).get("read") or {}).get("quarter_label")
        if q:
            labels.append(q)
    assert len(labels) >= 16, f"expected the deep pages to carry a quarter label, got {len(labels)}"

    script = ("\n".join(parts) + "\nconst L = " + json.dumps(labels) + ";\n"
              "console.log(JSON.stringify(L.map(s => [qMark(s), qCaveat(s)])));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    for original, (mark, caveat) in zip(labels, json.loads(out.stdout)):
        assert mark, original
        assert len(mark) <= 27, f"marker {mark!r} ({len(mark)} chars) will be clipped"
        assert original.startswith(mark), (mark, original)
        assert caveat.count("(") >= caveat.count(")"), f"orphaned bracket in {caveat!r}"
        # Nothing may be lost: every word of the label survives in one half or the other.
        words = set(re.findall(r"[A-Za-z0-9]+", original))
        kept = set(re.findall(r"[A-Za-z0-9]+", mark + " " + caveat))
        assert words == kept, f"dropped {sorted(words - kept)} from {original!r}"
