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
Pre-registered arms: pullback_b3_h20_mkt200+rs63, pead_proxy_g5_h20_mkt200+rs63, pullback_b3_h10 (filled in 2026-09-07T18:01Z under Stop B, before the single OOS run)

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

### Iteration 3, 2026-09-07T17:57Z, both filters, longer horizon
Hypothesis: the two filters together, at the 20-session horizon where the round trip is amortised over more days, is the most favourable honest form of each rule.
Change: `mkt200+rs63` on the three base kinds at horizon 20 (breakout stop 8%, pullback band 3%, gap 5%).
Command: `python scripts/paper_trade.py --live --split train --arms pullback_b3_h20_mkt200+rs63,breakout_s8_h20_mkt200+rs63,pead_proxy_g5_h20_mkt200+rs63 --slug it3_combined_h20`
Result file: `lab/results/2026-09-07T17-57-40+00-00_train_it3_combined_h20.json`
Numbers: pullback -9 bp [-30, +21], n=5,501, control p95 -11 bp, percentile 1.00 (above every one of the twenty
replicates). breakout -15 bp [-48, +7], n=3,013, percentile 0.70. pead_proxy +1 bp [-61, +22], n=283, percentile 0.85.
Kept / dropped: the filtered pullback at 20 sessions is the first arm to clear its whole control, and it still
loses 9 bp a trade to SPY with an interval that straddles zero. The rule needs both; it has neither. None meets Stop A.
Hypotheses used so far: 9 of 12 (k = 33).

### Iteration 4, 2026-09-07T17:58Z, a longer strength window and a breakout extension
Hypothesis: (a) six months of relative strength is a cleaner signal than three; (b) a breakout that clears the prior high by at least 1% is less often a one-day overshoot.
Change: `rs126` in place of `rs63` on the filtered pullback at 20 sessions; `x1` added to the filtered breakout at 20 sessions.
Command: `python scripts/paper_trade.py --live --split train --arms pullback_b3_h20_mkt200+rs126,breakout_s8_h20_x1+mkt200+rs63 --slug it4_rs126_x1`
Result file: `lab/results/2026-09-07T17-58-33+00-00_train_it4_rs126_x1.json`
Numbers: pullback -14 bp [-36, +34], n=5,881, percentile 0.85 (worse than rs63's 1.00). breakout -32 bp [-73, -4],
n=1,923, percentile 0.10, "wrong way": the extension filter selects the overshoots it was meant to avoid.
Kept / dropped: both dropped. None meets Stop A.
Hypotheses used so far: 11 of 12 (k = 35).

### Iteration 5, 2026-09-07T17:59Z, the band
Hypothesis: a wider pullback band (4%) on the filtered 20-session arm admits more of the same signal without changing its character.
Change: band 3% to 4% on `pullback_h20_mkt200+rs63`.
Command: `python scripts/paper_trade.py --live --split train --arms pullback_b4_h20_mkt200+rs63 --slug it5_band4`
Result file: `lab/results/2026-09-07T17-59-53+00-00_train_it5_band4.json`
Numbers: -8 bp [-35, +18], n=5,773, control p95 -10 bp, percentile 0.95. Same shape as band 3%: above most of the
control, below zero, interval straddling zero.
Kept / dropped: dropped. None meets Stop A.
Hypotheses used so far: 12 of 12 (k = 36). **Budget spent. Stop B applies.**

## Stop B, 2026-09-07T18:01Z: the budget is spent with nothing meeting the rule

No arm on the train window has an interval above zero, so nothing can be pre-registered as a candidate.
As the pre-registration block requires, the held-out window is still run **once**, for the best in-sample
arms, so the record shows how an in-sample favourite behaves out of sample. Three arms, chosen before the
run and for stated reasons:

- `pullback_b3_h20_mkt200+rs63`: the only arm to clear all twenty replicates of its control (iteration 3).
- `pead_proxy_g5_h20_mkt200+rs63`: the highest in-sample mean, +1 bp (iteration 3).
- `pullback_b3_h10`: the pullback rule exactly as `swing.md` writes it, so the held-out number describes the
  live rule and not a filtered cousin of it.

The verdict is whatever comes out, committed as is. Hypotheses counted for the widening: 36.

## The held-out result, 2026-09-07T18:02Z

Command: `python scripts/paper_trade.py --live --split test --arms "pullback_b3_h20_mkt200+rs63,pead_proxy_g5_h20_mkt200+rs63,pullback_b3_h10,spy_hold" --slug stopB`
Result file: `lab/results/2026-09-07T18-01-58+00-00_test_stopB.json`
Window: 2016-07-01 to 2018-02-07. Hypotheses counted: 36.

| arm | n | hit | mean excess vs SPY per trade | 95% interval | control p95 | percentile | sleeve after | SPY held |
|---|---|---|---|---|---|---|---|---|
| pullback_b3_h10 (swing.md as written) | 10,375 | 50.9% | -27 bp | [-39, -12] bp | -21 bp | 0.50 | $23,456 | $25,518 |
| pullback_b3_h20_mkt200+rs63 | 4,206 | 53.1% | -26 bp | [-51, +8] bp | -25 bp | 0.90 | $26,874 | $25,518 |
| pead_proxy_g5_h20_mkt200+rs63 | 289 | 42.9% | -55 bp | [-122, -15] bp | +6 bp | 0.10 | $17,199 | $25,518 |

**Verdict: no edge found at these horizons on this data.** No pre-registered arm has an interval above zero
and none sits above its random control's 95th percentile. The rule as written in `swing.md` lost 27 bp a
trade to SPY out of sample with an interval entirely below zero; the in-sample favourite that had cleared
its whole control fell back to the middle of it (percentile 0.90, mean below zero); the best in-sample mean
became the worst out-of-sample mean. That last line is the lesson: the arm picked for looking best on the
train window was the one that did worst on the held-out window, which is what selection on noise does.

One number that looks like it disagrees, and does not: the filtered pullback's $20,000 sleeve ended the
held-out window at $26,874 against $25,518 for SPY held. The ledger takes at most five of the 4,206 signals
at a time, in a pre-declared tie-break order, so its path is one draw from a distribution the event study
describes as centred below SPY. A single path above the benchmark is not evidence the rule is; the event
study over every signal is the measurement, and it says minus 26 bp.

The loop is closed. Nothing in `swing.md` changes. The thirty-graded-call floor is now the way to measure
the live rule the same way this measured the historical one.
