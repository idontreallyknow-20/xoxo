"""The brief's hardest constraint, enforced rather than promised.

"Do not restructure or break any existing pages" and "never modify existing routes
beyond adding links to the new tabs" are easy to honour on day one and easy to
erode by accident on day three, when a one-line fix to price_screen.py would save
an hour. These tests make that erosion loud.

They diff the branch against its merge base with the default branch, so they keep
working as the branch grows and they do not care what order the commits landed in.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Everything that existed before this work started and must come through unchanged.
UNTOUCHABLE = [
    "scripts/serve.py",
    "scripts/config.py",
    "scripts/universe.py",
    "scripts/fundamentals.py",
    "scripts/quality_screen.py",
    "scripts/price_screen.py",
    "scripts/build_dashboard.py",
    "scripts/write_research.py",
    "scripts/research_notes.py",
    "scripts/email_picks.py",
    "scripts/email_hero_gif.py",
    "scripts/_repull.py",
    "README.md",
    "criteria.md",
    "journal.md",
    "requirements.txt",
    "research/",
    "universe/quality_scores_latest.csv",
    "universe/price_screen_latest.csv",
    "universe/quality_top150.csv",
    "universe/universe_latest.csv",
    "dashboard/data.js",
]


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT))
    return r.stdout.strip(), r.returncode


def base_ref():
    """The commit this branch started from, whichever remote name is available."""
    for ref in ("origin/main", "main", "origin/master", "master"):
        out, code = git("merge-base", "HEAD", ref)
        if code == 0 and out:
            return out
    return None


@pytest.fixture(scope="module")
def base():
    b = base_ref()
    if not b:
        pytest.skip("no main branch to diff against")
    return b


def test_the_original_pipeline_is_untouched(base):
    out, _ = git("diff", "--stat", base, "--", *UNTOUCHABLE)
    assert not out, f"files that had to stay unchanged were modified:\n{out}"


def test_index_html_gained_only_nav_links(base):
    """The one existing file this work is allowed to touch, and only to add links."""
    diff, _ = git("diff", base, "--unified=0", "--", "dashboard/index.html")
    changed = [ln for ln in diff.splitlines()
               if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    assert changed, "expected the two nav links"
    assert all(ln.startswith("+") for ln in changed), f"index.html has removals:\n{diff}"
    assert len(changed) <= 4, f"more than a couple of lines added:\n{diff}"
    for ln in changed:
        assert "<a href=" in ln, ln
        assert "analyze/" in ln or "positioning/" in ln, ln


def test_everything_else_new_is_additive(base):
    """Any other pre-existing file that changed is a bug in this work, not a feature."""
    out, _ = git("diff", "--name-status", base)
    modified = [ln.split("\t", 1)[1] for ln in out.splitlines() if ln.startswith("M")]
    allowed = {"dashboard/index.html", ".gitignore"}
    unexpected = [m for m in modified if m not in allowed]
    assert not unexpected, f"pre-existing files modified: {unexpected}"


def test_gitignore_only_gained_lines(base):
    diff, _ = git("diff", base, "--unified=0", "--", ".gitignore")
    if not diff:
        return
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert not removed, f".gitignore lost lines:\n{diff}"


def test_portfolio_is_still_ignored_and_still_absent(base):
    _, code = git("check-ignore", "-q", "portfolio/holdings.csv")
    assert code == 0
    out, _ = git("ls-files", "portfolio")
    assert not out, "a personal position file is tracked"


def test_the_front_page_journal_still_disagrees_with_the_new_pages():
    """A known divergence, pinned here so it is not mistaken for a fresh bug.

    ``build_dashboard.py`` parses journal headings with ``(\\S+)`` for the ticker,
    so a heading naming five names produces one entry under the first and folds
    the other four into the title. The new pages parse the same file correctly
    and show sixteen names where the front page shows thirteen.

    Both the generator and its output are on the untouchable list, and the brief
    says not to modify existing routes beyond adding links, so this is documented
    rather than fixed. If someone lifts that constraint later, the repair is one
    line: give ``build_dashboard.py`` the grammar in ``scripts/an/journal.py``.
    This test fails the day that happens, which is the point.
    """
    import json
    import re

    from an import journal

    src = (ROOT / "dashboard" / "data.js").read_text(encoding="utf-8")
    front = json.loads(src[src.index("{"):].rstrip().rstrip(";"))["journal"]
    front_tickers = {e["ticker"] for e in front if e["ticker"] != "SYSTEM"}
    ours = set(journal.by_ticker())

    assert front_tickers < ours, "the front page caught up; delete this test and the note in NOTES.md"
    assert ours - front_tickers == {"NOW", "ACN", "AMAT", "NVR"}
    assert any(re.match(r"^[A-Z]+ [A-Z]+", e["title"]) for e in front), \
        "expected the folded multi-ticker title on the front page"


def test_the_generator_of_that_divergence_is_the_untouchable_one():
    """The reason it cannot be fixed here, asserted rather than asserted-in-a-comment."""
    assert "scripts/build_dashboard.py" in UNTOUCHABLE
    assert "dashboard/data.js" in UNTOUCHABLE
    src = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert r"(\S+)\s*(.*?)$" in src, "build_dashboard.py's journal grammar changed; recheck this note"
