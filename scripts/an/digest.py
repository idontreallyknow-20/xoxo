"""The daily email: the state of the names Joseph has committed to, and what moved.

One pure function builds the digest from the files the pipeline already writes
(``watch.json``, ``tracker.json``, ``positioning.json``), and one renders it as
an HTML email in the idiom of ``scripts/email_picks.py``: table layout, inline
styles, a light palette, no external asset, so it reads the same in Gmail, Apple
Mail and a text client.

What an email is allowed to say, decided in NOTES.md section 9a: the state of
every watched name, every alert that crossed a rule Joseph wrote, the new
filings and headlines, the tracker's grade count. Never a trade recommendation.
The README's cadence rule ("no trade recommendations unless something material
happened") is kept by construction: the email reports whether something
material happened, in the README's own terms, and stops.

When nothing has been scanned the email says so in its first line rather than
rendering an empty table as if the market had been quiet.

Three editions, one renderer. **morning** (07:00) is the full readout of
yesterday's closes plus what reports today and the mechanical setups from
``setups.json``. **midday** (12:00) is the intraday prints and nothing else.
**event** is sent by the half-hourly watch only when a written rule fired and
the alert has not been emailed yet; ``should_send`` returns False otherwise.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

__all__ = ["load_inputs", "build_digest", "render_html", "subject_for", "should_send", "MAX_HEADLINES", "EDITIONS"]

EDITIONS = ("morning", "midday", "event", "close")

MAX_HEADLINES = 4
MAX_HEADLINES_TOTAL = 24
DISCLAIMER = "Research and analysis from public data, not personalised financial advice."


def _read(p: Path) -> Optional[Dict[str, Any]]:
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _read_data_js(p: Path) -> Optional[Dict[str, Any]]:
    """``window.DASH = {...};`` written by the frozen build_dashboard.py. Read for its earnings calendar."""
    if not p.exists():
        return None
    try:
        src = p.read_text(encoding="utf-8")
        blob = json.loads(src[src.index("{"):].rstrip().rstrip(";"))
        return blob if isinstance(blob, dict) else None
    except (ValueError, OSError):
        return None


def load_inputs(root: Optional[Path] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    d = (root or paths.DASHBOARD_DIR)
    return {"watch": _read(d / "watch.json"), "tracker": _read(d / "tracker.json"),
            "memo": _read(d / "positioning.json"), "intraday": _read(d / "watch_intraday.json"),
            "setups": _read(d / "setups.json"), "index": _read(d / "analysis" / "index.json"),
            "dash": _read_data_js(d / "data.js"), "book": _read(d / "book.json"),
            "book_tracker": _read(d / "book_tracker.json"),
            # the last suite run belongs to the checkout, not to an arbitrary inputs directory
            "tests": _read(paths.CACHE_DIR / "pytest_last.json") if root is None else None}


def _pct(v: Optional[float], sign: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def _money(v: Optional[float]) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def _day(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        return dt.date.fromisoformat(iso[:10]).strftime("%a %d %b").replace(" 0", " ")
    except ValueError:
        return iso[:10]


def build_digest(inputs: Dict[str, Optional[Dict[str, Any]]], *, today: Optional[dt.date] = None,
                 edition: str = "morning", seen_alert_keys: Optional[set] = None) -> Dict[str, Any]:
    """Everything the email says, as data. ``render_html`` only lays it out.

    ``edition`` picks the source file and the sections. ``seen_alert_keys`` (the
    event edition) drops alerts already emailed, keyed by :func:`an.watch.alert_key`.
    """
    if edition not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}, not {edition!r}")
    today = today or dt.date.today()
    if edition in ("midday", "event"):
        w = inputs.get("intraday") or {}
    elif edition == "close":
        # the close marks the day's closes: the intraday file only if it actually scanned, else the daily scan
        intra = inputs.get("intraday") or {}
        w = intra if intra.get("status") == "SCANNED" else (inputs.get("watch") or intra or {})
    else:
        w = inputs.get("watch") or {}
    t = inputs.get("tracker") or {} if edition == "morning" else {}
    m = inputs.get("memo") or {} if edition in ("morning", "close") else {}
    scanned = bool(w) and w.get("status") == "SCANNED"
    synthetic = bool(w) and w.get("status") == "SYNTHETIC"
    intraday = w.get("mode") == "intraday"

    alerts = list(w.get("alerts") or [])
    if seen_alert_keys is not None:
        from .watch import alert_key

        alerts = [a for a in alerts if alert_key(a) not in seen_alert_keys]
    alert_names = {a["ticker"] for a in alerts}
    rule_alerts = [a for a in alerts if a.get("severity", 0) >= 3]
    rows: List[Dict[str, Any]] = []
    filings: List[Dict[str, Any]] = []
    headlines: List[Dict[str, Any]] = []
    for r in w.get("names") or []:
        if edition == "event" and r["ticker"] not in alert_names:
            continue
        p = r.get("price") or {}
        rows.append({
            "ticker": r["ticker"], "held": bool(r.get("held")), "action": r.get("action"),
            "close": p.get("last_close"), "close_date": p.get("last_date"),
            "chg_1d": p.get("chg_1d"), "chg_5d": p.get("chg_5d"), "since_call": p.get("since_call"),
            "trigger": r.get("trigger"), "to_trigger": p.get("to_trigger"), "breached": p.get("breached"),
            "max_severity": max([a.get("severity", 0) for a in alerts if a["ticker"] == r["ticker"]], default=0),
            "stale": (p.get("stale_days") or 0) > 5,
            "intraday": bool(p.get("intraday")), "at": p.get("at"),
        })
        if edition != "morning":
            continue
        for f in r.get("filings") or []:
            what = f.get("item_text") or f.get("description") or ""
            if what.strip().upper() == str(f["form"]).upper():
                what = ""
            filings.append({"ticker": r["ticker"], "form": f["form"], "date": f["filing_date"],
                            "what": what, "url": f.get("url")})
        for h in (r.get("headlines") or [])[:MAX_HEADLINES]:
            headlines.append({"ticker": r["ticker"], "title": h["title"], "link": h.get("link"),
                              "published": h.get("published"), "source": h.get("source")})
    rows.sort(key=lambda r: (-r["max_severity"], not r["held"], r["ticker"]))
    filings.sort(key=lambda f: f["date"], reverse=True)
    headlines = headlines[:MAX_HEADLINES_TOTAL]

    bench = {}
    for k, v in (w.get("benchmarks") or {}).items():
        bench[k] = {"chg_1d": v.get("chg_1d"), "chg_5d": v.get("chg_5d"), "close": v.get("last_close"),
                    "date": v.get("last_date")}

    ts = t.get("summary") or {}
    tracker_line = None
    if t:
        if t.get("status") == "GRADED":
            tracker_line = (f"{ts.get('n_graded', 0)} of {ts.get('n_calls', 0)} journal calls graded against prices, "
                            f"{ts.get('n_falsified', 0)} falsified on their own terms so far, "
                            f"{ts.get('buys_ahead_of_spy', 0)} of {ts.get('buys_graded', 0)} buys ahead of SPY so far.")
        else:
            tracker_line = (f"Journal calls: {ts.get('n_calls', 0)}, none graded yet (tracker status "
                            f"{t.get('status', 'unknown')}). python scripts/track_calls.py --live grades them.")

    port = (m.get("portfolio") or {})
    memo_line = None
    if m:
        memo_line = (f"Positioning memo as of {m.get('snapshot_date', 'n/a')}: {port.get('n_positions', 0)} positions, "
                     f"${port.get('cash_usd') or 0:,.0f} cash. The ranked memo is on /positioning/. "
                     f"Nothing in this email is a trade recommendation.")

    # what reports today (morning only): the watched names' next earnings dates from the analysis index
    reports_today: List[Dict[str, Any]] = []
    if edition == "morning":
        watched = {x["ticker"] for x in (w.get("watched") or [])}
        soon = {(today + dt.timedelta(days=i)).isoformat() for i in range(0, 8)}
        for row in ((inputs.get("index") or {}).get("tickers") or []):
            ne = row.get("next_earnings")
            if ne in soon and (row["ticker"] in watched or not watched):
                reports_today.append({"ticker": row["ticker"], "company": row.get("company"), "date": ne,
                                      "watched": row["ticker"] in watched})
        for e in ((inputs.get("dash") or {}).get("earnings") or []):
            if e.get("date") in soon and not any(r["ticker"] == e["ticker"] for r in reports_today):
                reports_today.append({"ticker": e["ticker"], "company": e.get("name"), "date": e["date"],
                                      "watched": e["ticker"] in watched})
        reports_today.sort(key=lambda r: (r["date"], not r["watched"], r["ticker"]))
        reports_today = [r for r in reports_today if r["watched"]] + [r for r in reports_today if not r["watched"]][:8]

    # the mechanical setups (morning only)
    st = (inputs.get("setups") or {}) if edition == "morning" else {}
    setups = list(st.get("setups") or []) if st.get("status") in ("SCANNED", "SYNTHETIC") else []
    setups_line = None
    if edition == "morning":
        if not st:
            setups_line = "No setups file. python scripts/setups.py writes one."
        elif st.get("status") == "SCANNED":
            setups_line = (f"{len(setups)} mechanical setup{'s' if len(setups) != 1 else ''} as of {st.get('as_of')}, "
                           f"by the rules in swing.md. A setup is a pattern that matched, not a forecast.")
        elif st.get("status") == "SYNTHETIC":
            setups_line = "SYNTHETIC setups from a fixture. None describes a company."
        else:
            setups_line = f"Setups: {st.get('status', 'NOT RUN')}. python scripts/setups.py --live scans for them."

    # Claude's book (morning and close): marks, the day's calls, the tracker's count
    book = _book_block(inputs.get("book"), inputs.get("book_tracker"), today) if edition in ("morning", "close") else None
    # the memo sized for $100,000 (morning only)
    memo = _memo_block(m) if edition == "morning" else None
    tests = _tests_block(inputs.get("tests"))

    where = "at " + (w.get("at") or "")[11:16] if intraday and w.get("at") else "as of the close"
    if not w:
        status = "NO SCAN FILE"
        lead = ("No watch_intraday.json exists. Run python scripts/scan.py --intraday first." if edition != "morning"
                else "No watch.json exists. Run python scripts/scan.py first.")
    elif scanned:
        if rule_alerts:
            lead = (f"{len(rule_alerts)} of your written rules fired across {len({a['ticker'] for a in rule_alerts})} "
                    f"name{'s' if len({a['ticker'] for a in rule_alerts}) != 1 else ''} {where}. Details below.")
        elif alerts:
            lead = f"Nothing crossed a written rule {where}. {len(alerts)} line{'s' if len(alerts) != 1 else ''} worth a glance."
        elif edition == "event":
            lead = "Nothing new crossed a rule."
        elif intraday:
            lead = f"Nothing crossed a written rule {where} and nothing is moving five percent."
        else:
            lead = "Nothing crossed a written rule and nothing moved five percent. A quiet day, as far as these sources see."
        status = "SCANNED"
    elif synthetic:
        status = "SYNTHETIC"
        lead = "SYNTHETIC. This digest was built from a fixture and random prices. It describes no company."
    else:
        status = w.get("status", "NOT RUN")
        lead = ("No scan has run yet, so there is nothing to report. The names below are what the scan watches. "
                "python scripts/scan.py --live, on a machine that can reach Yahoo and the SEC, changes that.")

    return {
        "edition": edition,
        "intraday": intraday,
        "at": w.get("at"),
        "date": today.isoformat(),
        "date_text": today.strftime("%A %d %B %Y").replace(" 0", " "),
        "status": status,
        "is_real": scanned,
        "lead": lead,
        "as_of": w.get("as_of"),
        "n_watched": w.get("n_watched", len(w.get("watched") or [])),
        "n_alerts": len(alerts),
        "n_rule_alerts": len(rule_alerts),
        "alerts": alerts,
        "rows": rows,
        "watched": [x["ticker"] for x in (w.get("watched") or [])],
        "filings": filings,
        "headlines": headlines,
        "benchmarks": bench,
        "tracker_line": tracker_line,
        "memo_line": memo_line,
        "reports_today": reports_today,
        "setups": setups,
        "setups_line": setups_line,
        "setups_status": st.get("status") if st else None,
        "book": book,
        "memo": memo,
        "tests": tests,
        "sources": w.get("sources") or {},
        "limitations": list(w.get("limitations") or []),
        "rules": list(w.get("rules") or []),
        "disclaimer": DISCLAIMER,
    }


def _book_block(b: Optional[Dict[str, Any]], bt: Optional[Dict[str, Any]], today: dt.date) -> Dict[str, Any]:
    """Claude's own paper book, as data. Says NOT MARKED plainly when there are no quotes."""
    if not b:
        return {"status": "NO BOOK FILE", "line": "No book.json exists. python scripts/build_book.py writes one.",
                "open": [], "calls_today": [], "flags": []}
    status = b.get("status", "NOT MARKED")
    eq, start = b.get("equity"), b.get("starting_cash") or 100_000.0
    xs = b.get("excess_since_start") or {}
    bench = b.get("benchmarks_since_start") or {}
    age = b.get("quotes_age_sessions")
    if status == "MARKED":
        line = (f"Equity ${eq:,.0f} ({_pct(b.get('ret_since_start'))} since {b.get('first_fill') or 'the start'}), "
                f"{b.get('n_open', 0)} open, ${b.get('cash') or 0:,.0f} cash, marked at the {b.get('as_of')} close"
                + (f" ({age} session{'s' if age != 1 else ''} old)" if age else "") + ". "
                + " ".join(f"{k} {_pct(bench.get(k))} over the same window ({_pct(xs.get(k))} for the book against it)."
                           for k in ("SPY", "QQQ", "VFV.TO") if bench.get(k) is not None))
    else:
        line = (f"{status}: {b.get('n_pending', 0)} call{'s' if b.get('n_pending', 0) != 1 else ''} pending at the first "
                f"close after each call date; no quotes on this machine yet.")
    calls_today = [p for p in (b.get("long") or []) + (b.get("swing") or []) if p.get("call_date") == today.isoformat()]
    recent = sorted((p for p in (b.get("long") or []) + (b.get("swing") or [])
                     if p.get("call_date") and p["call_date"] >= (today - dt.timedelta(days=3)).isoformat()),
                    key=lambda p: p["call_date"], reverse=True)
    graded = None
    if bt and bt.get("summary"):
        s = bt["summary"]
        graded = (f"{s.get('n_graded', 0)} of {s.get('n_calls', 0)} book calls graded against prices so far, "
                  f"{s.get('buys_ahead_of_spy', 0)} of {s.get('buys_graded', 0)} buys ahead of SPY, "
                  f"{s.get('n_falsified', 0)} falsified on their own terms.") if bt.get("status") == "GRADED" else \
                 f"Book calls: {s.get('n_calls', 0)}, none graded yet (tracker status {bt.get('status')})."
    return {"status": status, "line": line, "equity": eq, "cash": b.get("cash"), "ret_since_start": b.get("ret_since_start"),
            "benchmarks": bench, "excess": xs, "as_of": b.get("as_of"), "age_sessions": age,
            "open": b.get("open") or [], "closed_recent": [p for p in (b.get("closed") or []) if p.get("exit_date") and
                                                          p["exit_date"] >= (today - dt.timedelta(days=7)).isoformat()],
            "calls_today": calls_today or recent[:6], "refused": b.get("refused") or [], "flags": b.get("flags") or [],
            "rule_checks": b.get("rule_checks") or [], "tracker_line": graded, "max_drawdown": b.get("max_drawdown")}


