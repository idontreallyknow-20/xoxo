"""The morning note: one short email a person would write, from the files the desk already builds.

The full digest (``an/digest.py``) is the record: every watched name, every filing, every
limitation. This is the thing Joseph and his dad read over breakfast. Four paragraphs, no
table, no chart: how the market closed, how my book is doing, what I would buy this week
and why, and anything that crossed a rule I wrote down in advance.

Everything is data first (:func:`build_note`), then plain text (:func:`render_text`) and a
small HTML (:func:`render_html`) that say the same words. The wording lives here and nowhere
else, and the language guard in the tests runs over every string.

What the note may say: the state of the closes, the book, the journal's standing calls with
their falsifiers, and alerts. It never invents a price: a missing number reads as "no close".
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import journal, paths

__all__ = ["build_note", "render_text", "render_html", "subject_for", "load_entries", "benchmarks_from_panel", "BENCH_NAMES"]

BENCH_NAMES = {"SPY": "the S&P 500 (SPY)", "QQQ": "the Nasdaq 100 (QQQ)", "VFV.TO": "VFV, the Toronto-listed S&P 500 fund,",
               "IWM": "small caps (IWM)"}
DISCLAIMER = ("Fake money in the book, real closes. Research from public data, not personalised financial "
              "advice; every buy or sell is your call.")


# ---------------------------------------------------------------- helpers

def _pct(v: Optional[float], nd: int = 1) -> str:
    if v is None:
        return "n/a"
    s = f"{v * 100:+.{nd}f}%"
    return s.replace("+0.0%", "flat").replace("-0.0%", "flat")


def _word(v: Optional[float], nd: int = 1) -> str:
    """'up 0.5%', 'down 0.5%', 'flat', for prose."""
    if v is None:
        return "n/a"
    if abs(v) < 0.0005:
        return "flat"
    return f"{'up' if v > 0 else 'down'} {abs(v) * 100:.{nd}f}%"


def _money(v: Optional[float], nd: int = 2) -> str:
    return "n/a" if v is None else f"${v:,.{nd}f}"


def _shares_money(v: Optional[float]) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def _long_day(d: dt.date) -> str:
    return d.strftime("%A %d %B %Y").replace(" 0", " ")


def _weekday(iso: Optional[str], today: dt.date) -> str:
    """'Friday', or 'Friday 11 September' when it is more than a week back, or 'the last close'."""
    if not iso:
        return "the last close"
    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    if (today - d).days <= 6:
        return d.strftime("%A")
    return d.strftime("%A %d %B").replace(" 0", " ")


def _date_words(iso: Optional[str]) -> str:
    """'8 September', or the raw string when it is not a date."""
    if not iso:
        return "the start"
    try:
        return dt.date.fromisoformat(iso[:10]).strftime("%d %B").lstrip("0")
    except ValueError:
        return iso


def _sessions_old(n: Optional[int]) -> str:
    if n is None or n <= 1:
        return ""
    return f" They are {n} sessions old."


def load_entries(path=None) -> List[journal.JournalEntry]:
    """The latest non-system entry per ticker from journal.md, in journal order."""
    seen: Dict[str, journal.JournalEntry] = {}
    for e in journal.load(path or paths.JOURNAL_MD):
        if e.is_system:
            continue
        seen[e.ticker.upper()] = e
    return list(seen.values())


_ZONE = re.compile(r"pullback to \$?([\d,]+(?:\.\d+)?)\s*(?:to|-|and)\s*\$?([\d,]+(?:\.\d+)?)", re.I)
_AFTER = re.compile(r"after (\w+ \d{1,2}) (?:earnings|report|results)", re.I)
_MONTH = re.compile(r"\bin (January|February|March|April|May|June|July|August|September|October|November|December)\b", re.I)
_CLOSE_UNDER = re.compile(r"close (?:under|below) \$?([\d,]+(?:\.\d+)?)", re.I)


def _standing(e: journal.JournalEntry, price: Optional[float], today: dt.date) -> Tuple[str, str]:
    """Classify a journal call for the note: ('now' | 'wait' | 'skip', reason)."""
    a = (e.action or "").strip()
    low = a.lower()
    if low.startswith("buy now"):
        return "now", "the note calls it a buy now"
    m = _ZONE.search(a)
    if m:
        lo, hi = (float(x.replace(",", "")) for x in m.groups())
        if price is None:
            return "wait", f"buy zone {_money(lo, 0)} to {_money(hi, 0)}, no close to check it against"
        if lo <= price <= hi:
            return "now", f"inside the {_money(lo, 0)} to {_money(hi, 0)} buy zone"
        if price > hi:
            return "wait", f"above its {_money(lo, 0)} to {_money(hi, 0)} buy zone"
        return "wait", f"under its {_money(lo, 0)} to {_money(hi, 0)} buy zone, which is worth a look at the note before buying"
    m = _AFTER.search(a)
    if m:
        try:
            when = dt.datetime.strptime(f"{m.group(1)} {today.year}", "%B %d %Y").date()
        except ValueError:
            when = None
        if when and today > when:
            return "wait", f"the {m.group(1)} report has printed; the note wants a read of it before buying"
        return "wait", f"waiting for the {m.group(1)} report"
    m = _MONTH.search(a)
    if m:
        month = dt.datetime.strptime(m.group(1), "%B").month
        if today.month >= month:
            return "now", f"the note said {m.group(1)}, and it is {m.group(1)}"
        return "wait", f"pencilled in for {m.group(1)}"
    if low.startswith("buy"):
        return "now", a.lower()
    return "skip", a


def _trigger(e: journal.JournalEntry) -> Optional[float]:
    m = _CLOSE_UNDER.search(e.wrong_if or "")
    return float(m.group(1).replace(",", "")) if m else None


def benchmarks_from_panel(panel) -> Optional[Dict[str, Dict[str, Any]]]:
    """Close, one-day, five-day and year-to-date change for each benchmark, from a :class:`an.quotes.QuotePanel`."""
    if panel is None or panel.closes.empty:
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for t in ("SPY", "QQQ", "VFV.TO", "IWM"):
        if t not in panel.closes.columns:
            continue
        col = panel.closes[t].dropna()
        if col.empty:
            continue
        last = float(col.iloc[-1])
        year_start = col[col.index.year < col.index[-1].year]
        out[t] = {"last_close": last, "last_date": col.index[-1].date().isoformat(),
                  "chg_1d": last / float(col.iloc[-2]) - 1.0 if len(col) > 1 else None,
                  "chg_5d": last / float(col.iloc[-6]) - 1.0 if len(col) > 5 else None,
                  "ytd": last / float(year_start.iloc[-1]) - 1.0 if not year_start.empty else None}
    return out or None


# ---------------------------------------------------------------- the note as data

def build_note(inputs: Dict[str, Optional[Dict[str, Any]]], *, today: Optional[dt.date] = None,
               entries: Optional[Sequence[journal.JournalEntry]] = None,
               bench_from_quotes: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Everything the note says, as data. ``inputs`` is :func:`an.digest.load_inputs`'s dict.

    ``entries`` are the journal's standing calls (default: read journal.md). ``bench_from_quotes``
    is :func:`benchmarks_from_panel`'s output when a quotes file is on disk; it wins over the
    scan's benchmarks because it carries VFV and the year to date."""
    today = today or dt.date.today()
    w = inputs.get("watch") or {}
    b = inputs.get("book") or {}
    scanned = w.get("status") == "SCANNED"
    names = {r["ticker"].upper(): r for r in (w.get("names") or [])}
    bench = w.get("benchmarks") or {}
    if entries is None:
        entries = load_entries()

    # -- market
    spy = bench.get("SPY") or {}
    close_date = spy.get("last_date") or b.get("as_of")
    market: Dict[str, Any] = {"close_date": close_date, "benchmarks": [], "movers_up": [], "movers_down": []}
    if bench_from_quotes and bench_from_quotes.get("SPY"):
        close_date = bench_from_quotes["SPY"].get("last_date") or close_date
        market["close_date"] = close_date
    for k in ("SPY", "QQQ", "VFV.TO"):
        v = (bench_from_quotes or {}).get(k) or bench.get(k) or {}
        if v.get("last_close") is None and not v.get("chg_1d"):
            continue
        market["benchmarks"].append({"ticker": k, "name": BENCH_NAMES.get(k, k), "close": v.get("last_close"),
                                     "chg_1d": v.get("chg_1d"), "chg_5d": v.get("chg_5d"), "ytd": v.get("ytd")})
    moves = [(r["ticker"], (r.get("price") or {}).get("chg_1d")) for r in names.values()
             if (r.get("price") or {}).get("chg_1d") is not None]
    moves.sort(key=lambda x: x[1])
    market["movers_down"] = [{"ticker": t, "chg": c} for t, c in moves[:2] if c <= -0.02]
    market["movers_up"] = [{"ticker": t, "chg": c} for t, c in reversed(moves[-2:]) if c >= 0.02]

    # -- the book
    book: Dict[str, Any] = {"status": b.get("status") or "NO BOOK FILE", "equity": b.get("equity"),
                            "ret": b.get("ret_since_start"), "first_fill": b.get("first_fill"), "cash": b.get("cash"),
                            "spy_same_window": (b.get("benchmarks_since_start") or {}).get("SPY"),
                            "as_of": b.get("as_of"), "age": b.get("quotes_age_sessions"), "positions": [],
                            "breached": [], "calls_today": [], "n_pending": b.get("n_pending") or 0}
    for p in b.get("open") or []:
        book["positions"].append({"ticker": p["ticker"], "shares": p.get("shares"), "entry": p.get("entry"),
                                  "last": p.get("last"), "ret": p.get("ret"), "trigger": p.get("trigger"),
                                  "kind": p.get("kind"), "stop": p.get("stop")})
        if p.get("falsifier_breached") or p.get("stop_breached"):
            book["breached"].append(p["ticker"])
    for p in (b.get("long") or []) + (b.get("swing") or []):
        if p.get("call_date") == today.isoformat():
            book["calls_today"].append({"ticker": p["ticker"], "target_usd": p.get("target_usd"),
                                        "wrong_if": p.get("wrong_if"), "thesis": p.get("thesis")})
    book["closed_recent"] = [{"ticker": p["ticker"], "ret": p.get("ret"), "reason": p.get("exit_reason"),
                              "exit_date": p.get("exit_date")}
                             for p in (b.get("closed") or [])
                             if p.get("exit_date") and p["exit_date"] >= (today - dt.timedelta(days=4)).isoformat()]

    # -- what I would buy
    held = {p["ticker"] for p in book["positions"]}
    now_list: List[Dict[str, Any]] = []
    wait_list: List[Dict[str, Any]] = []
    out_list: List[Dict[str, Any]] = []
    for e in entries:
        t = e.ticker.upper()
        row = names.get(t) or {}
        price = (row.get("price") or {}).get("last_close")
        state, why = _standing(e, price, today)
        if state == "skip":
            continue
        trig = _trigger(e)
        breached = bool((row.get("price") or {}).get("breached")) or (price is not None and trig is not None and price < trig)
        item = {"ticker": t, "price": price, "target_usd": e.target_usd, "conviction": e.conviction,
                "wrong_if": e.wrong_if, "trigger": trig, "why": why, "held": t in held,
                "since_call": (row.get("price") or {}).get("since_call"), "thesis": e.thesis}
        if breached:
            item["why"] = f"closed under its own wrong-if level of {_money(trig, 0)}" if trig else "its falsifier fired"
            out_list.append(item)
        elif state == "now":
            now_list.append(item)
        elif t not in held:
            wait_list.append(item)
    now_list.sort(key=lambda i: (-(i["conviction"] or 0), i["held"], i["ticker"]))

    # -- rules that fired
    alerts = [{"ticker": a["ticker"], "text": a.get("text"), "rule": a.get("rule")}
              for a in (w.get("alerts") or []) if a.get("severity", 0) >= 3]

    tests = inputs.get("tests") or {}
    return {"date": today.isoformat(), "scanned": scanned, "market": market, "book": book,
            "buy_now": now_list, "waiting": wait_list, "out": out_list, "alerts": alerts,
            "tests_failed": int(tests.get("failed") or 0) + int(tests.get("errors") or 0) if tests else None,
            "price_note": (f"Prices are Yahoo closes through {_weekday(close_date, today)}"
                           + (f" {close_date}" if close_date and (today - dt.date.fromisoformat(close_date)).days > 6 else "")
                           + "." + _sessions_old(book.get("age"))) if close_date else "No closes were available this morning.",
            "disclaimer": DISCLAIMER}


