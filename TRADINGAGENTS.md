# TradingAgents Integration

Multi-agent LLM trading analysis, integrated as a subcomponent. Runs locally via llama.cpp, reads all market data from DuckDB (no live API calls during analysis).

---

## Installation (Fresh Setup)

```bash
# 1. Clone submodule (pinned to v0.3.1)
git submodule update --init

# 2. Install into existing venv
venv\Scripts\pip install -e vendor/TradingAgents
venv\Scripts\pip install duckdb

# 3. Verify
venv\Scripts\python -c "import tradingagents; import duckdb; print('OK')"
```

### Upgrading TradingAgents Later

```bash
cd vendor/TradingAgents
git fetch origin
git checkout v0.4.0   # or whatever version
cd ../..
venv\Scripts\pip install -e vendor/TradingAgents
```

The DuckDB adapter (`analysis/duckdb_vendor.py`) is decoupled from the vendor code. Upgrades only break if TradingAgents changes the function signatures in `tradingagents/dataflows/interface.py`.

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
       │  1. Monkey-patches TradingAgents VENDOR_METHODS → DuckDB
       │  2. Configures LLM → local llama.cpp (openai_compatible)
       │  3. Calls TradingAgentsGraph.propagate(ticker, date)
       ▼
TradingAgents (vendor/TradingAgents)
       │  Multi-agent pipeline:
       │  Fundamentals Analyst → Sentiment Analyst → News Analyst
       │  → Technical Analyst → Bull/Bear Researchers → Trader
       │  → Risk Manager → Portfolio Manager
       ▼
Decision text → sent back to Telegram
```

---

## Data Engineering (`data_eng/`)

### What's Implemented

| Module | Function | Source | DB Table |
|--------|----------|--------|----------|
| `ingest_prices()` | Daily OHLCV (1yr lookback) | yfinance `.history()` | `daily_prices` |
| `ingest_news()` | Recent ticker news (50 articles) | yfinance `.get_news()` | `news` |
| `ingest_global_news()` | Macro/market news | yfinance `Search` | `global_news` |
| `ingest_fundamentals()` | Company snapshot (PE, EPS, margins...) | yfinance `.info` | `fundamentals` |
| `ingest_financials()` | Quarterly balance sheet, cashflow, income stmt | yfinance `.quarterly_*` | `financials` |
| `ingest_analyst_targets()` | Price targets + upgrade/downgrade ratings | yfinance `.analyst_price_targets` + `.upgrades_downgrades` | `analyst_targets` |

All ingestors pause **1.5 seconds** between yfinance API calls to avoid rate limiting.

### Usage

```bash
# Ingest all data for one or more tickers
venv\Scripts\python -m data_eng AAPL MSFT NVDA

# Or import in code
from data_eng.ingest import ingest_all, ingest_prices
ingest_all("AAPL")
```

### Database

Single file: `data/market.duckdb`

Schema (6 tables):
- `daily_prices` — ticker, date, OHLCV
- `news` — ticker, date, title, summary, publisher, url
- `global_news` — date, title, summary, publisher, url
- `fundamentals` — ticker, date_fetched, ~28 metric columns
- `financials` — ticker, report_date, freq, statement_type, data_json (JSON blob)
- `analyst_targets` — ticker, date_fetched, analyst, target_price, rating

---

## What TradingAgents Sees

When analysis runs, TradingAgents calls its data interface functions. These are **monkey-patched** at runtime to read from DuckDB instead of hitting live APIs:

| TradingAgents Call | Served By | Returns |
|--------------------|-----------|---------|
| `get_stock_data(symbol, start, end)` | DuckDB `daily_prices` | CSV of OHLCV rows |
| `get_indicators(symbol, indicator, date, lookback)` | DuckDB + stockstats (computed on-the-fly) | Indicator values per day |
| `get_fundamentals(ticker)` | DuckDB `fundamentals` | Key-value text block |
| `get_balance_sheet(ticker)` | DuckDB `financials` | CSV |
| `get_cashflow(ticker)` | DuckDB `financials` | CSV |
| `get_income_statement(ticker)` | DuckDB `financials` | CSV |
| `get_news(ticker, start, end)` | DuckDB `news` | Formatted headlines |
| `get_global_news(date)` | DuckDB `global_news` | Formatted headlines |
| `get_insider_transactions(ticker)` | N/A (not stored) | Graceful "not available" message |

If data is missing, the adapter returns a `NO_DATA_AVAILABLE` message telling you to run ingestion first.

---

## LLM Configuration

Set in `analysis/runner.py`:

```python
config["llm_provider"] = "openai_compatible"
config["backend_url"] = "http://127.0.0.1:10000/v1"   # local llama.cpp
config["deep_think_llm"] = "MyQwythos"
config["quick_think_llm"] = "MyQwythos"
config["max_debate_rounds"] = 1
```

No API keys needed. Requires `llama-server` running on port 10000 (same as the voice bot).

---

## Extending

### Add a new data source

1. Add table schema in `data_eng/db.py`
2. Add ingestor function in `data_eng/ingest.py` (with `time.sleep(API_PAUSE)`)
3. Add adapter function in `analysis/duckdb_vendor.py`
4. Register in `analysis/runner.py` → `_patch_dataflows()`

### Add a new vendor to TradingAgents

The monkey-patch approach means zero changes to `vendor/TradingAgents`. Your code lives entirely in `analysis/`.

---

## Known Limitations

- **9B local model**: TradingAgents runs ~10+ LLM calls per analysis. A 9B model produces basic analysis. For deeper reasoning, swap to a larger model or cloud API in `runner.py`.
- **No insider transactions**: Not stored locally (returns graceful message).
- **Indicators computed on-the-fly**: Uses stockstats on DuckDB OHLCV data. Requires sufficient price history for warm-up (~250 days).
- **News freshness**: Only as fresh as your last `python -m data_eng TICKER` run.
