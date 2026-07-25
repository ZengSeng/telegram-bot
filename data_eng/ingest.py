"""yfinance data ingestors with rate-limit pauses."""

import json
import logging
import time
from datetime import date, datetime

import yfinance as yf

from .db import get_connection

log = logging.getLogger(__name__)

# Pause between yfinance API calls to avoid rate limiting
API_PAUSE = 1.5


def ingest_prices(ticker: str, lookback_days: int = 365) -> int:
    """Fetch daily OHLCV and upsert into daily_prices. Returns row count."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    df = tk.history(period=f"{lookback_days}d")
    if df.empty:
        log.warning("No price data for %s", ticker)
        conn.close()
        return 0

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    rows = []
    for idx, row in df.iterrows():
        rows.append((
            ticker,
            idx.date(),
            round(row["Open"], 2),
            round(row["High"], 2),
            round(row["Low"], 2),
            round(row["Close"], 2),
            int(row["Volume"]),
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO daily_prices
           (ticker, date, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.close()
    log.info("Ingested %d price rows for %s", len(rows), ticker)
    return len(rows)


def ingest_news(ticker: str) -> int:
    """Fetch recent news for a ticker. Returns article count."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    articles = tk.get_news(count=50)
    if not articles:
        log.warning("No news for %s", ticker)
        conn.close()
        return 0

    rows = []
    for art in articles:
        content = art.get("content", art)
        title = content.get("title", "")
        if not title:
            continue
        summary = content.get("summary", "")
        provider = content.get("provider", {})
        publisher = provider.get("displayName", content.get("publisher", "Unknown"))
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        url = url_obj.get("url", content.get("link", ""))

        # Parse date
        pub_date_str = content.get("pubDate", "")
        ts = art.get("providerPublishTime")
        pub_date = date.today()
        if pub_date_str:
            try:
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).date()
            except ValueError:
                pass
        elif ts:
            try:
                pub_date = datetime.fromtimestamp(ts).date()
            except (ValueError, OSError):
                pass

        rows.append((ticker, pub_date, title, summary, publisher, url))

    conn.executemany(
        """INSERT OR REPLACE INTO news
           (ticker, date, title, summary, publisher, url)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.close()
    log.info("Ingested %d news articles for %s", len(rows), ticker)
    return len(rows)


def ingest_global_news() -> int:
    """Fetch global market news. Returns article count."""
    conn = get_connection()
    queries = ["stock market today", "federal reserve interest rates", "global economy"]
    seen_titles = set()
    rows = []

    for query in queries:
        try:
            search = yf.Search(query=query, news_count=15, enable_fuzzy_query=True)
            if search.news:
                for art in search.news:
                    content = art.get("content", art)
                    title = content.get("title", "")
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)

                    summary = content.get("summary", "")
                    provider = content.get("provider", {})
                    publisher = provider.get("displayName", content.get("publisher", "Unknown"))
                    url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
                    url = url_obj.get("url", content.get("link", ""))

                    pub_date_str = content.get("pubDate", "")
                    ts = art.get("providerPublishTime")
                    pub_date = date.today()
                    if pub_date_str:
                        try:
                            pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).date()
                        except ValueError:
                            pass
                    elif ts:
                        try:
                            pub_date = datetime.fromtimestamp(ts).date()
                        except (ValueError, OSError):
                            pass

                    rows.append((pub_date, title, summary, publisher, url))
        except Exception as e:
            log.warning("Global news query '%s' failed: %s", query, e)
        time.sleep(API_PAUSE)

    if rows:
        conn.executemany(
            """INSERT OR REPLACE INTO global_news
               (date, title, summary, publisher, url)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
    conn.close()
    log.info("Ingested %d global news articles", len(rows))
    return len(rows)


def ingest_fundamentals(ticker: str) -> bool:
    """Fetch company fundamentals snapshot. Returns success."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    try:
        info = tk.info
    except Exception as e:
        log.warning("Fundamentals fetch failed for %s: %s", ticker, e)
        conn.close()
        return False

    if not info or not info.get("longName"):
        log.warning("No fundamentals for %s", ticker)
        conn.close()
        return False

    row = (
        ticker,
        date.today(),
        info.get("longName"),
        info.get("sector"),
        info.get("industry"),
        info.get("marketCap"),
        info.get("trailingPE"),
        info.get("forwardPE"),
        info.get("pegRatio"),
        info.get("priceToBook"),
        info.get("trailingEps"),
        info.get("forwardEps"),
        info.get("dividendYield"),
        info.get("beta"),
        info.get("fiftyTwoWeekHigh"),
        info.get("fiftyTwoWeekLow"),
        info.get("fiftyDayAverage"),
        info.get("twoHundredDayAverage"),
        info.get("totalRevenue"),
        info.get("grossProfits"),
        info.get("ebitda"),
        info.get("netIncomeToCommon"),
        info.get("profitMargins"),
        info.get("operatingMargins"),
        info.get("returnOnEquity"),
        info.get("returnOnAssets"),
        info.get("debtToEquity"),
        info.get("currentRatio"),
        info.get("bookValue"),
        info.get("freeCashflow"),
    )

    conn.execute(
        """INSERT OR REPLACE INTO fundamentals
           (ticker, date_fetched, name, sector, industry, market_cap,
            pe_ratio, forward_pe, peg_ratio, price_to_book, eps_ttm, forward_eps,
            dividend_yield, beta, week52_high, week52_low, day50_avg, day200_avg,
            revenue_ttm, gross_profit, ebitda, net_income, profit_margin,
            operating_margin, roe, roa, debt_to_equity, current_ratio,
            book_value, free_cash_flow)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )
    conn.close()
    log.info("Ingested fundamentals for %s", ticker)
    return True


