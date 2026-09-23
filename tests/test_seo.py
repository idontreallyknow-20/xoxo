"""Every public page says who made it and where it lives, the same way.

Desk is meant to help its author rank for his own name, so the head of every page
carries the author, a canonical URL, an Open Graph card and a Person graph whose @id
is the one his hub site uses. These checks run on the files as served, without a
browser: the generated shells, the three hand-written pages and the sitemap.
"""
import json
import re
from pathlib import Path

import pytest

from an import seo

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"

PAGES = {
    "index.html": "/",
    "analyze/index.html": "/analyze/",
    "analyze/KLAC/index.html": "/analyze/KLAC/",
    "analyze/DPM.TO/index.html": "/analyze/DPM.TO/",
    "analyze/compare/index.html": "/analyze/compare/",
    "positioning/index.html": "/positioning/",
    "paper/index.html": "/paper/",
}


def ld(src):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', src, re.S)
    assert m, "no JSON-LD"
    return json.loads(m.group(1))


@pytest.mark.parametrize("page,path", sorted(PAGES.items()))
def test_every_page_has_the_full_head(page, path):
    src = (DASH / page).read_text(encoding="utf-8")
    url = seo.SITE + path
    assert src.count("<title>") == 1
    title = re.search(r"<title>(.*?)</title>", src).group(1)
    assert "Desk" in title and 20 <= len(title) <= 90, title
    assert '<meta name="author" content="Joseph Leung">' in src
    assert f'<link rel="canonical" href="{url}">' in src
    assert f'<meta property="og:url" content="{url}">' in src
    assert '<meta property="og:image" content="' + seo.OG_IMAGE + '">' in src
    assert '<meta name="twitter:card" content="summary_large_image">' in src
    assert 'rel="icon"' in src and "noindex" not in src
    assert "fonts.googleapis.com" not in src, "fonts are self-hosted now"
    graph = ld(src)["@graph"]
    person = next(n for n in graph if n["@type"] == "Person")
    assert person["@id"] == seo.PERSON_ID and person["url"] == seo.HUB
    assert "Joseph Wah Sing Leung" in person["alternateName"]
    assert seo.HUB in person["sameAs"]
    site = next(n for n in graph if n["@type"] == "WebSite")
    assert site["creator"] == {"@id": seo.PERSON_ID}


def test_titles_are_unique_across_the_ticker_pages():
    titles = [re.search(r"<title>(.*?)</title>", p.read_text()).group(1)
              for p in (DASH / "analyze").glob("*/index.html")]
    assert len(titles) == len(set(titles))


def test_the_front_page_has_one_h1_and_the_credit():
    src = (DASH / "index.html").read_text(encoding="utf-8")
    assert src.count("<h1") == 1
    assert "Joseph Wah Sing Leung" in src
    assert f'Built by <a href="{seo.HUB}/" rel="author">Joseph Leung</a>' in src


def test_every_script_rendered_page_titles_itself_with_an_h1_and_ends_with_the_credit():
    for js in ("analyze.js", "analyze-index.js", "compare.js", "positioning.js", "paper.js"):
        src = (DASH / "assets" / js).read_text(encoding="utf-8")
        assert '<h1 class="tk">' in src, js
        assert "D.footer(" in src, js
    common = (DASH / "assets" / "desk-common.js").read_text(encoding="utf-8")
    assert seo.HUB + "/" in common and "Built by" in common


def test_sitemap_lists_every_ticker_page_and_robots_points_at_it():
    xml = (DASH / "sitemap.xml").read_text()
    locs = re.findall(r"<loc>(.*?)</loc>", xml)
    tickers = [p.parent.name for p in (DASH / "analyze").glob("*/index.html") if p.parent.name != "compare"]
    for t in tickers:
        assert f"{seo.SITE}/analyze/{t}/" in locs
    assert seo.SITE + "/" in locs and len(locs) == len(set(locs))
    robots = (DASH / "robots.txt").read_text()
    assert "Disallow: /\n" not in robots and f"Sitemap: {seo.SITE}/sitemap.xml" in robots


def test_the_404_page_exists_is_noindexed_and_uses_absolute_paths():
    src = (DASH / "404.html").read_text()
    assert 'content="noindex"' in src and 'rel="canonical"' not in src
    assert 'href="/assets/desk.css"' in src and 'href="/"' in src


def test_the_preview_image_and_icons_exist():
    for f in ("og.png", "favicon.svg", "favicon.ico", "apple-touch-icon.png"):
        assert (DASH / f).stat().st_size > 0, f
    assert (DASH / "og.png").read_bytes()[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big")
