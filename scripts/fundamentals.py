"""Pull and cache fundamentals for every ticker in the universe.

Each ticker gets data/cache/fundamentals/TICKER.json with:
  pulled (date), info subset, and per fiscal year: revenue, gross profit, EBIT, EBITDA,
  tax rate, invested capital, free cash flow, diluted shares, net debt.
Cached files younger than FUNDAMENTALS_MAX_AGE_DAYS are not re-pulled.

Run:  python scripts/fundamentals.py            (whole universe)
      python scripts/fundamentals.py AAPL MSFT  (specific tickers, forces refresh)
"""
import sys, json, time, datetime as dt, math
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import yfinance as yf
from config import *

INFO_KEYS = ["longName", "sector", "industry", "country", "currency", "financialCurrency",
             "marketCap", "currentPrice", "forwardPE", "trailingPE", "enterpriseValue",
             "enterpriseToEbitda", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "shortPercentOfFloat",
             "sharesShort", "sharesOutstanding", "heldPercentInsiders", "totalDebt", "totalCash",
             "ebitda", "returnOnEquity", "returnOnAssets", "grossMargins", "operatingMargins",
             "freeCashflow", "priceToBook", "dividendYield", "longBusinessSummary", "website",
             "fullTimeEmployees", "earningsTimestamp", "earningsTimestampStart"]

def first_row(df, names):
    """Return the first matching row (as a Series) from a statement DataFrame."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None

def clean(x):
    if x is None:
        return None
    try:
        if isinstance(x, (float, int)) and (math.isnan(x) or math.isinf(x)):
            return None
    except TypeError:
        pass
    if hasattr(x, "item"):
        try:
            x = x.item()
        except Exception:
            pass
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x

def pull_one(ticker):
    t = yf.Ticker(ticker)
    info = {}
    try:
        raw = t.info or {}
        info = {k: clean(raw.get(k)) for k in INFO_KEYS}
    except Exception as e:
        info = {"error": str(e)}
    inc, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
    years = {}
    cols = list(inc.columns) if inc is not None and not inc.empty else []
    rows = {
        "revenue": first_row(inc, ["Total Revenue", "Operating Revenue"]),
        "gross_profit": first_row(inc, ["Gross Profit"]),
        "operating_income": first_row(inc, ["Operating Income", "Total Operating Income As Reported"]),
        "ebit": first_row(inc, ["EBIT", "Operating Income"]),
        "ebitda": first_row(inc, ["EBITDA", "Normalized EBITDA"]),
        "net_income": first_row(inc, ["Net Income Common Stockholders", "Net Income"]),
        "tax_rate": first_row(inc, ["Tax Rate For Calcs"]),
        "diluted_eps": first_row(inc, ["Diluted EPS"]),
        "diluted_shares": first_row(inc, ["Diluted Average Shares", "Basic Average Shares"]),
        "invested_capital": first_row(bs, ["Invested Capital"]),
        "net_debt": first_row(bs, ["Net Debt"]),
        "total_debt": first_row(bs, ["Total Debt"]),
        "cash": first_row(bs, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]),
        "equity": first_row(bs, ["Stockholders Equity", "Common Stock Equity"]),
        "shares_outstanding": first_row(bs, ["Ordinary Shares Number", "Share Issued"]),
        "fcf": first_row(cf, ["Free Cash Flow"]),
        "ocf": first_row(cf, ["Operating Cash Flow"]),
        "capex": first_row(cf, ["Capital Expenditure"]),
        "buybacks": first_row(cf, ["Repurchase Of Capital Stock"]),
    }
    for c in cols:
        y = str(pd.Timestamp(c).date())
        years[y] = {}
        for k, s in rows.items():
            v = None
            if s is not None and c in s.index:
                v = clean(s[c])
            years[y][k] = v
    return {"ticker": ticker, "pulled": dt.date.today().isoformat(),
            "source": "Yahoo Finance via yfinance (annual statements)", "info": info, "years": years}

def is_fresh(path):
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
        has_years = any(r.get("revenue") for r in d.get("years", {}).values())
        if not has_years:
            return False   # an empty pull (rate limited) is never fresh
        return (dt.date.today() - dt.date.fromisoformat(d["pulled"])).days < FUNDAMENTALS_MAX_AGE_DAYS
    except Exception:
        return False

def work(ticker, force=False):
    path = FUND_CACHE / f"{ticker}.json"
    if not force and is_fresh(path):
        return ticker, "cached"
    for attempt in range(3):
        try:
            data = pull_one(ticker)
            if not any(r.get("revenue") for r in data["years"].values()):
                raise RuntimeError("empty statements, probably rate limited")
            path.write_text(json.dumps(data, indent=1, default=str))
            return ticker, "pulled"
        except Exception as e:
            time.sleep(8 * (attempt + 1))
            err = str(e)
    return ticker, f"failed: {err[:80]}"

def main():
    args = sys.argv[1:]
    force = bool(args)
    if args:
        tickers = args
    else:
        tickers = pd.read_csv(UNIVERSE_DIR / "universe_latest.csv")["ticker"].tolist()
    print(f"{len(tickers)} tickers, {WORKERS} workers")
    done, failed, t0 = 0, [], time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, tk, force) for tk in tickers]
        for f in as_completed(futs):
            tk, status = f.result()
            done += 1
            if status.startswith("failed"):
                failed.append((tk, status))
            if done % 25 == 0 or done == len(tickers):
                el = time.time() - t0
                print(f"{done}/{len(tickers)}  {el/60:.1f} min  failed {len(failed)}", flush=True)
    if failed:
        (DATA_DIR / "fundamentals_failed.csv").write_text("\n".join(f"{a},{b}" for a, b in failed))
        print("failed tickers written to data/fundamentals_failed.csv")

if __name__ == "__main__":
    main()
