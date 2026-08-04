# Pipeline Design — Ideal Schedule

## Scheduled Jobs (voice_logger_bot.py, all times NZT)

| Time | Job |
|------|-----|
| 8:00 AM | Daily pipeline (`run_daily_pipeline`, data refresh + portfolio chain) |
| 9:30 AM | Portfolio summary sent to Telegram |
| 4:00 PM | Night pipeline (bulk enrichment + TradingAgents batch) |
| 6:00 PM | Night pipeline (bulk enrichment + TradingAgents batch) |
| 8:00 PM | Night pipeline (bulk enrichment + TradingAgents batch) |

Config: `stock_bot/config.py` → `PIPELINE_HOUR`, `SUMMARY_HOUR/MINUTE`, `NIGHT_PIPELINE_TIMES`.

---

## Daily Pipeline (8:00 AM)

### Step 1 — Bulk data (full universe, ~2,700 tickers)

| Table | Scope | Notes |
|-------|-------|-------|
| `stock_universe` | All sectors | Read via `get_universe_tickers()`; full re-scrape happens in the night pipeline |
| `daily_prices` | Full universe + `NZDUSD=X` | Batch incremental download (fast, only fetches new rows) |
| `technicals` (derived) | Full universe | Computed locally from `daily_prices` via `ta` lib; skips tickers already done today |

### Step 2 — Watchlist-only ingestion

| Table | Notes |
|-------|-------|
| `news` | Per-ticker; smart-scheduling skips if < 1 day old (`--daily-smart` only) |
| `global_news` | 3 search queries, once per run |
| `gfinance_overview` | Playwright scrape: AI summary + sentiment + bull/bear points |
| `yfinance_overview` | Yahoo Finance web scrape |
| `news_summaries` (derived) | Local LLM summarizes today's news per ticker (needs llama-server) |

### Step 3 — Analysis chain (lines below tested via CLI flags)

| Step | Table | CLI to test | Notes |
|------|-------|-------------|-------|
| Screener | `screener_scores` | `--screen` | Percentile-rank scores: quality, value, momentum, sentiment, risk + overall. Scores all tickers with `fundamentals` data |
| Candidates | `candidates` | `--candidates` | Sector-balanced top-N from screener, correlation-filtered (0.85 threshold) |
| Events | `events` | `--events` | Detected events are stored when they trigger analysis |
| Portfolio engine | `portfolio_decisions` | `--portfolio` | Deterministic rules on `trading_agent_decisions` + `screener_scores` + holdings (trades.csv). Max 20%/stock, 35%/sector, min screener score 80, 10% cash reserve, stop loss mandatory |
| Portfolio review | `portfolio_reviews` | `--review` | LLM investment committee reviews today's decisions (needs llama-server + today's `portfolio_decisions`) |

TradingAgents (`trading_agent_decisions`) **moved to the night pipeline** —
see below. The morning run reuses the latest stored decisions; on-demand
single-ticker analysis is still available via `/analyze <TICKER>` in the bot.

**Dependency chain:** `fundamentals`/`technicals` → screener → candidates → portfolio engine → review. TradingAgents runs in the evening on the same queue (candidates + watchlist).

### Observed run time (measured 2026-08-04, ~2,755 universe / 4 watchlist)

| Step | Time | Notes |
|------|------|-------|
| Prices (bulk incremental + backfill) | ~1-2 min | Chunked 700/request with pauses + one retry pass; ~40 stale/new tickers backfilled individually |
| Technicals | ~3 min | 2,703 tickers, local computation |
| News (ticker + global) | ~15s | |
| Overviews (Google Finance + Yahoo) | ~43s | Playwright scrapes |
| AI news summaries | ~1 min | Needs llama-server |
| Screener + candidates | ~1s | |

**Total: ~6-7 min** (TradingAgents no longer part of the morning run).

---

## Night Pipeline (6 PM / 8 PM / 10 PM, 3x per evening)

