"""Bulk enrichment with priority-ordered scheduling.

Processes ALL universe tickers (any rating), ordered by:
1. Watchlist membership (always first)
2. Rating priority: Strong Buy > Buy > Hold > Sell > Underperform > NULL/other
3. Sector priority: technology > industrials > consumer-defensive > healthcare
   > financial-services > consumer-cyclical > energy > communication-services
   > utilities > basic-materials > real-estate > unknown
4. Data staleness: oldest-fetched first (never-fetched first)

This spreads the refresh across runs instead of one big staleness wave.
"""

import logging
import time
from pathlib import Path

from .db import SKIP_ATTEMPT_THRESHOLD, SKIP_RETRY_DAYS, clear_miss, get_connection, record_miss
from .ingest import API_PAUSE, ingest_fundamentals, ingest_analyst_targets, ingest_enriched

log = logging.getLogger(__name__)

# Default batch size per run
DEFAULT_LIMIT = 100

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"

# Rating priority (lower = processed first); NULL/NaN/unmatched ratings last
RATING_PRIORITY_SQL = """
    CASE
        WHEN LOWER(e.rating) = 'strong buy' THEN 1
        WHEN LOWER(e.rating) = 'buy' THEN 2
        WHEN LOWER(e.rating) = 'hold' THEN 3
        WHEN LOWER(e.rating) = 'sell' THEN 4
        WHEN LOWER(e.rating) = 'underperform' THEN 5
        ELSE 6
    END
"""

# Sector priority (lower = processed first); unknown/unmatched sectors last
SECTOR_PRIORITY_SQL = """
    CASE LOWER(e.sector)
        WHEN 'technology' THEN 1
        WHEN 'industrials' THEN 2
        WHEN 'consumer-defensive' THEN 3
        WHEN 'healthcare' THEN 4
        WHEN 'financial-services' THEN 5
        WHEN 'consumer-cyclical' THEN 6
        WHEN 'energy' THEN 7
        WHEN 'communication-services' THEN 8
        WHEN 'utilities' THEN 9
        WHEN 'basic-materials' THEN 10
        WHEN 'real-estate' THEN 11
        ELSE 12
    END
"""


def _load_watchlist() -> list[str]:
    """Load watchlist tickers."""
    if WATCHLIST_FILE.exists():
        import json
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Priority query builder
# ---------------------------------------------------------------------------


def _skip_condition_sql(skip_source: str | None, skip_retry_days: int) -> tuple[str, list]:
    """Build a NOT EXISTS filter excluding no-data tickers within retry window."""
    if not skip_source:
        return "1=1", []
    sql = f"""NOT EXISTS (
            SELECT 1 FROM skip_tickers s
            WHERE s.ticker = e.ticker AND s.source = ?
              AND s.attempts >= {SKIP_ATTEMPT_THRESHOLD}
              AND s.last_attempt >= CURRENT_DATE - {int(skip_retry_days)}
        )"""
    return sql, [skip_source]


