"""an.outcomes: the tracker's grades read back, and the refusal to read a thin sample.

The planted samples are built so the answer is known: a score that lines up with
the outcome, one that does not, and every way a sample can fall short.
"""
import json
import random
from pathlib import Path

import pytest

from an import outcomes as O

ROOT = Path(__file__).resolve().parent.parent


def grade(i, *, date="2026-09-04", score=None, excess=None, days=90, status="graded"):
    return {"date": date, "ticker": f"T{i:02d}", "status": status, "score_percentile_at_call": score,
            "excess_vs_spy": excess, "trading_days": days}


def sample(n=24, dates=6, days=90, *, aligned=True, seed=3):
    """n graded calls over `dates` distinct days whose excess return follows (or ignores) the score."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        s = 100.0 * (i + 0.5) / n
        noise = rng.gauss(0, 0.02)
        e = (0.002 * (s - 50) + noise) if aligned else noise
        out.append(grade(i, date=f"2026-0{1 + i % dates}-15", score=s, excess=e, days=days))
    return out


def test_zero_grades_is_insufficient_with_every_shortfall_named():
    r = O.readout([grade(0, status="ungraded")], is_real=False)
    assert r.status == "INSUFFICIENT" and r.rank_ic is None and r.rank_ic_n is None
    text = " ".join(r.shortfalls)
    assert "not really pulled" in text and "20 are required" in text and "4 are required" in text and "63 are required" in text
    assert "nothing is said" in r.verdict
    j = r.to_json()
    assert j["rank_ic"] is None and j["sample"]["n_calls"] == 1 and j["sample"]["n_graded"] == 0
    assert j["requirements"] == O.REQUIREMENTS


def test_synthetic_prices_can_never_be_evidence():
    r = O.readout(sample(), is_real=False)
    assert r.status == "INSUFFICIENT" and r.rank_ic is None
    assert r.shortfalls == [r.shortfalls[0]] and "not really pulled" in r.shortfalls[0]


def test_nineteen_calls_refuse_and_twenty_read():
    assert O.readout(sample(19), is_real=True).status == "INSUFFICIENT"
    r = O.readout(sample(20), is_real=True)
    assert r.status == "READ" and r.rank_ic_n == 20


def test_one_date_refuses_even_with_many_calls():
    r = O.readout(sample(30, dates=1), is_real=True)
    assert r.status == "INSUFFICIENT" and r.rank_ic is None
    assert any("1 distinct date" in s for s in r.shortfalls)


def test_a_short_window_refuses():
    r = O.readout(sample(30, days=40), is_real=True)
    assert r.status == "INSUFFICIENT" and any("40 trading days" in s for s in r.shortfalls)


def test_an_aligned_sample_reads_suggestive_and_never_stronger():
    r = O.readout(sample(30), is_real=True)
    assert r.status == "READ" and r.rank_ic is not None and r.rank_ic > 0.5
    assert r.verdict.startswith("suggestive so far")
    assert r.liked_mean_excess > r.disliked_mean_excess and r.liked_n + r.disliked_n == 30
    assert "supported" not in r.verdict and "Capped at 'suggestive'" in r.note


def test_a_noise_sample_reads_no_relationship():
    r = O.readout(sample(60, aligned=False, seed=11), is_real=True)
    assert r.status == "READ"
    assert abs(r.rank_ic) < 0.25
    assert r.verdict in ("no relationship visible so far",
                         "suggestive so far: higher-scored names did better against SPY in this sample",
                         "suggestive so far, the wrong way: higher-scored names did worse against SPY in this sample")
    # Whatever the noise says, the note carries the cap.
    assert "not a universe" in r.note


def test_the_wrong_way_is_said_plainly():
    s = sample(30)
    for g in s:
        g["excess_vs_spy"] = -g["excess_vs_spy"]
    r = O.readout(s, is_real=True)
    assert "the wrong way" in r.verdict and r.rank_ic < 0


def test_grades_missing_a_score_do_not_count_towards_the_floor():
    s = sample(30)
    for g in s[:15]:
        g["score_percentile_at_call"] = None
    r = O.readout(s, is_real=True)
    assert r.n_graded == 30 and r.n_usable == 15 and r.status == "INSUFFICIENT"


def test_the_committed_tracker_json_refuses():
    blob = json.loads((ROOT / "dashboard" / "tracker.json").read_text())
    r = O.readout_from_json(blob)
    assert r.status == "INSUFFICIENT" and r.rank_ic is None
    assert blob["score_vs_outcome"]["status"] == "INSUFFICIENT"
    assert blob["score_vs_outcome"]["rank_ic"] is None


def test_language_guard_over_every_verdict():
    import re

    from tests.test_language_guard import FORBIDDEN, walk_strings

    for s, real in ((sample(30), True), (sample(30, aligned=False), True), (sample(5), False)):
        for path, text in walk_strings(O.readout(s, is_real=real).to_json()):
            for pat in FORBIDDEN:
                assert not re.search(pat, text, re.I), (path, text)