| Order | Table | Limit/run | Skip if fresher than | ~Time/ticker | Notes |
|-------|-------|-----------|----------------------|--------------|-------|
| 1 | `stock_universe` | full re-scrape | — | ~2 min total | All 4 sector groups; updates ratings, discovers new tickers |
| 2 | `financials` | watchlist only | 80-day cycle | ~2s | Quarterly statements; refresh when last report_date + 80d passed |
| 3 | `fundamentals` | 130 | 7 days | ~2s | Company snapshot (P/E, margins, etc.) |
| 4 | `analyst_targets` | 500 | 3 days | ~3.5s | Consensus + individual analyst ratings/upgrades |
| 5 | `ticker_enriched` | 130 | 7 days | ~7s | Growth estimates, price targets, recommendations, stock trends |
| 6 | `trading_agent_decisions` | 6 | stale 7d / event-gated | ~7 min | TradingAgents batch; needs llama-server |

Limits and staleness thresholds are constants in `data_eng/pipeline.py`:
- Limits: `NIGHT_FUNDAMENTALS_LIMIT`, `NIGHT_ANALYST_LIMIT`, `NIGHT_ENRICHED_LIMIT`, `NIGHT_ANALYSIS_LIMIT` (6)
- Skip-if-fresh: `FUNDAMENTALS_STALE_DAYS` (7), `ANALYST_STALE_DAYS` (3), `ENRICHED_STALE_DAYS` (7), `ANALYSIS_STALE_DAYS` (7)
- No-data retry: `SKIP_RETRY_DAYS` (30) and `SKIP_ATTEMPT_THRESHOLD` (2), both in `db.py`

### TradingAgents batch (step 6)

Same stale-refresh pattern as the bulk steps, plus an event layer on top:

1. **Event layer** — candidates + watchlist pass the existing event gate
   (±5% price move, technical signal flip, new earnings filing, or never
   analyzed). Triggered tickers go first even if their last analysis is fresh.
   The news trigger is **disabled** (daily pipeline refreshes watchlist news
   every morning, so it would fire every day) — commented out in events.py.
2. **Stale layer** — universe tickers whose last decision is older than
   `ANALYSIS_STALE_DAYS` (or never analyzed), via the same priority query
   (watchlist → rating → sector → staleness).

At most `NIGHT_ANALYSIS_LIMIT` (6) analyses per run; leftovers roll to the
next run. 3 runs/day → up to 18/day capacity. Measured ~6-8.5 min per
analysis on the local 9B model. Never analyzes a ticker twice per day.

### TradingAgents runtime optimizations (analysis/runner.py)

Everything served from local DuckDB — no live network during an analysis:

- **Bull/Bear debate grounded with Google Finance**: the bull/bear researcher
  nodes are patched to inject the stored `gfinance_overview` bull/bear points
  as evidence. The night pipeline pre-fetches the overview for each queued
  ticker before analysis (`ensure_gfinance_overview`, fresh = <= 1 day);
  `/analyze` scrapes on demand as a fallback.
- **Verified market snapshot from local prices**: the validator's OHLCV
  loader is patched to read `daily_prices` instead of a live 5-year yfinance
  download (removes network + rate-limit risk mid-analysis).
- **Stubbed**: Reddit, StockTwits, FRED macro, Polymarket prediction markets
  (no keys/integrations) — each returns a "not available" string.
- All analyst data tools (prices, indicators, fundamentals, statements, news,
  insiders) route to the DuckDB vendor.

Each bulk step **skips tickers whose data is still fresh** (loaded within its
staleness window), so the watchlist is not needlessly re-loaded every run. A
stale watchlist ticker still jumps to the front of the queue and refreshes first.

### No-data skip tracking (`skip_tickers` table)

One shared table tracks tickers that keep returning **no data**, per source:
`fundamentals`, `analyst_targets`, `ticker_enriched`, and `prices`. They stop
burning API calls every run:

1. A fetch that succeeds but returns nothing records a miss in `skip_tickers`
   (transient errors/network failures are NOT counted).
2. After **2 consecutive misses** (`SKIP_ATTEMPT_THRESHOLD` in db.py) the
   ticker is excluded from that source's queue.