def _memo_block(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The positioning memo's candidates, sized for the $100,000 book, each with its falsifier."""
    if not m:
        return None
    rows = []
    for c in m.get("candidates") or []:
        band = c.get("suggested_band_usd") or [None, None]
        wrong = c.get("what_would_be_wrong") or []
        rows.append({"rank": c.get("rank"), "ticker": c.get("ticker"), "company": c.get("company"), "sector": c.get("sector"),
                     "percentile": c.get("percentile"), "price": c.get("price"), "forward_pe": c.get("forward_pe"),
                     "pe_vs_median": c.get("pe_vs_median"), "dd_52w": c.get("dd_52w"), "band_lo": band[0], "band_hi": band[1],
                     "wrong_if": wrong[0].get("text") if wrong else None, "next_earnings": c.get("next_earnings"),
                     "constraint_notes": c.get("constraint_notes") or [], "stance": c.get("stance")})
    return {"snapshot_date": m.get("snapshot_date"), "rows": rows,
            "declined": [c.get("ticker") for c in m.get("reviewed_and_declined") or []],
            "queue": [c.get("ticker") for c in m.get("research_queue") or []],
            "status": (m.get("status_of_the_score") or {}).get("statement"),
            "line": (f"{len(rows)} names with a research note whose verdict starts with buy, ranked by the score as of "
                     f"{m.get('snapshot_date')}, sized by criteria.md for a $100,000 book. The band is arithmetic from the "
                     f"rules, not a view on the outcome, and every row carries what would prove it wrong. The score has never "
                     f"been validated out of sample; the paper replay of the swing rules found no edge. You decide.")}


def _tests_block(t: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not t:
        return None
    failed = list(t.get("failed_tests") or [])
    return {"passed": t.get("passed"), "failed": t.get("failed"), "errors": t.get("errors"), "ran_at": t.get("ran_at"),
            "seconds": t.get("seconds"), "failed_tests": failed[:12],
            "line": (f"{t.get('passed', 0)} tests passed, {t.get('failed', 0)} failed, {t.get('errors', 0)} errors, "
                     f"in {t.get('seconds', 0):.0f}s at {t.get('ran_at', '')}")}


EDITION_NAMES = {"morning": "morning brief", "midday": "midday check", "event": "something crossed a rule",
                 "close": "close"}


def subject_for(d: Dict[str, Any]) -> str:
    day = dt.date.fromisoformat(d["date"]).strftime("%a %d %b").replace(" 0", " ")
    ed = d.get("edition", "morning")
    head = f"Desk {EDITION_NAMES.get(ed, ed).split(' ')[0] if ed != 'event' else 'alert'}, {day}"
    if d.get("tests") and d["tests"].get("failed"):
        head = "TESTS RED, " + head
    if d["status"] == "SCANNED":
        if d["n_rule_alerts"]:
            names = sorted({a["ticker"] for a in d["alerts"] if a.get("severity", 0) >= 3})
            return f"{head}: {d['n_rule_alerts']} rule{'s' if d['n_rule_alerts'] != 1 else ''} fired ({', '.join(names[:4])}{'...' if len(names) > 4 else ''})"
        return f"{head}: nothing crossed a rule"
    if d["status"] == "SYNTHETIC":
        return f"{head}: SYNTHETIC digest, not real"
    return f"{head}: no scan has run"


def should_send(d: Dict[str, Any], *, only_if_alerts: bool) -> bool:
    """The event edition sends only on a rule that fired; the others send on request."""
    if d.get("edition") == "event" or only_if_alerts:
        return d["status"] == "SCANNED" and d["n_rule_alerts"] > 0
    return True


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

BG, INK, INK2, INK3, SOFT, RULE = "#ffffff", "#121212", "#5b5b57", "#8f8f89", "#ecece9", "#d9d9d3"
UP, DOWN, MARK = "#0b7a3b", "#c2361f", "#fff27a"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'Courier New', Courier, monospace"


def _esc(s: Any) -> str:
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _rule() -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:6px;margin-bottom:12px;">'
            f'<tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>')


def _h(title: str, sub: str = "") -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>'
            f'<td style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};">{_esc(title)}</td>'
            f'<td align="right" style="font-family:{MONO};font-size:12px;color:{INK3};">{_esc(sub)}</td></tr></table>' + _rule())


