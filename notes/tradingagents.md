# TradingAgents Integration

Multi-agent LLM trading analysis. Runs locally via llama.cpp, reads all market data from DuckDB.

---

## Architecture

```
Telegram: /analyze AAPL
       │
       ▼
stock_bot/handlers.py (analyze_command)
       │  runs in background executor
       ▼
analysis/runner.py
       │  1. Monkey-patches VENDOR_METHODS → DuckDB
       │  2. Patches out Reddit/StockTwits (placeholders)
       │  3. Configures LLM → local llama.cpp
       │  4. Calls TradingAgentsGraph.propagate(ticker, date)
       │  5. Saves report tree to data/analysis_reports/
       ▼
TradingAgents (vendor/TradingAgents @ v0.3.1)
       │  Pipeline: Fundamentals → Sentiment → News → Technical
       │  → Bull/Bear → Trader → Risk → Portfolio Manager
       ▼
Decision → Telegram
Report  → data/analysis_reports/ (via /report TICKER)
```

---

## What TradingAgents Sees

All data calls are monkey-patched to read from DuckDB:

| Call | Source | Notes |
|------|--------|-------|
| `get_stock_data` | `daily_prices` | CSV of OHLCV |
| `get_indicators` | `daily_prices` + stockstats | Computed on-the-fly |
| `get_fundamentals` | `fundamentals` | Key-value text |
| `get_balance_sheet` | `financials` | CSV |
| `get_cashflow` | `financials` | CSV |
| `get_income_statement` | `financials` | CSV |
| `get_news` | `news` | Headlines |
| `get_global_news` | `global_news` | Headlines |
| `get_insider_transactions` | N/A | Graceful "not available" |
| `fetch_reddit_posts` | **Skipped** | Avoids 429s |
| `fetch_stocktwits_messages` | **Skipped** | Avoids 429s |

---

## Data Engineering (`data_eng/`)

| Function | Source | Table |
|----------|--------|-------|
| `ingest_prices()` | yfinance `.history()` | `daily_prices` |
| `ingest_news()` | yfinance `.get_news()` | `news` |
| `ingest_global_news()` | yfinance `Search` | `global_news` |
| `ingest_fundamentals()` | yfinance `.info` | `fundamentals` |
| `ingest_financials()` | yfinance `.quarterly_*` | `financials` |
| `ingest_analyst_targets()` | yfinance targets + upgrades | `analyst_targets` |
| `ingest_enriched()` | Yahoo scrape + yfinance + `ta` lib | `ticker_enriched` |
| `batch_ingest_prices()` | `yf.download()` batch | `daily_prices` |

All ingestors pause 1.5s between API calls. Yahoo scrape pauses 2-5s.

### CLI

```bash
python -m data_eng AAPL MSFT     # full ingestion
python -m data_eng --batch        # incremental prices for watchlist
```

---

## Reports

Saved to `data/analysis_reports/reports/TICKER_TIMESTAMP/`:

```
├── 1_analysts/     (market, sentiment, news, fundamentals)
├── 2_research/     (bull, bear, manager)
├── 3_trading/      (trader)
├── 4_risk/         (aggressive, conservative, neutral)
├── 5_portfolio/    (decision)
└── complete_report.md
```

| Command | Behavior |
|---------|----------|
| `/analyze AAPL` | First run: full pipeline. Same day again: cached instant reply |
| `/analyze AAPL --force` | Re-run ignoring cache |
| `/report AAPL` | Send full report in chunks |

---

## LLM Config

In `analysis/runner.py`:
- Provider: `openai_compatible` → `http://127.0.0.1:10000/v1`
- Model: `MyQwythos` (9B, local llama.cpp)
- No API keys needed

---

## Extending

1. Add table in `data_eng/db.py`
2. Add ingestor in `data_eng/ingest.py` (with `time.sleep(API_PAUSE)`)
3. Add adapter in `analysis/duckdb_vendor.py`
4. Register in `analysis/runner.py` → `_patch_dataflows()`

Zero changes to `vendor/TradingAgents` needed.

---

## Known Limitations

- **9B model**: ~10+ LLM calls per analysis. Basic but functional.
- **No structured output**: Falls back to free-text (warning suppressed).
- **No insider data**: Returns graceful message.
- **No Reddit/StockTwits**: Patched out (429 rate limits).
- **No FRED macro**: Optional, degrades gracefully. Add `FRED_API_KEY` if wanted.
- **Indicators on-the-fly**: stockstats computed at analysis time. `ticker_enriched` has pre-computed snapshot but TradingAgents still uses its own calculation.
- **Reports in-memory**: `/report` only works for tickers analyzed in current session.
- **News freshness**: Only as fresh as last ingestion run.
