"""Step 2: score every universe name 0 to 100 on quality. Keep the top 150.

Reads the fundamentals cache (run fundamentals.py first). Writes:
  universe/quality_scores_YYYY-MM-DD.csv   every scored name with all inputs
  universe/quality_top150.csv               the keepers
  universe/excluded_YYYY-MM-DD.csv          names dropped and why

Scoring weights and rules live in criteria.md. Change them there AND here.
"""
import json, datetime as dt, math
import numpy as np, pandas as pd
from config import *

WEIGHTS = {"roic": 25, "fcf": 20, "growth": 15, "balance": 15, "shares": 10, "gm_stability": 15}
FINANCIAL_INDUSTRY_WORDS = ("bank", "insurance", "capital markets", "mortgage", "credit services",
                            "asset management", "financial data", "financial conglomerates")

def pct_rank(s):
    """0..100 percentile rank, NaN stays NaN."""
    return s.rank(pct=True) * 100

def metrics_for(d):
    info = d.get("info", {})
    yrs = sorted(d.get("years", {}).items())
    yrs = [(y, r) for y, r in yrs if r.get("revenue")]
    out = {"ticker": d["ticker"], "name": info.get("longName"), "sector": info.get("sector"),
           "industry": info.get("industry"), "country": info.get("country"),
           "market_cap": info.get("marketCap"), "n_years": len(yrs),
           "fiscal_years": ",".join(y[:4] for y, _ in yrs)}
    if len(yrs) < 3:
        out["exclude_reason"] = f"only {len(yrs)} years of statements"
        return out
    ind = (info.get("industry") or "").lower()
    if any(w in ind for w in FINANCIAL_INDUSTRY_WORDS):
        out["exclude_reason"] = "bank/insurer/lender: ROIC, EBITDA and FCF screens do not apply"
        return out
    if info.get("country") in EXCLUDED_COUNTRIES:
        out["exclude_reason"] = f"country {info.get('country')}"
        return out

    rev = np.array([r["revenue"] for _, r in yrs], dtype=float)
    def col(k):
        return np.array([r.get(k) if r.get(k) is not None else np.nan for _, r in yrs], dtype=float)
    gp, ebit, ebitda, fcf = col("gross_profit"), col("ebit"), col("ebitda"), col("fcf")
    tax, ic, shares = col("tax_rate"), col("invested_capital"), col("diluted_shares")
    nd, td, cash = col("net_debt"), col("total_debt"), col("cash")
    if np.all(np.isnan(shares)):
        shares = col("shares_outstanding")

    # ROIC = EBIT * (1 - tax) / invested capital, averaged over available years
    tax = np.where(np.isnan(tax) | (tax <= 0) | (tax > 0.5), 0.21, tax)
    with np.errstate(all="ignore"):
        roic = ebit * (1 - tax) / ic
    roic = roic[np.isfinite(roic)]
    out["roic_avg"] = float(np.mean(roic)) if len(roic) else np.nan
    out["roic_trend"] = float(roic[-1] - roic[0]) if len(roic) >= 2 else np.nan
    out["roic_latest"] = float(roic[-1]) if len(roic) else np.nan

    # FCF margin and consistency
    with np.errstate(all="ignore"):
        fcfm = fcf / rev
    ok = np.isfinite(fcfm)
    out["fcf_margin_avg"] = float(np.mean(fcfm[ok])) if ok.any() else np.nan
    out["fcf_positive_years"] = int(np.sum(fcf[np.isfinite(fcf)] > 0))
    out["fcf_years"] = int(np.sum(np.isfinite(fcf)))

    # Revenue growth: CAGR over the window (3 yr if 4 years of data)
    n = len(rev) - 1
    out["rev_cagr"] = float((rev[-1] / rev[0]) ** (1 / n) - 1) if rev[0] > 0 and n > 0 else np.nan

    # Balance sheet: net debt / EBITDA on latest year
    nd_latest = nd[-1]
    if np.isnan(nd_latest):
        nd_latest = (td[-1] if np.isfinite(td[-1]) else 0) - (cash[-1] if np.isfinite(cash[-1]) else 0)
    e_latest = ebitda[-1] if np.isfinite(ebitda[-1]) else np.nan
    out["net_debt"] = float(nd_latest)
    out["ebitda_latest"] = float(e_latest) if np.isfinite(e_latest) else np.nan
    if nd_latest <= 0:
        out["nd_to_ebitda"] = 0.0
        out["net_cash"] = True
    elif np.isfinite(e_latest) and e_latest > 0:
        out["nd_to_ebitda"] = float(nd_latest / e_latest)
        out["net_cash"] = False
    else:
        out["nd_to_ebitda"] = np.nan
        out["net_cash"] = False

    # Share count trend, total change first to last year
    sh = shares[np.isfinite(shares)]
    out["share_change"] = float(sh[-1] / sh[0] - 1) if len(sh) >= 2 and sh[0] > 0 else np.nan

    # Gross margin stability (std dev of gross margin across years)
    with np.errstate(all="ignore"):
        gm = gp / rev
    gm = gm[np.isfinite(gm)]
    out["gm_avg"] = float(np.mean(gm)) if len(gm) else np.nan
    out["gm_std"] = float(np.std(gm)) if len(gm) >= 2 else np.nan
    return out

