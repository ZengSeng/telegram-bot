"""AI news summarization — runs at the end of the daily pipeline.

Fetches yesterday+today news from DuckDB, filters junk, calls the local LLM
per ticker (sequentially), and stores results in the news_summaries table.
"""

import logging
from datetime import date

from .db import get_connection

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Junk filtering
# ---------------------------------------------------------------------------

_JUNK_TITLE_KEYWORDS = {
    "advertisement", "sponsored", "promo", "subscribe", "newsletter",
    "podcast", "video:", "photos:", "gallery:", "quiz",
}

_JUNK_PUBLISHERS = {
    "Motley Fool", "The Motley Fool", "InvestorPlace", "TipRanks",
    "MarketBeat", "Zacks", "Simply Wall St",
}

_CLICKBAIT_PATTERNS = ("things to know", "reasons to", "stocks to buy", "etf to")

SUMMARY_PROMPT = (
    "You are a stock market analyst assistant. Below are recent news headlines and summaries "
    "for {ticker}.\n\n"
    "Output ONLY the following, with no title, no word count, no preamble, and no extra headers:\n\n"
    "Catalysts: <1-3 sentences, confirmed events only, with dates/numbers if given>\n"
    "Sentiment: <Bullish/Bearish/Neutral — one sentence why. If driven by one analyst/firm, name them>\n"
    "Risks: <1-2 sentences>\n\n"
    "Rules:\n"
    "- Start directly with 'Catalysts:' — do not add any text before it.\n"
    "- Use exactly these three labels, in this order, each on its own line.\n"
    "- Total response under 130 words.\n"
    "- Hedge unconfirmed claims ('reportedly', 'proposed'); don't state rumors as fact.\n"
    "- Avoid sensational words (e.g. 'crushes', 'cratering'); use neutral language.\n"
    "- If most input is low-quality/promotional, say so in one sentence instead of summarizing it.\n\n"
    "News:\n{news_block}"
)


def _is_junk(title: str, publisher: str | None) -> bool:
    """Heuristic filter for low-value news articles."""
    t = title.lower().strip()
    if len(t) < 15:
        return True
    if any(kw in t for kw in _JUNK_TITLE_KEYWORDS):
        return True
    if publisher and publisher.strip() in _JUNK_PUBLISHERS:
        return True
    if any(p in t for p in _CLICKBAIT_PATTERNS):
        return True
    return False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _fetch_recent_news() -> dict[str, list[dict]]:
    """Query DuckDB for yesterday+today news, grouped by ticker, junk filtered."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT ticker, date, title, summary, publisher
           FROM news
           WHERE date >= CURRENT_DATE - INTERVAL 1 DAY
           ORDER BY ticker, date DESC"""
    ).fetchall()
    conn.close()

    grouped: dict[str, list[dict]] = {}
    seen_titles: dict[str, set[str]] = {}

    for ticker, pub_date, title, summary, publisher in rows:
        if _is_junk(title, publisher):
            continue
        norm = title.lower().strip()
        seen = seen_titles.setdefault(ticker, set())
        if norm in seen:
            continue
        seen.add(norm)

        grouped.setdefault(ticker, []).append({
            "date": str(pub_date),
            "title": title,
            "summary": summary or "",
            "publisher": publisher or "",
        })

    return grouped


def _build_prompt(ticker: str, articles: list[dict]) -> str:
    """Build the LLM prompt for a single ticker."""
    lines = []
    for art in articles:
        line = f"- [{art['date']}] {art['title']}"
        if art["publisher"]:
            line += f" ({art['publisher']})"
        if art["summary"]:
            line += f"\n  {art['summary'][:300]}"
        lines.append(line)
    return SUMMARY_PROMPT.format(ticker=ticker, news_block="\n".join(lines))


def _store_summary(ticker: str, summary_date: date, summary: str) -> None:
    """Upsert a summary into the news_summaries table."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO news_summaries (ticker, date, summary)
           VALUES (?, ?, ?)
           ON CONFLICT (ticker, date) DO UPDATE SET summary = EXCLUDED.summary""",
        [ticker, summary_date, summary],
    )
    conn.close()


def generate_news_summaries(tickers: list[str] | None = None) -> dict[str, str]:
    """Generate AI summaries for all tickers with recent news.

    Args:
        tickers: If provided, only summarize these tickers. Otherwise all with news.

    Returns:
        Dict of ticker -> summary text.
    """
    from stock_bot.llm import ask_llm

    grouped = _fetch_recent_news()
    if not grouped:
        log.info("No recent news to summarize.")
        return {}

    # Filter to requested tickers if specified
    if tickers:
        grouped = {t: arts for t, arts in grouped.items() if t in tickers}

    today = date.today()
    results: dict[str, str] = {}

    for ticker, articles in sorted(grouped.items()):
        log.info("Summarizing news for %s (%d articles)...", ticker, len(articles))
        prompt = _build_prompt(ticker, articles)
        summary = ask_llm(prompt)

        # Skip if LLM was unreachable
        if summary.startswith("(couldn't reach"):
            log.warning("LLM unavailable, skipping summary for %s", ticker)
            continue

        _store_summary(ticker, today, summary)
        results[ticker] = summary
        log.info("Stored summary for %s", ticker)

    return results
