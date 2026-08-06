# Pipeline Design — Ideal Schedule

## Scheduled Jobs (voice_logger_bot.py, all times NZT)

| Time | Job |
|------|-----|
| 8:00 AM | Daily pipeline (`run_daily_pipeline`, data refresh + portfolio chain) |
| 9:30 AM | Portfolio summary + morning briefing sent to Telegram (trade plan, committee review, news) |
| 12:00 PM | Night pipeline (bulk enrichment + TradingAgents batch) |
| 2:00 PM | Night pipeline (bulk enrichment + TradingAgents batch) |
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
| Screener | `screener_scores` | `--screen` | Percentile-rank scores: quality, value, momentum, sentiment, risk + overall. Scores the **full universe** (all tickers with `fundamentals` data) so scores keep pace with night enrichment |
| Candidates | `candidates` | `--candidates` | Sector-balanced top-N from screener, correlation-filtered (0.85 threshold) |
| Events | `events` | `--events` | Detected events are stored when they trigger analysis |
| Portfolio engine | `portfolio_decisions` | `--portfolio` | Deterministic rules on `trading_agent_decisions` + `screener_scores` + holdings (trades.csv). Max 20%/stock, 35%/sector, min screener score 80, 10% cash reserve, stop loss mandatory |
| Portfolio review | `portfolio_reviews` | `--review` | LLM investment committee reviews today's decisions (needs llama-server + today's `portfolio_decisions`) |

TradingAgents (`trading_agent_decisions`) **moved to the night pipeline** —
see below. The morning run reuses the latest stored decisions; on-demand
single-ticker analysis is still available via `/analyze <TICKER>` in the bot.

**Dependency chain:** `fundamentals`/`technicals` → screener → candidates → candidate news → portfolio engine → review. TradingAgents runs in the evening on the same queue (candidates + watchlist).

**Candidate news (step 7b):** after candidate selection, the pipeline
ingests news + generates AI summaries for candidates that aren't on the
watchlist. This way the evening TradingAgents analyses have fresh news for
them too (before this, off-watchlist candidates were analyzed with stale or
missing news).

### Observed run time (measured 2026-08-06, ~2,772 universe / 4 watchlist)

| Step | ~Time/ticker | ~Total/step (measured) | Notes |
|------|--------------|------------------------|-------|
| Prices (bulk incremental + backfill) | ~0.05s | ~2 min | 2,755 tickers in 4 chunks, ~11,000 rows; ~14 stale/new tickers backfilled individually; delisted stragglers (FONR) warn and move on |
| Technicals | ~0.07s | ~3 min (0 when already done today) | Local computation; skips tickers already processed today |
| News (ticker + global) | ~14s | ~32s | Watchlist tickers only; fresh ones are skipped |
| Overviews (Google Finance + Yahoo) | ~8s each | ~15s | Playwright scrapes for the watchlist |
| AI news summaries | ~13s | ~53s | 4 tickers; needs llama-server |
| Screener + candidates + portfolio engine | n/a | ~1s | Screener scores the full fundamentals-covered universe (~445-750 tickers growing with night enrichment) |
| Portfolio review | n/a | ~22s | One LLM call over today's decisions |

**Total: ~4.5 min** on day one of the 5-run era (TradingAgents no longer
part of the morning run).

Day-one issue found and fixed: the screener crashed on
`float() ... not 'NAType'` — `safe_float` didn't treat `pd.NA` as missing
and `_fetch_price_metrics` let NULL closes carry `pd.NA` into the volatility
math. Both hardened on 2026-08-06; the morning candidates had fallen back to
the previous day's scores.

---

## Night Pipeline (12 PM / 2 PM / 4 PM / 6 PM / 8 PM, 5x per day)

More runs × smaller batches = same refresh cadence as before, but more
TradingAgents analyses per day (up to 30 vs 18).

