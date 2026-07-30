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
from .analysis_ingest import has_decision_today, ingest_analysis_decision
from .gfinance import ingest_gfinance_overview
from .summarize import generate_news_summaries

log = logging.getLogger(__name__)

# Days after last report_date before we expect the next quarterly filing
FINANCIALS_REFRESH_DAYS = 80


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


def run_daily_pipeline(tickers: list[str]) -> None:
    """Run the full daily data refresh for all watchlist tickers.

    1. Batch-download prices (incremental) for tickers + NZDUSD forex
    2. Per-ticker: news, analyst targets, enriched, financials (smart-scheduled)
    3. Global news
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

        ingest_news(ticker)
        time.sleep(API_PAUSE)

        ingest_analyst_targets(ticker)
        time.sleep(API_PAUSE)

        ingest_enriched(ticker)
        time.sleep(API_PAUSE)

        ingest_fundamentals(ticker)
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
    log.info("Pipeline: scraping Google Finance overviews...")
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

    # 6. TradingAgents analysis + decision ingestion (uses local LLM)
    log.info("Pipeline: running TradingAgents analysis...")
    for ticker in tickers:
        try:
            if has_decision_today(ticker):
                log.info("Pipeline: %s already analyzed today, skipping", ticker)
                continue

            from analysis.runner import run_analysis

            log.info("Pipeline: analyzing %s...", ticker)
            run_analysis(ticker)
            ingest_analysis_decision(ticker)
            log.info("Pipeline: analysis + ingestion complete for %s", ticker)
        except Exception as e:
            log.warning("Pipeline: analysis failed for %s (non-fatal): %s", ticker, e)

    log.info("=== Daily pipeline complete ===")
