"""Daily data pipeline: refresh all DuckDB tables for the watchlist."""

import logging
import time
from datetime import date, timedelta

from .db import SKIP_RETRY_DAYS, get_connection
from .ingest import (
    API_PAUSE,
    batch_ingest_prices,
    batch_ingest_technicals,
    ingest_financials,
    ingest_global_news,
    ingest_news,
    ingest_yfinance_overview,
)
from .analysis_ingest import ingest_analysis_decision
from .enrich import bulk_analyst_targets, bulk_enriched, run_enrich
from .gfinance import ingest_gfinance_overview
from .summarize import generate_news_summaries

log = logging.getLogger(__name__)

# Days after last report_date before we expect the next quarterly filing
FINANCIALS_REFRESH_DAYS = 80

# Staleness thresholds (days): a ticker is only re-ingested once its newest
# row is older than this. Drives the daily news gate and the night bulk skip.
NEWS_STALE_DAYS = 1
ENRICHED_STALE_DAYS = 7
ANALYST_STALE_DAYS = 3
FUNDAMENTALS_STALE_DAYS = 7
ANALYSIS_STALE_DAYS = 7

# Night pipeline bulk limits (tickers per run). Sized so
# limit x 3 runs/day x stale_days covers the ~2700-ticker universe:
#   fundamentals: 130 x 3 x 7 = 2730  -> 7-day refresh
#   analyst:      200 x 3 x 3 = 1800  -> 3-day refresh for the priority head
#                                        (watchlist/top-rated); long tail slower
#   enriched:     130 x 3 x 7 = 2730  -> 7-day refresh
NIGHT_FUNDAMENTALS_LIMIT = 130
NIGHT_ANALYST_LIMIT = 200
NIGHT_ENRICHED_LIMIT = 130

# TradingAgents analyses per night run (~7 min each). With 3 runs/day that's
# up to 18/day; un-analyzed tickers roll over to the next run.
NIGHT_ANALYSIS_LIMIT = 6

# No-data skip tracking lives in db.py: SKIP_ATTEMPT_THRESHOLD (misses before
# a ticker is skipped) and SKIP_RETRY_DAYS (days before it's retried),
# recorded in the skip_tickers table.


def _is_stale(table: str, date_col: str, ticker: str, max_age_days: int) -> bool:
    """Check if latest data for ticker in table is older than max_age_days.

    Returns True if no data exists or data is stale.
    """
    conn = get_connection()
    row = conn.execute(
        f"SELECT MAX({date_col}) FROM {table} WHERE ticker = ?", [ticker]
    ).fetchone()
    conn.close()

    if not row or not row[0]:
        return True

    last_date = row[0] if isinstance(row[0], date) else row[0].date()
    return date.today() - last_date >= timedelta(days=max_age_days)


def should_ingest_news(ticker: str) -> bool:
    """News changes daily — refresh if older than 1 day."""
    return _is_stale("news", "date", ticker, NEWS_STALE_DAYS)


def should_ingest_financials(ticker: str) -> bool:
    """Check if financials need refreshing based on projected next report date.

    Returns True if:
    - No financials exist for the ticker, OR
    - The latest report_date + 80 days <= today (new quarter likely reported)
    """
    return _is_stale("financials", "report_date", ticker, FINANCIALS_REFRESH_DAYS)


