"""an.guidance: guidance language read out of two press releases and diffed, on a fictional filer.

The fixtures are written so every classification appears at least once. The
expected answers were worked out by hand from the two texts before the code ran.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from an import analysis, guidance as g
from an.htmltext import to_text

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def texts():
    return (to_text((FIX / "ex991_current.htm").read_text()), to_text((FIX / "ex991_prior.htm").read_text()))


@pytest.fixture(scope="module")
def extracted(texts):
    cur, pri = texts
    return g.extract(cur), g.extract(pri)


# -- extraction ------------------------------------------------------------------


def test_sentences_survive_inc_and_quotes():
    s = g.split_sentences('Exemplar Devices Inc. reported results. "We expect growth," said the CFO. Revenue was $1 billion.')
    assert len(s) == 3 and s[0].endswith("results.")


def test_prior_release_yields_six_figures_with_periods(extracted):
    _, pri = extracted
    got = {(i.metric, i.period): (i.low, i.high, i.unit, i.kind) for i in pri}
    assert got[("revenue", "Q3 FY2026")] == (0.98e9, 1.02e9, "USD", "range")
    assert got[("gross_margin", "Q3 FY2026")] == (60.5, 60.5, "percent", "point")
    assert got[("eps", "Q3 FY2026")] == (1.05, 1.15, "USD", "range")
    assert got[("revenue", "FY2026")] == (4.0e9, 4.0e9, "USD", "point")
    assert got[("capex", "FY2026")] == (150e6, 150e6, "USD", "point")
    assert got[("tax_rate", "FY2026")] == (15.0, 15.0, "percent", "point")
    assert len(pri) == 6


def test_a_prior_outlook_comparison_inside_the_clause_is_not_the_guide(extracted):
    cur, _ = extracted
    rev = [i for i in cur if (i.metric, i.period) == ("revenue", "FY2026")]
    assert len(rev) == 1 and rev[0].low == 4.1e9, "the $4.0 billion 'prior outlook' must not be read as the guide"
    assert "up from its prior outlook" in rev[0].clause, "but it stays in the clause the reader sees"


def test_historical_and_boilerplate_sentences_are_ignored(extracted):
    cur, _ = extracted
    assert not any("was $1.01 billion" in i.sentence for i in cur), "a reported figure is not guidance"
    assert not any("forward-looking statements" in i.sentence for i in cur)


def test_a_period_after_the_metric_is_used_when_none_precedes_it():
    items = g.extract("The Company expects its tax rate to be approximately 15 percent for fiscal 2027.")
    assert [(i.metric, i.period, i.low) for i in items] == [("tax_rate", "FY2027", 15.0)]


def test_a_guide_without_a_figure_is_kept_as_qualitative():
    items = g.extract("Management expects revenue to grow modestly in fiscal 2027.")
    assert len(items) == 1 and items[0].kind == "qualitative" and items[0].low is None
    assert items[0].describe() == "no number given"


def test_plus_or_minus_and_month_quarter_forms():
    items = g.extract("September quarter revenue is guided to $4.0 billion plus or minus $200 million, and EPS to $1.16.")
    by = {i.metric: i for i in items}
    assert by["revenue"].period == "September quarter" and (by["revenue"].low, by["revenue"].high) == (3.8e9, 4.2e9)
    assert by["eps"].low == by["eps"].high == 1.16


def test_each_item_carries_the_verbatim_sentence(extracted):
    cur, pri = extracted
    for i in cur + pri:
        assert i.sentence and i.clause in i.sentence


# -- the diff -----------------------------------------------------------------------


def test_every_classification_appears_once_with_the_hand_computed_answer(extracted):
    cur, pri = extracted
    d = g.diff(cur, pri)
    by = {(it.metric, it.period): it for it in d.items}
    assert by[("revenue", "FY2026")].change == "raised" and "+2.5%" in by[("revenue", "FY2026")].detail
    assert by[("capex", "FY2026")].change == "reiterated"
    assert by[("tax_rate", "FY2026")].change == "not repeated"
    assert {k for k, v in by.items() if v.change == "introduced"} == {("revenue", "Q4 FY2026"), ("gross_margin", "Q4 FY2026"), ("eps", "Q4 FY2026")}
    assert {k for k, v in by.items() if v.change == "lapsed"} == {("revenue", "Q3 FY2026"), ("gross_margin", "Q3 FY2026"), ("eps", "Q3 FY2026")}
    assert d.by_change() == {"introduced": 3, "lapsed": 3, "not repeated": 1, "raised": 1, "reiterated": 1}
    assert d.items[0].change == "raised", "moves first, housekeeping last"
    assert d.items[-1].change == "lapsed"


def test_not_repeated_is_not_called_withdrawn(extracted):
    cur, pri = extracted
    it = {(x.metric, x.period): x for x in g.diff(cur, pri).items}[("tax_rate", "FY2026")]
    assert "not the same as withdrawn" in it.detail and it.current is None and it.prior is not None


def test_lowered_narrowed_widened_and_not_comparable():
    a = g.extract("For fiscal 2027 the Company expects revenue of $4.0 billion to $4.4 billion and gross margin of 60 percent.")
    b = g.extract("For fiscal 2027 the Company expects revenue of $4.1 billion to $4.3 billion and gross margin of 61 percent.")
    d = {(i.metric, i.period): i for i in g.diff(b, a).items}
    assert d[("revenue", "FY2027")].change == "narrowed"
    assert d[("gross_margin", "FY2027")].change == "raised" and "+1.0 points" in d[("gross_margin", "FY2027")].detail
    d2 = {(i.metric, i.period): i for i in g.diff(a, b).items}
    assert d2[("revenue", "FY2027")].change == "widened" and d2[("gross_margin", "FY2027")].change == "lowered"
    c = g.extract("For fiscal 2027 the Company expects revenue of $3.5 billion to $3.7 billion.")
    assert g.diff(c, a).items[0].change == "lowered"
    q = g.extract("For fiscal 2027 the Company expects revenue to grow modestly.")
    d3 = g.diff(q, a)
    assert {i.change for i in d3.items if i.metric == "revenue"} == {"not comparable"}
    assert d3.not_determinable and "without a figure" in d3.not_determinable[0]


def test_the_block_is_labelled_mechanical_and_carries_both_quotes(texts):
    cur, pri = texts
    b = g.build_block("EXMPL", current_meta={"filed": "2026-07-28"}, prior_meta={"filed": "2026-04-28"},
                      current_text=cur, prior_text=pri, built_at="2026-09-07T00:00:00")
    assert b["available"] and b["status"] == "mechanical"
    assert all(it["mechanical"] for it in b["items"])
    raised = next(it for it in b["items"] if it["change"] == "raised")
    assert raised["quote"].startswith("For fiscal 2026, the Company now expects")
    assert raised["prior_quote"].startswith("For fiscal 2026, the Company expects revenue of approximately $4.0 billion")
    assert "does not read tone" in b["caveat"]


# -- the record and the CLI ----------------------------------------------------------


def test_records_say_not_run_until_the_releases_are_pulled():
    recs = analysis.build_all(built_at="1970-01-01T00:00:00")
    gd = recs["KLAC"]["what_changed"]["guidance_diff"]
    assert gd["available"] is False and gd["status"] == "NOT RUN"
    assert "guidance_diff.py KLAC" in gd["command"]
    assert "free substitute for a transcript" in gd["why"]


def test_a_written_diff_is_merged_into_the_record(tmp_path, texts):
    cur, pri = texts
    b = g.build_block("KLAC", current_meta={"filed": "2026-07-28"}, prior_meta={"filed": "2026-04-28"},
                      current_text=cur, prior_text=pri, built_at="2026-09-07T00:00:00")
    (tmp_path / "KLAC.json").write_text(json.dumps(b))
    from an import local, research_md

    rec = local.load_universe()["KLAC"]
    out = analysis.build_record(rec, note=research_md.load_all().get("KLAC"), guidance_dir=tmp_path,
                                built_at="1970-01-01T00:00:00")
    gd = out["what_changed"]["guidance_diff"]
    assert gd["available"] and gd["summary"]["raised"] == 1
    assert g.load_diff("KLAC", tmp_path)["ticker"] == "KLAC"
    assert g.load_diff("NOPE", tmp_path) is None


def test_cli_fixture_prints_the_shape_and_writes_only_where_told(tmp_path):
    out = tmp_path / "x.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "guidance_diff.py"), "--fixture", "--out", str(out)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("# FIXTURE")
    assert "raised        FY2026 revenue" in r.stdout and "lapsed" in r.stdout
    blob = json.loads(out.read_text())
    assert blob["ticker"] == "EXMPL" and blob["summary"]["raised"] == 1
    assert not (ROOT / "dashboard" / "analysis" / "_guidance").exists(), "the fixture never lands under dashboard/"


def test_cli_dry_run_makes_no_request():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "guidance_diff.py"), "--dry-run", "AAPL"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert "dry run" in r.stdout and "never made a real request" in r.stdout


def test_language_guard_over_a_full_fixture_diff(texts):
    from tests.test_language_guard import FORBIDDEN, walk_strings
    import re

    cur, pri = texts
    b = g.build_block("EXMPL", current_meta={}, prior_meta={}, current_text=cur, prior_text=pri, built_at="x")
    for path, s in walk_strings(b):
        for pat in FORBIDDEN:
            assert not re.search(pat, s, re.I), (path, s)
