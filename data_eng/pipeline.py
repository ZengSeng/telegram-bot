"""Daily data pipeline: refresh all DuckDB tables for the watchlist."""

import logging
import time
from datetime import date, timedelta

from .db import get_connection
from .ingest import (
    API_PAUSE,
    batch_ingest_prices,
    ingest_analyst_targets,
    ingest_enriched,
    ingest_financials,
    ingest_fundamentals,
    ingest_global_news,
    ingest_news,
)
from .analysis_ingest import ingest_analysis_decision
from .gfinance import ingest_gfinance_overview
from .summarize import generate_news_summaries

log = logging.getLogger(__name__)

# Days after last report_date before we expect the next quarterly filing
FINANCIALS_REFRESH_DAYS = 80

# Staleness thresholds (days) for smart scheduling
NEWS_STALE_DAYS = 1
ENRICHED_STALE_DAYS = 3
ANALYST_STALE_DAYS = 3
FUNDAMENTALS_STALE_DAYS = 7


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


def should_ingest_enriched(ticker: str) -> bool:
    """Growth estimates + targets change slowly — 3-day threshold."""
    return _is_stale("ticker_enriched", "date_fetched", ticker, ENRICHED_STALE_DAYS)


def should_ingest_analyst(ticker: str) -> bool:
    """Analyst targets overlap with enrichment cycle — 3-day threshold."""
    return _is_stale("analyst_targets", "date_fetched", ticker, ANALYST_STALE_DAYS)


def should_ingest_fundamentals(ticker: str) -> bool:
    """Company info snapshot — 7-day threshold."""
    return _is_stale("fundamentals", "date_fetched", ticker, FUNDAMENTALS_STALE_DAYS)