def run_daily_pipeline(tickers: list[str], use_smart_scheduling: bool = False) -> None:
    """Run the full daily data refresh for all watchlist tickers.

    1. Batch-download prices (incremental) for full universe + NZDUSD forex
    2. Per-ticker: news (smart-scheduled)
    3. Global news, Google Finance, AI summaries
    4. Screener → candidates → portfolio engine → review

    Fundamentals, financials, analyst targets and enrichment are handled by
    the night pipeline (rolling batches). TradingAgents analysis is also
    night-only (event-gated + stale refresh, capped).

    When use_smart_scheduling=True, each per-ticker ingestion is gated by a
    staleness check to avoid unnecessary API calls.
    """
    if not tickers:
        log.warning("Pipeline called with empty tickers list.")
        return

    log.info("=== Daily pipeline started for %d tickers ===", len(tickers))

    # 1. Batch prices — full universe (not just watchlist) + NZDUSD forex
    from .universe import UniverseScraper

    scraper = UniverseScraper()
    universe_tickers = scraper.get_universe_tickers()
    all_price_tickers = list(dict.fromkeys(universe_tickers))  # dedupe, preserve order
    if "NZDUSD=X" not in all_price_tickers:
        all_price_tickers.append("NZDUSD=X")
    log.info("Daily: batch prices for %d universe tickers", len(all_price_tickers))
    batch_ingest_prices(all_price_tickers)

    # 1b. Technical indicators (computed locally from daily_prices)
    log.info("Daily: computing technical indicators...")
    batch_ingest_technicals(all_price_tickers)

    # 2. Per-ticker: news only (smart-scheduled)
    for ticker in tickers:
        log.info("Pipeline: processing %s", ticker)

        if not use_smart_scheduling or should_ingest_news(ticker):
            ingest_news(ticker)
        else:
            log.info("Pipeline: news still fresh for %s, skipping", ticker)
        time.sleep(API_PAUSE)

    # 3. Global news
    ingest_global_news()

    # 4. Google Finance AI overview (Playwright scrape)
    log.info("Pipeline: scraping Google Finance overviews, bull and bear view...")
    for ticker in tickers:
        try:
            ingest_gfinance_overview(ticker)
        except Exception as e:
            log.warning("Pipeline: Google Finance scrape failed for %s (non-fatal): %s", ticker, e)

    # 4b. Yahoo Finance AI overview (web scrape)
    log.info("Pipeline: fetching Yahoo Finance overviews...")
    for ticker in tickers:
        try:
            ingest_yfinance_overview(ticker)
        except Exception as e:
            log.warning("Pipeline: Yahoo Finance overview failed for %s (non-fatal): %s", ticker, e)

    # 5. AI news summaries (runs last, uses local LLM)
    log.info("Pipeline: generating AI news summaries...")
    try:
        results = generate_news_summaries(tickers)
        log.info("Pipeline: summarized %d ticker(s)", len(results))
    except Exception as e:
        log.warning("Pipeline: news summarization failed (non-fatal): %s", e)

    # 6. Quantitative screener (percentile-rank scoring)
    log.info("Pipeline: running quantitative screener...")
    try:
        from .screener import run_screener

        scores = run_screener(tickers)
        if not scores.empty:
            top = scores.sort_values("overall_score", ascending=False).head(5)
            log.info("Pipeline: screener top 5: %s", list(top.index))
    except Exception as e:
        log.warning("Pipeline: screener failed (non-fatal): %s", e)

    # 7. Candidate selection (sector-balanced, correlation-filtered)
    log.info("Pipeline: selecting candidates...")
    try:
        from .candidates import select_candidates

        candidates = select_candidates()
        if not candidates.empty:
            log.info("Pipeline: %d candidates selected: %s", len(candidates), list(candidates["ticker"]))
    except Exception as e:
        log.warning("Pipeline: candidate selection failed (non-fatal): %s", e)

    # 8. Portfolio engine (deterministic rules on decisions + scores)
    try:
        from .portfolio_engine import run_portfolio_engine

        log.info("Pipeline: running portfolio engine...")
        results = run_portfolio_engine()
        buys = [r for r in results if r["action"] == "BUY"]
        sells = [r for r in results if r["action"] == "SELL"]
        log.info("Pipeline: portfolio engine done — %d BUY, %d SELL", len(buys), len(sells))
    except Exception as e:
        log.warning("Pipeline: portfolio engine failed (non-fatal): %s", e)

    # 9. Portfolio review (LLM investment committee)
    try:
        from .portfolio_review import run_portfolio_review

        log.info("Pipeline: running portfolio review...")
        review = run_portfolio_review()
        if review:
            log.info("Pipeline: portfolio review complete (%d chars)", len(review))
        else:
            log.info("Pipeline: portfolio review skipped (no decisions or LLM unavailable)")
    except Exception as e:
        log.warning("Pipeline: portfolio review failed (non-fatal): %s", e)

    log.info("=== Daily pipeline complete ===")


# ---------------------------------------------------------------------------
# Universe build / group refresh
# ---------------------------------------------------------------------------


