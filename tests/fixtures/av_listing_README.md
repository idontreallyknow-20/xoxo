Hand-built fixtures for `an.listing_status`, shaped like Alpha Vantage's `LISTING_STATUS` CSV.

The active rows are real tickers with their first-trade dates as `universe_latest.csv` carries them.
The delisted rows are fictional (`FAK*`, `ZZZA`) apart from TWTR, so that no false corporate history
is attached to a real company. The answers `tests/test_listing_status.py` asserts were computed by
hand from these rows with today pinned to 2026-09-06.