def score(df):
    s = pd.DataFrame(index=df.index)
    # ROIC: percentile of average, nudged by trend
    s["roic"] = pct_rank(df["roic_avg"])
    s["roic"] = s["roic"] + np.sign(df["roic_trend"].fillna(0)) * 5
    s.loc[df["roic_avg"] < 0, "roic"] = 0
    # FCF: percentile of margin, gated by consistency (positive in all but at most one year)
    s["fcf"] = pct_rank(df["fcf_margin_avg"])
    weak = df["fcf_positive_years"] < (df["fcf_years"] - 1)
    s.loc[weak, "fcf"] = s.loc[weak, "fcf"] * 0.2
    s.loc[df["fcf_margin_avg"] < 0, "fcf"] = 0
    # Growth
    s["growth"] = pct_rank(df["rev_cagr"])
    # Balance sheet, rule based
    nde = df["nd_to_ebitda"]
    s["balance"] = np.select(
        [df["net_cash"] == True, nde < 1, nde < 2, nde < 3, nde >= 3],
        [100, 85, 65, 45, 10], default=30)  # default 30 when EBITDA negative or missing
    # Share count
    sc = df["share_change"]
    s["shares"] = np.select([sc < -0.05, sc < 0, sc < 0.03, sc < 0.10, sc >= 0.10],
                            [100, 80, 55, 25, 0], default=40)
    # Gross margin stability, lower std is better. Missing gross margin (some industries) = neutral 50
    s["gm_stability"] = pct_rank(-df["gm_std"]).fillna(50)
    s = s.clip(0, 100)
    total = sum(s[k].fillna(0) * w for k, w in WEIGHTS.items()) / sum(WEIGHTS.values())
    for k in WEIGHTS:
        df[f"score_{k}"] = s[k].round(1)
    df["quality_score"] = total.round(1)
    return df

def main():
    today = dt.date.today().isoformat()
    uni = pd.read_csv(UNIVERSE_DIR / "universe_latest.csv")
    rows = []
    for tk in uni["ticker"]:
        p = FUND_CACHE / f"{tk}.json"
        if not p.exists():
            rows.append({"ticker": tk, "exclude_reason": "no fundamentals in cache"})
            continue
        rows.append(metrics_for(json.loads(p.read_text())))
    df = pd.DataFrame(rows)
    df = df.merge(uni[["ticker", "exchange", "currency", "price", "market_cap_usd", "dollar_volume_usd"]], on="ticker", how="left")
    excluded = df[df["exclude_reason"].notna()]
    scored = score(df[df["exclude_reason"].isna()].copy())
    scored = scored.sort_values("quality_score", ascending=False)
    scored["rank"] = range(1, len(scored) + 1)
    scored["pulled"] = today
    scored.to_csv(UNIVERSE_DIR / f"quality_scores_{today}.csv", index=False)
    scored.to_csv(UNIVERSE_DIR / "quality_scores_latest.csv", index=False)
    scored.head(150).to_csv(UNIVERSE_DIR / "quality_top150.csv", index=False)
    excluded.to_csv(UNIVERSE_DIR / f"excluded_{today}.csv", index=False)
    print(f"scored {len(scored)}, excluded {len(excluded)}")
    print(excluded["exclude_reason"].str.split(":").str[0].value_counts().to_string())
    cols = ["rank", "ticker", "name", "sector", "quality_score", "roic_avg", "fcf_margin_avg", "rev_cagr", "nd_to_ebitda", "share_change"]
    print(scored.head(30)[cols].to_string(index=False))

if __name__ == "__main__":
    main()
