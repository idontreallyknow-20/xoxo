# Selection criteria

Edit this file freely. The scripts read the same rules, so if you change a number here, change it in `scripts/quality_screen.py` or `scripts/price_screen.py` too (the WEIGHTS dict and the bucket rules are at the top of each file).

## Step 1: Universe
- US and Canadian listed common stock (NYSE, Nasdaq, TSX). No CDRs, OTC, preferreds, warrants.
- Market cap above $2B USD.
- Average daily dollar volume above $10M USD (3 month average).
- Listed for at least 3 years (proxy for 3 years of filings).
- No SPACs (name filter) and no Chinese or Hong Kong domiciled companies.
- Banks, insurers and lenders are set aside. ROIC, EBITDA and free cash flow are not meaningful for them. They are listed in `universe/excluded_*.csv` so nothing is hidden, but they are not scored. If you want them in, we need a separate scorecard (ROE, book value growth, credit costs).

## Step 2: Quality score, 0 to 100

Weights:

| Component | Weight | How it is scored |
|---|---|---|
| Return on invested capital | 25 | Percentile rank of the average ROIC (EBIT x (1 minus tax) / invested capital) across available years, plus 5 if the trend is up, minus 5 if down. Negative average ROIC scores 0. |
| Free cash flow | 20 | Percentile rank of average FCF margin. If FCF was negative in more than one of the available years the component is cut to 20%. Negative average margin scores 0. |
| Revenue growth | 15 | Percentile rank of revenue CAGR over the window. |
| Balance sheet | 15 | Net cash 100. Net debt / EBITDA under 1x 85, under 2x 65, under 3x 45, over 3x 10. Missing or negative EBITDA 30. |
| Share count | 10 | Diluted share count change first year to last: shrank more than 5% 100, shrank 80, grew under 3% 55, grew under 10% 25, grew more 0. |
| Gross margin stability | 15 | Percentile rank of (minus) the standard deviation of gross margin. Companies with no gross margin line get a neutral 50. |

Keep the top 150.

**Data limitation, stated plainly:** yfinance returns 4 fiscal years of annual statements, not 5. Every "5 year" item in the original spec is computed over 4 years (a 3 year CAGR for growth). If that matters, a paid source (S&P Capital IQ, FactSet, or the cheaper Financial Modeling Prep / EODHD APIs at roughly $20 to $80 a month) gives 10+ years. Ask and I will wire one in.

## Step 3: Price screen (on the 150)
- Forward P/E and EV/EBITDA against the company's own median at past fiscal year ends (4 points).
- Drawdown from the 52 week high and from the all time high.
- Consensus EPS revisions for current and next fiscal year over 30 and 90 days, plus the count of analysts revising up and down.
- Short interest as % of float.

Buckets:
- **Compounder**: in the quality 150 and forward P/E or EV/EBITDA at or below its own historical median.
- **Cyclical turn**: cyclical sector or industry (semis, industrials, materials, energy, autos, transport), 35% or more below the all time high, and next year EPS estimates flat or rising over the last 30 days. Higher false positive rate. Size smaller.

## Step 4: Deep dive
Top 15 to 20 names, one file each in `research/`. Template in `research/_TEMPLATE.md`.

## Step 5: Portfolio rules (recommendations only, you decide)
- 8 to 12 names.
- No position above 12% at cost. No sector above 30%. Cyclical turn bucket capped at 30% of deployed capital.
- 20 to 30% cash. Deploy in tranches over 3 to 6 months. Never more than 25% of total capital in one month.
- Every recommendation: buy zone, size in dollars, and the "I was wrong" price or event.
