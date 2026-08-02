"""Bulk fundamentals enrichment with rolling-N scheduling.

Loads fundamentals for universe tickers gated by rating (Buy/Strong Buy)
and watchlist membership. Processes N tickers per run, prioritizing
never-loaded and oldest-loaded first. This spreads the weekly refresh
across daily runs instead of one big staleness wave.
"""

import logging
import time
from pathlib import Path

from .db import get_connection
from .ingest import API_PAUSE, ingest_fundamentals, ingest_analyst_targets, ingest_enriched

log = logging.getLogger(__name__)

# Default ratings eligible for enrichment
DEFAULT_RATINGS = ("Strong Buy", "Buy")

# Default batch size per run
DEFAULT_LIMIT = 100

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"


def _load_watchlist() -> list[str]:
    """Load watchlist tickers."""
    if WATCHLIST_FILE.exists():
        import json
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return []


def run_enrich(
    sector: str | None = None,
    ratings: tuple[str, ...] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Enrich fundamentals for the next batch of eligible tickers.

    Args:
        sector: Filter by sector (e.g. "technology"). None = all sectors.
        ratings: Eligible ratings. Defaults to ("Strong Buy", "Buy").
        limit: Max tickers to process this run.

    Returns:
        Number of tickers successfully enriched.
    """
    ratings = ratings or DEFAULT_RATINGS
    watchlist = _load_watchlist()

    # Build the eligible ticker query
    conn = get_connection()

    # Get eligible tickers: watchlist (always) + universe tickers matching rating filter
    # Then LEFT JOIN fundamentals to find stale/missing, ordered oldest-first
    rating_placeholders = ", ".join(["?"] * len(ratings))

    query = f"""
        WITH eligible AS (
            -- Watchlist tickers (always eligible, any rating)
            SELECT ticker FROM stock_universe
            WHERE ticker IN ({", ".join(["?"] * len(watchlist))})

            UNION

            -- Universe tickers matching rating filter
            SELECT ticker FROM stock_universe
            WHERE rating IN ({rating_placeholders})
            {"AND LOWER(sector) = LOWER(?)" if sector else ""}
        )
        SELECT e.ticker, f.date_fetched
        FROM eligible e
        LEFT JOIN (
            SELECT ticker, MAX(date_fetched) AS date_fetched
            FROM fundamentals
            GROUP BY ticker
        ) f ON e.ticker = f.ticker
        ORDER BY f.date_fetched ASC NULLS FIRST
        LIMIT ?
    """

    params: list = list(watchlist) + list(ratings)
    if sector:
        params.append(sector)
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        log.info("Enrich: no eligible tickers to process.")
        return 0

    log.info("Enrich: processing %d tickers (limit=%d, sector=%s, ratings=%s)",
             len(rows), limit, sector or "all", ratings)

    success = 0
    for i, (ticker, last_fetched) in enumerate(rows, 1):
        status = "never loaded" if last_fetched is None else f"last: {last_fetched}"
        log.info("Enrich [%d/%d]: %s (%s)", i, len(rows), ticker, status)

        try:
            ok = ingest_fundamentals(ticker)
            if ok:
                success += 1
        except Exception as e:
            log.warning("Enrich: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        # Progress checkpoint
        if i % 50 == 0:
            log.info("Enrich: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Enrich: complete — %d/%d succeeded", success, len(rows))
    return success


# ---------------------------------------------------------------------------
# Bulk analyst targets enrichment (night pipeline)
# ---------------------------------------------------------------------------


def bulk_analyst_targets(watchlist: list[str], limit: int = 500) -> int:
    """Bulk-ingest analyst targets: watchlist prioritized, then universe.

    Processes up to `limit` tickers per run, ordered by oldest-fetched first.
    Watchlist tickers are always eligible regardless of rating.
    """
    conn = get_connection()

    rating_placeholders = ", ".join(["?"] * len(DEFAULT_RATINGS))
    query = f"""
        WITH eligible AS (
            SELECT ticker FROM stock_universe
            WHERE ticker IN ({", ".join(["?" ] * len(watchlist))})

            UNION

            SELECT ticker FROM stock_universe
            WHERE rating IN ({rating_placeholders})
        )
        SELECT e.ticker, a.date_fetched
        FROM eligible e
        LEFT JOIN (
            SELECT ticker, MAX(date_fetched) AS date_fetched
            FROM analyst_targets
            GROUP BY ticker
        ) a ON e.ticker = a.ticker
        ORDER BY a.date_fetched ASC NULLS FIRST
        LIMIT ?
    """

    params: list = list(watchlist) + list(DEFAULT_RATINGS) + [limit]
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
        except Exception as e:
            log.warning("Bulk analyst: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        if i % 50 == 0:
            log.info("Bulk analyst: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Bulk analyst: complete — %d/%d succeeded", success, len(rows))
    return success


# ---------------------------------------------------------------------------
# Bulk ticker_enriched ingestion (night pipeline)
# ---------------------------------------------------------------------------


def bulk_enriched(watchlist: list[str], limit: int = 500) -> int:
    """Bulk-ingest ticker_enriched: watchlist prioritized, then universe.

    Processes up to `limit` tickers per run, ordered by oldest-fetched first.
    Watchlist tickers are always eligible regardless of rating.
    """
    conn = get_connection()

    rating_placeholders = ", ".join(["?"] * len(DEFAULT_RATINGS))
    query = f"""
        WITH eligible AS (
            SELECT ticker FROM stock_universe
            WHERE ticker IN ({", ".join(["?" ] * len(watchlist))})

            UNION

            SELECT ticker FROM stock_universe
            WHERE rating IN ({rating_placeholders})
        )
        SELECT e.ticker, t.date_fetched
        FROM eligible e
        LEFT JOIN (
            SELECT ticker, MAX(date_fetched) AS date_fetched
            FROM ticker_enriched
            GROUP BY ticker
        ) t ON e.ticker = t.ticker
        ORDER BY t.date_fetched ASC NULLS FIRST
        LIMIT ?
    """

    params: list = list(watchlist) + list(DEFAULT_RATINGS) + [limit]
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
        except Exception as e:
            log.warning("Bulk enriched: failed for %s: %s", ticker, e)

        time.sleep(API_PAUSE)

        if i % 50 == 0:
            log.info("Bulk enriched: progress %d/%d (%d ok)", i, len(rows), success)

    log.info("Bulk enriched: complete — %d/%d succeeded", success, len(rows))
    return success
