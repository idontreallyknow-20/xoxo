"""Step 3: price screen on the quality top 150. Flags two buckets.

Per name:
  forward P/E and EV/EBITDA vs the company's own historical median (fiscal year ends,
     4 years available from yfinance, so this is a 4 year median, not 5)
  drawdown from 52 week high and all time high
  EPS revision direction over 90 days (consensus current FY and next FY)
  short interest (% of float)

Buckets:
  compounder      forward P/E <= own median P/E  OR  EV/EBITDA <= own median (quality already top 150)
  cyclical_turn   cyclical sector, 35%+ below all time high, and forward EPS estimates flat or rising
                  over the last 30 days (they stopped falling)

Writes universe/price_screen_YYYY-MM-DD.csv, universe/price_screen_latest.csv,
       universe/bucket_compounders.csv, universe/bucket_cyclical_turns.csv
"""
import json, time, datetime as dt, math
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np, pandas as pd, yfinance as yf
from config import *

CYCLICAL_INDUSTRY_WORDS = ("semiconductor", "steel", "aluminum", "copper", "chemical", "oil", "gas",
                           "coal", "mining", "industrial", "machinery", "farm", "construction",
                           "auto", "truck", "aerospace", "railroad", "shipping", "marine", "airlines",
                           "building", "lumber", "paper", "packaging", "electrical equipment",
                           "engineering", "metal", "energy", "drilling", "equipment", "agricultural")
CYCLICAL_SECTORS = ("Technology", "Industrials", "Basic Materials", "Energy", "Consumer Cyclical")

def hist_multiples(tk, fund):
    """Historical P/E and EV/EBITDA at each fiscal year end using price history."""
    yrs = {y: r for y, r in fund.get("years", {}).items() if r.get("revenue")}
    if not yrs:
        return [], [], None, None
    t = yf.Ticker(tk)
    h = t.history(period="max", auto_adjust=False)
    if h is None or h.empty:
        return [], [], None, None
    close = h["Close"].dropna()
    ath = float(close.max())
    last = float(close.iloc[-1])
    pes, evs = [], []
    for y, r in yrs.items():
        d = pd.Timestamp(y).tz_localize(close.index.tz) if close.index.tz is not None else pd.Timestamp(y)
        px_s = close[:d]
        if px_s.empty:
            continue
        px = float(px_s.iloc[-1])
        eps = r.get("diluted_eps")
        if eps and eps > 0:
            pes.append(px / eps)
        sh, e, nd = r.get("diluted_shares") or r.get("shares_outstanding"), r.get("ebitda"), r.get("net_debt")
        if nd is None:
            nd = (r.get("total_debt") or 0) - (r.get("cash") or 0)
        if sh and e and e > 0:
            evs.append((px * sh + nd) / e)
    return pes, evs, ath, last

