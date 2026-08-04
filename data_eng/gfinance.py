"""Google Finance AI overview scraper via Playwright (headless Chromium).

Extracts the AI-generated summary, sentiment breakdown, and bull/bear points
from Google Finance's overview tab. Stores results in DuckDB.
"""

import json
import logging
import re
from datetime import date

from .db import get_connection

log = logging.getLogger(__name__)

# Exchange suffixes to try (Google Finance requires TICKER:EXCHANGE)
_EXCHANGES = ["NASDAQ", "NYSE", "NYSEAMERICAN"]


def _parse_sentiment(text: str) -> float | None:
    """Extract percentage from text like '58.3% bullish'."""
    m = re.search(r"([\d.]+)%", text)
    return float(m.group(1)) if m else None


def scrape_overview(ticker: str) -> dict | None:
    """Scrape Google Finance AI overview for a ticker.

    Returns dict with keys: summary, pct_bullish, pct_neutral, pct_bearish,
    bull_points (list[dict]), bear_points (list[dict]).
    Returns None on failure.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()

        result = None
        for exchange in _EXCHANGES:
            url = f"https://www.google.com/finance/quote/{ticker}:{exchange}"
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_selector("div.CV4Mbd", timeout=8000)
                result = _extract(page)
                if result:
                    break
            except Exception:
                continue

        browser.close()

    if not result:
        log.warning("Google Finance overview not found for %s", ticker)
    return result


def _extract(page) -> dict | None:
    """Extract overview data from a loaded Google Finance page."""
    try:
        # AI Summary
        summary = page.text_content("div.CV4Mbd span.LpnQz") or ""
        summary = summary.strip()

        # Sentiment percentages
        pct_bullish = pct_neutral = pct_bearish = None
        sentiments = page.query_selector_all("div.hXfVSd div")
        for s in sentiments:
            text = s.text_content().strip().lower()
            val = _parse_sentiment(text)
            if "bullish" in text:
                pct_bullish = val
            elif "neutral" in text:
                pct_neutral = val
            elif "bearish" in text:
                pct_bearish = val

        # Bull/Bear points
        bull_points = []
        bear_points = []
        try:
            page.wait_for_selector("div.pdSppd", timeout=5000)
            sections = page.query_selector_all("div.RTPuNd")
            for sec in sections:
                heading = sec.query_selector("div.N2LRAe")
                label = heading.text_content().strip().lower() if heading else ""
                items = sec.query_selector_all("li.METkLd")
                points = []
                for item in items:
                    title_el = item.query_selector("span.E8wfaf")
                    desc_el = item.query_selector("span.ezCH1d")
                    title = title_el.text_content().strip().rstrip(":") if title_el else ""
                    desc = desc_el.text_content().strip() if desc_el else ""
                    if title or desc:
                        points.append({"title": title, "description": desc})
                if "bullish" in label:
                    bull_points = points
                elif "bearish" in label:
                    bear_points = points
        except Exception:
            pass  # Bull/bear section may not exist for all tickers

        if not summary and not bull_points and not bear_points:
            return None

        return {
            "summary": summary,
            "pct_bullish": pct_bullish,
            "pct_neutral": pct_neutral,
            "pct_bearish": pct_bearish,
            "bull_points": bull_points,
            "bear_points": bear_points,
        }
    except Exception as e:
        log.debug("Extraction failed: %s", e)
        return None


def ingest_gfinance_overview(ticker: str) -> bool:
    """Scrape and store Google Finance overview for a ticker. Returns success."""
    data = scrape_overview(ticker)
    if not data:
        return False

    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO gfinance_overview
           (ticker, date_fetched, summary, pct_bullish, pct_neutral, pct_bearish,
            bull_points, bear_points)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ticker,
            date.today(),
            data["summary"],
            data["pct_bullish"],
            data["pct_neutral"],
            data["pct_bearish"],
            json.dumps(data["bull_points"]),
            json.dumps(data["bear_points"]),
        ],
    )
    conn.close()
    log.info(
        "Google Finance overview for %s: %.1f%% bullish, %d bull / %d bear points",
        ticker,
        data["pct_bullish"] or 0,
        len(data["bull_points"]),
        len(data["bear_points"]),
    )
    return True


def ensure_gfinance_overview(ticker: str, max_age_days: int = 1) -> bool:
    """Ensure a recent overview row exists for a ticker; scrape if missing/stale.

    Used by the night analysis to pre-populate bull/bear grounding before
    TradingAgents runs, so the debate nodes read from DuckDB instead of
    scraping mid-analysis.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(date_fetched) FROM gfinance_overview WHERE ticker = ?",
        [ticker],
    ).fetchone()
    conn.close()
    last = row[0] if row and row[0] else None
    if isinstance(last, str):
        last = date.fromisoformat(last)
    if last is not None and (date.today() - last).days <= max_age_days:
        return True
    return ingest_gfinance_overview(ticker)
