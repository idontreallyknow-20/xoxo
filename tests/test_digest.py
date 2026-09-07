"""an.digest: the daily email as data, then as HTML, on a scanned fixture and on the NOT RUN file.

The digest may report state and alerts and never a trade. The language guard runs
over every string it produces, the subject line says whether a rule fired, and
the only-if-alerts switch is a function of the rule count and nothing else.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from an import digest, mail

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_language_guard import check  # noqa: E402

TODAY = __import__("datetime").date(2026, 9, 7)


def scanned_watch():
    """A scanned file with one breached name, one quiet name and a filing."""
    return {
        "status": "SCANNED", "is_real": True, "as_of": "2026-09-04", "n_watched": 2,
        "watched": [{"ticker": "KLAC"}, {"ticker": "MSFT"}],
        "sources": {"prices": {"live": True, "detail": "18 live"}, "edgar": {"live": True, "detail": "1 new"},
                    "rss": {"live": False, "detail": "not requested"}},
        "names": [
            {"ticker": "KLAC", "action": "Buy in October", "held": False, "trigger": 130.0, "max_severity": 3,
             "price": {"last_close": 125.0, "last_date": "2026-09-04", "chg_1d": -0.08, "chg_5d": -0.17,
                       "since_call": -0.277, "to_trigger": -0.038, "breached": True, "stale_days": 3},
             "filings": [{"form": "8-K", "filing_date": "2026-09-03", "item_text": "earnings release, exhibits",
                          "url": "https://www.sec.gov/x", "is_earnings": True}],
             "headlines": [{"title": "KLA reports", "link": "https://example.com/a", "published": "2026-09-03T20:00:00+00:00",
                            "source": "Yahoo Finance RSS"}]},
            {"ticker": "MSFT", "action": "Buy in October", "held": True, "trigger": 400.0, "max_severity": 0,
             "price": {"last_close": 510.0, "last_date": "2026-09-04", "chg_1d": 0.002, "chg_5d": 0.01,
                       "since_call": 0.0, "to_trigger": 0.275, "breached": False, "stale_days": 3},
             "filings": [], "headlines": []},
        ],
        "alerts": [
            {"ticker": "KLAC", "kind": "trigger", "severity": 3, "text": "KLAC closed at $125.00 on 2026-09-04, under the $130.00 level the journal named as the falsifier. On the journal's own terms the call is falsified so far.",
             "rule": "journal.md, Wrong if", "value": -0.038, "as_of": "2026-09-04", "source": "prices"},
            {"ticker": "KLAC", "kind": "drop_week", "severity": 3, "text": "KLAC is -17.0% over the last five sessions.",
             "rule": "README event driven", "value": -0.17, "as_of": "2026-09-04", "source": "prices"},
            {"ticker": "KLAC", "kind": "move_day", "severity": 2, "text": "KLAC moved -8.0% on 2026-09-04.",
             "rule": "5% session", "value": -0.08, "as_of": "2026-09-04", "source": "prices"},
        ],
        "benchmarks": {"SPY": {"chg_1d": -0.01, "chg_5d": -0.02, "last_close": 500.0, "last_date": "2026-09-04"}},
        "rules": ["a rule"], "limitations": ["a limitation"],
    }


def inputs(watch=None):
    return {"watch": watch, "tracker": json.loads((ROOT / "dashboard" / "tracker.json").read_text()),
            "memo": json.loads((ROOT / "dashboard" / "positioning.json").read_text())}


def test_a_scanned_digest_leads_with_the_rules_that_fired():
    d = digest.build_digest(inputs(scanned_watch()), today=TODAY)
    assert d["status"] == "SCANNED" and d["is_real"]
    assert d["n_rule_alerts"] == 2 and d["n_alerts"] == 3
    assert d["lead"].startswith("2 of your written rules fired across 1 name.")
    assert [r["ticker"] for r in d["rows"]] == ["KLAC", "MSFT"], "the breached name sorts first, then held"
    assert d["filings"][0]["form"] == "8-K" and d["headlines"][0]["title"] == "KLA reports"
    assert d["benchmarks"]["SPY"]["chg_1d"] == -0.01
    assert "none graded yet" in d["tracker_line"]
    assert "Nothing in this email is a trade recommendation" in d["memo_line"]
    assert digest.subject_for(d) == "Desk Mon 7 Sep: 2 rules fired (KLAC)"


def test_the_not_run_file_produces_an_honest_digest():
    d = digest.build_digest(digest.load_inputs(ROOT / "dashboard"), today=TODAY)
    assert d["status"] == "NOT RUN" and not d["is_real"]
    assert d["rows"] == [] and d["alerts"] == []
    assert len(d["watched"]) == 16
    assert "No scan has run yet" in d["lead"]
    assert digest.subject_for(d) == "Desk Mon 7 Sep: no scan has run"


def test_no_scan_file_at_all_says_so(tmp_path):
    d = digest.build_digest(digest.load_inputs(tmp_path), today=TODAY)
    assert d["status"] == "NO SCAN FILE" and d["tracker_line"] is None and d["memo_line"] is None


def test_a_quiet_scan_reads_as_quiet():
    w = scanned_watch()
    w["alerts"] = []
    w["names"][0]["max_severity"] = 0
    d = digest.build_digest(inputs(w), today=TODAY)
    assert "quiet day" in d["lead"]
    assert digest.subject_for(d) == "Desk Mon 7 Sep: nothing crossed a rule"


def test_only_if_alerts_is_a_function_of_the_rule_count_only():
    fired = digest.build_digest(inputs(scanned_watch()), today=TODAY)
    quiet = digest.build_digest(digest.load_inputs(ROOT / "dashboard"), today=TODAY)
    assert digest.should_send(fired, only_if_alerts=True)
    assert not digest.should_send(quiet, only_if_alerts=True)
    assert digest.should_send(quiet, only_if_alerts=False)
    w = scanned_watch()
    w["alerts"] = [a for a in w["alerts"] if a["severity"] < 3]
    assert not digest.should_send(digest.build_digest(inputs(w), today=TODAY), only_if_alerts=True)


def test_the_html_is_self_contained_and_carries_every_section():
    d = digest.build_digest(inputs(scanned_watch()), today=TODAY)
    html = digest.render_html(d)
    for needle in ("What crossed a rule", "Every watched name", "New filings", "New headlines", "Standing state",
                   "Where this came from", "not personalised financial advice", "$125.00", "-17.0%", "+27.5%",
                   "held", "KLA reports", "https://www.sec.gov/x"):
        assert needle in html, needle
    assert "<script" not in html and "http" not in html.split("<body")[0].replace("http-equiv", "")
    assert 'href="https://example.com/a"' in html
    text = mail.text_from_html(html)
    assert "KLAC" in text and "$125.00" in text and "<" not in text


def test_a_missing_value_renders_as_na_never_as_zero():
    w = scanned_watch()
    w["names"][1]["price"] = {k: None for k in w["names"][1]["price"]}
    w["names"][1]["trigger"] = None
    d = digest.build_digest(inputs(w), today=TODAY)
    row = [r for r in d["rows"] if r["ticker"] == "MSFT"][0]
    assert row["close"] is None and row["chg_1d"] is None
    html = digest.render_html(d)
    assert "none set" in html
    assert "$0.00" not in html and "+0.0%" not in html.split("MSFT")[1].split("</tr>")[0]


def test_the_language_guard_passes_over_the_whole_digest():
    for w in (scanned_watch(), json.loads((ROOT / "dashboard" / "watch.json").read_text())):
        d = digest.build_digest(inputs(w), today=TODAY)
        bad = check(d, "digest")
        assert not bad, "\n".join(bad)


def test_the_cli_preview_writes_only_under_the_ignored_prefix(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_email.py"), "--dry-run", "--date", "2026-09-07"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120,
                       env={**__import__("os").environ, "DESK_OFFLINE": "1", mail.ENV_USER: "", mail.ENV_PASSWORD: ""})
    assert r.returncode == 0, r.stderr[-1500:]
    assert "nothing was sent" in r.stdout and "no, never" in r.stdout or "yes," in r.stdout
    assert "DESK_MAIL_USER" in r.stdout
    assert "abcd" not in r.stdout


def test_send_without_a_credential_fails_loudly_and_names_the_variable():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_email.py"), "--send", "--date", "2026-09-07"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120,
                       env={**__import__("os").environ, "DESK_OFFLINE": "1", mail.ENV_USER: "", mail.ENV_PASSWORD: ""})
    assert r.returncode == 2
    assert mail.ENV_USER in r.stderr
