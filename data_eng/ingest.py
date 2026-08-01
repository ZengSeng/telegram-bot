"""yfinance data ingestors with rate-limit pauses."""

import json
import logging
import random
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

from .db import get_connection

log = logging.getLogger(__name__)

# Pause between yfinance API calls to avoid rate limiting
API_PAUSE = 1.5


def ingest_prices(ticker: str, lookback_days: int = 365) -> int:
    """Fetch daily OHLCV + corporate actions and upsert into daily_prices. Returns row count."""
    conn = get_connection()
    tk = yf.Ticker(ticker)
    df = tk.history(period=f"{lookback_days}d", actions=True)
    if df.empty:
        log.warning("No price data for %s", ticker)
        conn.close()
        return 0

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    rows = []
    for idx, row in df.iterrows():
        div = float(row.get("Dividends", 0) or 0)
        split = float(row.get("Stock Splits", 0) or 0)
        rows.append((
            ticker,
            idx.date(),
            round(row["Open"], 2),
            round(row["High"], 2),
            round(row["Low"], 2),
            round(row["Close"], 2),
            int(row["Volume"]),
            div,
            split,
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO daily_prices
           (ticker, date, open, high, low, close, volume, dividends, stock_splits)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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

    # 1. Consensus price targets (aggregate dict: current, high, low, mean, median)
    try:
        targets = tk.analyst_price_targets
        if targets and isinstance(targets, dict) and targets.get("mean"):
            rows.append((
                ticker,
                date.today(),
                "Consensus",
                targets.get("mean"),
                f"low={targets.get('low')} high={targets.get('high')} median={targets.get('median')} current={targets.get('current')}",
            ))
    except Exception as e:
        log.debug("No analyst_price_targets for %s: %s", ticker, e)

    time.sleep(API_PAUSE)

    # 2. Individual analyst upgrades/downgrades (has currentPriceTarget column)
    try:
        upgrades = tk.upgrades_downgrades
        if upgrades is not None and not upgrades.empty:
            for idx, row in upgrades.head(20).iterrows():
                pub_date = idx.date() if hasattr(idx, "date") else date.today()
                price_target = row.get("currentPriceTarget")
                # Skip 0.0 targets (means not provided)
                if price_target == 0.0:
                    price_target = None
                rows.append((
                    ticker,
                    pub_date,
                    row.get("Firm", "Unknown"),
                    price_target,
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


# ---------------------------------------------------------------------------
# Yahoo AI summary scraper
# ---------------------------------------------------------------------------

def _fetch_yahoo_summary(symbol: str) -> str | None:
    """Scrape Yahoo Finance quote page for AI-generated business summary."""
    url = f"https://finance.yahoo.com/quote/{symbol}/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.debug("Yahoo summary fetch failed for %s: %s", symbol, e)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one("h2.header span.titleInfo")
    if el:
        return el.get_text(strip=True)
    return None


# ---------------------------------------------------------------------------
# Enrichment: growth estimates + price targets + recommendations
# ---------------------------------------------------------------------------

def _safe_loc(df, idx, col):
    """Safely locate a value in a DataFrame."""
    try:
        if df is None or df.empty:
            return None
        val = df.loc[idx, col]
        return None if pd.isna(val) else float(val)
    except Exception:
        return None


def _safe_dict(d, key):
    """Safely get a numeric value from a dict."""
    if isinstance(d, dict):
        val = d.get(key)
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            return float(val)
    return None


def _safe_pct(numerator, denominator):
    """Calculate percentage difference safely."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round((numerator / denominator - 1) * 100, 2)


def _normalize_recommendations(recs) -> dict:
    """Normalize yfinance recommendations_summary to a dict."""
    if isinstance(recs, dict):
        return recs
    if isinstance(recs, pd.DataFrame) and not recs.empty:
        row = recs.iloc[0]
        return {
            "strongBuy": row.get("strongBuy"),
            "buy": row.get("buy"),
            "hold": row.get("hold"),
            "sell": row.get("sell"),
            "strongSell": row.get("strongSell"),
        }
    return {}


def _fetch_enrichment(ticker: str) -> dict:
    """Fetch growth estimates, price targets, and recommendations via yfinance."""
    tk = yf.Ticker(ticker)
    result = {}

    # Growth estimates
    try:
        growth = tk.growth_estimates
        result["stock_trend_0q"] = _safe_loc(growth, "0q", "stockTrend")
        result["stock_trend_1q"] = _safe_loc(growth, "+1q", "stockTrend")
        result["stock_trend_0y"] = _safe_loc(growth, "0y", "stockTrend")
        result["stock_trend_1y"] = _safe_loc(growth, "+1y", "stockTrend")
        result["index_trend_ltg"] = _safe_loc(growth, "LTG", "indexTrend")
        result["stock_over_index_0q"] = _safe_pct(
            _safe_loc(growth, "0q", "stockTrend"), _safe_loc(growth, "0q", "indexTrend"))
        result["stock_over_index_1q"] = _safe_pct(
            _safe_loc(growth, "+1q", "stockTrend"), _safe_loc(growth, "+1q", "indexTrend"))
        result["stock_over_index_0y"] = _safe_pct(
            _safe_loc(growth, "0y", "stockTrend"), _safe_loc(growth, "0y", "indexTrend"))
        result["stock_over_index_1y"] = _safe_pct(
            _safe_loc(growth, "+1y", "stockTrend"), _safe_loc(growth, "+1y", "indexTrend"))
    except Exception as e:
        log.debug("Growth estimates failed for %s: %s", ticker, e)

    time.sleep(API_PAUSE)

    # Price targets
    try:
        targets = tk.analyst_price_targets or {}
        result["target_low"] = _safe_dict(targets, "low")
        result["target_mean"] = _safe_dict(targets, "mean")
        result["target_median"] = _safe_dict(targets, "median")
        result["target_current"] = _safe_dict(targets, "current")
        result["target_high"] = _safe_dict(targets, "high")
        result["target_over_mean"] = _safe_pct(
            _safe_dict(targets, "mean"), _safe_dict(targets, "current"))
        result["target_over_median"] = _safe_pct(
            _safe_dict(targets, "median"), _safe_dict(targets, "current"))
    except Exception as e:
        log.debug("Price targets failed for %s: %s", ticker, e)

    time.sleep(API_PAUSE)

    # Recommendations
    try:
        recs = _normalize_recommendations(tk.recommendations_summary)
        result["rec_strong_buy"] = recs.get("strongBuy")
        result["rec_buy"] = recs.get("buy")
        result["rec_hold"] = recs.get("hold")
        result["rec_sell"] = recs.get("sell")
        result["rec_strong_sell"] = recs.get("strongSell")
    except Exception as e:
        log.debug("Recommendations failed for %s: %s", ticker, e)

    return result


# ---------------------------------------------------------------------------
# Technical indicators (computed from daily_prices in DuckDB)
# ---------------------------------------------------------------------------

def _compute_technicals(ticker: str) -> dict:
    """Compute technical indicators from stored daily_prices. Returns latest snapshot."""
    import ta as ta_lib

    conn = get_connection()
    df = conn.execute(
        """SELECT date, close, volume FROM daily_prices
           WHERE ticker = ? ORDER BY date""",
        [ticker],
    ).fetchdf()
    conn.close()

    if df.empty or len(df) < 30:
        log.debug("Insufficient price data for technicals on %s (%d rows)", ticker, len(df))
        return {}

    close = df["close"]
    volume = df["volume"].astype(float)

    result = {}

    # Trend
    result["sma_20"] = _round(ta_lib.trend.sma_indicator(close, window=20))
    result["sma_50"] = _round(ta_lib.trend.sma_indicator(close, window=50))
    result["ema_12"] = _round(ta_lib.trend.ema_indicator(close, window=12))
    result["ema_26"] = _round(ta_lib.trend.ema_indicator(close, window=26))

    # MACD
    macd = ta_lib.trend.MACD(close)
    result["macd"] = _round(macd.macd())
    result["macd_signal"] = _round(macd.macd_signal())
    result["macd_hist"] = _round(macd.macd_diff())

    # RSI
    result["rsi_14"] = _round(ta_lib.momentum.rsi(close, window=14))

    # Bollinger Bands
    bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
    result["bb_upper"] = _round(bb.bollinger_hband())
    result["bb_middle"] = _round(bb.bollinger_mavg())
    result["bb_lower"] = _round(bb.bollinger_lband())
    result["bb_width"] = _round(bb.bollinger_wband())

    # Volume
    vol_sma = ta_lib.trend.sma_indicator(volume, window=20)
    result["volume_sma_20"] = _round(vol_sma)
    result["obv"] = _round(ta_lib.volume.on_balance_volume(close, volume))
    last_vol_ratio = volume.iloc[-1] / vol_sma.iloc[-1] if vol_sma.iloc[-1] != 0 else None
    result["volume_ratio"] = round(last_vol_ratio, 2) if last_vol_ratio else None

    # Signals
    rsi = result.get("rsi_14")
    result["signal_rsi"] = 1 if rsi and rsi < 30 else (-1 if rsi and rsi > 70 else 0)

    macd_val = result.get("macd")
    macd_sig = result.get("macd_signal")
    if macd_val is not None and macd_sig is not None:
        result["signal_macd"] = 1 if macd_val > macd_sig else (-1 if macd_val < macd_sig else 0)
    else:
        result["signal_macd"] = 0

    last_close = close.iloc[-1]
    sma20 = result.get("sma_20")
    result["signal_trend"] = 1 if sma20 and last_close > sma20 else (-1 if sma20 and last_close < sma20 else 0)

    bb_low = result.get("bb_lower")
    bb_up = result.get("bb_upper")
    result["signal_bb"] = 1 if bb_low and last_close < bb_low else (-1 if bb_up and last_close > bb_up else 0)

    vr = result.get("volume_ratio")
    result["signal_volume"] = 1 if vr and vr > 1.5 else (-1 if vr and vr < 0.5 else 0)

    # Combined
    combined = (
        result["signal_rsi"] * 0.25 +
        result["signal_macd"] * 0.25 +
        result["signal_trend"] * 0.20 +
        result["signal_bb"] * 0.15 +
        result["signal_volume"] * 0.15
    )
    result["combined_signal"] = round(combined, 4)
    result["trade_signal"] = 1 if combined > 0.3 else (-1 if combined < -0.3 else 0)

    return result


def _round(series_or_val, decimals=2):
    """Get last value from a pandas Series (or scalar), rounded."""
    if isinstance(series_or_val, pd.Series):
        val = series_or_val.iloc[-1]
    else:
        val = series_or_val
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return round(float(val), decimals)


def _to_native(val):
    """Convert numpy/pandas types to native Python for DuckDB compatibility."""
    if val is None:
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return None if np.isnan(val) else float(val)
    if isinstance(val, float) and np.isnan(val):
        return None
    return val


# ---------------------------------------------------------------------------
# Combined enrichment ingestor
# ---------------------------------------------------------------------------

def ingest_enriched(ticker: str) -> bool:
    """Fetch yahoo summary + growth + targets + recs + technicals → ticker_enriched table."""
    log.info("Enriching %s ...", ticker)

    # 1. Yahoo AI summary (web scrape)
    yahoo_summary = _fetch_yahoo_summary(ticker)
    time.sleep(2 + random.random() * 3)  # rate-limit for web scrape

    # 2. yfinance enrichment
    enrichment = _fetch_enrichment(ticker)
    time.sleep(API_PAUSE)

    # 3. Technical indicators (computed locally from DuckDB prices)
    technicals = _compute_technicals(ticker)

    # Merge all
    row_data = {"yahoo_summary": yahoo_summary}
    row_data.update(enrichment)
    row_data.update(technicals)

    # Sanitize numpy types → native Python for DuckDB
    row_data = {k: _to_native(v) for k, v in row_data.items()}

    # Build INSERT
    columns = ["ticker", "date_fetched"] + list(row_data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_str = ", ".join(columns)
    values = [ticker, date.today()] + list(row_data.values())

    conn = get_connection()
    conn.execute(
        f"INSERT OR REPLACE INTO ticker_enriched ({col_str}) VALUES ({placeholders})",
        values,
    )
    conn.close()
    log.info("Enriched %s (summary=%s, technicals=%d indicators)",
             ticker, "yes" if yahoo_summary else "no", len(technicals))
    return True


# ---------------------------------------------------------------------------
# Batch price download for watchlist
# ---------------------------------------------------------------------------

def batch_ingest_prices(tickers: list[str], lookback_days: int = 3 * 365) -> int:
    """Batch-download daily prices for multiple tickers using yf.download.

    Only downloads data newer than what's already stored (incremental).
    Returns total rows inserted.
    """
    if not tickers:
        log.info("No tickers to batch-download.")
        return 0

    conn = get_connection()
    today = date.today()

    # Get latest stored date per ticker
    latest_dates: dict[str, date] = {}
    for t in tickers:
        row = conn.execute(
            "SELECT MAX(date) FROM daily_prices WHERE ticker = ?", [t]
        ).fetchone()
        if row and row[0]:
            latest_dates[t] = row[0] if isinstance(row[0], date) else row[0].date()

    # Determine which tickers need data
    to_download = []
    default_start = today - timedelta(days=lookback_days)
    for t in tickers:
        latest = latest_dates.get(t)
        start = (latest - timedelta(days=5)) if latest else default_start
        if start >= today:
            log.debug("%s already up to date, skipping", t)
            continue
        to_download.append((t, start))

    if not to_download:
        log.info("All %d tickers up to date.", len(tickers))
        conn.close()
        return 0

    # Batch download using earliest start
    min_start = min(s for _, s in to_download)
    batch_tickers = [t for t, _ in to_download]
    log.info("Batch downloading %d tickers from %s", len(batch_tickers), min_start)

    try:
        data = yf.download(
            tickers=batch_tickers,
            start=min_start.strftime("%Y-%m-%d"),
            end=today.strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=True,
            actions=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as e:
        log.error("Batch download failed: %s", e)
        conn.close()
        return 0

    if data is None or data.empty:
        log.warning("Batch download returned no data.")
        conn.close()
        return 0

    total_rows = 0

    if len(batch_tickers) == 1:
        # Single ticker: no multi-level columns
        ticker = batch_tickers[0]
        start_date = dict(to_download)[ticker]
        df = data.copy()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        rows = []
        for idx, row in df.iterrows():
            d = idx.date()
            if d < start_date:
                continue
            div = float(row.get("Dividends", 0) or 0)
            split = float(row.get("Stock Splits", 0) or 0)
            rows.append((ticker, d, round(row["Open"], 2), round(row["High"], 2),
                         round(row["Low"], 2), round(row["Close"], 2), int(row["Volume"]),
                         div, split))
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO daily_prices
                   (ticker, date, open, high, low, close, volume, dividends, stock_splits)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
            total_rows += len(rows)
    else:
        # Multi-ticker: columns are MultiIndex (ticker, field)
        for ticker, start_date in to_download:
            try:
                sub = data[ticker].copy()
            except KeyError:
                log.warning("No data in batch for %s", ticker)
                continue
            if sub.empty:
                continue
            if sub.index.tz is not None:
                sub.index = sub.index.tz_localize(None)
            rows = []
            for idx, row in sub.iterrows():
                d = idx.date()
                if d < start_date:
                    continue
                if pd.isna(row.get("Close")):
                    continue
                div = float(row.get("Dividends", 0) or 0)
                split = float(row.get("Stock Splits", 0) or 0)
                rows.append((ticker, d, round(row["Open"], 2), round(row["High"], 2),
                             round(row["Low"], 2), round(row["Close"], 2), int(row["Volume"]),
                             div, split))
            if rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO daily_prices
                       (ticker, date, open, high, low, close, volume, dividends, stock_splits)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
                total_rows += len(rows)

    conn.close()
    log.info("Batch ingest complete: %d rows for %d tickers", total_rows, len(batch_tickers))
    return total_rows


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

    ingest_enriched(ticker)
    time.sleep(API_PAUSE)

    ingest_global_news()

    log.info("=== Done: %s ===", ticker)
