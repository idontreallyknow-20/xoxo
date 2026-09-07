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
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

__all__ = ["load_inputs", "build_digest", "render_html", "subject_for", "should_send", "MAX_HEADLINES"]

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


def load_inputs(root: Optional[Path] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    d = (root or paths.DASHBOARD_DIR)
    return {"watch": _read(d / "watch.json"), "tracker": _read(d / "tracker.json"),
            "memo": _read(d / "positioning.json")}


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


def build_digest(inputs: Dict[str, Optional[Dict[str, Any]]], *, today: Optional[dt.date] = None) -> Dict[str, Any]:
    """Everything the email says, as data. ``render_html`` only lays it out."""
    today = today or dt.date.today()
    w = inputs.get("watch") or {}
    t = inputs.get("tracker") or {}
    m = inputs.get("memo") or {}
    scanned = bool(w) and w.get("status") == "SCANNED"
    synthetic = bool(w) and w.get("status") == "SYNTHETIC"

    alerts = list(w.get("alerts") or [])
    rule_alerts = [a for a in alerts if a.get("severity", 0) >= 3]
    rows: List[Dict[str, Any]] = []
    filings: List[Dict[str, Any]] = []
    headlines: List[Dict[str, Any]] = []
    for r in w.get("names") or []:
        p = r.get("price") or {}
        rows.append({
            "ticker": r["ticker"], "held": bool(r.get("held")), "action": r.get("action"),
            "close": p.get("last_close"), "close_date": p.get("last_date"),
            "chg_1d": p.get("chg_1d"), "chg_5d": p.get("chg_5d"), "since_call": p.get("since_call"),
            "trigger": r.get("trigger"), "to_trigger": p.get("to_trigger"), "breached": p.get("breached"),
            "max_severity": r.get("max_severity", 0),
            "stale": (p.get("stale_days") or 0) > 5,
        })
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

    if not w:
        status = "NO SCAN FILE"
        lead = "No watch.json exists. Run python scripts/scan.py first."
    elif scanned:
        if rule_alerts:
            lead = (f"{len(rule_alerts)} of your written rules fired across {len({a['ticker'] for a in rule_alerts})} "
                    f"name{'s' if len({a['ticker'] for a in rule_alerts}) != 1 else ''}. Details below.")
        elif alerts:
            lead = f"Nothing crossed a written rule. {len(alerts)} line{'s' if len(alerts) != 1 else ''} worth a glance."
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
        "sources": w.get("sources") or {},
        "limitations": list(w.get("limitations") or []),
        "rules": list(w.get("rules") or []),
        "disclaimer": DISCLAIMER,
    }


def subject_for(d: Dict[str, Any]) -> str:
    day = dt.date.fromisoformat(d["date"]).strftime("%a %d %b").replace(" 0", " ")
    if d["status"] == "SCANNED":
        if d["n_rule_alerts"]:
            names = sorted({a["ticker"] for a in d["alerts"] if a.get("severity", 0) >= 3})
            return f"Desk {day}: {d['n_rule_alerts']} rule{'s' if d['n_rule_alerts'] != 1 else ''} fired ({', '.join(names[:4])}{'...' if len(names) > 4 else ''})"
        return f"Desk {day}: nothing crossed a rule"
    if d["status"] == "SYNTHETIC":
        return f"Desk {day}: SYNTHETIC digest, not real"
    return f"Desk {day}: no scan has run"


def should_send(d: Dict[str, Any], *, only_if_alerts: bool) -> bool:
    if not only_if_alerts:
        return True
    return d["status"] == "SCANNED" and d["n_rule_alerts"] > 0


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
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Every watched name", "close, one day, five days, since the call, and room above the falsifier")}'
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

    standing = "".join(f'<div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};margin-bottom:8px;">{_esc(s)}</div>'
                       for s in (d["tracker_line"], d["memo_line"]) if s)
    if standing:
        parts.append(f'<tr><td style="padding-top:36px;">{_h("Standing state")}{standing}</td></tr>')

    src = "".join(f'<div>{_esc(k)}: {"live" if v.get("live") else "not live"}, {_esc(v.get("detail"))}</div>'
                  for k, v in d["sources"].items())
    lims = "".join(f'<li style="margin-bottom:4px;">{_esc(s)}</li>' for s in d["limitations"])
    parts.append(f'<tr><td style="padding-top:36px;">{_h("Where this came from")}'
                 f'<div style="font-family:{MONO};font-size:12px;color:{INK2};line-height:1.7;">{src or "no sources"}</div>'
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
      <td style="font-family:{SANS};font-size:20px;font-weight:700;color:{INK};">Desk</td>
      <td align="right" style="font-family:{MONO};font-size:12px;color:{INK2};">{_esc(d["date_text"])}{(" &nbsp; closes as of " + _esc(d["as_of"])) if d.get("as_of") else ""}</td>
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
