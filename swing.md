# Swing rules

Written 2026-09-07, before the setups scanner ran for the first time, so no rule here was chosen
after seeing a result. The scanner (`scripts/setups.py`) and the morning email quote this file.
Edit it freely; the scanner's constants are at the top of `scripts/an/setups.py` and should be
changed in the same commit.

## What this is not

Nothing in this repository has a measured edge at a days-to-weeks horizon. The research layer,
the score and the journal were built for a 2 to 5 year holding period, and the one thing that can
judge them, the backtest, has one dated snapshot and says `NOT RUN`. The four patterns below are
the best-documented mechanical setups at this horizon, and the honest summary of the literature
is: post-earnings drift is real in small names and has faded in large ones, trend and pullback
rules work in some decades and not others, and a guidance raise is information the market prices
in minutes. Every name this scanner can reach is over $2bn by construction.

So the rules are about **survival while the edge is measured**, not about the edge. The tracker
grades every swing call logged in `journal.md` at 5, 10 and 20 sessions against SPY. Until it has
graded **thirty** of them, the sleeve stays at its starting size whatever the results look like.

## The sleeve

- 20% of capital, $20,000 of the $100,000, is the swing sleeve. The rest is the long book and is
  governed by `criteria.md`. The two do not borrow from each other.
- **1% of total capital at risk per trade**, $1,000, meaning position size = $1,000 divided by the
  distance from entry to stop. A setup with an 8% stop is a $12,500 position; a 3% stop is not a
  $33,000 position, because no position exceeds a quarter of the sleeve ($5,000) in any case.
- At most **five** open swing positions. A sixth setup waits.
- No more than two positions in one sector.

## The trade

- Every trade has a stop written down before entry, in `journal.md`, as `Stop: $X`. A stop is a
  closing level; the scanner deals in closes and so does the journal. A print through it during
  the session is not a stop, and the intraday email says so.
- Every trade has a horizon written down before entry, as `Horizon: N trading days`. At the
  horizon the position is closed whatever it has done, unless a new setup on the same name fires on
  that day. A trade that is not working by its horizon was wrong; holding it longer is a new trade
  with no rule behind it.
- No entry on a release session except through the post-earnings drift setup, which enters on the
  close **after** the release.
- No entry in the last thirty minutes of a session and no entry before 10:00 ET. Opening prints are
  not closes and the rules are closing rules.
- No adds to a losing position. No moving a stop down. A stop may be raised to the entry price
  once the position is up by the initial risk.

## The setups (from `scripts/an/setups.py`)

- **post-earnings drift**: an 8-K with item 2.02 in the last three sessions, the release session
  closed 5% or more above the prior close, and the latest close holds the release close. Stop: the
  lowest close since the release. Horizon: 20 sessions.
- **guidance raise**: the mechanical 8-K diff reads "raised" on revenue or EPS for a period still
  ahead. Stop: the lowest close of the last five sessions. Horizon: 20 sessions.
- **breakout with revisions**: the latest close is above every close of the prior 251 sessions and
  next-year EPS estimates rose over 30 days. Quality 150 only. Stop: 8% under entry. Horizon: 10.
- **pullback in trend**: quality 150, the 50-session mean above the 200, close above the 200, close
  within 3% of the 20-session mean, no worse than 15% under the 252-session high. Stop: the lowest
  close of the last 20 sessions. Horizon: 10.

A setup is a pattern that matched. It is not a recommendation, and the email that lists it says
so on every row.

## The review

- **Weekly, Monday**: every open swing position against its stop and its horizon, and the tracker's
  grade count. Under 200 words.
- **After thirty graded calls**: hit rate, mean excess against SPY at each horizon, and the worst
  five. If the mean excess is not positive at the 10-day horizon, the sleeve halves. If it is,
  nothing changes until sixty.
- **Never**: a size change because the last three worked.

## Measured

The rules above were replayed on real closes for the first time in the sixth session: 505 S&P 500
names, February 2013 to February 2018, fake money, the sizing and the mechanics exactly as written
here, against twenty matched random-entry controls and SPY. `dashboard/paper.json` (the `/paper/`
page) holds the result and `lab/LAB.md` the record of every arm tried. The short version:
No edge found at these horizons on this data. The pullback rule as written lost 27 bp a trade to SPY on the held-out window (2016-07 to 2018-02, n=10,375, interval [-39, -12] bp); the in-sample favourite, the filtered pullback at 20 sessions, lost 26 bp with an interval straddling zero and sat inside its random control; the gap-on-volume proxy lost 55 bp. 36 hypotheses were counted. No arm beat a matched random entry.

None of that moves the thirty-graded-call floor. A backtest on one bull market with the earnings
setups reduced to proxies is a reason to log calls and grade them, not a reason to size up or down.
No constant above was changed on the strength of the train window; anything the grid preferred is
recorded there as in-sample.

## Tax and account

A taxable Canadian account that trades frequently can have its gains treated by the CRA as
business income rather than capital gains, which is taxed at the full rate instead of half. The
factors are frequency, holding period, intent and time spent. Thirty short trades a year in a
$20,000 sleeve is the kind of pattern that raises the question. Log every trade; the journal is
the record either way.

Research and analysis from public data, not personalised financial advice.
