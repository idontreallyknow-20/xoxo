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
    "dashboard/analyze/KLAC/index.html": "../../",
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
    assert len(hrefs) >= 9, hrefs  # brand plus eight tabs
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