def ingest_financials(ticker: str) -> int:
    """Fetch quarterly financial statements. Returns statement count."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    count = 0

    statements = [
        ("balance_sheet", tk.quarterly_balance_sheet),
        ("cashflow", tk.quarterly_cashflow),
        ("income_statement", tk.quarterly_income_stmt),
    ]

    for stmt_type, df in statements:
        time.sleep(API_PAUSE)
        if df is None or df.empty:
            continue
        # Each column is a report date
        for col in df.columns:
            report_date = col.date() if hasattr(col, "date") else col
            row_data = df[col].to_dict()
            # Convert to JSON-serializable
            clean = {k: (None if str(v) == "nan" else v) for k, v in row_data.items()}
            conn.execute(
                """INSERT OR REPLACE INTO financials
                   (ticker, report_date, freq, statement_type, data_json)
                   VALUES (?, ?, 'quarterly', ?, ?)""",
                (ticker, report_date, stmt_type, json.dumps(clean, default=str)),
            )
            count += 1

    conn.close()
    log.info("Ingested %d financial statement rows for %s", count, ticker)
    return count


def ingest_analyst_targets(ticker: str) -> int:
    """Fetch analyst price targets and ratings. Returns count."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    rows = []

    # 1. Actual price targets (if available)
    try:
        targets = tk.analyst_price_targets
        if targets:
            for entry in targets[:20]:
                rows.append((
                    ticker,
                    date.today(),
                    entry.get("analyst", entry.get("firm", "Unknown")),
                    entry.get("target", entry.get("priceTarget")),
                    entry.get("rating"),
                ))
    except Exception as e:
        log.debug("No analyst_price_targets for %s: %s", ticker, e)

    time.sleep(API_PAUSE)

    # 2. Upgrades/downgrades (ratings only, no price)
    try:
        upgrades = tk.upgrades_downgrades
        if upgrades is not None and not upgrades.empty:
            for idx, row in upgrades.head(20).iterrows():
                pub_date = idx.date() if hasattr(idx, "date") else date.today()
                rows.append((
                    ticker,
                    pub_date,
                    row.get("Firm", "Unknown"),
                    None,  # no price target in upgrades_downgrades
                    row.get("ToGrade"),
                ))
    except Exception as e:
        log.debug("No upgrades_downgrades for %s: %s", ticker, e)

    if not rows:
        log.warning("No analyst data for %s", ticker)
        conn.close()
        return 0

    conn.executemany(
        """INSERT OR REPLACE INTO analyst_targets
           (ticker, date_fetched, analyst, target_price, rating)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.close()
    log.info("Ingested %d analyst entries for %s", len(rows), ticker)
    return len(rows)


def ingest_all(ticker: str) -> None:
    """Run all ingestors for a ticker with pauses between calls."""
    log.info("=== Full ingestion for %s ===", ticker)

    ingest_prices(ticker)
    time.sleep(API_PAUSE)

    ingest_news(ticker)
    time.sleep(API_PAUSE)

    ingest_fundamentals(ticker)
    time.sleep(API_PAUSE)

    ingest_financials(ticker)
    time.sleep(API_PAUSE)

    ingest_analyst_targets(ticker)
    time.sleep(API_PAUSE)

    ingest_global_news()

    log.info("=== Done: %s ===", ticker)
