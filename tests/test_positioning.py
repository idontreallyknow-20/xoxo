import json
import subprocess
import sys
from pathlib import Path

import pytest

from an import positioning as P
from an import score

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def memo():
    return P.build_memo(built_at="X")


def test_two_lists_not_one(memo):
    """Merging them would put a name nobody has read at the top of a list about
    deploying capital."""
    assert memo["candidates"] and memo["research_queue"]
    acted = {c["ticker"] for c in memo["candidates"]}
    queued = {c["ticker"] for c in memo["research_queue"]}
    assert not (acted & queued)


def test_everything_actionable_has_a_research_note(memo):
    from an import research_md

    notes = set(research_md.load_all())
    for c in memo["candidates"]:
        assert c["ticker"] in notes, c["ticker"]
        assert c["depth"] == "deep"


def test_everything_actionable_has_a_falsifier(memo):
    """criteria.md: every recommendation carries the price or event that would prove
    it wrong. A candidate without one is not a candidate."""
    for c in memo["candidates"]:
        assert c["what_would_be_wrong"], c["ticker"]
        assert all(len(w["text"]) > 15 for w in c["what_would_be_wrong"]), c["ticker"]


def test_every_falsifier_says_where_it_came_from(memo):
    """No merging, because merging means guessing at semantic equivalence. An earlier
    version compared shared words against the shorter phrase, which let two common
    words delete a longer and more specific condition; dividing by the union instead
    kept obvious restatements. A falsifier is the last thing to quietly drop."""
    sources = set()
    for c in memo["candidates"]:
        for w in c["what_would_be_wrong"]:
            assert w["source"], c["ticker"]
            sources.add(w["source"])
    assert "as logged in journal.md" in sources
    assert "thesis killers, from the research note" in sources
    assert "price trigger, from the research note" in sources


def test_a_specific_threshold_is_never_deleted_as_a_restatement(memo):
    """"under 8%" and "under 6%" are different conditions. The first dedup stripped
    numeric tokens under three characters and could not tell them apart."""
    idxx = next(c for c in memo["candidates"] if c["ticker"] == "IDXX")
    texts = " ".join(w["text"] for w in idxx["what_would_be_wrong"])
    assert "6%" in texts
    assert "$410" in texts
    assert "US clinic visits" in texts, "the note's more specific condition must survive"


def test_falsifiers_are_whole_sentences_not_fragments(memo):
    for c in memo["candidates"]:
        for w in c["what_would_be_wrong"]:
            assert not w["text"].lower().startswith(("or ", "and ")), f"{c['ticker']}: {w!r}"


def test_research_queue_is_labelled_as_not_for_capital(memo):
    note = memo["research_queue_note"]
    assert "not candidates for capital" in note
    assert "next deep dive" in note
    for c in memo["research_queue"]:
        assert "no filing read" in c["confidence"] or "screen output only" in c["confidence"]


def test_the_scores_top_name_being_unread_is_called_out(memo):
    """NVDA ranks first and nobody has read it. That is a gap in the process, and
    the memo has to say so rather than let the ranking imply otherwise."""
    top_queue = memo["research_queue"][0]["ticker"]
    assert f"{top_queue}, is one of them" in memo["research_queue_note"]


def test_sizes_come_from_the_rules_not_from_conviction(memo):
    rules = memo["rules"]
    total = memo["portfolio"]["total_usd"]
    ceiling = total * rules["max_position_pct"]
    for c in memo["candidates"]:
        band = c["suggested_band_usd"]
        assert band is not None, c["ticker"]
        lo, hi = band
        assert 0 < lo <= hi <= ceiling + 1, f"{c['ticker']} band {band} vs ceiling {ceiling}"


def test_cyclical_names_are_sized_smaller(memo):
    """criteria.md caps the cyclical bucket at 30% of deployed capital and says to
    size it smaller because the false positive rate is higher."""
    cyc = [c for c in memo["candidates"] if "cyclical turn" in c["buckets"]]
    plain = [c for c in memo["candidates"] if not c["buckets"] or c["buckets"] == ["compounder"]]
    assert cyc and plain
    assert max(c["suggested_band_usd"][1] for c in cyc) < max(c["suggested_band_usd"][1] for c in plain)
    for c in cyc:
        assert any("cyclical" in n for n in c["constraint_notes"]), c["ticker"]


def test_thin_evidence_shrinks_the_size(memo):
    thin = [c for c in memo["candidates"] if c["thinly_evidenced"]]
    for c in thin:
        assert any("thin evidence" in n for n in c["constraint_notes"])


def test_every_rule_in_criteria_is_checked(memo):
    rules = " ".join(c["rule"] for c in memo["constraints"])
    assert "8 to 12 names" in rules
    assert "cash" in rules
    assert "sector" in rules
    assert "position above" in rules
    assert "cyclical" in rules
    for c in memo["constraints"]:
        assert c["status"] in ("ok", "not met")
        assert c["detail"]
        assert c["source"] == "criteria.md, Step 5"


