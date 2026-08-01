# Stock Universe & Data Refresh Pipeline

## Summary

Expand the system from a small watchlist to a broad Yahoo Finance-sourced stock universe, with tiered refresh scheduling. One new module (`data_eng/universe.py`), additive changes to existing files, zero regression risk.

---

## Phase 1: Build the Stock Universe

### Task 1 — Create `data_eng/universe.py` and `stock_universe` table

**New file:** `data_eng/universe.py`

**`stock_universe` table** (created in the module, following the pattern in `ingest.py` where each function does `CREATE TABLE IF NOT EXISTS`):

| Column | Type | Notes |
|--------|------|-------|
| ticker | VARCHAR PK | |
| company_name | VARCHAR | |
| sector | VARCHAR | yfinance sector slug |
| industry | VARCHAR | yfinance industry name |
| rating | VARCHAR | "Strong Buy", "Buy", "Hold", "Sell", "Underperform" |
| group_id | SMALLINT | 1-4, which staggered group this belongs to |
| sector_weight | DOUBLE | sector market weight |
| company_weight | DOUBLE | company weight within sector |
| date_added | DATE | first scrape date |
| last_updated | DATE | most recent scrape date |

**Constants:**
- `SECTOR_GROUPS` — the 4 groups from `notes.md`:
  - Group 1: industrials, utilities, basic-materials
  - Group 2: financial-services, real-estate, communication-services
  - Group 3: technology, energy, consumer-defensive
  - Group 4: healthcare, consumer-cyclical

**Class `UniverseScraper`:**
- `scrape_sector_group(group_id: int) -> set[str]` — iterates sectors in the group using `yf.Sector(sector_key)` → `sector.industries` → `yf.Industry(name)` → `industry.top_companies`. Extracts ticker, name, rating, sector/industry weights. Upserts to `stock_universe`. Rate limit: 0.5s between industries. try/except per industry (continue on failure).
- `scrape_all() -> set[str]` — iterates all 4 groups.
- `get_universe_tickers(group_id=None, min_rating=None) -> list[str]` — queries universe table with optional filters.
- `get_shortlisted_tickers() -> list[str]` — returns Strong Buy + Buy rated tickers (for Phase 3+ enrichment).

**Adapt from** `dump/scrape_sector.py` lines 33-86 (SectorScraper.scrape_sectors pattern). Ignore `empty_enrichment_fields` per user instruction.

**Critical files:** `data_eng/universe.py` (new), `dump/scrape_sector.py` (reference only)

---

### Task 2 — Add universe CLI flags and pipeline entry points

**Modify:** `data_eng/pipeline.py`

Add two functions (appended, no changes to existing code):
- `run_universe_build()` — calls `UniverseScraper().scrape_all()`, then `batch_ingest_prices(all_tickers)` for the full universe
- `run_universe_group(group_id)` — calls `UniverseScraper().scrape_sector_group(group_id)`, then `batch_ingest_prices(group_tickers)` for that group only

Both log timing and ticker counts.

**Modify:** `data_eng/__main__.py`

Add argparse arguments:
- `--universe` → calls `run_universe_build()`
- `--universe-group N` (choices 1-4) → calls `run_universe_group(N)`

Existing `--batch`, `--daily`, positional tickers unchanged.

---

## Phase 2: Smart Data Refresh Scheduling

### Task 3 — Add staleness-check helpers to `pipeline.py`

**Modify:** `data_eng/pipeline.py`

Generalize the existing `should_ingest_financials()` pattern (lines 28-46) into a reusable helper:

```python
def _is_stale(table: str, date_col: str, ticker: str, max_age_days: int) -> bool:
    """Check if latest data for ticker in table is older than max_age_days."""

def should_ingest_news(ticker: str) -> bool:        # 1-day threshold
def should_ingest_enriched(ticker: str) -> bool:     # 3-day threshold  
def should_ingest_analyst(ticker: str) -> bool:      # 3-day threshold
def should_ingest_fundamentals(ticker: str) -> bool: # 7-day threshold
```

**Refresh thresholds** (days, conservative to avoid unnecessary API calls):

| Data Type | Threshold | Rationale |
|-----------|-----------|-----------|
| daily_prices | Always (incremental) | `batch_ingest_prices` already skips up-to-date tickers |
| news | 1 | Changes daily |
| ticker_enriched | 3 | Growth estimates + targets change slowly |
| analyst_targets | 3 | Overlaps with enrichment cycle |
| fundamentals | 7 | Company info snapshot |
| financials | 80 | Existing quarterly logic (unchanged) |

