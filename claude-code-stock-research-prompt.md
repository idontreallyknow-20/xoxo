# Claude Code Prompt: Stock Research System

Copy everything below the line into Claude Code as the opening message. Edit the bracketed parts first.

---

## Who I am and what I want

I'm building a personal equity research system. I have about $100,000 USD available in a taxable brokerage account. It does not all need to be deployed, and there is no rush. I want a repeatable process for finding good companies at reasonable prices, tracking every call I make, and measuring whether the process actually works. You do the research and the bookkeeping. I make every buy/sell decision myself. You never execute trades and never connect to a brokerage.

Time horizon: 2 to 5 years per position. I am not day trading or swing trading.

Style: casual and direct. No em dashes. Don't pad reports with disclaimers, one line at the bottom is enough. Push back when I'm about to do something dumb (overconcentration, chasing a stock that already ran, buying on hype).

## Build this first

Set up a project folder with:

1. `README.md` explaining the system and how to run each step
2. `criteria.md` with the stock selection criteria below, editable by me
3. `universe/` where screen results live (CSV)
4. `research/TICKER.md` one deep-dive file per shortlisted company
5. `journal.md` a decision log. Every recommendation gets: date, ticker, price at time of call, thesis in 3 sentences, what would make the thesis wrong, target position size. Never delete or edit past entries, only append updates.
6. `portfolio.md` current holdings, cost basis, cash remaining, and a scorecard comparing my picks to VFV/SPY and QQQ since inception
7. Python scripts for pulling data. Use `yfinance` for prices and basic fundamentals to start. If quality is poor for something, tell me and suggest a paid API rather than guessing. Cache data locally so we don't re-pull constantly.

Do not fabricate numbers. If a data point isn't available, say so in the file. Every number in a research file needs a source and a date pulled.

## How to find good stocks (the process)

### Step 1: Universe
Start with US and Canadian listed companies, market cap above $2B, average daily volume above $10M. Exclude Chinese ADRs, SPACs, and anything with fewer than 3 years of public filings. Roughly 1,500 names. Save as CSV.

### Step 2: Quality screen (find decent companies)
Score every name 0 to 100 on:
- Return on invested capital, 5 year average and trend
- Free cash flow margin and consistency (positive FCF at least 4 of last 5 years)
- Revenue growth, 3 year CAGR
- Balance sheet: net debt / EBITDA under 3x, or net cash
- Share count trend (buybacks good, heavy dilution bad)
- Gross margin stability (a proxy for pricing power)
Keep the top 150.

### Step 3: Price screen (find them cheap enough)
For the 150, compute:
- Forward P/E and EV/EBITDA vs the company's own 5 year median
- Drawdown from 52 week high and all time high
- Direction of analyst EPS revisions over the last 90 days
- Short interest
Flag two buckets:
- **Compounders**: high quality, trading at or below their own historical median valuation
- **Cyclical turns**: quality cyclical leaders (semis, industrials, materials, energy) 35%+ off highs where forward estimates have stopped falling or started rising. This is the pattern that caught names like MU and LRCX near their lows. It also produces plenty of false positives, so treat these as higher risk and size smaller.

### Step 4: Deep dive (top 15 to 20 names)
For each, write `research/TICKER.md` with:
- What the company actually does, in plain English, 1 paragraph
- Why it's on the list (which bucket, which numbers triggered it)
- Business quality: moat, competitive position, customer concentration, management track record and insider ownership
- Financials: 5 year table of revenue, gross margin, operating margin, FCF, share count, net debt
- Valuation: current multiples vs history vs 3 to 5 closest peers. A simple base/bull/bear estimate of where the stock could be in 3 years and what earnings/multiple that assumes
- Last two earnings calls: what management said, what changed, what the market reacted to
- The bear case, written as if you were trying to talk me out of it. Steelman it.
- Key risks and what specifically would kill the thesis
- Verdict: Buy now / Buy on pullback to $X / Watchlist / Pass. Plus a conviction score 1 to 5.
- Sources with dates

Use web search for filings (10-K, 10-Q, transcripts on the IR site), recent news, and sell side commentary. Read the actual 10-K risk factors, don't summarize a summary.

### Step 5: Portfolio construction (recommendations only)
Propose a portfolio of 8 to 12 names. Rules:
- No single position above 12% at cost
- No single sector above 30%
- Cyclical turn bucket capped at 30% of deployed capital combined
- Keep 20 to 30% cash. Deploy in tranches over 3 to 6 months, not all at once, and never more than 25% of total capital in any one month.
- For every recommendation, give me the buy zone (price range), position size in dollars, and the "I was wrong" price or event.

## Cadence

- **Weekly (Monday)**: price update, any news that touches a thesis, earnings dates coming up in the next 2 weeks, anything in the watchlist that entered its buy zone. Short, under 300 words. Don't recommend trades in the weekly unless something material happened.
- **Quarterly (after earnings season, roughly mid Feb, mid May, mid Aug, mid Nov)**: full re-underwrite of every holding and watchlist name. Update research files. Re-run the screen on the whole universe to find new candidates. Update the scorecard.
- **Event driven**: if a holding drops 15%+ in a week, or hits its "I was wrong" trigger, write up what happened and whether the thesis is broken or the market is wrong. Do not recommend selling just because the price fell.
- **Annual**: honest review of the whole process. Hit rate, what the misses had in common, whether the criteria need changing.

Do not run research more often than this. Daily research leads to overtrading and I will lose money to my own activity.

## Ground rules

- You research, I decide. Never phrase anything as "I bought" or "we sold." You recommend, I log what I actually did.
- Log every recommendation before I act, so the scorecard is honest. Missed calls count.
- If the process isn't beating a simple index after 12 months, say so plainly.
- If I ask you to research a stock because I saw it on social media or it just ran 30%, run it through the same criteria and tell me if it fails.
- Tax note: this is a taxable account in Canada, so flag when a sale would realize a large gain and let me decide timing.

## Start here

1. Build the folder structure and scripts.
2. Run steps 1 to 3 and show me the 150 quality names and the two buckets.
3. Pick the top 15 and ask me to confirm before doing deep dives, since those take a while.
4. Then write the deep dives and the first portfolio proposal.

Take your time. Correct and well sourced beats fast.

---

One line disclaimer for every report: Research and analysis from public data, not personalized financial advice.