def test_the_unmet_rules_are_reported_not_hidden(memo):
    """With nothing bought, the name count and the cash band are both breached.
    A memo that hid that would be useless."""
    unmet = [c["rule"] for c in memo["constraints"] if c["status"] == "not met"]
    assert "8 to 12 names" in " ".join(unmet)
    assert "cash" in " ".join(unmet)


def test_the_score_status_leads_and_says_it_is_unvalidated(memo):
    st = memo["status_of_the_score"]
    assert st["validated"] is False
    assert "never been tested" in st["statement"]
    assert "one dated cross section" in st["statement"]


def test_how_to_read_this_says_it_is_not_a_signal(memo):
    joined = " ".join(memo["how_to_read_this"])
    assert "memo, not a signal" in joined
    assert "not that it will go up" in joined


def test_holdings_source_is_stated(memo):
    p = memo["portfolio"]
    assert p["source"]
    assert p["note"]
    if not p["holdings"]:
        assert "stated intent" in p["note"]


def test_logged_calls_are_carried_from_the_journal(memo):
    calls = memo["portfolio"]["logged_calls"]
    assert len(calls) == 16, "one entry per ticker, including the five-name heading"
    buys = [c for c in calls if (c["action"] or "").lower().startswith("buy")]
    assert len(buys) == 11
    assert all(c["wrong_if"] for c in buys), "a buy without a falsifier is not a call"


def test_variant_agreement_is_reported(memo):
    va = memo["variant_agreement"]
    assert va["overlap"]
    assert "opposite views on purpose" in va["note"]
    assert all(0 <= v <= va["top_n"] for v in va["overlap"].values())


def test_deterministic():
    a = json.dumps(P.build_memo(built_at="X"), sort_keys=True)
    b = json.dumps(P.build_memo(built_at="X"), sort_keys=True)
    assert a == b


def test_cli_writes_both_files():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_positioning.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    card = json.loads((ROOT / "dashboard" / "scorecard.json").read_text())
    assert card["n_names"] == 150
    assert set(card["variants"]) == set(score.VARIANTS)
    assert card["weights"]["quality_value"]
    memo = json.loads((ROOT / "dashboard" / "positioning.json").read_text())
    assert memo["candidates"]


def test_scorecard_rows_carry_their_components():
    card = json.loads((ROOT / "dashboard" / "scorecard.json").read_text())
    rows = card["variants"]["quality_value"]["rows"]
    assert len(rows) == 150
    for r in rows[:20]:
        assert r["contributions"]
        assert 0 <= r["coverage"] <= 1
        assert r["percentile"] is not None


def test_names_the_analyst_passed_on_are_not_ranked_as_buy_candidates(memo):
    """The memo used to size every researched name in dollars without ever reading
    the verdict at the bottom of its note. Four of the sixteen say Pass or Watch."""
    from an import research_md

    notes = research_md.load_all()
    for c in memo["candidates"]:
        verdict = (notes[c["ticker"]].verdict_action or "").lower()
        assert verdict.startswith("buy"), f"{c['ticker']} is in the buy list but the note says {verdict!r}"
        assert c["stance"] == "buy"


def test_the_declined_names_are_shown_with_their_verdict_and_no_size(memo):
    declined = memo["reviewed_and_declined"]
    assert {c["ticker"] for c in declined} == {"ACN", "AMAT", "META", "NVR"}
    for c in declined:
        assert c["stance"] in ("passed", "watching")
        assert c["verdict_text"]
        assert "Not sized" in c["constraint_notes"][0]
        assert c["note_verdict_full"]


def test_a_high_scoring_declined_name_is_surfaced_not_buried(memo):
    """Applied Materials ranks in the top third and the note says "Pass for now".
    That disagreement is the most informative thing on the page."""
    amat = next(c for c in memo["reviewed_and_declined"] if c["ticker"] == "AMAT")
    assert amat["percentile"] > 60
    assert amat["verdict_text"].lower().startswith("pass")
    assert "disagreement is the point" in memo["reviewed_and_declined_note"]


def test_no_name_appears_in_two_lists(memo):
    lists = [{c["ticker"] for c in memo[k]}
             for k in ("candidates", "reviewed_and_declined", "research_queue")]
    for i, a in enumerate(lists):
        for b in lists[i + 1:]:
            assert not (a & b)


def test_every_researched_name_lands_in_exactly_one_list(memo):
    from an import research_md

    placed = {c["ticker"] for c in memo["candidates"]} | {c["ticker"] for c in memo["reviewed_and_declined"]}
    assert placed == set(research_md.load_all())


def test_how_to_read_this_explains_the_split(memo):
    joined = " ".join(memo["how_to_read_this"])
    assert "concluded to buy" in joined
    assert "read and declined" in joined
