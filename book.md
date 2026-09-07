# Claude's book

My own paper portfolio: $100,000 of fake money, the same rules as `criteria.md` and `swing.md`,
every call logged here before it fills, and graded by the tracker like Joseph's journal. Append
only. Never edit or delete a past entry; a change of mind is a new entry that says why.

The book is derived, never kept by hand: `scripts/build_book.py` fills every Buy at the first
close after its date, applies the caps (12% per name, 25% of equity deployed per month, cash
never under 20%), sizes a swing call by its stop, marks everything at the latest close, and
writes `dashboard/book.json`. `tests/test_book.py` fails if an old entry changes.

Same grammar as `journal.md`:

```
## YYYY-MM-DD TICKER  Recommendation: Buy / Buy (swing) / Sell / Trim / Pass
Price at call: 123.45
Thesis: why, in my words, from the research note and the filings it cites.
Wrong if: what would break it, with a closing level the tracker can check.
Target size: $X (Y% of capital)
Conviction: 1 to 5
Bucket: compounder / cyclical turn / swing
Horizon: N trading days      (swing calls only)
Stop: $X                     (swing calls only, a closing level)
```

Research and analysis from public data, not personalised financial advice.

---

## 2026-09-07 SYSTEM  Inception
Price at call: n/a
Thesis: Starting with $100,000 cash on Labour Day 2026, the day the desk learned to run itself. The long book follows criteria.md, eight to twelve names built over September, October and November with no month above 25% of equity, cash held between 20% and 30%. The swing sleeve is 20% and follows swing.md mechanically once the setups scanner runs on live quotes; the paper replay of those rules on 2013 to 2018 closes found no edge, so a swing call here is a way to measure the live rule, not a bet on it. Every call is graded against SPY by the tracker.
Wrong if: The book trails SPY, QQQ and VFV after twelve months. The email will say so.
Target size: $0 deployed, $100,000 cash
Conviction: n/a
Bucket: n/a

## 2026-09-07 SYSTEM  The plan for the first three months
Price at call: n/a
Thesis: September, $24,000: BKNG, REGN, MSFT, the three notes where the numbers and the price agree and the next report is furthest away or already priced. October, about $22,000: KLAC sized as a cyclical, ADBE only after the September 10 report has printed, IDXX if it stays in its buy zone. November, about $20,000: ADSK, ISRG, CPRT. That leaves roughly $34,000 of cash at the end of November, above the 30% band by design until the swing sleeve is measured. Names I read and left out on purpose: NOW at 29x forward with the same seat-pricing risk as ADBE at three times the multiple; LRCX because KLAC already carries the equipment cycle; GOOGL because its score percentile is 35 and the memo's own confidence is the weakest of the twelve.
Wrong if: A falsifier below is crossed before its tranche, in which case that name is skipped and the reason logged.
Target size: about $66,000 across nine names by November
Conviction: n/a
Bucket: n/a

## 2026-09-07 BKNG  Recommendation: Buy
Price at call: 195.13
Thesis: The largest online travel business in the world printed record bookings, room nights up 5% and EPS up 15%, and the stock is priced at 15.8 times forward earnings against its own four-year median of 29.5 because the market has decided AI agents end the 15% commission. Buybacks of $3.7 billion a quarter shrink the share count 4 to 5% a year, so even flat earnings compound per share. The fear is priced as a certainty and the evidence so far runs the other way: when OpenAI pulled checkout from ChatGPT in March the stock rose 8%.
Wrong if: Two consecutive quarters of negative room nights, the take rate down 100 basis points year over year, buybacks paused, or a close under $150.
Target size: $8,000 (8%)
Conviction: 4
Bucket: compounder

## 2026-09-07 REGN  Recommendation: Buy
Price at call: 843.47
Thesis: Revenue up 17% and Dupixent up 38% on a net-cash balance sheet, priced at 13.9 times forward earnings, a pharma multiple on a company growing like a growth stock, because the market is watching legacy Eylea lose 45% a year to biosimilars. Eylea HD is replacing it (up 52% in the US) and Dupixent is now the larger franchise by a wide margin. This is the cheapest name on the list relative to its growth, and the one whose bear case is already visible in the numbers rather than hypothetical.
Wrong if: Dupixent growth under 15%, Eylea HD down sequentially for two quarters, a drug pricing law that names Dupixent, or a close under $650.
Target size: $8,000 (8%)
Conviction: 4
Bucket: compounder

## 2026-09-07 MSFT  Recommendation: Buy
Price at call: 510.12
Thesis: Azure grew 43% in constant currency and passed $100 billion of annual revenue, and the stock trades at 21.6 times forward earnings, its lowest multiple in three years and below Alphabet's, because $190 billion of capex has cut free cash flow and the market wants proof it earns a return. This is the one name in the book bought at fair value rather than at a discount, and I am taking it now rather than in October as the note suggests because 8% off the high with the multiple at a three-year low is as good an entry as this business has offered in the window I can see.
Wrong if: Azure growth under 30% while capex still rises, an OpenAI restructuring that cuts committed Azure spend, free cash flow margin under 20% for a full year, or a close under $400.
Target size: $8,000 (8%)
Conviction: 4
Bucket: compounder
