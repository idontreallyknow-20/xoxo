"""Render the current picks as a self contained HTML email (table layout, inline styles).
Writes dashboard/email_picks.html. Animations are CSS keyframes that play in Apple Mail and
iOS Mail; Gmail strips them and shows the static layout, which is designed to stand on its own.
"""
import json, re, datetime as dt
from config import *

D = json.loads(re.sub(r"^window\.DASH = |;$", "", (DASHBOARD_DIR / "data.js").read_text(encoding="utf-8")))
P = D["picks"]
today = dt.date.today().strftime("%B %d, %Y").replace(" 0", " ")

BG, INK, INK2, INK3, RULE, SOFT, MARK, UP, TRACK = "#ffffff", "#121212", "#5b5b57", "#8f8f89", "#d9d9d3", "#ecece9", "#fff27a", "#0b7a3b", "#e4e4e0"
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'Courier New', Courier, monospace"

def money(v, dec=0):
    return f"{v:,.{dec}f}"

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def zone_bar(p):
    price, lo, hi = p["price"], p["low"], p["high"]
    span = max(hi * 1.3, (price or 0) * 1.12); lo0 = min(lo * 0.75, (price or lo) * 0.88)
    f = lambda v: max(0, min(100, (v - lo0) / (span - lo0) * 100))
    a, b, m = f(lo), f(hi), f(price) if price else None
    # three segment track: before band, band, after band. marker drawn as a thin cell inside whichever segment holds it.
    def seg(w, color, marker_at=None):
        if marker_at is None or w <= 0:
            return f'<td width="{w:.1f}%" bgcolor="{color}" style="background-color:{color};height:10px;font-size:0;line-height:0;">&nbsp;</td>'
        left = (marker_at / w) * 100
        return (f'<td width="{w:.1f}%" style="height:10px;font-size:0;line-height:0;padding:0;"><table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="height:10px"><tr>'
                f'<td width="{left:.1f}%" bgcolor="{color}" style="background-color:{color};height:10px;font-size:0;">&nbsp;</td><td width="3" bgcolor="{UP if p["in_zone"] else INK}" style="background-color:{UP if p["in_zone"] else INK};height:10px;font-size:0;">&nbsp;</td>'
                f'<td bgcolor="{color}" style="background-color:{color};height:10px;font-size:0;">&nbsp;</td></tr></table></td>')
    band_color = "#9a9a94"
    cells = ""
    if m is not None and m < a:
        cells += seg(a, TRACK, m) + seg(b - a, band_color) + seg(100 - b, TRACK)
    elif m is not None and m <= b:
        cells += seg(a, TRACK) + seg(b - a, band_color, m - a) + seg(100 - b, TRACK)
    elif m is not None:
        cells += seg(a, TRACK) + seg(b - a, band_color) + seg(100 - b, TRACK, m - b)
    else:
        cells += seg(a, TRACK) + seg(b - a, band_color) + seg(100 - b, TRACK)
    return f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" class="zone" style="height:10px;"><tr>{cells}</tr></table>'

def conviction(n):
    cells = "".join(f'<td width="7" height="7" bgcolor="{INK if i < n else TRACK}" style="background-color:{INK if i < n else TRACK};font-size:0;line-height:0;">&nbsp;</td><td width="3" style="font-size:0;">&nbsp;</td>' for i in range(5))
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="display:inline-table;vertical-align:middle;margin-left:8px;"><tr>{cells}</tr></table>'

rows = ""
for i, p in enumerate(P):
    status = "in the buy zone" if p["in_zone"] else ("above the zone, wait" if p["price"] and p["price"] > p["high"] else "below the zone, check the thesis")
    scolor = UP if p["in_zone"] else INK3
    rows += f'''
<tr class="pick" style="animation-delay:{120 + i * 90}ms">
<td style="padding:22px 0 20px;border-bottom:1px solid {SOFT};">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
  <td width="34" valign="top" style="font-family:{MONO};font-size:13px;color:{INK3};padding-top:4px;">{i + 1}</td>
  <td valign="top">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td valign="top" width="46%" style="padding-right:16px;">
        <div style="font-family:{SANS};font-size:20px;font-weight:700;color:{INK};letter-spacing:-0.01em;">{esc(p["ticker"])} {conviction(p["conviction"] or 0)}</div>
        <div style="font-family:{SANS};font-size:13px;color:{INK2};margin-top:2px;">{esc(p["name"] or "")}</div>
        <div style="font-family:{SANS};font-size:13px;color:{INK2};margin-top:6px;"><b style="color:{INK};font-weight:600;">{esc(p["action"])}</b>, {esc(p["bucket"])}</div>
      </td>
      <td valign="top" width="54%" align="right" style="font-family:{MONO};">
        <div class="amt" style="font-size:22px;color:{INK};">${money(p["usd"])}</div>
        <div style="font-size:12px;color:{INK3};margin-top:2px;">{money(p["shares"])} shares at about {money(p["price"], 2)}</div>
        <div style="font-size:12px;color:{scolor};margin-top:2px;">{status}</div>
      </td>
    </tr></table>
    <div style="height:14px;font-size:0;">&nbsp;</div>
    {zone_bar(p)}
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="font-family:{MONO};font-size:11px;color:{INK3};padding-top:7px;">buy {money(p["low"], 2)} to {money(p["high"], 2)}</td>
      <td align="right" style="font-family:{MONO};font-size:11px;color:{INK3};padding-top:7px;">now {money(p["price"], 2)}</td>
    </tr></table>
    <div style="font-family:{SANS};font-size:14px;line-height:1.55;color:{INK2};margin-top:12px;">{esc(p["thesis"])} <b style="color:{INK};font-weight:600;">Wrong if</b> {esc(p["wrong_if"])}</div>
  </td>
</tr></table>
</td></tr>'''