# ---------------------------------------------------------------- the words

def _paragraphs(n: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """The note as a list of ('h', title) / ('p', text) / ('ul', [lines]) blocks. Text and HTML share it."""
    today = dt.date.fromisoformat(n["date"])
    blocks: List[Tuple[str, Any]] = []
    m, b = n["market"], n["book"]

    # market
    blocks.append(("h", "The market"))
    if not m["benchmarks"]:
        blocks.append(("p", "No scan ran, so I have no closes to report this morning. The rest is from the last mark."))
    else:
        day = _weekday(m["close_date"], today)
        bits = []
        for i, x in enumerate(m["benchmarks"]):
            name = x["name"][0].upper() + x["name"][1:]
            if i == 0 and x["close"] is not None:
                s = f"{name} closed at {_money(x['close'])} on {day}, {_word(x['chg_1d'])} on the day"
            else:
                s = f"{name} was {_word(x['chg_1d'])} on the day"
            s += f", {_word(x['chg_5d'])} on the week"
            if x.get("ytd") is not None:
                s += f" and {_word(x['ytd'])} since 1 January"
            bits.append(s + ".")
        blocks.append(("p", " ".join(bits)))
        mv = []
        for x in m["movers_up"]:
            mv.append(f"{x['ticker']} {_pct(x['chg'])}")
        for x in m["movers_down"]:
            mv.append(f"{x['ticker']} {_pct(x['chg'])}")
        if mv:
            blocks.append(("p", f"Among the names I watch, the moves worth a look on the day: {', '.join(mv)}."))

    # book
    blocks.append(("h", "My book"))
    if b["status"] != "MARKED":
        blocks.append(("p", f"Not marked this morning ({b['status'].lower()}): {b['n_pending']} call"
                            f"{'s' if b['n_pending'] != 1 else ''} waiting for a close to fill at."))
    else:
        s = f"{_shares_money(b['equity'])}, {_word(b['ret'])} since I started on {_date_words(b['first_fill'])}"
        if b.get("spy_same_window") is not None:
            diff = (b["ret"] or 0) - b["spy_same_window"]
            s += (f". SPY was {_word(b['spy_same_window'])} over the same stretch, so I am "
                  + (f"{abs(diff) * 100:.1f} points {'ahead' if diff >= 0 else 'behind'}" if abs(diff) >= 0.0005 else "level with it"))
        s += f". {len(b['positions'])} position{'s' if len(b['positions']) != 1 else ''} and {_shares_money(b['cash'])} in cash."
        blocks.append(("p", s))
        lines = []
        for p in b["positions"]:
            line = f"{p['ticker']}: {p['shares']} shares at {_money(p['entry'])}, now {_money(p['last'])}, {_pct(p['ret'])}."
            if p.get("kind") == "swing" and p.get("stop"):
                line += f" Stop {_money(p['stop'])}."
            elif p.get("trigger"):
                line += f" Wrong if it closes under {_money(p['trigger'], 0)}."
            lines.append(line)
        if lines:
            blocks.append(("ul", lines))
        if b["breached"]:
            blocks.append(("p", f"{', '.join(b['breached'])} closed under the level I said would prove me wrong. "
                                f"I decide today whether to sell or say why not, and the decision goes in the book."))
        elif b["positions"]:
            blocks.append(("p", "Nothing is under the level I said would prove me wrong."))
        for c in b["closed_recent"]:
            blocks.append(("p", f"Closed {c['ticker']} on {_weekday(c['exit_date'], today)} at {_pct(c['ret'])}: {c['reason']}."))
        for c in b["calls_today"]:
            blocks.append(("p", f"New today: {c['ticker']}, {_shares_money(c['target_usd'])}. {c['thesis']} Wrong if: {c['wrong_if']}"))

    # buy
    blocks.append(("h", "What I'd buy this week"))
    if n["buy_now"]:
        blocks.append(("p", "If I were putting fresh money in, in this order. Sizes are for a $100,000 account, "
                            "and the last line of each is what would make me wrong."))
        lines = []
        for i, x in enumerate(n["buy_now"], 1):
            s = f"{i}. {x['ticker']} at {_money(x['price']) if x['price'] is not None else 'no close'}"
            s += f", {x['why']}. {_shares_money(x['target_usd'])}." if x["target_usd"] else f", {x['why']}."
            if x["held"]:
                s += " Already in my book."
            if x["wrong_if"]:
                s += f" Wrong if: {x['wrong_if'].rstrip('.')}."
            lines.append(s)
        blocks.append(("ul", lines))
    else:
        blocks.append(("p", "Nothing is in a buy zone this morning. I would sit on the cash."))
    if n["waiting"]:
        blocks.append(("p", "Waiting: " + "; ".join(f"{x['ticker']} ({x['why']}{', now ' + _money(x['price']) if x['price'] is not None else ''})"
                                                    for x in n["waiting"]) + "."))
    if n["out"]:
        blocks.append(("p", "Off the list until I re-read the note: " + "; ".join(f"{x['ticker']} ({x['why']})" for x in n["out"]) + "."))

    # alerts
    if n["alerts"]:
        blocks.append(("h", "What crossed a rule"))
        blocks.append(("ul", [f"{a['ticker']}: {a['text']} (rule: {a['rule']})" for a in n["alerts"]]))

    foot = n["price_note"]
    if n.get("tests_failed"):
        foot += f" {n['tests_failed']} of the desk's own tests failed this morning, so treat the numbers with care."
    blocks.append(("p", foot + " " + n["disclaimer"]))
    return blocks


def subject_for(n: Dict[str, Any]) -> str:
    day = dt.date.fromisoformat(n["date"]).strftime("%a %d %b").replace(" 0", " ")
    head = f"Morning note, {day}"
    if n.get("tests_failed"):
        head = "TESTS RED, " + head
    b = n["book"]
    if b["breached"]:
        return f"{head}: {', '.join(b['breached'])} under its wrong-if level"
    if n["alerts"]:
        names = sorted({a["ticker"] for a in n["alerts"]})
        return f"{head}: {', '.join(names[:3])} crossed a rule"
    if b["status"] == "MARKED" and b.get("ret") is not None:
        return f"{head}: book {_pct(b['ret'])}, {len(n['buy_now'])} name{'s' if len(n['buy_now']) != 1 else ''} in a buy zone"
    return head


def render_text(n: Dict[str, Any]) -> str:
    today = dt.date.fromisoformat(n["date"])
    out = [f"Morning note, {_long_day(today)}", ""]
    for kind, body in _paragraphs(n):
        if kind == "h":
            out += [body.upper(), ""]
        elif kind == "p":
            out += [body, ""]
        else:
            out += [f"  {ln}" for ln in body] + [""]
    return "\n".join(out).rstrip() + "\n"


_SANS = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"


def render_html(n: Dict[str, Any]) -> str:
    today = dt.date.fromisoformat(n["date"])
    e = htmllib.escape
    parts = [f'<p style="margin:0 0 20px;font-size:13px;color:#777;">Morning note, {e(_long_day(today))}</p>']
    for kind, body in _paragraphs(n):
        if kind == "h":
            parts.append(f'<h2 style="font-size:16px;font-weight:600;margin:26px 0 8px;color:#111;">{e(body)}</h2>')
        elif kind == "p":
            parts.append(f'<p style="margin:0 0 12px;">{e(body)}</p>')
        else:
            items = "".join(f'<li style="margin:0 0 6px;">{e(ln)}</li>' for ln in body)
            parts.append(f'<ul style="margin:0 0 12px;padding-left:20px;">{items}</ul>')
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
            f"<title>{e(subject_for(n))}</title></head>"
            f'<body style="margin:0;padding:24px 16px;background:#fff;"><div style="max-width:560px;margin:0 auto;'
            f'font-family:{_SANS};font-size:15px;line-height:1.55;color:#222;">' + "".join(parts) + "</div></body></html>")
