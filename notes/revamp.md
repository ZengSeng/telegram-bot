# Revamp: Phases 1-8 Implementation

Done in one session. All phases build on each other. DB must be free (stop bot) to run CLI commands.

---

## Phase 1: Stock Universe

Scrapes Yahoo Finance sectors/industries/top-companies into a broad ticker universe.

| File | Change |
|------|--------|
| `data_eng/universe.py` | **NEW** — `UniverseScraper` class, `SECTOR_GROUPS`, `stock_universe` table |
| `data_eng/pipeline.py` | Added `run_universe_build()`, `run_universe_group()` |
| `data_eng/__main__.py` | Added `--universe`, `--universe-group N` flags |

**Run:**
```
venv\Scripts\python -m data_eng --universe            # full scrape (~15 min)
venv\Scripts\python -m data_eng --universe-group 1    # stagger by group (1-4)
```

**Tables created:** `stock_universe`

---

## Phase 2: Smart Data Refresh

Staleness-check helpers + dividends/stock_splits in daily_prices.

| File | Change |
|------|--------|
| `data_eng/pipeline.py` | Added `_is_stale()`, `should_ingest_news/enriched/analyst/fundamentals()`, `use_smart_scheduling` param |
| `data_eng/db.py` | ALTER TABLE `daily_prices` ADD `dividends`, `stock_splits` |
| `data_eng/ingest.py` | `ingest_prices()` + `batch_ingest_prices()` now capture corporate actions |
| `data_eng/__main__.py` | Added `--daily-smart` flag |

**Run:**
```
venv\Scripts\python -m data_eng --daily-smart
```

**Thresholds:** news=1d, enriched=3d, analyst=3d, fundamentals=7d, financials=80d (unchanged)

---

## Phase 3: Quantitative Screener

Percentile-rank scoring across 5 categories (18 metrics total).

| File | Change |
|------|--------|
| `data_eng/screener.py` | **NEW** — `compute_raw_metrics()`, `percentile_rank()`, `run_screener()` |
| `data_eng/db.py` | Added `screener_scores` table |
| `data_eng/pipeline.py` | Step 6 in daily pipeline |
| `data_eng/__main__.py` | Added `--screen` flag |

**Run:**
```
venv\Scripts\python -m data_eng --screen
```

**Tables created:** `screener_scores`

**Categories:** Quality (6 metrics), Value (3), Momentum (4), Sentiment (2), Risk (3)

**Note:** Only scores tickers that have `fundamentals` data. Currently watchlist only. Universe tickers need fundamentals ingested first.

---

## Phase 4: Candidate Selection

Sector-balanced top-N + correlation filtering.

| File | Change |
|------|--------|
| `data_eng/candidates.py` | **NEW** — `select_candidates()`, `SECTOR_ALLOCATION`, correlation filter |
| `data_eng/db.py` | Added `candidates` table |
| `data_eng/pipeline.py` | Step 7 in daily pipeline |
| `data_eng/__main__.py` | Added `--candidates` flag |

**Run:**
```
venv\Scripts\python -m data_eng --candidates
```

**Tables created:** `candidates`

**Allocation:** Tech=5, Healthcare=3, Financials=3, Consumer-cyclical=2, Industrials=2, Energy=1, Comm=1, Consumer-defensive=1, Unknown=2

**Correlation:** removes pairs >0.85 (6-month daily returns), keeps higher-scored ticker.

---

## Phase 5: Event Detection

Gates expensive TradingAgents analysis — only re-runs when something meaningful changed.

| File | Change |
|------|--------|
| `data_eng/events.py` | **NEW** — `should_run_analysis()`, `detect_events()`, 4 detectors |
| `data_eng/db.py` | Added `events` table (audit trail) |
| `data_eng/pipeline.py` | Step 8 gated by `should_run_analysis()` instead of `has_decision_today()` |
| `data_eng/__main__.py` | Added `--events` flag |

**Run:**
```
venv\Scripts\python -m data_eng --events
```

**Tables created:** `events`

**Detectors:**
- `price_move` — abs(daily return) > 5%
- `news` — new articles since last analysis
- `technical_change` — trade_signal flipped vs previous enrichment
- `earnings` — new financials report_date since last analysis

**Behavior:** TradingAgents skips if no events detected ("reusing last analysis"). Always runs on first-time tickers.

---

## Pipeline Order (daily)

