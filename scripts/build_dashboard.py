"""Build dashboard/data.js for the static dashboard (open dashboard/index.html).

Reads: portfolio/holdings.csv, portfolio/cash.csv, portfolio/watchlist.csv, journal.md,
       universe/quality_top150.csv, universe/price_screen_latest.csv, fundamentals cache.
Pulls: daily closes for holdings, watchlist and benchmarks since inception (cached 1 day).
Writes: dashboard/data.js (window.DASH = {...}) and regenerates portfolio.md.
"""
import json, re, datetime as dt, math
import numpy as np, pandas as pd, yfinance as yf
from config import *


def read_csv(p, cols):
    try:
        df = pd.read_csv(p)
        return df if len(df.columns) else pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


def closes(tickers, start):
    """Daily closes since start, cached per day."""
    key = f"closes_{start}_{dt.date.today().isoformat()}_{abs(hash(tuple(sorted(tickers)))) % 10**8}.json"
    p = PRICE_CACHE / key
    if p.exists():
        d = json.loads(p.read_text())
        return pd.DataFrame(d["data"], index=pd.to_datetime(d["index"]))
    if not tickers:
        return pd.DataFrame()
    h = yf.download(tickers, start=start, auto_adjust=True, progress=False, group_by="column")
    if h is None or h.empty:
        return pd.DataFrame()
    c = h["Close"] if "Close" in h.columns.get_level_values(0) else h
    if isinstance(c, pd.Series):
        c = c.to_frame(tickers[0])
    c = c.ffill()
    p.write_text(json.dumps({"index": [str(i.date()) for i in c.index],
                             "data": {col: c[col].tolist() for col in c.columns}}, default=str))
    return c


def num(x, nd=None):
    if x is None:
        return None
    if hasattr(x, "item"):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if isinstance(x, float):
        return round(x, nd) if nd is not None else x
    return x


def parse_journal():
    txt = (ROOT / "journal.md").read_text(encoding="utf-8")
    entries = []
    pattern = r"^## (\d{4}-\d{2}-\d{2}) (\S+)\s*(.*?)$\n(.*?)(?=^## |\Z)"
    for m in re.finditer(pattern, txt, re.M | re.S):
        body = m.group(4)

        def field(name):
            mm = re.search(r"^" + name + r":\s*(.*)$", body, re.M)
            return mm.group(1).strip() if mm else None

        entries.append({"date": m.group(1), "ticker": m.group(2), "title": m.group(3).strip(),
                        "price": field("Price at call"), "thesis": field("Thesis"), "wrong_if": field("Wrong if"),
                        "size": field("Target size"), "conviction": field("Conviction"), "bucket": field("Bucket")})
    return entries[::-1]


def info_of(tk):
    p = FUND_CACHE / f"{tk}.json"
    return json.loads(p.read_text()).get("info", {}) if p.exists() else {}