| Order | Table | Limit/run | Skip if fresher than | ~Time/ticker | ~Total/step (measured) | Notes |
|-------|-------|-----------|----------------------|--------------|------------------------|-------|
| 1 | `stock_universe` | full re-scrape, **once per day** | already scraped today | ~2 min total | ~2 min (run 1 only) | All 4 sector groups; updates ratings, discovers new tickers. Runs 2-5 reuse the first run's scrape (`_universe_scraped_today()`) |
| 2 | `financials` | 80 | 80-day filing cycle (newest `report_date`) | ~7s | ~9 min | Quarterly statements, rolling over the **full universe** (was watchlist-only). A ticker is due when its latest filing is ~80 days old |
| 3 | `fundamentals` | 80 | 7 days | ~2s | ~3 min | Company snapshot (P/E, margins, etc.) |
| 4 | `analyst_targets` | 120 | 3 days | ~3.5s | ~7 min | Consensus + individual analyst ratings/upgrades |
| 5 | `ticker_enriched` | 80 | 7 days | ~7s | ~9 min | Growth estimates, price targets, recommendations, stock trends |
| 6 | `gfinance_overview` + `yfinance_overview` | 12 each | 14 days | Playwright scrape | ~4 min | AI overviews for the wider universe; the 8 AM pipeline keeps the watchlist fresh daily, so this batch works down the priority list |
| 7 | `trading_agent_decisions` | 6 | stale 7d / event-gated | ~6 min | ~34-40 min | TradingAgents batch; needs llama-server |

Limits and staleness thresholds are constants in `data_eng/pipeline.py`:
- Limits: `NIGHT_FUNDAMENTALS_LIMIT` (80), `NIGHT_FINANCIALS_LIMIT` (80), `NIGHT_ANALYST_LIMIT` (120), `NIGHT_ENRICHED_LIMIT` (80), `NIGHT_OVERVIEW_LIMIT` (12), `NIGHT_ANALYSIS_LIMIT` (6)
- Skip-if-fresh: `FUNDAMENTALS_STALE_DAYS` (7), `ANALYST_STALE_DAYS` (3), `ENRICHED_STALE_DAYS` (7), `OVERVIEW_STALE_DAYS` (14), `ANALYSIS_STALE_DAYS` (7), and `FINANCIALS_REFRESH_DAYS` (80, judged on newest `report_date`)
- No-data retry: `SKIP_RETRY_DAYS` (30) and `SKIP_ATTEMPT_THRESHOLD` (2), both in `db.py`

### TradingAgents batch (step 7)

Same stale-refresh pattern as the bulk steps, plus an event layer on top:

1. **Event layer** — candidates + watchlist pass the existing event gate
   (±5% price move, technical signal flip, new earnings filing, or never
   analyzed). Triggered tickers go first even if their last analysis is fresh.
   The news trigger was **removed** (daily pipeline refreshes watchlist news
   every morning, so it would fire every day).
2. **Stale layer** — universe tickers whose last decision is older than
   `ANALYSIS_STALE_DAYS` (or never analyzed), via the same priority query
   (watchlist → rating → sector → staleness).

At most `NIGHT_ANALYSIS_LIMIT` (6) analyses per run; leftovers roll to the
next run — there's no stored queue: selection is stateless, so un-analyzed
tickers stay stale and get picked again (oldest-first within their priority
class). 5 runs/day → up to 30/day capacity. Never analyzes a ticker twice
per day (`_has_decision_today`).

The stale layer uses the **same priority ordering as the bulk steps**
(watchlist → rating → sector → staleness) via `_build_priority_query` on
`trading_agent_decisions`; event-triggered tickers jump ahead of it.

### TradingAgents runtime optimizations (analysis/runner.py)

Everything served from local DuckDB — no live network during an analysis:

- **Bull/Bear debate grounded with Google Finance — no LLM calls**: the
  bull/bear researcher nodes are replaced with LLM-free nodes that write the
  stored `gfinance_overview` bull/bear points straight into the debate
  history (saves ~2 long generations per analysis). The Research Manager
  still evaluates the bull/bear text alongside the analyst reports. The
  night pipeline pre-fetches the overview for each queued ticker before
  analysis (`ensure_gfinance_overview`, fresh = <= 1 day); `/analyze`
  scrapes on demand as a fallback.
- **Verified market snapshot from local prices**: the validator's OHLCV
  loader is patched to read `daily_prices` instead of a live 5-year yfinance
  download (removes network + rate-limit risk mid-analysis).