1. Batch prices (incremental)
2. Per-ticker: news, analyst, enriched, fundamentals, financials (smart-scheduled)
3. Global news
4. Google Finance overviews
5. AI news summaries
6. **Screener** (percentile-rank scoring)
7. **Candidate selection** (sector-balanced + correlation filter)
8. TradingAgents analysis **(candidates + watchlist, gated by event detection)**
9. **Portfolio engine** (deterministic rules → BUY/SELL/HOLD)
10. **Portfolio review** (LLM investment committee)

---

## Phase 6: TradingAgents at Scale

Wires TradingAgents to analyze screener-selected candidates (not just watchlist).

| File | Change |
|------|--------|
| `data_eng/candidates.py` | Added `get_analysis_tickers(watchlist)` — unions candidates + watchlist |
| `data_eng/pipeline.py` | Step 8 iterates analysis set (candidates + watchlist) instead of watchlist only |

**Behavior:** Analyzes ~12-15 candidates + watchlist (deduplicated). Falls back to watchlist-only if no candidates exist yet.

---

## Phase 7: Portfolio Engine

Deterministic rules engine — no AI. Takes TradingAgents decisions + screener scores and produces actionable trade proposals.

| File | Change |
|------|--------|
| `data_eng/portfolio_engine.py` | **NEW** — `run_portfolio_engine()`, FIFO holdings loader, sector/position rule enforcement |
| `data_eng/db.py` | Added `portfolio_decisions` table |
| `data_eng/pipeline.py` | Step 9 in daily pipeline |
| `data_eng/__main__.py` | Added `--portfolio` flag |

**Run:**
```
venv\Scripts\python -m data_eng --portfolio
```

**Tables created:** `portfolio_decisions`

**Rules:**
- max 20% portfolio in one stock
- max 35% in one sector
- don't buy if screener score < 80 (top 20%)
- keep 10% cash reserve
- stop loss mandatory (defaults to 8% below entry if TradingAgents doesn't provide one)
- position sizing: equal-weight among approved buys from remaining deployable capital

**Config:** `TOTAL_CAPITAL` in `portfolio_engine.py` (default $10,000) — adjust to your portfolio size.

---

## Phase 8: Portfolio Review (LLM)

LLM acts as an investment committee — reviews today's portfolio decisions for contradictions, concentration risks, and cross-holding interactions. Not a decision-maker, a second pair of eyes.

| File | Change |
|------|--------|
| `data_eng/portfolio_review.py` | **NEW** — `run_portfolio_review()`, prompt builder, LLM call |
| `data_eng/db.py` | Added `portfolio_reviews` table |
| `data_eng/pipeline.py` | Step 10 in daily pipeline |
| `data_eng/__main__.py` | Added `--review` flag |

**Run:**
```
venv\Scripts\python -m data_eng --review
```

**Tables created:** `portfolio_reviews`

**Prompt focus:** contradictions, concentration risks, cross-holding interactions, oversized positions, missing considerations the rules engine can't see.

**Requires:** Local llama-server running on port 10000. Gracefully skips if LLM unavailable.

---

## What's Next (not built)

All 8 phases complete. Remaining work:

- **Universe fundamentals:** Need bulk fundamentals ingestion for 2700+ universe tickers (only watchlist has data currently). Screener + candidates only work on tickers with fundamentals.
- **Telegram integration:** Surface portfolio decisions and reviews via bot commands.

---

## Testing Checklist (when DB is free)

```powershell
# 1. Verify schema (creates new tables if missing)
venv\Scripts\python -c "from data_eng.db import get_connection; conn = get_connection(); print('Schema OK'); conn.close()"

# 2. Check universe populated
venv\Scripts\python -c "from data_eng.universe import UniverseScraper; s = UniverseScraper(); t = s.get_universe_tickers(); print(f'{len(t)} tickers in universe')"

# 3. Run screener (needs fundamentals data)
venv\Scripts\python -m data_eng --screen

# 4. Run candidate selection (needs screener scores)
venv\Scripts\python -m data_eng --candidates

# 5. Detect events for watchlist
venv\Scripts\python -m data_eng --events

# 6. Run portfolio engine
venv\Scripts\python -m data_eng --portfolio

# 7. Run portfolio review (needs LLM server)
venv\Scripts\python -m data_eng --review

# 8. Full smart pipeline (watchlist + candidates + portfolio + review)
venv\Scripts\python -m data_eng --daily-smart
```