def run_universe_build() -> None:
    """Scrape the full stock universe, then batch-ingest prices for all tickers."""
    from .universe import UniverseScraper

    log.info("=== Universe build started ===")
    t0 = time.time()

    scraper = UniverseScraper()
    all_tickers = scraper.scrape_all()

    elapsed_scrape = time.time() - t0
    log.info("Universe scrape complete: %d tickers in %.1fs", len(all_tickers), elapsed_scrape)

    if all_tickers:
        t1 = time.time()
        batch_ingest_prices(list(all_tickers))
        log.info("Universe price ingest complete in %.1fs", time.time() - t1)

    log.info("=== Universe build complete (%.1fs total) ===", time.time() - t0)


def run_universe_group(group_id: int) -> None:
    """Scrape one sector group, then batch-ingest prices for that group's tickers."""
    from .universe import UniverseScraper

    log.info("=== Universe group %d started ===", group_id)
    t0 = time.time()

    scraper = UniverseScraper()
    tickers = scraper.scrape_sector_group(group_id)

    elapsed_scrape = time.time() - t0
    log.info("Group %d scrape complete: %d tickers in %.1fs", group_id, len(tickers), elapsed_scrape)

    if tickers:
        t1 = time.time()
        batch_ingest_prices(list(tickers))
        log.info("Group %d price ingest complete in %.1fs", group_id, time.time() - t1)

    log.info("=== Universe group %d complete (%.1fs total) ===", group_id, time.time() - t0)


# ---------------------------------------------------------------------------
# Night pipeline: universe refresh + prices + enrich + TradingAgents batch
# ---------------------------------------------------------------------------


def run_night_analysis(watchlist: list[str], limit: int = NIGHT_ANALYSIS_LIMIT) -> int:
    """TradingAgents batch: same stale-refresh pattern as the bulk enrich steps,
    with an event layer on top.

    1. Event layer: candidates + watchlist pass the existing event gate
       (price move / technical flip / earnings / never-analyzed). Triggered
       tickers go first even if their last analysis is fresh.
    2. Stale layer: universe tickers whose last decision is older than
       ANALYSIS_STALE_DAYS (or never analyzed), reusing the priority query
       (watchlist -> rating -> sector -> staleness).

    At most `limit` analyses run; the rest rolls over to the next night run.
    Returns the number of analyses completed.
    """
    from .candidates import get_analysis_tickers
    from .enrich import _build_priority_query
    from .events import _has_decision_today, should_run_analysis

    # 1. Event layer (candidates + watchlist through the existing gate)
    event_queue: list[str] = []
    for ticker in get_analysis_tickers(watchlist):
        try:
            if should_run_analysis(ticker):
                event_queue.append(ticker)
        except Exception as e:
            log.warning("Night: event check failed for %s: %s", ticker, e)

    # 2. Stale layer (rolling refresh over the universe, watchlist prioritized)
    conn = get_connection()
    query, params = _build_priority_query(
        "trading_agent_decisions", watchlist, None, limit,
        stale_days=ANALYSIS_STALE_DAYS, date_col="date",
    )
    stale_queue = [r[0] for r in conn.execute(query, params).fetchall()]
    conn.close()

    # Merge: events first, then stale fill; dedupe preserving order
    queue = list(dict.fromkeys(event_queue + stale_queue))
    log.info("Night: TradingAgents queue — %d event-triggered + %d stale "
             "(%d unique, budget %d)",
             len(event_queue), len(stale_queue), len(queue), limit)

    analyzed = 0
    for ticker in queue:
        if analyzed >= limit:
            log.info("Night: analysis budget (%d) reached — remaining "
                     "tickers roll to the next run", limit)
            break
        try:
            # Stale-layer picks may already have been analyzed today via the
            # event layer (or /analyze); never run twice per day.
            if _has_decision_today(ticker):
                continue

            # Pre-populate Google Finance bull/bear grounding so the debate
            # nodes read from DuckDB instead of scraping mid-analysis.
            try:
                from .gfinance import ensure_gfinance_overview

                ensure_gfinance_overview(ticker)
            except Exception as e:
                log.warning("Night: gfinance pre-fetch failed for %s (non-fatal): %s",
                            ticker, e)

            from analysis.runner import run_analysis

            log.info("Night: analyzing %s (%d/%d)...", ticker, analyzed + 1, limit)
            run_analysis(ticker)
            ingest_analysis_decision(ticker)
            analyzed += 1
            log.info("Night: analysis + ingestion complete for %s", ticker)
        except Exception as e:
            log.warning("Night: analysis failed for %s (non-fatal): %s", ticker, e)
    return analyzed