def should_ingest_financials(ticker: str) -> bool:
    """Check if financials need refreshing based on projected next report date.

    Returns True if:
    - No financials exist for the ticker, OR
    - The latest report_date + 80 days <= today (new quarter likely reported)
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(report_date) FROM financials WHERE ticker = ?", [ticker]
    ).fetchone()
    conn.close()

    if not row or not row[0]:
        return True

    last_report = row[0] if isinstance(row[0], date) else row[0].date()
    projected_next = last_report + timedelta(days=FINANCIALS_REFRESH_DAYS)
    return date.today() >= projected_next


def run_daily_pipeline(tickers: list[str], use_smart_scheduling: bool = False) -> None:
    """Run the full daily data refresh for all watchlist tickers.

    1. Batch-download prices (incremental) for tickers + NZDUSD forex
    2. Per-ticker: news, analyst targets, enriched, financials (smart-scheduled)
    3. Global news

    When use_smart_scheduling=True, each per-ticker ingestion is gated by a
    staleness check to avoid unnecessary API calls.
    """
    if not tickers:
        log.warning("Pipeline called with empty tickers list.")
        return

    log.info("=== Daily pipeline started for %d tickers ===", len(tickers))

    # 1. Batch prices (includes NZDUSD=X for forex rate)
    all_price_tickers = list(tickers)
    if "NZDUSD=X" not in all_price_tickers:
        all_price_tickers.append("NZDUSD=X")
    batch_ingest_prices(all_price_tickers)

    # 2. Per-ticker enrichment loop
    for ticker in tickers:
        log.info("Pipeline: processing %s", ticker)

        if not use_smart_scheduling or should_ingest_news(ticker):
            ingest_news(ticker)
        else:
            log.info("Pipeline: news still fresh for %s, skipping", ticker)
        time.sleep(API_PAUSE)

        if not use_smart_scheduling or should_ingest_analyst(ticker):
            ingest_analyst_targets(ticker)
        else:
            log.info("Pipeline: analyst targets still fresh for %s, skipping", ticker)
        time.sleep(API_PAUSE)

        if not use_smart_scheduling or should_ingest_enriched(ticker):
            ingest_enriched(ticker)
        else:
            log.info("Pipeline: enriched still fresh for %s, skipping", ticker)
        time.sleep(API_PAUSE)

        if not use_smart_scheduling or should_ingest_fundamentals(ticker):
            ingest_fundamentals(ticker)
        else:
            log.info("Pipeline: fundamentals still fresh for %s, skipping", ticker)
        time.sleep(API_PAUSE)

        if should_ingest_financials(ticker):
            log.info("Pipeline: financials due for %s", ticker)
            ingest_financials(ticker)
        else:
            log.info("Pipeline: financials still fresh for %s, skipping", ticker)
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

    # 5. AI news summaries (runs last, uses local LLM)
    log.info("Pipeline: generating AI news summaries...")
    try:
        results = generate_news_summaries(tickers)
        log.info("Pipeline: summarized %d ticker(s)", len(results))
    except Exception as e:
        log.warning("Pipeline: news summarization failed (non-fatal): %s", e)

    # 5b. Bulk fundamentals enrichment (rolling N/day for universe)
    try:
        from .enrich import run_enrich

        log.info("Pipeline: enriching universe fundamentals (rolling batch)...")
        enriched_count = run_enrich(limit=100)
        log.info("Pipeline: enriched %d tickers", enriched_count)
    except Exception as e:
        log.warning("Pipeline: enrichment failed (non-fatal): %s", e)

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

    # 8. TradingAgents analysis (candidates + watchlist, event-gated)
    from .candidates import get_analysis_tickers

    analysis_tickers = get_analysis_tickers(tickers)
    log.info("Pipeline: running TradingAgents on %d tickers...", len(analysis_tickers))
    for ticker in analysis_tickers:
        try:
            from .events import should_run_analysis

            if not should_run_analysis(ticker):
                log.info("Pipeline: %s — no events, reusing last analysis", ticker)
                continue

            from analysis.runner import run_analysis

            log.info("Pipeline: analyzing %s...", ticker)
            run_analysis(ticker)
            ingest_analysis_decision(ticker)
            log.info("Pipeline: analysis + ingestion complete for %s", ticker)
        except Exception as e:
            log.warning("Pipeline: analysis failed for %s (non-fatal): %s", ticker, e)

    # 9. Portfolio engine (deterministic rules on decisions + scores)
    try:
        from .portfolio_engine import run_portfolio_engine

        log.info("Pipeline: running portfolio engine...")
        results = run_portfolio_engine()
        buys = [r for r in results if r["action"] == "BUY"]
        sells = [r for r in results if r["action"] == "SELL"]
        log.info("Pipeline: portfolio engine done — %d BUY, %d SELL", len(buys), len(sells))
    except Exception as e:
        log.warning("Pipeline: portfolio engine failed (non-fatal): %s", e)

    # 10. Portfolio review (LLM investment committee)
    try:
        from .portfolio_review import run_portfolio_review

        log.info("Pipeline: running portfolio review...")
        review = run_portfolio_review()
        if review and not review.startswith("("):
            log.info("Pipeline: portfolio review complete (%d chars)", len(review))
        else:
            log.info("Pipeline: portfolio review skipped (%s)", review or "no decisions")
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
# Night pipeline (3 PM NZT): universe refresh + prices + enrich
# ---------------------------------------------------------------------------


def run_night_pipeline(enrich_limit: int = 100) -> None:
    """Night pipeline: refresh universe, batch prices, then enrich fundamentals.

    Designed to run at 3 PM NZT (after US pre-market data settles).
    1. Scrape universe (updates ratings, discovers new tickers)
    2. Batch-ingest prices for all universe tickers
    3. Enrich fundamentals (rolling N/day)
    """
    from .universe import UniverseScraper
    from .enrich import run_enrich

    log.info("=== Night pipeline started ===")
    t0 = time.time()

    # 1. Universe scrape (all groups)
    scraper = UniverseScraper()
    all_tickers = scraper.scrape_all()
    log.info("Night: universe scrape done — %d tickers in %.1fs",
             len(all_tickers), time.time() - t0)

    # 2. Batch prices for full universe
    if all_tickers:
        t1 = time.time()
        batch_ingest_prices(list(all_tickers))
        log.info("Night: price ingest done in %.1fs", time.time() - t1)

    # 3. Enrich fundamentals (rolling batch)
    t2 = time.time()
    enriched = run_enrich(limit=enrich_limit)
    log.info("Night: enriched %d tickers in %.1fs", enriched, time.time() - t2)

    log.info("=== Night pipeline complete (%.1fs total) ===", time.time() - t0)
