"""The daily scan: what moved, for the names in the journal and in the portfolio.

Between the quarterly re-underwrites nothing in this repo looked at anything.
This module does, once a day, from three sources that cost nothing and break no
terms of use (NOTES.md section 9b has the evaluation of the ones that do):

* **prices**, through :mod:`an.prices`, the same cached yfinance closes the
  tracker grades on. Each name is read against its price at call and against the
  one falsifier a price series can check, "a close under $X", parsed by
  :func:`an.tracker.parse_trigger` so the scan and the tracker cannot disagree
  about what the trigger is.
* **SEC EDGAR filings**, through :mod:`an.edgar`. New 8-Ks (with their item
  codes, so an earnings release is told apart from a change of auditor), 10-Qs,
  10-Ks, Form 4 insider filings and 13D/13G stakes since the last scan.
* **RSS headlines**, from the per-ticker feed Yahoo Finance publishes for
  syndication. Titles, links and dates. No sentiment is read from them, because
  nothing here can read sentiment honestly, and a headline is listed as a
  headline rather than scored.

An **alert** is a mechanical fact that crosses a rule Joseph wrote down before
any of this ran: the journal's own falsifier level, the README's "drops 15% or
more in a week" event trigger, a new earnings 8-K. Every alert carries the rule
it fired on and the number that fired it. Nothing here recommends a trade. The
README says a check-in carries no trade recommendation unless something material
happened, and the scan's job is to say whether something did, not what to do.

State. ``data/cache/watch/state.json`` remembers which filings and headlines
have been reported, so tomorrow's scan lists only what is new. ``watch.json``
carries the whole readout for the dashboard and for the daily email.

Nothing here has run against a live source. The machine it was written on could
reach none of them. ``scripts/scan.py --dry-run`` prints the plan.
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from . import edgar, journal, paths, prices, tracker
from .http import FixtureTransport, HttpTransport, Transport, redact
from .store import Cache, FetchError, Offline, cache_key

__all__ = [
    "WatchName", "PriceRead", "FilingNote", "Headline", "Alert", "WatchState",
    "RssReader", "RSS_TEMPLATE", "WATCH_CACHE", "STATE_PATH",
    "DROP_WEEK", "MOVE_DAY", "NEAR_TRIGGER", "FORMS_WATCHED",
    "names_to_watch", "price_read", "intraday_read", "filings_since", "alerts_for", "build_report",
    "not_run_report", "rules_text", "BENCHMARKS", "alert_key",
]

WATCH_CACHE = paths.CACHE_DIR / "watch"
STATE_PATH = WATCH_CACHE / "state.json"
RSS_CACHE = WATCH_CACHE / "rss"

BENCHMARKS: Tuple[str, ...] = ("SPY", "QQQ")

# The rules, each traceable to a sentence Joseph wrote.
DROP_WEEK = -0.15      # README, Cadence, Event driven: "a holding drops 15%+ in a week"
MOVE_DAY = 0.05        # a single-session move worth a line in the email; not a trade signal
NEAR_TRIGGER = 0.05    # within five percent above the journal's "close under $X" level
WINDOW_DAYS = 45       # calendar days of closes pulled, enough for five sessions plus holidays

FORMS_WATCHED: Tuple[str, ...] = ("8-K", "10-Q", "10-K", "4", "SC 13D", "SC 13G", "DEF 14A", "10-Q/A", "10-K/A")

# Yahoo Finance's syndication feed, one per ticker. Published for feed readers,
# which is what this is. Cached an hour so a re-run inside the day costs nothing.
RSS_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
RSS_TTL = 3600.0
RSS_KEEP_DAYS = 10

ITEM_NAMES = {
    "1.01": "material agreement", "1.02": "agreement terminated", "1.03": "bankruptcy",
    "2.01": "acquisition or disposition", "2.02": "earnings release", "2.03": "new debt obligation",
    "2.04": "debt acceleration", "2.05": "exit or disposal costs", "2.06": "impairment",
    "3.01": "delisting notice", "4.01": "change of auditor", "4.02": "financials not to be relied on",
    "5.01": "change of control", "5.02": "officer or director change", "5.03": "bylaw change",
    "5.07": "shareholder vote", "7.01": "Reg FD disclosure", "8.01": "other event", "9.01": "exhibits",
}


# ---------------------------------------------------------------------------
# who is watched
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchName:
    ticker: str
    action: Optional[str]
    kind: str
    date: Optional[str]
    price_at_call: Optional[float]
    wrong_if: Optional[str]
    trigger: Optional[float]
    held: bool
    conviction: Optional[int] = None
    bucket: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {"ticker": self.ticker, "action": self.action, "kind": self.kind, "date": self.date,
                "price_at_call": self.price_at_call, "wrong_if": self.wrong_if, "trigger": self.trigger,
                "held": self.held, "conviction": self.conviction, "bucket": self.bucket}


def names_to_watch(entries: Optional[Sequence[journal.JournalEntry]] = None,
                   held: Optional[Iterable[str]] = None,
                   held_triggers: Optional[Dict[str, float]] = None) -> List[WatchName]:
    """One row per ticker: the latest journal call for it, and whether it is held.

    A held name with no journal entry is watched too, with the holdings file's
    ``wrong_if_price`` as its trigger. A name in neither is not watched: the scan
    is about the names Joseph has committed to in writing, not the whole 150.
    """
    entries = journal.load() if entries is None else entries
    held_set = {h.upper() for h in (held or [])}
    held_triggers = {k.upper(): v for k, v in (held_triggers or {}).items()}
    latest: Dict[str, journal.JournalEntry] = {}
    for e in entries:
        if e.is_system:
            continue
        cur = latest.get(e.ticker)
        if cur is None or e.date >= cur.date:
            latest[e.ticker] = e
    out: List[WatchName] = []
    for t, e in latest.items():
        trig = tracker.parse_trigger(e.wrong_if)
        if trig is None and t in held_triggers:
            trig = held_triggers[t]
        out.append(WatchName(ticker=t, action=e.action, kind=tracker.classify_action(e.action), date=e.date,
                             price_at_call=e.price_at_call, wrong_if=e.wrong_if, trigger=trig,
                             held=t in held_set, conviction=e.conviction, bucket=e.bucket))
    for t in sorted(held_set - set(latest)):
        out.append(WatchName(ticker=t, action=None, kind="held", date=None, price_at_call=None,
                             wrong_if=None, trigger=held_triggers.get(t), held=True))
    out.sort(key=lambda n: (not n.held, n.ticker))
    return out


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceRead:
    ticker: str
    last_close: Optional[float]
    last_date: Optional[str]
    prev_close: Optional[float]
    chg_1d: Optional[float]
    chg_5d: Optional[float]
    since_call: Optional[float]
    to_trigger: Optional[float]
    """Distance above the trigger as a fraction of the trigger; negative means under it."""
    breached: Optional[bool]
    low_in_window: Optional[float]
    sessions: int
    stale_days: Optional[int]
    intraday: bool = False
    """True when ``last_close`` is the last fifteen-minute print, not a close."""
    at: Optional[str] = None
    """The print's timestamp when intraday, America/New_York."""

    def to_json(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def intraday_read(prints: Dict[str, Any], ticker: str, *, as_of: dt.date,
                  price_at_call: Optional[float] = None, trigger: Optional[float] = None) -> PriceRead:
    """The same readout from an :class:`an.intraday.Print`. ``chg_1d`` is the move on the
    day against the prior close; ``chg_5d`` is None because five sessions of bars is not
    the question. ``breached`` means trading under the level, which the alert says is not a close."""
    p = prints.get(ticker.upper())
    empty = PriceRead(ticker, None, None, None, None, None, None, None, None, None, 0, None, True, None)
    if p is None or p.price is None:
        return empty
    last = p.price
    since = (last / price_at_call - 1.0) if price_at_call else None
    to_trig = (last / trigger - 1.0) if trigger else None
    session = dt.date.fromisoformat(p.session) if p.session else None
    return PriceRead(ticker=ticker, last_close=last, last_date=p.session, prev_close=p.prior_close,
                     chg_1d=p.change, chg_5d=None, since_call=since, to_trigger=to_trig,
                     breached=(last < trigger) if trigger else None, low_in_window=p.session_low,
                     sessions=p.bars_in_session, stale_days=(as_of - session).days if session else None,
                     intraday=True, at=p.at)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def price_read(closes: Optional[pd.DataFrame], ticker: str, *, as_of: dt.date,
               price_at_call: Optional[float] = None, trigger: Optional[float] = None) -> PriceRead:
    """The mechanical readout of one column. Every hole is ``None``."""
    empty = PriceRead(ticker, None, None, None, None, None, None, None, None, None, 0, None)
    if closes is None or ticker not in closes.columns:
        return empty
    s = closes[ticker].dropna()
    s = s[s.index <= pd.Timestamp(as_of)]
    if s.empty:
        return empty
    last = _f(s.iloc[-1])
    last_date = pd.Timestamp(s.index[-1]).date()
    prev = _f(s.iloc[-2]) if len(s) >= 2 else None
    week = _f(s.iloc[-6]) if len(s) >= 6 else None
    chg_1d = (last / prev - 1.0) if last is not None and prev else None
    chg_5d = (last / week - 1.0) if last is not None and week else None
    since = (last / price_at_call - 1.0) if last is not None and price_at_call else None
    to_trig = (last / trigger - 1.0) if last is not None and trigger else None
    breached = (last < trigger) if last is not None and trigger else None
    low = _f(s.min())
    return PriceRead(ticker=ticker, last_close=last, last_date=last_date.isoformat(), prev_close=prev,
                     chg_1d=chg_1d, chg_5d=chg_5d, since_call=since, to_trigger=to_trig, breached=breached,
                     low_in_window=low, sessions=int(len(s)), stale_days=(as_of - last_date).days)


# ---------------------------------------------------------------------------
# filings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilingNote:
    ticker: str
    form: str
    filing_date: str
    accession: str
    items: List[str]
    description: Optional[str]
    url: Optional[str]
    is_earnings: bool

    @property
    def item_text(self) -> str:
        names = [ITEM_NAMES.get(i, i) for i in self.items]
        return ", ".join(names)

    def to_json(self) -> Dict[str, Any]:
        return {"ticker": self.ticker, "form": self.form, "filing_date": self.filing_date,
                "accession": self.accession, "items": list(self.items), "item_text": self.item_text,
                "description": self.description, "url": self.url, "is_earnings": self.is_earnings}


def filings_since(client: edgar.EdgarClient, ticker: str, since: dt.date, *,
                  forms: Sequence[str] = FORMS_WATCHED, limit: int = 60) -> Tuple[List[FilingNote], Optional[str]]:
    """Filings dated after ``since``, newest first, and a note when the name has no CIK.

    Canadian listings and foreign private issuers on the list have no EDGAR
    presence or file 40-F/6-K instead; the note says so rather than reporting
    "no filings" as if it had looked.
    """
    cik = client.cik_for(ticker)
    if cik is None:
        return [], f"{ticker} has no CIK in EDGAR's ticker map, so its filings are not scanned here."
    out: List[FilingNote] = []
    for f in client.filings(cik, forms=forms, limit=limit):
        if f.filing_date <= since.isoformat():
            continue
        out.append(FilingNote(ticker=ticker, form=f.form, filing_date=f.filing_date, accession=f.accession,
                              items=list(f.items), description=f.primary_doc_description, url=f.primary_url,
                              is_earnings=f.is_earnings_8k))
    return out, None


# ---------------------------------------------------------------------------
# headlines
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Headline:
    ticker: str
    title: str
    link: Optional[str]
    published: Optional[str]
    guid: str
    source: str = "Yahoo Finance RSS"

    def to_json(self) -> Dict[str, Any]:
        return {"ticker": self.ticker, "title": self.title, "link": self.link, "published": self.published,
                "guid": self.guid, "source": self.source}


_RFC822 = "%a, %d %b %Y %H:%M:%S %z"


def _parse_pubdate(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = s.strip().replace(" GMT", " +0000").replace(" UTC", " +0000")
    for fmt in (_RFC822, "%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d.isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def parse_rss(raw: bytes, ticker: str, *, source: str = "Yahoo Finance RSS") -> List[Headline]:
    """Titles, links, dates and a stable id out of an RSS 2.0 document. Malformed XML is
    an empty list plus a raised :class:`FetchError`, never a partial read."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise FetchError(f"RSS for {ticker} is not XML: {e}")
    out: List[Headline] = []
    for item in root.iter("item"):
        title = htmllib.unescape((item.findtext("title") or "").strip())
        if not title:
            continue
        link = (item.findtext("link") or "").strip() or None
        guid = (item.findtext("guid") or "").strip() or link or title
        out.append(Headline(ticker=ticker, title=re.sub(r"\s+", " ", title), link=link,
                            published=_parse_pubdate(item.findtext("pubDate")), guid=guid, source=source))
    return out


@dataclass
class RssReader:
    transport: Transport
    cache: Cache = field(default_factory=lambda: Cache(RSS_CACHE, default_ttl=RSS_TTL))
    template: str = RSS_TEMPLATE
    user_agent: str = "desk personal research feed reader"
    calls: List[str] = field(default_factory=list)

    def url_for(self, ticker: str) -> str:
        return self.template.format(ticker=ticker)

    @property
    def live(self) -> bool:
        return isinstance(self.transport, HttpTransport)

    def headlines(self, ticker: str) -> List[Headline]:
        url = self.url_for(ticker)
        self.calls.append(redact(url))

        def fetch() -> Dict[str, Any]:
            raw = self.transport.get(url, headers={"User-Agent": self.user_agent, "Accept": "application/rss+xml, application/xml, text/xml"})
            items = parse_rss(raw, ticker)
            return {"items": [h.to_json() for h in items], "live": self.live}

        payload = self.cache.get_or_fetch(cache_key("rss", ticker), fetch,
                                          meta={"url": redact(url), "transport": type(self.transport).__name__, "live": self.live})
        rows = payload.get("items") if isinstance(payload, dict) else []
        return [Headline(ticker=ticker, title=r["title"], link=r.get("link"), published=r.get("published"),
                         guid=r["guid"], source=r.get("source") or "Yahoo Finance RSS") for r in rows or []]


# ---------------------------------------------------------------------------
# state: what has already been reported
# ---------------------------------------------------------------------------

@dataclass
class WatchState:
    seen_filings: Dict[str, List[str]] = field(default_factory=dict)
    seen_headlines: Dict[str, List[str]] = field(default_factory=dict)
    seen_alerts: List[str] = field(default_factory=list)
    """Alert keys already sent in an email, so the event edition never repeats one."""
    last_scan: Optional[str] = None
    n_scans: int = 0

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "WatchState":
        p = path or STATE_PATH
        if not p.exists():
            return cls()
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        return cls(seen_filings={k: list(v) for k, v in (blob.get("seen_filings") or {}).items()},
                   seen_headlines={k: list(v) for k, v in (blob.get("seen_headlines") or {}).items()},
                   seen_alerts=list(blob.get("seen_alerts") or []),
                   last_scan=blob.get("last_scan"), n_scans=int(blob.get("n_scans") or 0))

    def save(self, path: Optional[Path] = None) -> Path:
        p = path or STATE_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        blob = {"seen_filings": self.seen_filings, "seen_headlines": self.seen_headlines,
                "seen_alerts": self.seen_alerts[-2000:], "last_scan": self.last_scan, "n_scans": self.n_scans}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def new_filings(self, notes: Iterable[FilingNote]) -> List[FilingNote]:
        return [n for n in notes if n.accession not in set(self.seen_filings.get(n.ticker, []))]

    def new_headlines(self, items: Iterable[Headline]) -> List[Headline]:
        return [h for h in items if h.guid not in set(self.seen_headlines.get(h.ticker, []))]

    def unsent_alerts(self, alerts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set(self.seen_alerts)
        return [a for a in alerts if alert_key(a) not in seen]

    def remember_alerts(self, alerts: Iterable[Dict[str, Any]]) -> None:
        for a in alerts:
            k = alert_key(a)
            if k not in self.seen_alerts:
                self.seen_alerts.append(k)

    def remember(self, notes: Iterable[FilingNote], items: Iterable[Headline], *, when: str) -> None:
        for n in notes:
            self.seen_filings.setdefault(n.ticker, [])
            if n.accession not in self.seen_filings[n.ticker]:
                self.seen_filings[n.ticker].append(n.accession)
            self.seen_filings[n.ticker] = self.seen_filings[n.ticker][-500:]
        for h in items:
            self.seen_headlines.setdefault(h.ticker, [])
            if h.guid not in self.seen_headlines[h.ticker]:
                self.seen_headlines[h.ticker].append(h.guid)
            self.seen_headlines[h.ticker] = self.seen_headlines[h.ticker][-500:]
        self.last_scan = when
        self.n_scans += 1


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    ticker: str
    kind: str
    """``trigger``, ``near_trigger``, ``drop_week``, ``move_day``, ``earnings_8k``, ``filing``, ``stake``."""
    severity: int
    """3 fires a written rule, 2 is worth a line, 1 is for the record."""
    text: str
    rule: str
    value: Optional[float]
    as_of: Optional[str]
    source: str

    def to_json(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def key(self) -> str:
        """What makes two alerts the same event: name, kind and the day. A print at 10:30
        and one at 11:00 under the same level are one alert, not two emails."""
        return f"{self.ticker}|{self.kind}|{(self.as_of or '')[:10]}"


def alert_key(a: Dict[str, Any]) -> str:
    return f"{a.get('ticker')}|{a.get('kind')}|{(a.get('as_of') or '')[:10]}"


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def alerts_for(name: WatchName, read: PriceRead, filings: Sequence[FilingNote]) -> List[Alert]:
    """Mechanical, rule by rule. The text says what crossed what; it never says what to do."""
    out: List[Alert] = []
    t = name.ticker
    when = (read.at or "")[11:16]
    if read.intraday and read.last_close is not None and name.trigger and read.breached:
        out.append(Alert(t, "trigger_intraday", 3,
                         f"{t} is trading at {_money(read.last_close)} at {when}, under the {_money(name.trigger)} "
                         f"level the journal named. That is a print, not a close: the journal's rule is a close under "
                         f"the level, and the day is not over.",
                         rule="journal.md, Wrong if: a close under the named level (intraday print)",
                         value=read.to_trigger, as_of=read.at or read.last_date, source="intraday prices"))
    elif read.last_close is not None and name.trigger:
        if read.breached:
            out.append(Alert(t, "trigger", 3,
                             f"{t} closed at {_money(read.last_close)} on {read.last_date}, under the "
                             f"{_money(name.trigger)} level the journal named as the falsifier. On the journal's own "
                             f"terms the call is falsified so far. Whether the thesis is broken or the market is "
                             f"wrong is a reading, not a price, and the README asks for a write-up either way.",
                             rule="journal.md, Wrong if: a close under the named level",
                             value=read.to_trigger, as_of=read.last_date, source="prices"))
        elif read.to_trigger is not None and read.to_trigger <= NEAR_TRIGGER:
            out.append(Alert(t, "near_trigger", 2,
                             f"{t} closed at {_money(read.last_close)}, {read.to_trigger * 100:.1f}% above the "
                             f"{_money(name.trigger)} falsifier.",
                             rule=f"within {NEAR_TRIGGER:.0%} of the journal's falsifier",
                             value=read.to_trigger, as_of=read.last_date, source="prices"))
    if read.chg_5d is not None and read.chg_5d <= DROP_WEEK:
        out.append(Alert(t, "drop_week", 3,
                         f"{t} is {_pct(read.chg_5d)} over the last five sessions, past the 15% weekly fall the "
                         f"README lists as an event-driven check: is the thesis broken or the market wrong?",
                         rule="README, Cadence, Event driven: a holding drops 15%+ in a week",
                         value=read.chg_5d, as_of=read.last_date, source="prices"))
    if read.chg_1d is not None and abs(read.chg_1d) >= MOVE_DAY:
        if read.intraday:
            out.append(Alert(t, "move_day", 2,
                             f"{t} is {_pct(read.chg_1d)} on the day at {when}, at {_money(read.last_close)}.",
                             rule=f"move of {MOVE_DAY:.0%} or more against the prior close (intraday print)",
                             value=read.chg_1d, as_of=read.at or read.last_date, source="intraday prices"))
        else:
            out.append(Alert(t, "move_day", 2,
                             f"{t} moved {_pct(read.chg_1d)} on {read.last_date}.",
                             rule=f"single session move of {MOVE_DAY:.0%} or more",
                             value=read.chg_1d, as_of=read.last_date, source="prices"))
    form4 = [f for f in filings if f.form == "4"]
    for f in filings:
        if f.is_earnings:
            others = [ITEM_NAMES.get(i, i) for i in f.items if not i.startswith(("2.02", "9.01"))]
            out.append(Alert(t, "earnings_8k", 3,
                             f"{t} filed an 8-K on {f.filing_date} with item 2.02, an earnings release"
                             f"{' (also ' + ', '.join(others) + ')' if others else ''}. Not read here: "
                             f"python scripts/guidance_diff.py {t} diffs its guidance against the last one.",
                             rule="new 8-K, item 2.02", value=None, as_of=f.filing_date, source="EDGAR"))
        elif f.form.startswith(("10-Q", "10-K")):
            out.append(Alert(t, "filing", 2,
                             f"{t} filed a {f.form} on {f.filing_date}. The as-reported numbers reach the score "
                             f"through the DERA data set for that quarter, once pulled.",
                             rule=f"new {f.form}", value=None, as_of=f.filing_date, source="EDGAR"))
        elif f.form.startswith("SC 13"):
            out.append(Alert(t, "stake", 2,
                             f"A {f.form} was filed on {t} on {f.filing_date}: a holder crossed a 5% stake, or changed one.",
                             rule=f"new {f.form}", value=None, as_of=f.filing_date, source="EDGAR"))
        elif f.form.startswith("8-K"):
            out.append(Alert(t, "filing", 1,
                             f"{t} filed an 8-K on {f.filing_date}" + (f": {f.item_text}." if f.items else "."),
                             rule="new 8-K", value=None, as_of=f.filing_date, source="EDGAR"))
    if form4:
        out.append(Alert(t, "filing", 1,
                         f"{len(form4)} Form 4 insider filing{'s' if len(form4) != 1 else ''} on {t} since the last scan, "
                         f"latest {form4[0].filing_date}. Direction and size are in the filings, not read here.",
                         rule="new Form 4", value=float(len(form4)), as_of=form4[0].filing_date, source="EDGAR"))
    out.sort(key=lambda a: (-a.severity, a.kind))
    return out


def rules_text() -> List[str]:
    return [
        "A trigger alert fires when the latest close is under the 'close under $X' level in the journal's "
        "Wrong if line, parsed the same way the tracker parses it. Falsifiers that name no price are not checked.",
        f"A weekly-drop alert fires at {DROP_WEEK:.0%} or worse over five sessions, the README's event-driven rule.",
        f"A single-session move of {MOVE_DAY:.0%} or more gets a line. It is a line, not a signal.",
        "A new 8-K with item 2.02 is an earnings release. Other new 8-Ks, 10-Qs, 10-Ks, Form 4s and 13D/13Gs are "
        "listed with their item codes and not read.",
        "Headlines are listed with their source and date. No sentiment is read from them.",
        "Nothing in the scan recommends a trade. It says what crossed a rule you wrote down.",
    ]


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------

DISCLAIMER = "Research and analysis from public data, not personalised financial advice."


def not_run_report(names: Sequence[WatchName], *, built_at: Optional[str] = None) -> Dict[str, Any]:
    """The honest file for a checkout where nothing has been pulled."""
    return {
        "built_at": built_at or dt.datetime.now().isoformat(timespec="seconds"),
        "status": "NOT RUN",
        "is_real": False,
        "mode": "close",
        "at": None,
        "as_of": None,
        "watched": [n.to_json() for n in names],
        "n_watched": len(names),
        "sources": {"prices": {"live": False, "detail": "no closes pulled"},
                    "edgar": {"live": False, "detail": "no filings pulled"},
                    "rss": {"live": False, "detail": "no feeds pulled"}},
        "names": [],
        "alerts": [],
        "benchmarks": {},
        "why_not_run": [
            "No scan has run. The names below are what it would watch: every ticker with a journal call, plus "
            "anything in portfolio/holdings.csv, which is not committed.",
            "To run it: python scripts/scan.py --live, on a machine that can reach Yahoo, data.sec.gov and "
            "feeds.finance.yahoo.com. setup.ps1 -InstallTask schedules it daily on Windows.",
            "Until then the daily email has nothing to report and says so.",
        ],
        "rules": rules_text(),
        "limitations": [],
        "disclaimer": DISCLAIMER,
    }


def build_report(names: Sequence[WatchName], reads: Dict[str, PriceRead], filings: Dict[str, List[FilingNote]],
                 headlines: Dict[str, List[Headline]], notes: Sequence[str], *, as_of: dt.date,
                 sources: Dict[str, Dict[str, Any]], bench_reads: Optional[Dict[str, PriceRead]] = None,
                 built_at: Optional[str] = None, status: str = "SCANNED", mode: str = "close",
                 at: Optional[str] = None) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    alerts: List[Alert] = []
    for n in names:
        r = reads.get(n.ticker) or price_read(None, n.ticker, as_of=as_of)
        fl = filings.get(n.ticker, [])
        hl = headlines.get(n.ticker, [])
        a = alerts_for(n, r, fl)
        alerts += a
        rows.append({**n.to_json(), "price": r.to_json(), "filings": [f.to_json() for f in fl],
                     "headlines": [h.to_json() for h in hl], "alerts": [x.to_json() for x in a],
                     "n_alerts": len(a), "max_severity": max([x.severity for x in a], default=0)})
    alerts.sort(key=lambda a: (-a.severity, a.ticker, a.kind))
    limitations = [
        "Prices are yfinance adjusted closes and can be stale on a holiday or after a throttled pull; each row "
        "carries the date its close is from.",
        "Only the 'close under $X' falsifier is checked. Guidance and growth falsifiers are real and are left to "
        "the reader.",
        "Filings are as listed in EDGAR's submissions index. A filing accepted after 17:30 ET carries that day's "
        "date and reached the market the next session.",
        "Headlines come from one syndication feed and are not scored. A quiet feed is not a quiet company.",
        "Names without a CIK, which is every Canadian listing here, are not scanned for filings, and the row says so.",
    ] + list(notes)
    if mode == "intraday":
        limitations.insert(0, "INTRADAY. Every price is the last fifteen-minute print, not a close. The journal's "
                              "rules are closing rules; a level crossed at 11:00 can be uncrossed by 16:00, and the "
                              "alert says so.")
    if status == "SYNTHETIC":
        limitations.insert(0, "SYNTHETIC. Prices are a seeded random walk, the filings and headlines are a committed "
                              "fixture, and nothing here is a fact about any company.")
    stale = [r.ticker for r in reads.values() if r.stale_days is not None and r.stale_days > 5]
    if stale:
        limitations.append(f"Closes more than five days old for: {', '.join(sorted(stale))}.")
    return {
        "built_at": built_at or dt.datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "is_real": status == "SCANNED",
        "mode": mode,
        "at": at,
        "as_of": as_of.isoformat(),
        "watched": [n.to_json() for n in names],
        "n_watched": len(names),
        "sources": sources,
        "names": rows,
        "alerts": [a.to_json() for a in alerts],
        "n_alerts": len(alerts),
        "n_rule_alerts": sum(1 for a in alerts if a.severity >= 3),
        "benchmarks": {k: v.to_json() for k, v in (bench_reads or {}).items()},
        "rules": rules_text(),
        "limitations": limitations,
        "disclaimer": DISCLAIMER,
    }
