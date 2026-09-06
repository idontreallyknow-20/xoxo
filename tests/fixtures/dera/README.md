Hand-built miniatures of two SEC DERA Financial Statement Data Sets, in the published layout
(tab-separated `sub.txt`, `num.txt`, `pre.txt`, `tag.txt` with the documented headers).

`2023q3` holds Apple's Q3 FY2023 10-Q (accession 0000320193-23-000077, filed 2023-08-04) and
`2023q4` holds Apple's FY2023 10-K (0000320193-23-000106, filed 2023-11-03). The Apple figures are the
ones those filings report: net sales $383.285bn for FY2023, $394.328bn FY2022, $365.817bn FY2021;
nine months to 2023-07-01 $293.787bn; Q3 FY2023 $81.797bn. The `accepted` timestamps are
approximate. Address and phone fields are Apple's public ones.

`EXAMPLE RESTATER CORP` (CIK 9999999) is fictional. It exists to exercise a restatement (10-K in
2023q3 says 1,000,000; 10-K/A in 2023q4 says 950,000), a co-registrant row that must be dropped, and
a footnote-only row with no value.

`tests/test_dera.py` reconstructs the known figures from these files, including the derived fourth
quarters: 383,285 minus 293,787 is 89,498 (million) and 394,328 minus 304,182 is 90,146, which are
what Apple reported for Q4 FY2023 and Q4 FY2022.
