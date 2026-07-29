# Daily Ops

## Start (every session)

| # | Run | What it does |
|---|-----|--------------|
| 1 | `llama-server -hf empero-ai/Qwythos-9B-v2-GGUF:Q8_0 --alias MyQwythos --port 10000` | LLM server (skip if already running) |
| 2 | `venv\Scripts\python voice_logger_bot.py` | Start bot |

## Daily (automated)

The bot auto-runs the data pipeline at **8:00 AM NZT** (1 hr before the 9 AM summary).
No manual step needed unless first-time setup or recovering from downtime.

| Run (manual) | Impacts | What it does |
|-----|---------|--------------|
| `venv\Scripts\python -m data_eng --daily` | `data/market.duckdb` → all tables | Batch prices + news + targets + enriched + smart financials |
| `venv\Scripts\python -m data_eng --batch` | `data/market.duckdb` → `daily_prices` | Prices only (quick incremental) |

## On demand (Telegram)

| Command | Impacts | What it does |
|---------|---------|--------------|
| `/watch NVDA` | `data/watchlist.json` + `data/market.duckdb` (all tables) | Adds ticker, runs full ingestion in background |
| `/portfolio` | reads DuckDB | Charts + watchlist prices + portfolio summary + targets |
| `/charts` | reads DuckDB `daily_prices` | 90-day price charts only (for testing) |
| `/analyze AAPL` | `data/analysis_reports/` | Multi-agent analysis (cached per day, `--force` to re-run) |
| `/report AAPL` | reads `data/analysis_reports/` | Sends full report to chat |
| `/gains` | reads `data/trades.csv` + DuckDB | FIFO P&L |

## Full refresh (single ticker)

| Run | Impacts | What it does |
|-----|---------|--------------|
| `venv\Scripts\python -m data_eng RKLB` | `data/market.duckdb` → all 7 tables | Prices, news, fundamentals, financials, targets, enrichment |

## Files reference

| Path | What lives there |
|------|-----------------|
| `data/market.duckdb` | All market data (7 tables) |
| `data/watchlist.json` | Watched tickers (drives `--batch`) |
| `data/trades.csv` | Logged trades |
| `data/analysis_reports/` | Saved TradingAgents reports |
| `data/voice_log.jsonl` | Voice/text log |
