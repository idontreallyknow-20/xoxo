"""/analyze/compare/: the row model, run in node over the real analysis records.

compare.js keeps its row-building half pure and exports it as window.DeskCompare,
so these tests evaluate the actual function over the committed JSON rather than
a Python re-statement of it. What is checked is what the page promises: every
cell comes from the record the deep page renders, valuation is never marked
best, the call and the falsifier share a class, and the selection in the URL is
parsed the way the docstring says.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
ASSETS = DASH / "assets"
ANALYSIS = DASH / "analysis"


def node(script: str):
    if shutil.which("node") is None:
        pytest.skip("node not available to evaluate compare.js")
    prelude = (
        "global.window = {}; global.localStorage = undefined;\n"
        "global.document = { documentElement: { dataset: {} } };\n"
        "global.location = { search: '', pathname: '/analyze/compare/' };\n"
        + (ASSETS / "desk-common.js").read_text()
        + "\n" + (ASSETS / "compare.js").read_text() + "\n"
    )
    out = subprocess.run(["node", "-e", prelude + script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout)


def records(*tickers):
    return [json.loads((ANALYSIS / f"{t}.json").read_text()) for t in tickers]


def model(*tickers):
    return node("const R = " + json.dumps(records(*tickers)) + ";\n"
                "console.log(JSON.stringify(window.DeskCompare.model(R)));")


def rows_by_label(m):
    out = {}
    for sec in m["sections"]:
        for r in sec["rows"]:
            out.setdefault(r["label"], []).append((sec["title"], r))
    return out


# -- selection parsing -----------------------------------------------------------


def test_selection_is_uppercased_deduplicated_and_capped_at_four():
    got = node("console.log(JSON.stringify(window.DeskCompare.parseSelection("
               "'?t=klac,bkng, KLAC aapl;nvda|msft', new Set(['KLAC','BKNG','AAPL','NVDA','MSFT']))));")
    assert got == {"tickers": ["KLAC", "BKNG", "AAPL", "NVDA"], "rejected": [], "overflow": ["MSFT"]}


def test_unknown_names_are_reported_not_dropped_silently():
    got = node("console.log(JSON.stringify(window.DeskCompare.parseSelection('?x=1&t=KLAC%2CZZZZ', new Set(['KLAC']))));")
    assert got == {"tickers": ["KLAC"], "rejected": ["ZZZZ"], "overflow": []}
    got = node("console.log(JSON.stringify(window.DeskCompare.parseSelection('', new Set(['KLAC']))));")
    assert got == {"tickers": [], "rejected": [], "overflow": []}


# -- the model over real records -----------------------------------------------------


def test_every_cell_is_the_records_own_number():
    m = model("KLAC", "BKNG")
    assert m["tickers"] == ["KLAC", "BKNG"]
    klac, bkng = records("KLAC", "BKNG")
    rows = rows_by_label(m)
    price = rows["price"][0][1]["cells"]
    assert price[0]["value"] == klac["identity"]["price"] and price[1]["value"] == bkng["identity"]["price"]
    assert price[0]["text"] == f"${klac['identity']['price']:,.2f}"
    company = rows["company"][0][1]["cells"]
    assert [c["text"] for c in company] == [klac["identity"]["company"], bkng["identity"]["company"]]
    # a trend cell carries the record's last value and growth
    rev = rows["Revenue"][0][1]["cells"]
    t = next(x for x in klac["trends"] if x["key"] == "revenue")
    assert rev[0]["value"] == t["cagr"]
    assert f"${t['last']:.2f}bn" in rev[0]["text"]
    assert "<svg" in rev[0]["html"], "the four-year sparkline is drawn"
    # the score row is the record's percentile
    qv = rows["quality + value, percentile"][0][1]["cells"]
    assert qv[0]["value"] == klac["score"]["variants"]["quality_value"]["percentile"]


def test_the_call_and_the_falsifier_are_adjacent_and_share_a_class():
    m = model("KLAC", "AAPL")
    sec = next(s for s in m["sections"] if s["title"].startswith("The standing call"))
    labels = [r["label"] for r in sec["rows"]]
    assert labels[:2] == ["the call", "wrong if"]
    assert sec["rows"][0]["cls"] == sec["rows"][1]["cls"] == "cmp-call"
    klac = records("KLAC")[0]
    assert sec["rows"][0]["cells"][0]["text"] == klac["thesis"]["action"]
    assert sec["rows"][1]["cells"][0]["text"] == klac["thesis"]["logged"]["wrong_if"]
    # AAPL is screen-only: no view, no falsifier, and it says so rather than inventing one
    assert "no view formed" in sec["rows"][0]["cells"][1]["text"]
    assert sec["rows"][1]["cells"][1]["text"] == "no falsifier recorded"
    assert "n/a" not in sec["rows"][1]["cells"][1]["text"]


def test_valuation_is_never_marked_best_and_never_coloured():
    m = model("KLAC", "BKNG", "AAPL", "NVDA")
    sec = next(s for s in m["sections"] if s["title"] == "Valuation")
    for r in sec["rows"]:
        for c in r["cells"]:
            assert not c.get("best"), f"{r['label']} marks a best cell"
            assert 'class="up"' not in c["html"] and 'class="down"' not in c["html"], r["label"]


def test_best_is_marked_only_where_the_row_knows_which_way_is_better():
    m = model("KLAC", "BKNG", "AAPL", "NVDA")
    rows = rows_by_label(m)
    # ROIC: higher is better, so exactly the maximum is marked
    _, roic = rows["Return on invested capital, average"][0]
    vals = [c["value"] for c in roic["cells"] if c["value"] is not None]
    marked = [c for c in roic["cells"] if c.get("best")]
    assert len(marked) == 1 and marked[0]["value"] == max(vals)
    # diluted shares: lower is better (a shrinking count), so the minimum change is marked
    _, sh = rows["Diluted shares"][0]
    vals = [c["value"] for c in sh["cells"] if c["value"] is not None]
    marked = [c for c in sh["cells"] if c.get("best")]
    assert len(marked) == 1 and marked[0]["value"] == min(vals)
    # identity rows have no direction, so nothing is marked
    for label in ("company", "price", "market cap", "sector"):
        assert not any(c.get("best") for c in rows[label][0][1]["cells"]), label


def test_a_single_name_marks_nothing_best():
    m = model("KLAC")
    for sec in m["sections"]:
        for r in sec["rows"]:
            assert not any(c.get("best") for c in r["cells"]), (sec["title"], r["label"])


def test_missing_data_is_na_not_zero():
    m = model("KLAC", "BKNG")
    rows = rows_by_label(m)
    # BKNG reports no cost of revenue, so its gross margin trend is absent
    gm = rows["Gross margin"][0][1]["cells"]
    assert gm[1]["text"] == "n/a" and gm[1]["value"] is None
    assert gm[0]["text"] != "n/a"
    assert not gm[1].get("best")


def test_empty_selection_has_no_sections():
    m = node("console.log(JSON.stringify(window.DeskCompare.model([])));")
    assert m == {"tickers": [], "sections": []}


def test_sections_are_in_the_deep_pages_order():
    m = model("KLAC", "BKNG")
    titles = [s["title"] for s in m["sections"]]
    assert titles == ["The names", "The standing call, and what would prove it wrong", "Four fiscal years",
                      "Screen measures", "Valuation", "The score", "Provenance"]
    assert all(s["rows"] for s in m["sections"])


# -- the shell and the links -----------------------------------------------------------


def test_the_shell_exists_and_points_two_levels_up():
    p = DASH / "analyze" / "compare" / "index.html"
    assert p.exists()
    html = p.read_text()
    assert 'src="../../assets/compare.js"' in html and 'href="../../assets/desk.css"' in html
    assert "window.DESK_COMPARE = true;" in html
    assert "<title>Compare · Desk</title>" in html


def test_the_index_and_the_deep_page_link_to_compare():
    assert 'href="compare/"' in (ASSETS / "analyze-index.js").read_text()
    assert '../compare/?t=${esc(id.ticker)}' in (ASSETS / "analyze.js").read_text()


def test_every_class_compare_uses_is_defined_in_the_stylesheet():
    """The general test covers this too; this one names the file so a failure reads clearly."""
    import re

    css = (ASSETS / "desk.css").read_text()
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    used = set()
    for m in re.finditer(r'class="([^"$]+)"', (ASSETS / "compare.js").read_text()):
        used.update(m.group(1).split())
    assert not (used - defined), sorted(used - defined)