def _build_priority_query(
    target_table: str,
    watchlist: list[str],
    sector: str | None,
    limit: int,
    stale_days: int | None = None,
    skip_source: str | None = None,
    skip_retry_days: int = SKIP_RETRY_DAYS,
    date_col: str = "date_fetched",
) -> tuple[str, list]:
    """Build a priority-ordered query for bulk enrichment.

    All universe tickers are eligible (any rating). Ordering:
    watchlist first, then rating priority, sector priority, staleness.

    Args:
        stale_days: If set, exclude tickers whose newest row is fresher than
            this many days (never-loaded tickers always stay eligible). This
            stops re-loading data that was just refreshed. None = no filter.
        skip_source: If set, exclude tickers recorded in skip_tickers for this
            source with >= SKIP_ATTEMPT_THRESHOLD consecutive empty results,
            until skip_retry_days have passed since the last attempt.
        skip_retry_days: How long a no-data ticker stays skipped.
        date_col: Freshness column of target_table (date_fetched for the
            enrichment tables, date for trading_agent_decisions).

    Returns (query, params) ready for conn.execute().
    """
    watchlist_ph = ", ".join(["?"] * len(watchlist)) if watchlist else "'__none__'"

    conditions = []
    params: list = []
    if sector:
        conditions.append("LOWER(e.sector) = LOWER(?)")
        params.append(sector)
    if stale_days is not None:
        # Keep only stale (older than stale_days) or never-loaded tickers.
        conditions.append(
            f"(f.date_fetched IS NULL OR f.date_fetched < CURRENT_DATE - {int(stale_days)})"
        )
    skip_sql, skip_params = _skip_condition_sql(skip_source, skip_retry_days)
    conditions.append(skip_sql)
    params.extend(skip_params)

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT e.ticker, f.date_fetched
        FROM stock_universe e
        LEFT JOIN (
            SELECT ticker, MAX({date_col}) AS date_fetched
            FROM {target_table}
            GROUP BY ticker
        ) f ON e.ticker = f.ticker
        WHERE {where_clause}
        ORDER BY
            CASE WHEN e.ticker IN ({watchlist_ph}) THEN 0 ELSE 1 END,
            {RATING_PRIORITY_SQL},
            {SECTOR_PRIORITY_SQL},
            f.date_fetched ASC NULLS FIRST
        LIMIT ?
    """

    if watchlist:
        params.extend(watchlist)
    params.append(limit)

    return query, params


# ---------------------------------------------------------------------------
# Fundamentals enrichment (night pipeline)
# ---------------------------------------------------------------------------


def run_enrich(
    sector: str | None = None,
    limit: int = DEFAULT_LIMIT,
    stale_days: int | None = None,
    skip_source: str | None = "fundamentals",
    skip_retry_days: int = SKIP_RETRY_DAYS,
) -> int:
    """Enrich fundamentals for the next batch of tickers (all ratings).

    Args:
        sector: Filter by sector (e.g. "technology"). None = all sectors.
        limit: Max tickers to process this run.
        stale_days: Skip tickers loaded within this many days. None = no skip
            (manual CLI runs process up to limit regardless of freshness).
        skip_source: skip_tickers source to filter/record. None disables
            no-data skip tracking.
        skip_retry_days: How long a no-data ticker stays skipped.

    Returns:
        Number of tickers successfully enriched.
    """
    watchlist = _load_watchlist()
    query, params = _build_priority_query(
        "fundamentals", watchlist, sector, limit, stale_days,
        skip_source, skip_retry_days,
    )

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        log.info("Enrich: no eligible tickers to process.")
        return 0

    log.info("Enrich: processing %d tickers (limit=%d, sector=%s)",
             len(rows), limit, sector or "all")

    success = 0
    for i, (ticker, last_fetched) in enumerate(rows, 1):
        status = "never loaded" if last_fetched is None else f"last: {last_fetched}"
        log.info("Enrich [%d/%d]: %s (%s)", i, len(rows), ticker, status)

        try:
            ok = ingest_fundamentals(ticker)
            if ok:
                success += 1
                if skip_source:
                    clear_miss(ticker, skip_source)
            elif skip_source:
                # Genuine "no data" (fetch errors raise instead)
                record_miss(ticker, skip_source)
        except Exception as e:
            log.warning("Enrich: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        # Progress checkpoint
        if i % 50 == 0:
            log.info("Enrich: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Enrich: complete — %d/%d succeeded", success, len(rows))
    return success


# ---------------------------------------------------------------------------
# Bulk analyst targets (night pipeline)
# ---------------------------------------------------------------------------


def bulk_analyst_targets(
    watchlist: list[str],
    limit: int = 500,
    stale_days: int | None = None,
    skip_source: str | None = "analyst_targets",
    skip_retry_days: int = SKIP_RETRY_DAYS,
) -> int:
    """Bulk-ingest analyst targets for the next batch of tickers (all ratings).

    Ordered by: watchlist > rating priority > sector priority > staleness.
    stale_days: skip tickers loaded within this many days (None = no skip).
    skip_source: skip_tickers source to filter/record (None disables).
    """
    query, params = _build_priority_query(
        "analyst_targets", watchlist, None, limit, stale_days,
        skip_source, skip_retry_days,
    )

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        log.info("Bulk analyst: no eligible tickers to process.")
        return 0

    log.info("Bulk analyst: processing %d tickers (limit=%d)", len(rows), limit)

    success = 0
    for i, (ticker, last_fetched) in enumerate(rows, 1):
        status = "never loaded" if last_fetched is None else f"last: {last_fetched}"
        log.info("Bulk analyst [%d/%d]: %s (%s)", i, len(rows), ticker, status)

        try:
            count = ingest_analyst_targets(ticker)
            if count > 0:
                success += 1
                if skip_source:
                    clear_miss(ticker, skip_source)
            elif skip_source:
                # Fetched fine but nothing came back
                record_miss(ticker, skip_source)
        except Exception as e:
            log.warning("Bulk analyst: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        if i % 50 == 0:
            log.info("Bulk analyst: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Bulk analyst: complete — %d/%d succeeded", success, len(rows))
    return success


# ---------------------------------------------------------------------------
# Bulk ticker_enriched (night pipeline)
# ---------------------------------------------------------------------------


def bulk_enriched(
    watchlist: list[str],
    limit: int = 500,
    stale_days: int | None = None,
    skip_source: str | None = "ticker_enriched",
    skip_retry_days: int = SKIP_RETRY_DAYS,
) -> int:
    """Bulk-ingest ticker_enriched for the next batch of tickers (all ratings).

    Ordered by: watchlist > rating priority > sector priority > staleness.
    stale_days: skip tickers loaded within this many days (None = no skip).
    skip_source: skip_tickers source to filter/record (None disables).
    """
    query, params = _build_priority_query(
        "ticker_enriched", watchlist, None, limit, stale_days,
        skip_source, skip_retry_days,
    )

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        log.info("Bulk enriched: no eligible tickers to process.")
        return 0

    log.info("Bulk enriched: processing %d tickers (limit=%d)", len(rows), limit)

    success = 0
    for i, (ticker, last_fetched) in enumerate(rows, 1):
        status = "never loaded" if last_fetched is None else f"last: {last_fetched}"
        log.info("Bulk enriched [%d/%d]: %s (%s)", i, len(rows), ticker, status)

        try:
            ok = ingest_enriched(ticker)
            if ok:
                success += 1
                if skip_source:
                    clear_miss(ticker, skip_source)
            elif skip_source:
                # Fetched fine but nothing came back
                record_miss(ticker, skip_source)
        except Exception as e:
            log.warning("Bulk enriched: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        if i % 50 == 0:
            log.info("Bulk enriched: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Bulk enriched: complete — %d/%d succeeded", success, len(rows))
    return success
