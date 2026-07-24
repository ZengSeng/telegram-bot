---
kind: external_dependency
name: Yahoo Finance Data (yfinance)
slug: yfinance
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
source_files:
    - voice_logger_bot.py
    - requirements.txt
---

Used to fetch current stock prices via `yf.Ticker(t).fast_info` for the watchlist tickers. The code falls back between `lastPrice` and `last_price` fields in fast_info. Also used to resolve ticker names via the `info` property. Errors during price fetching are logged but do not crash the summary generation.