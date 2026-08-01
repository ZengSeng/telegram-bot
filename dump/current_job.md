
Here's the full picture:

## Two jobs in `voice_logger_bot.py`

| Job | Time | Function | What it does |
|-----|------|----------|--------------|
| 1 | **8:00 AM** NZT | `run_pipeline_job` | `run_daily_pipeline(watchlist)` — full 15-step pipeline |
| 2 | **9:30 AM** NZT | `send_daily_summary` | Sends portfolio summary to Telegram |

**Note:** The night pipeline (`--night`) is NOT scheduled in the bot. It's manual CLI only.

## Tables refreshed by Job 1 (8 AM daily pipeline)

| Step | Tables | Scope |
|------|--------|-------|
| ① Batch prices | `daily_prices` | watchlist + NZDUSD |
| ②-⑥ Per-ticker | `news`, `analyst_targets`, `ticker_enriched`, `fundamentals`, `financials` | watchlist |
| ⑦-⑨ Global | `global_news`, `gfinance_overview`, `news_summaries` | watchlist |
| ⑩ Rolling enrich | `fundamentals` | 100 universe tickers |
| ⑪-⑫ Screen | `screener_scores`, `candidates` | all tickers with data |
| ⑬ TradingAgents | `trading_agent_decisions`, `events` | candidates + watchlist |
| ⑭-⑮ Portfolio | `portfolio_decisions`, `portfolio_reviews` | derived |

## Your two proposals — my confirmation:

**1. Remove fundamentals from daily per-ticker loop (step ⑤):**
Yes, safe. The nightly enrich already covers watchlist (always eligible). Fundamentals have a 7-day staleness window — they don't need daily refresh. The screener at 8 AM will still use valid data (at most 1 day stale from last night's enrich).

**2. Move universe batch prices from night → daily:**
Yes, and this actually **fixes two problems:**
- Screener at 8 AM gets fresh prices for all 2700 tickers (currently uses yesterday's)
- Solves the DuckDB lock issue — the bot holds the DB, so `--night` can't run while bot is alive. Moving prices into the daily pipeline (runs inside the bot) eliminates the conflict.

**Resulting split:**
- **Daily (8 AM, in-bot):** batch prices (ALL universe), per-ticker enrichment (watchlist), rolling enrich, screener, candidates, TradingAgents, portfolio
- **Night (3 PM, manual/cron):** universe scrape only (updates ratings/sectors, discovers new tickers)

The night pipeline becomes just a lightweight universe metadata refresh (~2 min). Want me to proceed?