- **Risk debate is rule-based — no LLM calls** (changed 2026-08-06): the
  aggressive/neutral/conservative debaters are replaced with LLM-free nodes
  that write a fixed-perspective stance into the debate history (saves ~3
  long generations per analysis — each otherwise received all analyst
  reports plus full context). The trader plan is NOT echoed in the notes;
  the Portfolio Manager receives it directly. The PM still weighs the
  trader plan against the three stances.
- **Output token caps**: quick-tier generations capped at 1536 tokens,
  deep-tier at 2048 (`LLM_MAX_TOKENS_*` in `analysis/runner.py`). The GPU is
  generation-bound (~96% util), so runaway outputs were the biggest time
  sink; caps remove the long tail without touching the short structured
  outputs (trader/PM JSON).
- **Reports**: only the consolidated report is kept —
  `data/analysis_reports/reports/TICKER_TIMESTAMP.md` (e.g.
  `FROG_20260806_211112.md`). The per-section tree (1_analysts/… etc.) is no
  longer written.
- **Stubbed**: Reddit, StockTwits, FRED macro, Polymarket prediction markets
  (no keys/integrations) — each returns a "not available" string.
- All analyst data tools (prices, indicators, fundamentals, statements, news,
  insiders) route to the DuckDB vendor.

Each bulk step **skips tickers whose data is still fresh** (loaded within its
staleness window), so the watchlist is not needlessly re-loaded every run. A
stale watchlist ticker still jumps to the front of the queue and refreshes first.

### No-data skip tracking (`skip_tickers` table)

One shared table tracks tickers that keep returning **no data**, per source:
`fundamentals`, `financials`, `analyst_targets`, `ticker_enriched`,
`gfinance_overview`, `yfinance_overview`, and `prices`. They stop
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

### Bulk ordering (all bulk steps)

All universe tickers are eligible (any rating), processed in priority order:

1. **Watchlist** tickers first (always)
2. **Rating**: Strong Buy → Buy → Hold → Sell → Underperform → NULL/nan
3. **Sector**: technology → industrials → consumer-defensive → healthcare → financial-services → consumer-cyclical → energy → communication-services → utilities → basic-materials → real-estate → unknown
4. **Staleness**: never-loaded first, then oldest-loaded first

### Refresh cadence (2,700 tickers, 5 runs/day)

With the skip-if-fresh filter, a ticker is re-ingested roughly when it crosses
its staleness threshold. Capacity (limit × 5 runs) is sized to cover it:

| Table | Capacity | Stale after | Effective refresh |
|-------|----------|-------------|-------------------|
| `fundamentals` | 400/day | 7 days | ~7 days (needs ~386/day) |
| `financials` | 400/day | 80-day filing cycle | Mostly skip-gated; only tickers due for a new quarter are fetched |
| `analyst_targets` | 600/day cap | 3 days | ~3 days for the priority head; long tail cycles slower |
| `ticker_enriched` | 400/day | 7 days | ~7 days (needs ~386/day) |
| AI overviews | 60/day each | 14 days | Priority head (top ~840) on a 14-day window; watchlist already fresh from the morning run |

### Estimated run time

- Ingestion (steps 1-6), **worst case** (full limits, clearing a backlog): ~35 min.
- Typical steady state: ~20-30 min (most steps only process stale tickers; overviews add Playwright scrape time).
- TradingAgents adds up to 6 analyses when the budget is full. With
  bull/bear AND the risk trio LLM-free plus output token caps (2026-08-06),
  per-analysis time should drop well below the measured ~6 min — re-measure
  on the next night run.
- Total stays well under the 2-hour spacing; if runs ever overlap, lower
  `NIGHT_ANALYSIS_LIMIT` first — unfinished tickers just roll over.

### Observed run time (measured 2026-08-06, first day on the 5-run schedule)

Three full runs (12/2/4 PM slots), all at full limits while clearing backlog:

| Step | Measured | Notes |
|------|----------|-------|
| Financials | ~556s / 80 tickers | Slowest bulk step (~7s/ticker) |
| Fundamentals | ~160s / 76-79 | A few no-data tickers (RPT, AMBQ) — skip-tracked |
| Analyst targets | ~409s / 119 of 120 | 404s on data-less microcaps (PRPO, ERIE, SHOE) are expected |
| Ticker enriched | ~548s / 80 | |
| AI overviews | ~230-265s | gfinance 12/12 every run; **yfinance yield is low** (2-8/12, many empty overviews) |
| TradingAgents | 2055-2374s / 5-6 analyses | ~6 min/analysis with the LLM-free bull/bear nodes |
| **Total** | **3950-4310s (~66-72 min)** | Comfortably inside the 2-hour spacing |