3. It gets retried once `SKIP_RETRY_DAYS` (30) pass since the last attempt.
4. Any successful fetch clears the record (attempt counter resets).

For **prices** this covers the dead/delisted tickers (SPACs etc.) in the daily
backfill loop — after 2 empty backfills they're skipped, so the log noise and
wasted requests disappear. Helpers: `record_miss` / `clear_miss` /
`get_skipped_tickers` in db.py.

To reset manually: `DELETE FROM skip_tickers;` (optionally `WHERE source = '...'`).

### Bulk ordering (all 3 bulk steps)

All universe tickers are eligible (any rating), processed in priority order:

1. **Watchlist** tickers first (always)
2. **Rating**: Strong Buy → Buy → Hold → Sell → Underperform → NULL/nan
3. **Sector**: technology → industrials → consumer-defensive → healthcare → financial-services → consumer-cyclical → energy → communication-services → utilities → basic-materials → real-estate → unknown
4. **Staleness**: never-loaded first, then oldest-loaded first

### Refresh cadence (2,700 tickers, 3 runs/day)

With the skip-if-fresh filter, a ticker is re-ingested roughly when it crosses
its staleness threshold. Capacity (limit × 3 runs) is sized to cover it:

| Table | Capacity | Stale after | Effective refresh |
|-------|----------|-------------|-------------------|
| `fundamentals` | 390/day | 7 days | ~7 days (needs ~386/day) |
| `analyst_targets` | 1,500/day cap | 3 days | ~3 days (needs ~900/day; headroom) |
| `ticker_enriched` | 390/day | 7 days | ~7 days (needs ~386/day) |

### Estimated run time

- Ingestion (steps 1-5), **worst case** (full limits, clearing a backlog): ~50 min.
- Typical steady state: ~35-40 min (analyst step processes only ~300 stale).
- TradingAgents adds up to 6 × ~7 min = ~42 min when the budget is full.
- Total stays well under the 2-hour spacing; if runs ever overlap, lower
  `NIGHT_ANALYSIS_LIMIT` first — unfinished tickers just roll over.

---

## Manual CLI Commands

```powershell
cd C:\repo\telegram-bot
.\venv\Scripts\activate

# Daily pipeline (what the bot runs at 8 AM)
python -m data_eng --daily
python -m data_eng --daily-smart   # same, but skips fresh per-ticker data

# Night pipeline
python -m data_eng --night              # full limits (130/500/130) + up to 6 analyses (needs llama-server)
python -m data_eng --night --limit 10 --analysis-limit 1   # smoke test: 10 tickers/step, 1 analysis
python -m data_eng --night --limit 10 --analysis-limit 0   # ingestion only

# Individual analysis steps (test screener → review chain)
python -m data_eng --screen
python -m data_eng --candidates
python -m data_eng --events
python -m data_eng --portfolio
python -m data_eng --review          # needs llama-server on :10000

# Other
python -m data_eng --batch           # watchlist prices only
python -m data_eng --batch-universe  # universe prices only
python -m data_eng --enrich --sector technology --limit 20
```

## Notes

- LLM steps (news summaries, TradingAgents, portfolio review) need llama-server on `http://127.0.0.1:10000`. The bot starts it automatically; manual CLI runs need it started separately.
- All pipeline steps after ingestion are non-fatal: a failure logs a warning and the pipeline continues.
- `daily_prices` uses a fast bulk incremental download, chunked 700 tickers per yfinance request with pauses between chunks, plus one retry pass after 20s for rate-limited tickers (constants `PRICE_CHUNK_SIZE` / `PRICE_CHUNK_PAUSE` / `PRICE_RETRY_PAUSE` in ingest.py). Only tickers with no data or stale > 30 days are backfilled one-at-a-time; repeat no-data tickers are skipped via `skip_tickers` (source `prices`).
- The manual `--enrich` CLI does NOT skip fresh tickers (processes up to `--limit` regardless of age) so it behaves predictably for one-off runs; the scheduled night pipeline is the one that skips fresh.
- `rating` in stock_universe has string `'nan'` values (~270 rows) treated as lowest priority; consider normalizing to NULL someday.
