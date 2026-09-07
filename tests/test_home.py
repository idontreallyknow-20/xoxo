"""The redesigned front page and the motion layer, checked without a browser.

The browser checks live in scripts/shoot.py (console errors, horizontal overflow
at 390px, two themes). These pin the facts that do not need one: no market-data
key in the browser, the shared stylesheet and motion layer on every page, a
reduced-motion path, and the journal tab reading the tracker rather than the
file that folds five tickers into one entry.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
INDEX = (DASH / "index.html").read_text()
HOME = (DASH / "assets" / "home.js").read_text()
MOTION = (DASH / "assets" / "motion.js").read_text()
CSS = (DASH / "assets" / "desk.css").read_text()


def test_the_front_page_no_longer_calls_a_market_data_service_from_the_browser():
    for src in (INDEX, HOME):
        assert "finnhub.io" not in src.lower(), "the comment may name it; the code may not call it"
        assert "token=" not in src and "https://" not in src.replace("https://fonts.g", "")
        assert 'store.get("key"' not in src and "savekey" not in src


def test_the_front_page_is_a_shell_on_the_shared_assets():
    assert "assets/desk.css" in INDEX and "assets/motion.js" in INDEX and "assets/desk-common.js" in INDEX
    assert "<style" not in INDEX, "styles belong in desk.css"
    assert 'href="analyze/"' in INDEX and 'href="positioning/"' in INDEX
    assert 'href="#journal"' in INDEX and 'href="#setups"' in INDEX


def test_the_setups_tab_reads_setups_json_and_never_calls_it_a_recommendation():
    m = re.search(r"function renderSetups\(\) \{(.*?)\n  \}", HOME, re.S)
    assert m
    assert 'loadJson("setups.json")' in HOME
    assert "what_this_is" in m.group(1) and "swing.md" in m.group(1)
    assert "recommend" not in m.group(1).lower().replace("nothing here is a recommendation", "").replace("never a recommendation", "")


def test_every_shell_loads_the_motion_layer_before_the_page_script():
    for shell in [DASH / "positioning" / "index.html", DASH / "analyze" / "index.html",
                  DASH / "analyze" / "KLAC" / "index.html", DASH / "analyze" / "compare" / "index.html"]:
        src = shell.read_text()
        assert src.index("assets/motion.js") < src.index("assets/desk-common.js"), shell


def test_motion_has_a_reduced_motion_path_and_no_library():
    assert "prefers-reduced-motion" in MOTION
    assert 'localStorage.getItem("desk-motion")' in MOTION
    assert "https://" not in MOTION and "import" not in MOTION and "THREE" not in MOTION
    assert "requestAnimationFrame" in MOTION and "getContext(\"2d\")" in MOTION
    assert "visibilitychange" in MOTION, "the field must pause in a hidden tab"


def test_the_stylesheet_switches_every_effect_off_without_motion():
    for cls in (".tilt", ".lift", "#depth", ".view.on", "tr.row", ".h-bar i"):
        assert re.search(r'html\[data-motion="off"\][^{]*' + re.escape(cls), CSS), f"{cls} has no motion-off rule"
    assert CSS.count("prefers-reduced-motion") >= 4


def test_the_tilt_light_is_a_background_not_an_overlay():
    assert ".tilt::before" not in CSS, "an overlay over the text changes its contrast; keep the light underneath"
    assert "background-image: radial-gradient" in CSS


def test_the_journal_tab_reads_the_tracker_not_data_js():
    m = re.search(r"function renderJournal\(\) \{(.*?)\n  \}", HOME, re.S)
    assert m
    body = m.group(1)
    assert "T.grades" in body and "D.journal" not in body


def test_the_front_page_still_reads_the_frozen_data_js():
    assert "window.DASH" in HOME
    assert '<script src="data.js">' in INDEX