def one(row):
    tk = row["ticker"]
    fund = json.loads((FUND_CACHE / f"{tk}.json").read_text())
    info = fund.get("info", {})
    out = {"ticker": tk}
    try:
        t = yf.Ticker(tk)
        trend = t.eps_trend
        rev = t.eps_revisions
        for period, label in (("0y", "fy0"), ("+1y", "fy1")):
            if trend is not None and period in trend.index:
                cur, d30, d90 = trend.loc[period, "current"], trend.loc[period, "30daysAgo"], trend.loc[period, "90daysAgo"]
                out[f"eps_{label}_now"] = cur
                out[f"eps_{label}_chg_30d"] = (cur / d30 - 1) if d30 and abs(d30) > 0 else np.nan
                out[f"eps_{label}_chg_90d"] = (cur / d90 - 1) if d90 and abs(d90) > 0 else np.nan
            if rev is not None and period in rev.index:
                out[f"rev_up30_{label}"] = rev.loc[period, "upLast30days"]
                out[f"rev_down30_{label}"] = rev.loc[period, "downLast30days"]
    except Exception as e:
        out["eps_error"] = str(e)[:60]
    pes, evs, ath, last = hist_multiples(tk, fund)
    fin_ccy, px_ccy = info.get("financialCurrency"), info.get("currency")
    if fin_ccy and px_ccy and fin_ccy != px_ccy:
        pes, evs = [], []          # statements in one currency, price in another: historical multiples would be wrong
        out["multiples_note"] = f"statements in {fin_ccy}, price in {px_ccy}, own-history multiples skipped"
    out["price"] = last if last else info.get("currentPrice")
    out["forward_pe"] = info.get("forwardPE")
    out["ev_ebitda"] = info.get("enterpriseToEbitda")
    out["median_pe_hist"] = float(np.median(pes)) if pes else np.nan
    out["median_ev_ebitda_hist"] = float(np.median(evs)) if evs else np.nan
    out["n_hist_years"] = len(pes)
    out["pe_vs_median"] = (out["forward_pe"] / out["median_pe_hist"] - 1) if out["forward_pe"] and pes and out["median_pe_hist"] > 0 else np.nan
    out["ev_vs_median"] = (out["ev_ebitda"] / out["median_ev_ebitda_hist"] - 1) if out["ev_ebitda"] and evs and out["median_ev_ebitda_hist"] > 0 else np.nan
    hi52 = info.get("fiftyTwoWeekHigh")
    out["high_52w"] = hi52
    out["ath"] = ath
    out["dd_52w"] = (out["price"] / hi52 - 1) if hi52 and out["price"] else np.nan
    out["dd_ath"] = (out["price"] / ath - 1) if ath and out["price"] else np.nan
    out["short_pct_float"] = info.get("shortPercentOfFloat")
    out["insider_pct"] = info.get("heldPercentInsiders")
    out["next_earnings"] = dt.datetime.fromtimestamp(info["earningsTimestampStart"]).date().isoformat() if info.get("earningsTimestampStart") else None
    return out

def main():
    today = dt.date.today().isoformat()
    top = pd.read_csv(UNIVERSE_DIR / "quality_top150.csv")
    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one, r): r["ticker"] for _, r in top.iterrows()}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                rows.append(f.result())
            except Exception as e:
                rows.append({"ticker": futs[f], "error": str(e)[:80]})
            if i % 25 == 0:
                print(f"{i}/{len(top)}", flush=True)
    ps = pd.DataFrame(rows)
    df = top.merge(ps, on="ticker", how="left", suffixes=("", "_ps"))

    cheap_pe = df["pe_vs_median"] <= 0
    cheap_ev = df["ev_vs_median"] <= 0
    df["bucket_compounder"] = (cheap_pe | cheap_ev).fillna(False)

    ind = df["industry"].fillna("").str.lower()
    cyc = df["sector"].isin(CYCLICAL_SECTORS) & ind.apply(lambda s: any(w in s for w in CYCLICAL_INDUSTRY_WORDS))
    est_stable = (df["eps_fy1_chg_30d"].fillna(0) >= -0.005) | (df["eps_fy0_chg_30d"].fillna(0) >= -0.005)
    df["bucket_cyclical_turn"] = (cyc & (df["dd_ath"] <= -0.35) & est_stable).fillna(False)
    df["pulled"] = today

    df.to_csv(UNIVERSE_DIR / f"price_screen_{today}.csv", index=False)
    df.to_csv(UNIVERSE_DIR / "price_screen_latest.csv", index=False)
    comp = df[df["bucket_compounder"]].sort_values("quality_score", ascending=False)
    cyc_df = df[df["bucket_cyclical_turn"]].sort_values("dd_ath")
    comp.to_csv(UNIVERSE_DIR / "bucket_compounders.csv", index=False)
    cyc_df.to_csv(UNIVERSE_DIR / "bucket_cyclical_turns.csv", index=False)
    cols = ["rank", "ticker", "name", "quality_score", "forward_pe", "median_pe_hist", "pe_vs_median", "ev_vs_median", "dd_ath", "eps_fy1_chg_90d", "short_pct_float"]
    print(f"\nCOMPOUNDERS ({len(comp)})"); print(comp[cols].head(40).to_string(index=False))
    print(f"\nCYCLICAL TURNS ({len(cyc_df)})"); print(cyc_df[cols].to_string(index=False))

if __name__ == "__main__":
    main()