---

### Task 4 — Smart-scheduled pipeline + CLI flag

**Modify:** `data_eng/pipeline.py` — `run_daily_pipeline()`

Add optional parameter (backward-compatible, default=False):

```python
def run_daily_pipeline(tickers: list[str], use_smart_scheduling: bool = False) -> None:
```

When `use_smart_scheduling=True`, wrap each per-ticker ingestion in `should_ingest_*()` checks. Log skip/ingest decisions. When False, behavior is identical to current code.

**Modify:** `data_eng/__main__.py`

Add: `--daily-smart` → calls `run_daily_pipeline(tickers, use_smart_scheduling=True)`

---

### Task 5 — Add `actions=True` columns to `daily_prices`

**Modify:** `data_eng/db.py` — add columns to schema:
```sql
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS dividends DECIMAL(18,4) DEFAULT 0;
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS stock_splits DECIMAL(18,4) DEFAULT 0;
```
(Use ALTER TABLE to handle existing database safely.)

**Modify:** `data_eng/ingest.py` — `batch_ingest_prices()` (lines 610-725) and `ingest_prices()` (lines 23-56):
- Capture Dividends and Stock Splits columns from `yf.download()` output
- Insert into `daily_prices` with the new columns
- Follow the pattern from `dump/scrape_stock_prices.py` lines 86-108 (column flattening, fillna(0))

---

## Future Phase Coherence

| Future Phase | What Phase 1+2 Provides |
|---|---|
| Phase 3 (Quantitative screener) | `universe` table has sector/industry/rating for grouping; `fundamentals` + `ticker_enriched` have all metrics; `daily_prices` has returns data |
| Phase 4 (Candidate selection) | `get_shortlisted_tickers()` filters by rating; sector diversity via `group_id` |
| Phase 5 (Event detection) | `daily_prices` with dividends/splits enables split detection; staleness helpers enable "skip if nothing changed" |
| Phase 6 (TradingAgents at scale) | Smart scheduling already limits expensive analysis to shortlisted tickers |
| Phase 7-8 (Portfolio) | Sector/industry metadata in universe table enables concentration rules |

---

## Files Changed

| File | Change Type | Description |
|------|-------------|-------------|
| `data_eng/universe.py` | **NEW** | UniverseScraper class, SECTOR_GROUPS, stock_universe table |
| `data_eng/pipeline.py` | MODIFY | Staleness helpers, smart scheduling param, universe pipeline functions |
| `data_eng/__main__.py` | MODIFY | `--universe`, `--universe-group`, `--daily-smart` flags |
| `data_eng/db.py` | MODIFY | ALTER TABLE for dividends/stock_splits columns |
| `data_eng/ingest.py` | MODIFY | Capture dividends/stock_splits in price ingestion |

---

## Execution Order

1. **Task 1** (universe.py + table) — no dependencies, foundational
2. **Task 5** (db.py schema + ingest.py dividends) — parallel with Task 1 (different files)
3. **Task 2** (pipeline universe functions + CLI) — depends on Task 1
4. **Task 3** (staleness helpers) — depends on Task 2 (same file, sequential edits)
5. **Task 4** (smart pipeline + CLI flag) — depends on Task 3

Tasks 1 and 5 can run in parallel. Tasks 2-4 are sequential on the same files.

---

## Rejected Alternatives

- **Separate `scheduler.py` module** (Plan A): Over-engineering. Smart scheduling is 4 helper functions that naturally extend `pipeline.py` alongside the existing `should_ingest_financials()`.
- **Concurrent news fetching with ThreadPoolExecutor** (Plan B): Premature. Serial with 1.5s pauses is proven reliable. Can revisit in Phase 3+ when ticker count demands it.
- **`DELETE FROM stock_universe` before rebuild** (Plan B): Destructive. `INSERT OR REPLACE` preserves historical membership and enables tracking universe changes over time.
- **Merging `analyst_targets` into `ticker_enriched`**: The user noted this overlap. However, `ingest_analyst_targets()` also fetches `upgrades_downgrades` (individual analyst calls) which `_fetch_enrichment()` doesn't. Merging would require restructuring both. Deferred to a future cleanup — smart scheduling already ensures both run on the same 3-day cycle.
- **Config-driven thresholds** (Plan C): Scheduling thresholds belong in `pipeline.py` next to the functions that use them, not in `stock_bot/config.py` which is bot-specific.