def main():
    today = dt.date.today()
    holdings = read_csv(PORTFOLIO_DIR / "holdings.csv", ["ticker", "shares", "cost_basis_per_share", "currency", "date_bought", "bucket", "wrong_if_price", "notes"])
    cash_ledger = read_csv(PORTFOLIO_DIR / "cash.csv", ["date", "cash_usd", "note"]).sort_values("date")
    picks = read_csv(PORTFOLIO_DIR / "picks.csv", ["rank", "ticker", "action", "buy_zone_low", "buy_zone_high", "recommended_usd", "conviction", "bucket", "thesis", "wrong_if", "date"])
    watch = pd.DataFrame({"ticker": picks["ticker"], "buy_zone_low": picks["buy_zone_low"], "buy_zone_high": picks["buy_zone_high"],
                          "target_size_usd": picks["recommended_usd"], "bucket": picks["bucket"], "conviction": picks["conviction"],
                          "journal_date": picks["date"], "notes": picks["thesis"]})

    tickers = sorted(set(holdings["ticker"].dropna()) | set(watch["ticker"].dropna()) | set(BENCHMARKS) | {"CAD=X"})
    px = closes(tickers, INCEPTION_DATE)
    if not px.empty:
        px = px[px.index >= pd.Timestamp(INCEPTION_DATE)]
    if px.empty:
        px = pd.DataFrame(index=pd.to_datetime([today]))
    fx = px["CAD=X"].ffill() if "CAD=X" in px else pd.Series(1.37, index=px.index)
    fx_last = float(fx.dropna().iloc[-1]) if fx.dropna().size else 1.37

    # cash series: last ledger balance on or before each date
    cash_ledger["date"] = pd.to_datetime(cash_ledger["date"])
    cash_s = pd.Series(index=px.index, dtype=float)
    for d in px.index:
        rows = cash_ledger[cash_ledger["date"] <= d]
        cash_s[d] = float(rows["cash_usd"].iloc[-1]) if len(rows) else STARTING_CASH_USD
    cash_s = cash_s.ffill().fillna(STARTING_CASH_USD)

    # holdings value series in USD
    hold_val = pd.Series(0.0, index=px.index)
    hrows = []
    for _, h in holdings.iterrows():
        tk = h["ticker"]
        if tk not in px:
            continue
        s = px[tk].copy()
        s[s.index < pd.Timestamp(h["date_bought"])] = 0.0
        is_cad = str(h.get("currency", "USD")).upper() == "CAD"
        if is_cad:
            s = s / fx
        hold_val = hold_val.add((s * float(h["shares"])).fillna(0), fill_value=0)
        last_s = px[tk].dropna()
        last = float(last_s.iloc[-1]) if last_s.size else None
        conv = (1 / fx_last) if is_cad else 1.0
        cost_ps = float(h["cost_basis_per_share"])
        hrows.append({"ticker": tk, "name": info_of(tk).get("longName"), "sector": info_of(tk).get("sector"),
                      "shares": num(float(h["shares"])), "cost_per_share": num(cost_ps, 2),
                      "price": num(last, 2), "currency": h.get("currency", "USD"),
                      "value_usd": num(last * float(h["shares"]) * conv, 2) if last else None,
                      "cost_usd": num(cost_ps * float(h["shares"]) * conv, 2),
                      "pl_pct": num(last / cost_ps - 1, 4) if last else None,
                      "date_bought": str(h["date_bought"]), "bucket": h.get("bucket"),
                      "wrong_if_price": num(h.get("wrong_if_price"))})
    total_s = cash_s + hold_val
    dates = [str(d.date()) for d in px.index]

    def indexed(tk):
        if tk not in px:
            return None
        s = px[tk].dropna()
        if s.empty:
            return None
        base = float(s.iloc[0])
        return [num(v / base * STARTING_CASH_USD, 2) for v in px[tk].ffill().bfill()]

    series = {"portfolio": [num(v, 2) for v in total_s]}
    for b in BENCHMARKS:
        series[b] = indexed(b)

    def ret(arr):
        return num(arr[-1] / arr[0] - 1, 4) if arr and arr[0] else None

    scorecard = {b: {"return": ret(series[b]), "portfolio_return": ret(series["portfolio"])}
                 for b in BENCHMARKS if series.get(b)}

    # screens
    top = read_csv(UNIVERSE_DIR / "quality_top150.csv", ["ticker"])
    ps = read_csv(UNIVERSE_DIR / "price_screen_latest.csv", ["ticker"])
    src = ps if len(ps) else top
    keep = ["rank", "ticker", "name", "sector", "industry", "quality_score", "score_roic", "score_fcf", "score_growth",
            "score_balance", "score_shares", "score_gm_stability", "roic_avg", "fcf_margin_avg", "rev_cagr", "nd_to_ebitda",
            "share_change", "gm_avg", "market_cap_usd", "price", "forward_pe", "median_pe_hist", "pe_vs_median", "ev_ebitda",
            "ev_vs_median", "dd_52w", "dd_ath", "eps_fy1_chg_90d", "eps_fy1_chg_30d", "short_pct_float", "bucket_compounder",
            "bucket_cyclical_turn", "next_earnings", "currency", "exchange"]
    screen = []
    for _, r in src.iterrows():
        row = {}
        for k in keep:
            if k not in src.columns:
                continue
            v = r[k]
            if isinstance(v, (bool, np.bool_)):
                row[k] = bool(v)
            elif isinstance(v, (float, np.floating)):
                row[k] = num(float(v), 4)
            elif isinstance(v, (int, np.integer)):
                row[k] = int(v)
            else:
                row[k] = None if (v is None or (isinstance(v, float) and math.isnan(v))) else v
        screen.append(row)

    # earnings in the next 14 days for holdings + watchlist + bucket names
    held = set(holdings["ticker"].dropna())
    watched = set(watch["ticker"].dropna())
    pool = set(held) | set(watched)
    if "bucket_compounder" in ps.columns:
        pool |= set(ps[ps["bucket_compounder"] == True]["ticker"])
    if "bucket_cyclical_turn" in ps.columns:
        pool |= set(ps[ps["bucket_cyclical_turn"] == True]["ticker"])
    earnings = []
    for tk in sorted(pool):
        i = info_of(tk)
        ts = i.get("earningsTimestampStart")
        if ts:
            d = dt.datetime.fromtimestamp(ts).date()
            if 0 <= (d - today).days <= 14:
                earnings.append({"ticker": tk, "date": d.isoformat(), "name": i.get("longName"),
                                 "held": tk in held, "watch": tk in watched})
    earnings.sort(key=lambda e: e["date"])

    wrows = []
    for _, w in watch.iterrows():
        tk = w["ticker"]
        last_s = px[tk].dropna() if tk in px else pd.Series(dtype=float)
        last = float(last_s.iloc[-1]) if last_s.size else None
        lo, hi = float(w["buy_zone_low"]), float(w["buy_zone_high"])
        wrows.append({"ticker": tk, "name": info_of(tk).get("longName"), "low": lo, "high": hi,
                      "price": num(last, 2), "size": num(float(w["target_size_usd"])), "bucket": w.get("bucket"),
                      "conviction": num(w.get("conviction")), "in_zone": bool(last is not None and lo <= last <= hi),
                      "notes": None if pd.isna(w.get("notes")) else w.get("notes")})

    prow = []
    for _, w in picks.sort_values("rank").iterrows():
        tk = w["ticker"]
        last_s = px[tk].dropna() if tk in px else pd.Series(dtype=float)
        last = float(last_s.iloc[-1]) if last_s.size else None
        lo, hi, usd = float(w["buy_zone_low"]), float(w["buy_zone_high"]), float(w["recommended_usd"])
        shares = int(usd // last) if last else None
        prow.append({"rank": int(w["rank"]), "ticker": tk, "name": info_of(tk).get("longName"), "action": w["action"], "low": lo, "high": hi,
                     "usd": usd, "shares": shares, "price": num(last, 2), "conviction": num(w.get("conviction")), "bucket": w.get("bucket"),
                     "thesis": w.get("thesis"), "wrong_if": w.get("wrong_if"), "in_zone": bool(last is not None and lo <= last <= hi), "date": str(w.get("date"))})
    # one year of closes for the charts tab: holdings, picks, benchmarks
    hist_tk = sorted(set(holdings["ticker"].dropna()) | set(picks["ticker"].dropna()) | set(BENCHMARKS))
    history = {}
    try:
        h1 = closes(hist_tk, (today - dt.timedelta(days=365)).isoformat())
        for tk in hist_tk:
            if tk in h1:
                ser = h1[tk].dropna()
                history[tk] = {"dates": [str(d.date()) for d in ser.index], "close": [round(float(v), 2) for v in ser]}
    except Exception as e:
        print("history skipped:", e)
    uni_count = len(read_csv(UNIVERSE_DIR / "universe_latest.csv", ["ticker"]))
    scored_count = len(read_csv(UNIVERSE_DIR / "quality_scores_latest.csv", ["ticker"]))
    data = {
        "as_of": today.isoformat(), "inception": INCEPTION_DATE, "starting_cash": STARTING_CASH_USD,
        "total_value": num(float(total_s.iloc[-1]), 2), "cash": num(float(cash_s.iloc[-1]), 2),
        "holdings_value": num(float(hold_val.iloc[-1]), 2),
        "holdings": hrows, "dates": dates, "series": series, "scorecard": scorecard, "benchmarks": BENCHMARKS,
        "screen": screen, "watchlist": wrows, "picks": prow, "earnings": earnings, "journal": parse_journal(),
        "counts": {"universe": uni_count, "scored": scored_count, "top": len(top),
                   "compounders": int(ps["bucket_compounder"].sum()) if "bucket_compounder" in ps.columns else 0,
                   "cyclical_turns": int(ps["bucket_cyclical_turn"].sum()) if "bucket_cyclical_turn" in ps.columns else 0},
        "usdcad": round(fx_last, 4), "source": "Yahoo Finance via yfinance", "history": history,
        "built_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    (DASHBOARD_DIR / "data.js").write_text("window.DASH = " + json.dumps(data, default=str) + ";", encoding="utf-8")

    # portfolio.md
    L = ["# Portfolio", "", f"As of {today}. Source: {data['source']}. USDCAD {data['usdcad']}.", "",
         f"Total value: ${data['total_value']:,.0f}. Cash: ${data['cash']:,.0f}. Invested: ${data['holdings_value']:,.0f}.", "",
         "## Holdings", "",
         "| Ticker | Shares | Cost/share | Price | Value USD | P/L | Bought | Bucket | Wrong if |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in hrows:
        L.append(f"| {r['ticker']} | {r['shares']} | {r['cost_per_share']} | {r['price']} | {r['value_usd'] or 0:,.0f} | {(r['pl_pct'] or 0) * 100:.1f}% | {r['date_bought']} | {r['bucket']} | {r['wrong_if_price']} |")
    if not hrows:
        L.append("| none yet | | | | | | | | |")
    L += ["", f"## Scorecard since {INCEPTION_DATE}", "", "| Benchmark | Benchmark return | Portfolio return | Difference |", "|---|---|---|---|"]
    for b, s in scorecard.items():
        if s["return"] is not None and s["portfolio_return"] is not None:
            L.append(f"| {b} | {s['return'] * 100:.2f}% | {s['portfolio_return'] * 100:.2f}% | {(s['portfolio_return'] - s['return']) * 100:+.2f} pts |")
    L += ["", "## Watchlist", "", "| Ticker | Buy zone | Price | In zone | Size | Bucket |", "|---|---|---|---|---|---|"]
    for w in wrows:
        L.append(f"| {w['ticker']} | {w['low']} to {w['high']} | {w['price']} | {'yes' if w['in_zone'] else 'no'} | ${w['size'] or 0:,.0f} | {w['bucket']} |")
    if not wrows:
        L.append("| empty | | | | | |")
    L += ["", "Research and analysis from public data, not personalized financial advice.", ""]
    (ROOT / "portfolio.md").write_text("\n".join(L), encoding="utf-8")
    print(f"dashboard/data.js written. total ${data['total_value']:,.0f}, {len(hrows)} holdings, "
          f"{len(screen)} screened names, {len(earnings)} earnings within 14 days")


if __name__ == "__main__":
    main()