One analysis failure on day 1 (ALMU): the LLM called the `get_global_news`
tool without the optional `look_back_days`/`limit` args and the DuckDB vendor
crashed on `None`. Fixed on 2026-08-06 — the vendor now resolves omitted args
from `DEFAULT_CONFIG`, same as the yfinance vendor.

---

## Telegram Briefing & Advice

The 9:30 AM push is a full morning briefing (sent after the charts +
portfolio summary):

1. **Trade plan** — today's BUY proposals (shares, % of portfolio, stop loss,
   reason) and SELLs from `portfolio_decisions`; HOLD count only.
2. **Committee review** — the LLM investment-committee review from
   `portfolio_reviews` (previously generated but never shown).
3. **News briefing** — AI news summaries (Catalysts/Sentiment/Risks format)
   for watchlist + today's candidates.

`/advice` command (on demand):

- `/advice` — same trade plan + **Ideas**: top 3 screener tickers you don't
  hold and haven't watched (score, price + day change, latest TradingAgents
  verdict if analyzed).
- `/advice TICKER` — full card: latest price + day change, screener score,
  latest TradingAgents decision (action/rating, entry/stop/target, horizon,
  trimmed summary), and latest news summary.

All reads are straight from DuckDB — no LLM call at command time.

---

## Manual CLI Commands

```powershell
cd C:\repo\telegram-bot
.\venv\Scripts\activate

# Daily pipeline (what the bot runs at 8 AM)
python -m data_eng --daily
python -m data_eng --daily-smart   # same, but skips fresh per-ticker data

# Night pipeline
python -m data_eng --night              # scheduled limits (80/80/120/80/12) + up to 6 analyses (needs llama-server)
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
- **Why rolling `financials` matters for TradingAgents**: the analysis fundamentals tools (`analysis/duckdb_vendor.py`) read balance sheet / cashflow / income statement from this table. Before the rolling batch, off-watchlist tickers returned `NO_DATA_AVAILABLE` during analysis; now they get 4 quarters of real statements. It also unlocks the earnings event trigger (new filing → re-analysis) for the whole universe, not just the watchlist.
- `/summary TICKER` in Telegram shows a ticker's **latest** decision regardless of date (the night pipeline only re-analyzes on events/staleness, so "today" is often empty); bare `/summary` lists today's decisions.
- `daily_prices` uses a fast bulk incremental download, chunked 700 tickers per yfinance request with pauses between chunks, plus one retry pass after 20s for rate-limited tickers (constants `PRICE_CHUNK_SIZE` / `PRICE_CHUNK_PAUSE` / `PRICE_RETRY_PAUSE` in ingest.py). Only tickers with no data or stale > 30 days are backfilled one-at-a-time; repeat no-data tickers are skipped via `skip_tickers` (source `prices`).
- The manual `--enrich` CLI does NOT skip fresh tickers (processes up to `--limit` regardless of age) so it behaves predictably for one-off runs; the scheduled night pipeline is the one that skips fresh.
- `rating` in stock_universe has string `'nan'` values (~270 rows) treated as lowest priority; consider normalizing to NULL someday.
- **Portfolio sizing uses market value, not cost basis**: deployable capital = `TOTAL_CAPITAL` ($28,000) × 90% − current holdings value at latest close. Market value is correct here — new buys are sized against what the portfolio is worth now.
- **`trade_signal` is a trend regime, not a prediction** (changed 2026-08-06): majority vote of three consistently trend-following tests — close vs SMA50, SMA20 vs SMA50, MACD histogram sign. It replaced the old hand-picked weighted sum that mixed trend and mean-reversion signals. Its only consumer is the `technical_change` event gate, which re-analyzes a ticker when the regime flips.
- **Data provenance**: see `notes/report_definition.md` for where every number in `/advice` and the 9:30 briefing comes from (external source → DuckDB table → display).
