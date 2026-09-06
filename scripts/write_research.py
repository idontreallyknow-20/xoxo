"""Write research/TICKER.md for the shortlist. Financial tables come from the fundamentals cache
(Yahoo Finance via yfinance, annual statements). Narrative sections are written by Claude from the
sources listed in each file. Run after fundamentals.py and price_screen.py.
"""
import json, datetime as dt
import pandas as pd
from config import *
from research_notes import NOTES

PULLED = dt.date.today().isoformat()
DISC = "Research and analysis from public data, not personalized financial advice."


def m(v, unit=1e9, nd=2):
    if v is None:
        return "n/a"
    return f"{v / unit:,.{nd}f}"


def pct(v):
    return "n/a" if v is None else f"{v * 100:.1f}%"


def table(tk):
    d = json.loads((FUND_CACHE / f"{tk}.json").read_text())
    rows = []
    for y, r in sorted(d["years"].items()):
        if not r.get("revenue"):
            continue
        rev = r["revenue"]
        gm = (r["gross_profit"] / rev) if r.get("gross_profit") else None
        om = (r["operating_income"] / rev) if r.get("operating_income") else None
        nd = r.get("net_debt")
        if nd is None:
            nd = (r.get("total_debt") or 0) - (r.get("cash") or 0)
        sh = r.get("diluted_shares") or r.get("shares_outstanding")
        rows.append(f"| FY{y[:4]} ({y}) | {m(rev)} | {pct(gm)} | {pct(om)} | {m(r.get('fcf'))} | {m(sh, 1e6, 0)} | {m(nd)} |")
    ccy = d["info"].get("financialCurrency") or "USD"
    hdr = (f"| Fiscal year end | Revenue ({ccy} bn) | Gross margin | Operating margin | Free cash flow ({ccy} bn) | Diluted shares (m) | Net debt ({ccy} bn, negative = net cash) |\n"
           "|---|---|---|---|---|---|---|")
    return hdr + "\n" + "\n".join(rows) + f"\n\nSource: annual statements, Yahoo Finance via yfinance, pulled {d['pulled']}. Four fiscal years is all the source provides."


def main():
    ps = pd.read_csv(UNIVERSE_DIR / "price_screen_latest.csv").set_index("ticker")
    for tk, n in NOTES.items():
        r = ps.loc[tk]
        info = json.loads((FUND_CACHE / f"{tk}.json").read_text())["info"]
        bucket = "compounder" if r["bucket_compounder"] else ""
        if r["bucket_cyclical_turn"]:
            bucket = (bucket + " and " if bucket else "") + "cyclical turn"
        screen = (f"Quality rank {int(r['rank'])} of 150, score {r['quality_score']:.0f}. Bucket: {bucket or 'none'}. "
                  f"Price {r['price']:.2f} {info.get('currency','USD')} on {r['pulled']}. "
                  f"Forward P/E {r['forward_pe']:.1f} vs own 4 year median {r['median_pe_hist']:.1f} ({r['pe_vs_median']*100:+.0f}%). "
                  f"EV/EBITDA {r['ev_ebitda']:.1f}. {r['dd_52w']*100:.0f}% from 52 week high, {r['dd_ath']*100:.0f}% from all time high. "
                  f"Next fiscal year EPS estimate moved {r['eps_fy1_chg_90d']*100:+.1f}% in 90 days. Short interest {r['short_pct_float']*100:.1f}% of float. "
                  f"Next earnings {r['next_earnings']}. Screen data: Yahoo Finance via yfinance, pulled {r['pulled']}.")
        md = f"""# {tk}  {info.get('longName')}

{screen}

## What the company does
{n['what']}

## Why it is on the list
{n['why']}

## Business quality
{n['quality']}

## Financials
{table(tk)}

## Valuation
{n['valuation']}

## Last two earnings reports
{n['calls']}

## The bear case
{n['bear']}

## Key risks and thesis killers
{n['risks']}

## Verdict
{n['verdict']}

## Sources
{n['sources']}
- Screen and statement data: Yahoo Finance via yfinance, pulled {PULLED}

{DISC}
"""
        (RESEARCH_DIR / f"{tk}.md").write_text(md, encoding="utf-8")
        print("wrote", tk)


if __name__ == "__main__":
    main()
