# Bot Guide

## Quick Start

```bash
# 1. Make sure llama-server is running (port 10000)
llama-server -hf empero-ai/Qwythos-9B-v2-GGUF:Q8_0 --alias MyQwythos --port 10000

# 2. Start the bot
venv\Scripts\python voice_logger_bot.py
```

---

## Telegram Commands

| Command | What it does |
|---------|--------------|
| `/start` | Register chat (needed for daily summaries) |
| `/system <prompt>` | Set/change AI system prompt |
| `/watch <TICKER>` | Add ticker + auto-runs full data ingestion |
| `/unwatch <TICKER>` | Remove ticker from watchlist |
| `/summary` | Portfolio summary (price, avg cost, shares, %) |
| `/gains` | Realized & unrealized P&L (FIFO) |
| `/analyze <TICKER>` | Multi-agent AI analysis (cached per day) |
| `/analyze <TICKER> --force` | Re-run analysis ignoring cache |
| `/report <TICKER>` | Full analysis report (detailed) |

---

## Data Engineering Workflows

### Add a new ticker

```
/watch NVDA
```

This adds to watchlist AND automatically runs full ingestion in background:
prices (1yr), news, fundamentals, financials, analyst targets, enrichment (yahoo summary + growth + technicals).

### Daily batch update (prices only, incremental)

```bash
venv\Scripts\python -m data_eng --batch
```

Downloads only new price data for all watchlist tickers. Fast — uses single batch `yf.download()` call. Run this daily (manually or scheduled).

### Full refresh for one ticker

```bash
venv\Scripts\python -m data_eng RKLB
```

Re-runs everything: prices, news, fundamentals, financials, targets, enrichment.

### Run analysis

```
/analyze AAPL
```

First run today: full multi-agent pipeline (~1-3 min). Subsequent runs same day: instant cached reply. Use `--force` to override.

---

## Database

Single file: `data/market.duckdb`

| Table | Contents |
|-------|----------|
| `daily_prices` | OHLCV per ticker per day |
| `news` | Ticker news headlines |
| `global_news` | Macro/market news |
| `fundamentals` | Company snapshot (PE, EPS, margins, etc.) |
| `financials` | Quarterly statements (JSON blobs) |
| `analyst_targets` | Price targets + ratings |
| `ticker_enriched` | Yahoo AI summary + growth estimates + price targets + recommendations + technical indicators + signals |

### ticker_enriched columns

Pre-computed daily so TradingAgents doesn't calculate on the fly:

- **Yahoo summary** — AI-generated business description
- **Growth** — stockTrend (0q/1q/0y/1y), indexTrend LTG, stock-over-index %
- **Targets** — low/mean/median/current/high, over-mean %, over-median %
- **Recommendations** — strongBuy/buy/hold/sell/strongSell counts
- **Technicals** — SMA20/50, EMA12/26, MACD/signal/hist, RSI14, Bollinger (upper/mid/lower/width), volume SMA20, OBV, volume ratio
- **Signals** — RSI/MACD/trend/BB/volume signals, combined signal, trade signal

---

## Data Files

| File | Purpose |
|------|---------|
| `data/trades.csv` | Logged trades (from photos) |
| `data/watchlist.json` | Tickers being monitored |
| `data/voice_log.jsonl` | Voice/text interaction log |
| `data/market.duckdb` | All market data |
| `data/analysis_reports/` | Saved TradingAgents reports |
| `data/audio/` | Voice notes + transcripts |
| `data/images/` | Trade photos |

---

## Code Structure

| File | Role |
|------|------|
| `voice_logger_bot.py` | Entry point — wires handlers + starts bot |
| `stock_bot/config.py` | Constants, paths, prompts, logging |
| `stock_bot/llm.py` | Llama-server lifecycle, whisper, AI requests |
| `stock_bot/trades.py` | CSV read/write, duplicate detection, watchlist |
| `stock_bot/portfolio.py` | FIFO matching, yfinance prices, summary |
| `stock_bot/handlers.py` | All Telegram command/message handlers |
| `data_eng/db.py` | DuckDB schema (7 tables) |
| `data_eng/ingest.py` | All ingestors + batch download |
| `data_eng/__main__.py` | CLI: `python -m data_eng` |
| `analysis/runner.py` | TradingAgents config + report saving |
| `analysis/duckdb_vendor.py` | DuckDB adapter (replaces live API calls) |

---

## Daily Routine

1. `llama-server` running on port 10000
2. `python voice_logger_bot.py` running
3. `python -m data_eng --batch` once a day (keeps prices fresh)
4. Use `/analyze TICKER` when you want a decision
5. Use `/report TICKER` when you want the full reasoning