total = sum(p["usd"] for p in P)
html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>Desk, the picks</title>
<style>
  @keyframes rise {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: none; }} }}
  @keyframes wipe {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
  @keyframes fadein {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
  .pick {{ animation: rise 620ms cubic-bezier(0.23, 1, 0.32, 1) both; }}
  .hero {{ animation: rise 700ms cubic-bezier(0.23, 1, 0.32, 1) both; }}
  .zone {{ transform-origin: left center; animation: wipe 800ms cubic-bezier(0.23, 1, 0.32, 1) 400ms both; }}
  .amt {{ animation: fadein 600ms ease 300ms both; }}
  .rule-anim {{ transform-origin: left center; animation: wipe 900ms cubic-bezier(0.23, 1, 0.32, 1) both; }}
  @media (prefers-reduced-motion: reduce) {{ .pick, .hero, .zone, .amt, .rule-anim {{ animation: none; }} }}
  @media (max-width: 600px) {{ .wrap {{ padding: 20px 16px !important; }} }}
</style></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{BG};"><tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" width="680" class="wrap" style="max-width:680px;width:100%;padding:36px 32px 48px;">
  <tr><td>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="font-family:{SANS};font-size:20px;font-weight:700;color:{INK};">Desk</td>
      <td align="right" style="font-family:{MONO};font-size:12px;color:{INK2};">picks as of {today} &nbsp; USD</td>
    </tr></table>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:12px;"><tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td></tr>
  <tr><td class="hero" style="padding-top:34px;">
    <div style="font-family:{SANS};font-size:64px;font-weight:300;letter-spacing:-0.035em;line-height:0.95;color:{INK};"><span style="font-size:26px;color:{INK3};vertical-align:top;line-height:1.6;">$</span>{money(total)}</div>
    <div style="font-family:{MONO};font-size:13px;color:{INK2};margin-top:14px;">{len(P)} names &nbsp; ${money(D["starting_cash"] - total)} kept as cash &nbsp; bought over September, October and November</div>
  </td></tr>
  <tr><td style="padding-top:44px;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>
      <td style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};">Top picks</td>
      <td align="right" style="font-family:{MONO};font-size:12px;color:{INK3};">the bar is the buy zone, the tick is today's price</td>
    </tr></table>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:6px;"><tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td></tr>
  {rows}
  <tr><td style="padding-top:36px;">
    <div style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};">Looked at, not bought</div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:6px;margin-bottom:12px;"><tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>
    <div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};">
      <b style="color:{INK};font-weight:600;">Meta</b> is spending all of its cash flow on AI data centres. <b style="color:{INK};font-weight:600;">ServiceNow</b> carries the same AI risk as Adobe at twice the price.
      <b style="color:{INK};font-weight:600;">Accenture</b> is signing less business each quarter. <b style="color:{INK};font-weight:600;">Applied Materials</b> already doubled this year. <b style="color:{INK};font-weight:600;">NVR</b> is a homebuilder with margins falling into rising rates.
    </div>
  </td></tr>
  <tr><td style="padding-top:36px;">
    <div style="font-family:{SANS};font-size:15px;font-weight:600;color:{INK};">How to read it</div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-top:6px;margin-bottom:12px;"><tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>
    <div style="font-family:{SANS};font-size:14px;line-height:1.6;color:{INK2};">
      Every name here passed a quality screen of 1,895 US and Canadian companies (return on capital, cash generation, growth, debt, share count, margin steadiness) and then a price screen against its own history.
      The squares next to the ticker are conviction out of five. The buy zone is the price range worth paying; the tick shows where the price is today.
      Nothing is bought all at once: about $25,000 a month over three months, and $30,000 stays in cash. "Wrong if" is the number or event that would mean the idea failed, decided before buying so it cannot be rationalised later.
    </div>
  </td></tr>
  <tr><td style="padding-top:40px;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom:12px;"><tr><td height="1" bgcolor="{INK}" style="background-color:{INK};font-size:0;line-height:0;">&nbsp;</td></tr></table>
    <div style="font-family:{SANS};font-size:12px;color:{INK3};">Research and analysis from public data, not personalized financial advice.</div>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''
out = DASHBOARD_DIR / "email_picks.html"
out.write_text(html, encoding="utf-8")
print("wrote", out, len(html), "chars")
