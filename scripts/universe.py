"""Step 1: build the universe CSV.

US + Canadian listed common stocks, market cap > $2B, average daily dollar volume > $10M,
listed at least 3 years, no CDRs, no SPACs, no preferreds. Chinese ADRs are removed in
quality_screen.py once we have the country field (the screener does not expose it).

Output: universe/universe_YYYY-MM-DD.csv and universe/universe_latest.csv
"""
import sys, time, datetime as dt
import pandas as pd
import yfinance as yf
from config import *

def usdcad():
    try:
        h = yf.Ticker("CAD=X").history(period="5d")["Close"].dropna()
        return float(h.iloc[-1])
    except Exception as e:
        print("Could not fetch USDCAD, assuming 1.37:", e)
        return 1.37

def pull_all():
    q = yf.EquityQuery("and", [
        yf.EquityQuery("gt", ["intradaymarketcap", MIN_MARKET_CAP_USD]),
        yf.EquityQuery("or", [yf.EquityQuery("eq", ["region", "us"]),
                              yf.EquityQuery("eq", ["region", "ca"])]),
    ])
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        for attempt in range(4):
            try:
                r = yf.screen(q, offset=offset, size=250, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as e:
                print(f"retry {attempt} at offset {offset}: {e}")
                time.sleep(3 * (attempt + 1))
        else:
            raise SystemExit("screener kept failing")
        total = r.get("total", 0)
        quotes = r.get("quotes", [])
        if not quotes:
            break
        rows.extend(quotes)
        offset += len(quotes)
        print(f"  {offset}/{total}", end="\r")
        time.sleep(0.6)
    print()
    return pd.DataFrame(rows)

def main():
    today = dt.date.today().isoformat()
    fx = usdcad()
    raw = pull_all()
    raw.to_csv(UNIVERSE_DIR / f"screener_raw_{today}.csv", index=False)
    n0 = len(raw)

    df = raw.copy()
    df = df[df["quoteType"] == "EQUITY"]
    df = df[df["exchange"].isin(ALLOWED_EXCHANGES)]
    df = df[~df["symbol"].str.contains(r"[-^]", regex=True)]          # preferreds, warrants, units
    name = df["longName"].fillna(df["shortName"]).fillna("").str.lower()
    df = df[~name.str.contains("|".join(SPAC_WORDS))]

    # dollar volume in USD
    px = df["regularMarketPrice"].astype(float)
    vol = df["averageDailyVolume3Month"].fillna(df["averageDailyVolume10Day"]).astype(float)
    is_cad = df["currency"].eq("CAD")
    df["dollar_volume_usd"] = px * vol / is_cad.map({True: fx, False: 1.0})
    df["market_cap_usd"] = df["marketCap"].astype(float) / is_cad.map({True: fx, False: 1.0})
    df = df[df["dollar_volume_usd"] >= MIN_DOLLAR_VOLUME_USD]
    df = df[df["market_cap_usd"] >= MIN_MARKET_CAP_USD]

    # 3 years listed (proxy for 3 years of filings)
    cutoff = (dt.datetime.now() - dt.timedelta(days=365 * MIN_YEARS_LISTED)).timestamp() * 1000
    df = df[df["firstTradeDateMilliseconds"].fillna(0) <= cutoff]

    # dual listings: keep the most liquid listing per company
    df["company_key"] = df["messageBoardId"].fillna(df["longName"].fillna(df["symbol"]))
    df = df.sort_values("dollar_volume_usd", ascending=False).drop_duplicates("company_key")

    out = pd.DataFrame({
        "ticker": df["symbol"],
        "name": df["longName"].fillna(df["shortName"]),
        "exchange": df["exchange"],
        "currency": df["currency"],
        "price": px.loc[df.index],
        "market_cap_usd": df["market_cap_usd"].round(0),
        "dollar_volume_usd": df["dollar_volume_usd"].round(0),
        "first_trade": pd.to_datetime(df["firstTradeDateMilliseconds"], unit="ms", errors="coerce").dt.date,
        "fifty_two_week_high": df.get("fiftyTwoWeekHigh"),
        "pulled": today,
        "source": "Yahoo Finance screener via yfinance",
    }).sort_values("market_cap_usd", ascending=False)

    out.to_csv(UNIVERSE_DIR / f"universe_{today}.csv", index=False)
    out.to_csv(UNIVERSE_DIR / "universe_latest.csv", index=False)
    print(f"raw {n0} -> universe {len(out)} names  (USDCAD {fx:.3f})")
    print(out["exchange"].value_counts().to_string())

if __name__ == "__main__":
    main()