def _signed(v: Optional[float]) -> str:
    """Up and down are business-number colours here: a price move is coloured because the
    email is about moves. The sign is always printed too, so colour is never the only signal."""
    if v is None:
        return f'<span style="color:{INK3}">n/a</span>'
    c = UP if v > 0 else DOWN if v < 0 else INK2
    return f'<span style="color:{c}">{_pct(v)}</span>'


def render_html(d: Dict[str, Any]) -> str:
    parts: List[str] = []
    sev_bg = {3: MARK, 2: SOFT, 1: BG}

    # alerts
    if d["alerts"]:
        items = ""
        for a in d["alerts"]:
            items += (f'<tr><td style="padding:10px 12px;border-bottom:1px solid {SOFT};background:{sev_bg.get(a.get("severity", 1), BG)};">'
                      f'<div style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};">{_esc(a["ticker"])} '
                      f'<span style="font-family:{MONO};font-size:11px;font-weight:400;color:{INK3};">{_esc(a["kind"].replace("_", " "))} · {_esc(a.get("source"))} · {_esc(a.get("as_of"))}</span></div>'
                      f'<div style="font-family:{SANS};font-size:14px;line-height:1.5;color:{INK2};margin-top:3px;">{_esc(a["text"])}</div>'
                      f'<div style="font-family:{MONO};font-size:11px;color:{INK3};margin-top:4px;">rule: {_esc(a.get("rule"))}</div></td></tr>')
        sub = f"{d['n_rule_alerts']} of {d['n_alerts']} are written rules"
        parts.append(f'<tr><td style="padding-top:36px;">{_h("What crossed a rule", sub)}'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{items}</table></td></tr>')

    # the table of names
    if d["rows"]:
        head = "".join(f'<td style="font-family:{MONO};font-size:11px;color:{INK3};padding:6px 6px 6px 0;border-bottom:1px solid {INK};{al}">{h}</td>'
                       for h, al in (("name", ""), ("close", "text-align:right"), ("1d", "text-align:right"), ("5d", "text-align:right"),
                                     ("since call", "text-align:right"), ("falsifier", "text-align:right"), ("room", "text-align:right")))
        body = ""
        for r in d["rows"]:
            room = _signed(r["to_trigger"]) if r["trigger"] else f'<span style="color:{INK3}">none set</span>'
            flag = " ●" if r["max_severity"] >= 3 else ""
            held = f' <span style="font-family:{MONO};font-size:10px;color:{INK3};">held</span>' if r["held"] else ""
            stale = f' <span style="font-family:{MONO};font-size:10px;color:{DOWN};">stale</span>' if r["stale"] else ""
            body += (f'<tr><td style="font-family:{SANS};font-size:14px;color:{INK};padding:8px 6px 8px 0;border-bottom:1px solid {SOFT};">'
                     f'<b style="font-weight:600;">{_esc(r["ticker"])}</b>{flag}{held}{stale}<div style="font-size:11px;color:{INK3};font-family:{MONO};">{_esc(r["action"] or "")}</div></td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;color:{INK};padding:8px 6px;border-bottom:1px solid {SOFT};">{_esc(_money(r["close"]))}<div style="font-size:10px;color:{INK3};">{_esc(_day(r["close_date"]))}</div></td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;padding:8px 6px;border-bottom:1px solid {SOFT};">{_signed(r["chg_1d"])}</td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;padding:8px 6px;border-bottom:1px solid {SOFT};">{_signed(r["chg_5d"])}</td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;padding:8px 6px;border-bottom:1px solid {SOFT};">{_signed(r["since_call"])}</td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;color:{INK2};padding:8px 6px;border-bottom:1px solid {SOFT};">{_esc(_money(r["trigger"]) if r["trigger"] else "none set")}</td>'
                     f'<td align="right" style="font-family:{MONO};font-size:13px;padding:8px 0 8px 6px;border-bottom:1px solid {SOFT};">{room}</td></tr>')
        bench = " &nbsp; ".join(f'{_esc(k)} {_signed(v["chg_1d"])} / {_signed(v["chg_5d"])}' for k, v in d["benchmarks"].items())
        sub = ("last print, on the day, since the call, and room above the falsifier" if d.get("intraday")
               else "close, one day, five days, since the call, and room above the falsifier")
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Every watched name", sub)}'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>{head}</tr>{body}</table>'
                     + (f'<div style="font-family:{MONO};font-size:12px;color:{INK2};margin-top:10px;">benchmarks 1d / 5d: {bench}</div>' if bench else "")
                     + f'<div style="font-family:{SANS};font-size:12px;color:{INK3};margin-top:8px;">● a written rule fired. Room is the distance above the journal\'s close-under level; negative means under it.</div></td></tr>')
    elif d["watched"]:
        sub = f"{len(d['watched'])} names"
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Watched", sub)}'
                     f'<div style="font-family:{MONO};font-size:13px;color:{INK2};line-height:1.7;">{_esc(" ".join(d["watched"]))}</div></td></tr>')

    if d["filings"]:
        rows = "".join(f'<tr><td style="font-family:{MONO};font-size:12px;color:{INK3};padding:6px 10px 6px 0;border-bottom:1px solid {SOFT};white-space:nowrap;">{_esc(f["date"])}</td>'
                       f'<td style="font-family:{SANS};font-size:14px;color:{INK};padding:6px 10px 6px 0;border-bottom:1px solid {SOFT};"><b style="font-weight:600;">{_esc(f["ticker"])}</b> {_esc(f["form"])}'
                       f'<span style="color:{INK2};"> {_esc(f["what"])}</span>'
                       + (f' <a href="{_esc(f["url"])}" style="color:{INK2};font-family:{MONO};font-size:11px;">filing</a>' if f.get("url") else "")
                       + '</td></tr>' for f in d["filings"])
        parts.append(f'<tr><td style="padding-top:36px;">{_h("New filings", "EDGAR, since the last scan")}'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table></td></tr>')

    if d["headlines"]:
        rows = "".join(f'<tr><td style="font-family:{MONO};font-size:12px;color:{INK3};padding:6px 10px 6px 0;border-bottom:1px solid {SOFT};white-space:nowrap;">{_esc(h["ticker"])}</td>'
                       f'<td style="font-family:{SANS};font-size:14px;color:{INK};padding:6px 0;border-bottom:1px solid {SOFT};">'
                       + (f'<a href="{_esc(h["link"])}" style="color:{INK};text-decoration:none;">{_esc(h["title"])}</a>' if h.get("link") else _esc(h["title"]))
                       + f'<div style="font-family:{MONO};font-size:11px;color:{INK3};">{_esc(h.get("source"))} · {_esc(_day(h.get("published")))}</div></td></tr>' for h in d["headlines"])
        parts.append(f'<tr><td style="padding-top:36px;">{_h("New headlines", "listed, not scored")}'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table></td></tr>')

    if d.get("reports_today"):
        rows = "".join(f'<tr><td style="font-family:{MONO};font-size:12px;color:{INK3};padding:6px 10px 6px 0;border-bottom:1px solid {SOFT};white-space:nowrap;">{_esc(_day(r["date"]))}</td>'
                       f'<td style="font-family:{SANS};font-size:14px;color:{INK};padding:6px 0;border-bottom:1px solid {SOFT};"><b style="font-weight:600;">{_esc(r["ticker"])}</b> '
                       f'<span style="color:{INK2};">{_esc(r.get("company") or "")}</span>'
                       + (f' <span style="font-family:{MONO};font-size:10px;color:{INK3};">watched</span>' if r.get("watched") else "")
                       + '</td></tr>' for r in d["reports_today"])
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Reports in the next week", "estimated dates from the screen")}'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table></td></tr>')

    if d.get("setups_line") is not None:
        rows = ""
        for x in d.get("setups") or []:
            nums = " · ".join(f"{k} {v}" for k, v in (x.get("numbers") or {}).items())
            rows += (f'<tr><td style="padding:10px 0;border-bottom:1px solid {SOFT};">'
                     f'<div style="font-family:{SANS};font-size:15px;color:{INK};"><b style="font-weight:600;">{_esc(x["ticker"])}</b> '
                     f'<span style="color:{INK2};">{_esc(x.get("name") or "")}</span> '
                     f'<span style="font-family:{MONO};font-size:11px;color:{INK3};">{_esc(x.get("label") or x.get("kind"))}</span></div>'
                     f'<div style="font-family:{MONO};font-size:12px;color:{INK};margin-top:4px;">entry {_esc(_money(x.get("entry")))} &nbsp; stop {_esc(_money(x.get("stop")))} '
                     f'({_esc(_pct(x.get("risk_pct"), sign=False))} risk) &nbsp; horizon {_esc(x.get("horizon_days"))} sessions</div>'
                     f'<div style="font-family:{MONO};font-size:11px;color:{INK3};margin-top:3px;">{_esc(nums)}</div>'
                     f'<div style="font-family:{SANS};font-size:13px;color:{INK2};margin-top:4px;">{_esc(x.get("rule") or "")}</div></td></tr>')
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Setups", "mechanical, from swing.md")}'
                     f'<div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};margin-bottom:8px;">{_esc(d["setups_line"])}</div>'
                     + (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>' if rows else "")
                     + '</td></tr>')

    bk = d.get("book")
    if bk:
        rows = ""
        for p in bk.get("open") or []:
            rows += (f'<tr><td style="font-family:{SANS};font-size:14px;color:{INK};padding:6px 0;border-bottom:1px solid {SOFT};">'
                     f'<b style="font-weight:600;">{_esc(p["ticker"])}</b> <span style="font-family:{MONO};font-size:11px;color:{INK3};">{_esc(p.get("kind"))}</span></td>'
                     f'<td style="font-family:{MONO};font-size:12px;color:{INK2};padding:6px 0;border-bottom:1px solid {SOFT};">{_esc(p.get("shares"))} @ {_esc(_money(p.get("entry")))}</td>'
                     f'<td style="font-family:{MONO};font-size:12px;color:{INK2};padding:6px 0;border-bottom:1px solid {SOFT};">last {_esc(_money(p.get("last")))}</td>'
                     f'<td style="font-family:{MONO};font-size:12px;padding:6px 0;border-bottom:1px solid {SOFT};">{_signed(p.get("ret"))}</td>'
                     f'<td style="font-family:{MONO};font-size:12px;padding:6px 0;border-bottom:1px solid {SOFT};">vs SPY {_signed(p.get("excess_vs_spy"))}</td>'
                     f'<td style="font-family:{MONO};font-size:11px;color:{INK3};padding:6px 0;border-bottom:1px solid {SOFT};">'
                     + (f'{_esc(_pct(p.get("distance_to_trigger"), sign=False))} above its wrong-if' if p.get("distance_to_trigger") is not None else "")
                     + (f' <span style="background:{MARK};">FALSIFIER BREACHED</span>' if p.get("falsifier_breached") else "")
                     + (f' <span style="background:{MARK};">STOP</span>' if p.get("stop_breached") else "")
                     + '</td></tr>')
        calls = ""
        for p in bk.get("calls_today") or []:
            calls += (f'<tr><td style="padding:10px 0;border-bottom:1px solid {SOFT};">'
                      f'<div style="font-family:{SANS};font-size:15px;color:{INK};"><b style="font-weight:600;">{_esc(p["ticker"])}</b> '
                      f'<span style="font-family:{MONO};font-size:11px;color:{INK3};">{_esc(p.get("call_date"))} · {_esc(p.get("status"))} · target {_esc(_money(p.get("target_usd")))} · conviction {_esc(p.get("conviction"))}</span></div>'
                      f'<div style="font-family:{SANS};font-size:14px;line-height:1.5;color:{INK2};margin-top:4px;">{_esc(p.get("thesis"))}</div>'
                      f'<div style="font-family:{SANS};font-size:13px;line-height:1.5;color:{INK};margin-top:4px;">Wrong if: {_esc(p.get("wrong_if"))}</div>'
                      + (f'<div style="font-family:{MONO};font-size:11px;color:{DOWN};margin-top:3px;">refused: {_esc(p.get("refused_because"))}</div>' if p.get("refused_because") else "")
                      + '</td></tr>')
        extra = "".join(f'<div style="font-family:{MONO};font-size:11px;color:{INK3};margin-top:4px;">{_esc(f)}</div>' for f in (bk.get("flags") or [])[:6])
        tl = f'<div style="font-family:{SANS};font-size:13px;color:{INK2};margin-top:8px;">{_esc(bk["tracker_line"])}</div>' if bk.get("tracker_line") else ""
        parts.append(f'<tr><td style="padding-top:36px;">{_h("My book", "fake money, real closes, my own calls")}'
                     f'<div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};margin-bottom:8px;">{_esc(bk["line"])}</div>'
                     + (f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>' if rows else "")
                     + (f'<div style="font-family:{SANS};font-size:13px;font-weight:600;color:{INK};margin-top:14px;">Calls</div>'
                        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{calls}</table>' if calls else "")
                     + tl + extra + '</td></tr>')

    memo = d.get("memo")
    if memo:
        rows = ""
        for r in memo.get("rows") or []:
            band = (f'{_esc(_money(r.get("band_lo")))} to {_esc(_money(r.get("band_hi")))}' if r.get("band_lo") is not None else "unsized")
            rows += (f'<tr><td style="padding:10px 0;border-bottom:1px solid {SOFT};">'
                     f'<div style="font-family:{SANS};font-size:15px;color:{INK};"><b style="font-weight:600;">{_esc(r["ticker"])}</b> '
                     f'<span style="color:{INK2};">{_esc(r.get("company") or "")}</span> '
                     f'<span style="font-family:{MONO};font-size:11px;color:{INK3};">#{_esc(r.get("rank"))} · p{_esc(None if r.get("percentile") is None else round(r["percentile"]))} · {_esc(r.get("sector"))}</span></div>'
                     f'<div style="font-family:{MONO};font-size:12px;color:{INK};margin-top:4px;">sized at {band} &nbsp; price {_esc(_money(r.get("price")))} &nbsp; '
                     f'forward P/E {_esc(None if r.get("forward_pe") is None else round(r["forward_pe"], 1))} ({_esc(_pct(r.get("pe_vs_median")))} vs its own median) &nbsp; '
                     f'{_esc(_pct(r.get("dd_52w")))} from the 52-week high &nbsp; reports {_esc(r.get("next_earnings") or "n/a")}</div>'
                     f'<div style="font-family:{SANS};font-size:13px;line-height:1.5;color:{INK2};margin-top:4px;">Wrong if: {_esc(r.get("wrong_if") or "not written")}</div>'
                     + "".join(f'<div style="font-family:{MONO};font-size:11px;color:{INK3};margin-top:2px;">{_esc(n)}</div>' for n in (r.get("constraint_notes") or [])[:2])
                     + '</td></tr>')
        tail = (f'<div style="font-family:{MONO};font-size:11px;color:{INK3};margin-top:10px;">read and left out: {_esc(", ".join(memo.get("declined") or []) or "none")} '
                f'&nbsp; not yet read: {_esc(", ".join(memo.get("queue") or []) or "none")}</div>')
        memo_sub = "as of " + str(memo.get("snapshot_date"))
        parts.append(f'<tr><td style="padding-top:36px;">{_h("The memo, sized for $100,000", memo_sub)}'
                     f'<div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};margin-bottom:8px;">{_esc(memo["line"])}</div>'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rows}</table>{tail}</td></tr>')

    standing = "".join(f'<div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};margin-bottom:8px;">{_esc(s)}</div>'
                       for s in (d["tracker_line"], d["memo_line"]) if s)
    if standing:
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Standing state")}{standing}</td></tr>')

    src = "".join(f'<div>{_esc(k)}: {"live" if v.get("live") else "not live"}, {_esc(v.get("detail"))}</div>'
                  for k, v in d["sources"].items())
    lims = "".join(f'<li style="margin-bottom:4px;">{_esc(s)}</li>' for s in d["limitations"])
    tests = d.get("tests")
    tests_html = ""
    if tests:
        colour = DOWN if (tests.get("failed") or tests.get("errors")) else INK2
        tests_html = (f'<div style="font-family:{MONO};font-size:12px;color:{colour};line-height:1.7;">tests: {_esc(tests["line"])}'
                      + "".join(f'<div style="padding-left:12px;">{_esc(x)}</div>' for x in tests.get("failed_tests") or []) + '</div>')
    parts.append(f'<tr><td style="padding-top:36px;">{_h("Where this came from")}'
                 f'<div style="font-family:{MONO};font-size:12px;color:{INK2};line-height:1.7;">{src or "no sources"}</div>{tests_html}'
                 + (f'<ul style="font-family:{SANS};font-size:12px;color:{INK3};line-height:1.5;padding-left:18px;margin:10px 0 0;">{lims}</ul>' if lims else "")
                 + '</td></tr>')

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{_esc(subject_for(d))}</title>
<style>
  @media (max-width: 600px) {{ .wrap {{ padding: 20px 12px !important; }} }}
</style></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{BG};"><tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" width="680" class="wrap" style="max-width:680px;width:100%;padding:36px 32px 48px;">
  <tr><td>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="font-family:{SANS};font-size:20px;font-weight:700;color:{INK};">Desk <span style="font-weight:400;color:{INK2};font-size:15px;">{_esc(EDITION_NAMES.get(d.get("edition", "morning"), ""))}</span></td>
      <td align="right" style="font-family:{MONO};font-size:12px;color:{INK2};">{_esc(d["date_text"])}{(" &nbsp; prints at " + _esc((d.get("at") or "")[11:16])) if d.get("intraday") and d.get("at") else ((" &nbsp; closes as of " + _esc(d["as_of"])) if d.get("as_of") else "")}</td>
    </tr></table>
    {_rule()}
  </td></tr>
  <tr><td style="padding-top:22px;">
    <div style="font-family:{SANS};font-size:26px;font-weight:300;letter-spacing:-0.02em;line-height:1.25;color:{INK};">{_esc(d["lead"])}</div>
    <div style="font-family:{MONO};font-size:13px;color:{INK2};margin-top:14px;">{d["n_watched"]} name{"s" if d["n_watched"] != 1 else ""} watched &nbsp; {d["n_alerts"]} lines &nbsp; {d["n_rule_alerts"]} rules fired &nbsp; status {_esc(d["status"])}</div>
  </td></tr>
  {"".join(parts)}
  <tr><td style="padding-top:40px;">
    {_rule()}
    <div style="font-family:{SANS};font-size:12px;color:{INK3};">{_esc(d["disclaimer"])}</div>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''
