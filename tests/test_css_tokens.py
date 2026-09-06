"""The new stylesheet must stay in step with the original page.

If a theme value drifts, the new pages stop looking like the site they belong to,
and the drift is invisible until someone opens both in the same theme.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "dashboard" / "index.html"
CSS = ROOT / "dashboard" / "assets" / "desk.css"
THEMES = ["night", "paper", "slate", "forest", "bone", "amber"]


def tokens(src: str, theme: str):
    if theme == "night":
        m = re.search(r':root,\s*\[data-theme="night"\]\s*\{(.*?)\}', src, re.S)
    else:
        m = re.search(r'\[data-theme="%s"\]\s*\{(.*?)\}' % theme, src, re.S)
    assert m, f"no block for {theme}"
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", m.group(1)))


@pytest.mark.parametrize("theme", THEMES)
def test_theme_values_match_the_original_page(theme):
    a = tokens(INDEX.read_text(), theme)
    b = tokens(CSS.read_text(), theme)
    assert a == b, {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}


def test_every_variable_used_is_defined_in_every_theme():
    css = CSS.read_text()
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    # --i is a per-element reveal index set inline, not a theme token.
    non_theme = {"--ease-out", "--display", "--mono", "--i"}
    for theme in THEMES:
        defined = set(tokens(css, theme)) | non_theme
        missing = used - defined
        assert not missing, f"{theme} is missing {sorted(missing)}"


def test_index_html_was_changed_only_by_adding_nav_links():
    """The brief said not to modify existing pages beyond adding links to the new
    tabs. This is that promise, enforced."""
    import subprocess

    diff = subprocess.run(["git", "diff", "HEAD", "--unified=0", "--", "dashboard/index.html"],
                          capture_output=True, text=True, cwd=str(ROOT)).stdout
    changed = [ln for ln in diff.splitlines()
               if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---"))]
    assert all(ln.startswith("+") for ln in changed), f"index.html has removals: {changed}"
    for ln in changed:
        assert "<a href=" in ln and ("analyze/" in ln or "positioning/" in ln), ln


def test_the_new_pages_do_not_load_the_old_stylesheet_or_vice_versa():
    assert "desk.css" not in INDEX.read_text()
    for shell in (ROOT / "dashboard" / "analyze").glob("*/index.html"):
        assert "assets/desk.css" in shell.read_text()
        break


def test_border_radius_is_zero_everywhere():
    """The original forces it globally; a new page that quietly rounds a corner
    would read as belonging to a different site."""
    css = CSS.read_text()
    assert "border-radius: 0 !important" in css
    others = [m for m in re.findall(r"border-radius:\s*([^;]+);", css) if "0 !important" not in m]
    assert not others, others


def test_only_the_two_loaded_font_families_are_used():
    css = CSS.read_text()
    fams = set(re.findall(r"font-family:\s*([^;]+);", css))
    for f in fams:
        assert "var(--mono)" in f or "var(--display)" in f or "inherit" in f, f


def test_no_font_weight_outside_the_loaded_range():
    """Bricolage is loaded at 300/400/500/600 and Plex Mono at 400/500. A 700 would
    be synthesised by the browser and look wrong."""
    css = CSS.read_text()
    weights = {w.strip() for w in re.findall(r"font-weight:\s*([^;]+);", css)}
    allowed = {"300", "400", "500", "600", "inherit", "normal"}
    assert weights <= allowed, weights - allowed


ASSETS = ROOT / "dashboard" / "assets"


def test_no_multi_column_grid_is_declared_inline():
    """An inline ``grid-template-columns`` cannot be overridden by a media query, so
    the layout it sets is the layout on a phone too. The sources-and-gaps footer
    was two 157px columns either side of a 44px gutter at 390px wide."""
    for js in ASSETS.glob("*.js"):
        for m in re.finditer(r'style="[^"]*grid-template-columns:([^";]+)', js.read_text()):
            cols = m.group(1)
            assert cols.count("fr") + cols.count("px") + cols.count("%") < 2, (
                f"{js.name}: inline multi-column grid {cols!r}; put it in desk.css "
                "where a breakpoint can reach it")


def test_every_class_the_scripts_use_is_defined():
    """A class name that no rule matches is a layout that only looks finished."""
    css = CSS.read_text()
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    used = set()
    for js in ASSETS.glob("*.js"):
        for m in re.finditer(r'class="([^"$]+)"', js.read_text()):
            used.update(m.group(1).split())
    missing = {c for c in used if c not in defined}
    assert not missing, f"classes used by the scripts with no rule in desk.css: {sorted(missing)}"


def test_the_two_column_footer_collapses_on_a_phone():
    css = CSS.read_text()
    assert re.search(r"^\.cols\s*\{[^}]*grid-template-columns:\s*1fr 1fr", css, re.M), \
        ".cols should carry the two-column layout"
    assert re.search(r"@media \(max-width: \d+px\) \{ \.cols \{ grid-template-columns: 1fr", css), \
        ".cols should collapse to one column at a breakpoint"
    assert re.search(r"\.cols > \*\s*\{[^}]*min-width:\s*0", css), \
        "grid items default to min-content and will blow the page out"
