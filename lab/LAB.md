# Lab notebook: paper trading the swing rules

Fake money, real closes. This file is the append-only record of every experiment run by
`scripts/paper_trade.py --live`. Entries are added at the end and never edited; the results
directory next to it holds one JSON summary per run and is never pruned. A losing arm stays in
the log and in the hypothesis count. That is the whole point of the file.

Nothing here is a recommendation. Research and analysis from public data, not personalised
financial advice.

## Pre-registration (written 2026-09-07 before any held-out run)

OOS window: 2016-07-01 to 2018-02-07. Never changed.
Train window: 2013-02-08 to 2016-06-30.
Data: plotly `all_stocks_5yr.csv` (505 S&P 500 names as of 2018-02, split-adjusted, price returns), SPY and QQQ
from QuantConnect Lean split-only adjusted, membership from fja05680/sp500. Audit gate PASS; see `data/cache/history/manifest.json`
(regenerable with `python scripts/fetch_history.py`).
Decision rule: an arm counts only if, on the held-out window, its mean excess vs SPY per trade has a 95%
block-bootstrap interval (monthly buckets) above zero after the `1.96 + 0.5 ln(k)` widening for k hypotheses,
AND its mean excess sits above the 95th percentile of twenty matched random-entry replicates (same count,
horizon and stop distance, same mechanics, same window).
Budget: 12 train-window iterations after the initial grid. Every arm ever run on train counts toward k.
Mechanics fixed before any run: signal on close t, fill at close t+1; stop = first close strictly below the
stop level, exit at the following close; horizon exit at the close of the horizon-th session after the
signal; stop raised to entry once up by the initial risk; one open position per name (a fresh signal is
allowed again on the session the previous horizon ends); 5 bps commission plus 5 bps slippage each way.
Pre-registered arms: (to be filled in after the train phase, before the single OOS run)

## Iterations

### Iteration 0, 2026-09-07T17:50Z, the pre-declared grid
Hypothesis: the four setups in swing.md, in their price-only forms, carry information at 5, 10 and 20 sessions.
Change: none; this is the grid declared in `scripts/an/ab.py::default_arms` (pullback band 2/3/4% x horizons,
breakout stop 6/8/10% x horizons, gap proxy 5/8% x horizons) plus SPY held.
Command: `python scripts/paper_trade.py --live --split train --slug grid`
Result file: `lab/results/2026-09-07T17-50-21+00-00_train_grid.json`
Numbers: 24 of 24 rule arms have a negative mean excess vs SPY per trade after costs (between -10 and -51 bp).
The random controls sit at -5 to -20 bp, so the rules are indistinguishable from random entries with the same
stop and horizon: percentile in the control between 0.10 and 1.00, median about 0.6. One arm, pullback_b4_h5,
sits above its control's p95 but its own mean is -17 bp with an interval entirely below zero, which the decision
rule rejects (the interval must be above zero). Best in-sample mean: pead_proxy_g5_h20 at -10 bp, interval
[-37, +62] bp, n=430. The $20,000 sleeve replay finishes between $15,385 and $24,437 across arms against
$27,606 for SPY held. Hit rates 42% to 53%.
Kept / dropped: nothing meets Stop A. All 24 stay in the count.
Hypotheses used so far: 0 of 12 (the grid is the baseline, not an iteration).
Reading: the largest single drag is the round trip of 20 bp on a mean holding of days; the second is that
an equal-weighted random draw from these names lagged cap-weighted SPY over 2013 to 2016. Any next change
has to beat the control, not zero.

### Iteration 1, 2026-09-07T17:56Z, regime filter
Hypothesis: the rules lose money in drawdowns; requiring SPY above its own 200-session mean on the signal session removes the worst tape.
Change: the `mkt200` entry filter added to the three base arms (pullback_b3_h10, breakout_s8_h10, pead_proxy_g5_h20). Nothing else.
Command: `python scripts/paper_trade.py --live --split train --arms pullback_b3_h10_mkt200,breakout_s8_h10_mkt200,pead_proxy_g5_h20_mkt200 --slug it1_mkt200`
Result file: `lab/results/2026-09-07T17-56-13+00-00_train_it1_mkt200.json`
Numbers: pullback -16 bp [-29, +5], n=12,916, control p95 -14 bp, percentile 0.70. breakout -13 bp [-36, -1], n=4,466,
percentile 0.65, verdict "wrong way". pead_proxy +2 bp [-55, +66], n=342, control p95 +7 bp, percentile 0.85.
Kept / dropped: the filter removes 4 to 20% of signals and moves the means by 2 to 12 bp, inside the noise. None meets Stop A.
Hypotheses used so far: 3 of 12 (k = 27).

### Iteration 2, 2026-09-07T17:56Z, relative strength
Hypothesis: a setup on a name that has been outrunning SPY for a quarter carries more information than one on a laggard.
Change: the `rs63` entry filter (63-session return above SPY's) on the three base arms, instead of `mkt200`.
Command: `python scripts/paper_trade.py --live --split train --arms pullback_b3_h10_rs63,breakout_s8_h10_rs63,pead_proxy_g5_h20_rs63 --slug it2_rs63`
Result file: `lab/results/2026-09-07T17-56-42+00-00_train_it2_rs63.json`
Numbers: pullback -15 bp [-32, +2], n=10,274, percentile 0.85. breakout -17 bp [-54, -11], n=4,455, percentile 0.55, "wrong way".
pead_proxy -13 bp [-45, +27], n=330, percentile 0.50.
Kept / dropped: relative strength removes a quarter of the pullback signals and moves nothing outside the noise. None meets Stop A.
Hypotheses used so far: 6 of 12 (k = 30).