def run_night_pipeline(
    tickers: list[str] | None = None,
    fundamentals_limit: int = NIGHT_FUNDAMENTALS_LIMIT,
    analyst_limit: int = NIGHT_ANALYST_LIMIT,
    enriched_limit: int = NIGHT_ENRICHED_LIMIT,
    analysis_limit: int = NIGHT_ANALYSIS_LIMIT,
) -> None:
    """Night pipeline: universe refresh + bulk enrichment + analysis batch.

    Runs 3x in the evening (see NIGHT_PIPELINE_TIMES in stock_bot/config.py).
    1. Scrape universe (updates ratings, discovers new tickers)
    2. Ingest financials for watchlist tickers (80-day cycle)
    3. Bulk-ingest fundamentals (rolling N/night, watchlist prioritized)
    4. Bulk-ingest analyst targets (rolling N/night, watchlist prioritized)
    5. Bulk-ingest ticker_enriched (rolling N/night, watchlist prioritized)
    6. TradingAgents analysis (event-gated, capped at NIGHT_ANALYSIS_LIMIT)
    """
    from .universe import UniverseScraper

    log.info("=== Night pipeline started ===")
    t0 = time.time()

    # 1. Universe scrape (all groups)
    scraper = UniverseScraper()
    all_tickers = scraper.scrape_all()
    log.info("Night: universe scrape done — %d tickers in %.1fs",
             len(all_tickers), time.time() - t0)

    # 2. Financials for watchlist tickers (quarterly, 80-day cycle)
    watchlist = tickers or []
    if watchlist:
        t1 = time.time()
        fin_count = 0
        for ticker in watchlist:
            if should_ingest_financials(ticker):
                log.info("Night: financials due for %s", ticker)
                try:
                    ingest_financials(ticker)
                    fin_count += 1
                except Exception as e:
                    log.warning("Night: financials failed for %s (non-fatal): %s", ticker, e)
                time.sleep(API_PAUSE)
            else:
                log.info("Night: financials still fresh for %s, skipping", ticker)
        log.info("Night: financials done — %d refreshed in %.1fs",
                 fin_count, time.time() - t1)

    # 3. Bulk-ingest fundamentals (rolling batch, watchlist prioritized, skip fresh)
    t2 = time.time()
    enriched = run_enrich(
        limit=fundamentals_limit,
        stale_days=FUNDAMENTALS_STALE_DAYS,
        skip_retry_days=SKIP_RETRY_DAYS,
    )
    log.info("Night: fundamentals enriched %d tickers in %.1fs", enriched, time.time() - t2)

    # 4. Bulk-ingest analyst targets (rolling batch, watchlist prioritized, skip fresh)
    t3 = time.time()
    analyst_count = bulk_analyst_targets(
        watchlist,
        limit=analyst_limit,
        stale_days=ANALYST_STALE_DAYS,
        skip_retry_days=SKIP_RETRY_DAYS,
    )
    log.info("Night: analyst targets done — %d tickers in %.1fs",
             analyst_count, time.time() - t3)

    # 5. Bulk-ingest ticker_enriched (rolling batch, watchlist prioritized, skip fresh)
    t4 = time.time()
    enriched_count = bulk_enriched(
        watchlist,
        limit=enriched_limit,
        stale_days=ENRICHED_STALE_DAYS,
        skip_retry_days=SKIP_RETRY_DAYS,
    )
    log.info("Night: ticker_enriched done — %d tickers in %.1fs",
             enriched_count, time.time() - t4)

    # 6. TradingAgents analysis (event-gated rolling batch, needs llama-server)
    t5 = time.time()
    try:
        analyzed = run_night_analysis(watchlist, limit=analysis_limit)
        log.info("Night: TradingAgents done — %d analyses in %.1fs",
                 analyzed, time.time() - t5)
    except Exception as e:
        log.warning("Night: TradingAgents batch failed (non-fatal): %s", e)

    log.info("=== Night pipeline complete (%.1fs total) ===", time.time() - t0)
