import json
import subprocess
import sys
from pathlib import Path

import pytest

from an import analysis, local, paths, research_md, score

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def records():
    return analysis.build_all(built_at="1970-01-01T00:00:00")


def test_one_record_per_top_150_plus_every_note(records):
    assert len(records) == 150
    assert set(research_md.load_all()) <= set(records)


def test_depth_is_labelled(records):
    deep = [t for t, r in records.items() if r["depth"] == "deep"]
    assert sorted(deep) == sorted(research_md.load_all())
    assert all(r["depth"] in analysis.DEPTHS for r in records.values())


def test_every_record_carries_provenance(records):
    for t, r in records.items():
        assert r["identity"]["source"], t
        assert r["identity"]["as_of"] == "2026-09-04", t
        assert r["fundamentals"]["source"], t
        assert r["sources"], t
        assert r["disclaimer"].startswith("Research and analysis from public data")


def test_every_record_says_what_it_does_not_know(records):
    """The gaps list is a first-class field, not a footnote."""
    for t, r in records.items():
        assert r["gaps"], t
        assert any("Four fiscal years" in g for g in r["gaps"]), t
        assert any("dated 2026-09-04" in g for g in r["gaps"]), t


def test_a_screen_only_name_says_so_rather_than_faking_depth(records):
    r = records["AAPL"] if "AAPL" in records else records[[t for t, x in records.items()
                                                           if x["depth"] == "screen"][0]]
    assert r["depth"] == "screen"
    assert r["business"]["available"] is False
    assert "No research note" in r["business"]["why"]
    assert r["thesis"]["available"] is False
    assert r["risks"]["available"] is False
    assert any("No research note" in g for g in r["gaps"])


def test_a_deep_name_has_the_full_set(records):
    k = records["KLAC"]
    assert k["business"]["available"] is True
    assert "inspection" in k["business"]["text"]
    assert len(k["trends"]) == 6
    assert k["thesis"]["available"] and k["thesis"]["conviction"] == 4
    assert k["risks"]["available"] and k["risks"]["price_trigger"] == 130.0
    assert k["risks"]["killers"]
    assert k["valuation"]["available"] and k["valuation"]["written_view"]["text"]
    assert k["journal"] and k["journal"][0]["action"]


def test_missing_price_trigger_is_explained_not_zeroed(records):
    """Three notes name no trigger and AMAT says so outright. An absent trigger is
    not a trigger of zero."""
    for t in ("ACN", "AMAT", "NVR"):
        r = records[t]
        assert r["risks"]["price_trigger"] is None
        assert "not a trigger of zero" in r["risks"]["price_trigger_note"]


def test_what_changed_is_mechanical_where_it_can_be(records):
    k = records["KLAC"]
    labels = [m["label"] for m in k["what_changed"]["mechanical"]]
    assert "Next-year EPS consensus" in labels
    assert "Analyst breadth, 30 days" in labels
    assert "Next report" in labels
    detail = next(m for m in k["what_changed"]["mechanical"] if m["label"] == "Next-year EPS consensus")["detail"]
    assert "90 days" in detail and "last 30" in detail


def test_revision_acceleration_is_computed_not_asserted(records):
    """Splitting a 90-day revision into the last 30 and the 60 before it says whether
    the estimate move is fresh or stale. A single 90-day number cannot."""
    for t, r in records.items():
        for m in r["what_changed"]["mechanical"]:
            if m["label"] == "Next-year EPS consensus":
                assert m["accelerating"] in (True, False, None)


def test_what_changed_admits_when_it_needs_a_reader(records):
    r = records["KLAC"]
    read = r["what_changed"]["read"]
    if read.get("available") is False:
        assert "cannot be parsed out of a screen" in read["why"]


def test_valuation_carries_both_caveats(records):
    k = records["KLAC"]
    assert "four-point median" in k["valuation"]["own_history"]["caveat"]
    assert "not on the same basis" in k["valuation"]["drawdown"]["caveat"]


def test_the_twelve_currency_mismatched_names_explain_themselves(records):
    priced = local.load_price_screen()
    no_hist = [t for t, v in priced.items() if not v.has_own_history]
    assert len(no_hist) == 12
    for t in no_hist:
        oh = records[t]["valuation"]["own_history"]
        assert oh["usable"] is False
        assert oh["caveat"]
        assert any("own-history multiples" in g for g in records[t]["gaps"])


def test_scores_are_attached_for_all_three_variants(records):
    for t, r in records.items():
        assert r["score"]["available"] is True, t
        assert set(r["score"]["variants"]) == set(score.VARIANTS), t
        qv = r["score"]["variants"]["quality_value"]
        assert 0 <= qv["percentile"] <= 100
        assert 0 <= qv["coverage"] <= 1
        assert "not been backtested" in r["score"]["caveat"]


def test_bkng_gross_margin_absence_survives_to_the_record(records):
    gm = next(t for t in records["BKNG"]["trends"] if t["key"] == "gross_margin")
    assert gm["values"] == [None, None, None, None]
    assert gm["span_label"] == "no data"


def test_records_are_json_serialisable_and_reasonably_sized(records):
    for t, r in records.items():
        blob = json.dumps(r)
        assert len(blob) < 200_000, t


def test_build_is_deterministic():
    a = analysis.build_all(built_at="X")
    b = analysis.build_all(built_at="X")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_narrative_merges_when_present(tmp_path):
    d = tmp_path / "_narrative"
    d.mkdir()
    (d / "KLAC.json").write_text(json.dumps({"headline": "a thing happened", "guidance": []}))
    rec = local.load_universe()["KLAC"]
    note = research_md.load_all()["KLAC"]
    r = analysis.build_record(rec, note=note, narrative_dir=d)
    assert r["what_changed"]["read"]["headline"] == "a thing happened"
    assert not any("No earnings-call transcript" in g for g in r["gaps"])


def test_corrupt_narrative_is_ignored_not_fatal(tmp_path):
    d = tmp_path / "_narrative"
    d.mkdir()
    (d / "KLAC.json").write_text("{not json")
    rec = local.load_universe()["KLAC"]
    r = analysis.build_record(rec, note=research_md.load_all()["KLAC"], narrative_dir=d)
    assert r["what_changed"]["read"]["available"] is False


def test_cli_writes_files_and_an_index(tmp_path, monkeypatch):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_analysis.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    idx = json.loads((ROOT / "dashboard" / "analysis" / "index.json").read_text())
    assert idx["count"] == 150
    assert len(idx["tickers"]) == 150
    klac = next(t for t in idx["tickers"] if t["ticker"] == "KLAC")
    assert klac["depth"] == "deep" and klac["score"] is not None
    assert "cyclical turn" in klac["buckets"]


def test_rebuild_is_byte_identical():
    """--check pins the timestamp so a rebuild can be diffed. If this drifts, the
    build has become nondeterministic and the git history stops being meaningful."""
    p = ROOT / "dashboard" / "analysis" / "KLAC.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_analysis.py"), "--check"],
                   capture_output=True, text=True, cwd=str(ROOT), check=True)
    first = p.read_bytes()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_analysis.py"), "--check"],
                   capture_output=True, text=True, cwd=str(ROOT), check=True)
    assert p.read_bytes() == first
