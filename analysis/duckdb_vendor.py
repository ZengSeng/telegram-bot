"""DuckDB data adapter matching TradingAgents vendor interface signatures.

These functions are monkey-patched into TradingAgents' VENDOR_METHODS at runtime,
replacing live yfinance calls with local DuckDB queries.
"""

import json
import logging
from datetime import datetime

import pandas as pd

from data_eng.db import get_connection

log = logging.getLogger(__name__)


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """Return OHLCV CSV from DuckDB, matching yfinance vendor format."""
    conn = get_connection()
    df = conn.execute(
        """SELECT date, open, high, low, close, volume
           FROM daily_prices
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date""",
        [symbol, start_date, end_date],
    ).fetchdf()
    conn.close()

    if df.empty:
        return f"NO_DATA_AVAILABLE: No price data for '{symbol}' between {start_date} and {end_date}. Run: python -m data_eng {symbol}"

    df = df.set_index("date")
    df.index.name = "Date"
    csv_string = df.to_csv()

    header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(df)}\n"
    header += f"# Source: local DuckDB\n\n"
    return header + csv_string


def get_indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Compute technical indicators from DuckDB OHLCV using stockstats."""
    from dateutil.relativedelta import relativedelta
    from stockstats import wrap

    conn = get_connection()
    # Fetch extra history for indicator warm-up
    warmup_start = (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days + 250)).strftime("%Y-%m-%d")
    df = conn.execute(
        """SELECT date as Date, open, high, low, close, volume
           FROM daily_prices
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date""",
        [symbol, warmup_start, curr_date],
    ).fetchdf()
    conn.close()

    if df.empty:
        return f"NO_DATA_AVAILABLE: No price data for '{symbol}' to compute {indicator}."

    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    sdf = wrap(df)
    try:
        sdf[indicator]  # trigger calculation
    except Exception as e:
        return f"Error computing indicator {indicator}: {e}"

    # Build date->value map
    indicator_data = {}
    for _, row in sdf.iterrows():
        val = row[indicator]
        indicator_data[row["Date"]] = "N/A" if pd.isna(val) else str(val)

    # Generate output for requested window
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)
    lines = []
    current = curr_dt
    while current >= before:
        ds = current.strftime("%Y-%m-%d")
        val = indicator_data.get(ds, "N/A: Not a trading day")
        lines.append(f"{ds}: {val}")
        current -= relativedelta(days=1)

    result = f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
    result += "\n".join(lines)
    return result


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """Return fundamentals from DuckDB."""
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM fundamentals
           WHERE ticker = ?
           ORDER BY date_fetched DESC LIMIT 1""",
        [ticker],
    ).fetchone()
    columns = [desc[0] for desc in conn.description]
    conn.close()

    if not row:
        return f"NO_DATA_AVAILABLE: No fundamentals for '{ticker}'. Run: python -m data_eng {ticker}"

    data = dict(zip(columns, row))
    lines = [
        f"Name: {data['name']}",
        f"Sector: {data['sector']}",
        f"Industry: {data['industry']}",
        f"Market Cap: {data['market_cap']}",
        f"PE Ratio (TTM): {data['pe_ratio']}",
        f"Forward PE: {data['forward_pe']}",
        f"PEG Ratio: {data['peg_ratio']}",
        f"Price to Book: {data['price_to_book']}",
        f"EPS (TTM): {data['eps_ttm']}",
        f"Forward EPS: {data['forward_eps']}",
        f"Dividend Yield: {data['dividend_yield']}",
        f"Beta: {data['beta']}",
        f"52 Week High: {data['week52_high']}",
        f"52 Week Low: {data['week52_low']}",
        f"50 Day Average: {data['day50_avg']}",
        f"200 Day Average: {data['day200_avg']}",
        f"Revenue (TTM): {data['revenue_ttm']}",
        f"Gross Profit: {data['gross_profit']}",
        f"EBITDA: {data['ebitda']}",
        f"Net Income: {data['net_income']}",
        f"Profit Margin: {data['profit_margin']}",
        f"Operating Margin: {data['operating_margin']}",
        f"Return on Equity: {data['roe']}",
        f"Return on Assets: {data['roa']}",
        f"Debt to Equity: {data['debt_to_equity']}",
        f"Current Ratio: {data['current_ratio']}",
        f"Book Value: {data['book_value']}",
        f"Free Cash Flow: {data['free_cash_flow']}",
    ]
    lines = [l for l in lines if "None" not in l]

    header = f"# Company Fundamentals for {ticker}\n"
    header += f"# Source: local DuckDB (fetched {data['date_fetched']})\n\n"
    return header + "\n".join(lines)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return balance sheet from DuckDB."""
    return _get_financial_statement(ticker, "balance_sheet", freq, curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return cash flow from DuckDB."""
    return _get_financial_statement(ticker, "cashflow", freq, curr_date)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return income statement from DuckDB."""
    return _get_financial_statement(ticker, "income_statement", freq, curr_date)


def _get_financial_statement(ticker: str, stmt_type: str, freq: str, curr_date: str) -> str:
    """Generic financial statement fetcher from DuckDB."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT report_date, data_json FROM financials
           WHERE ticker = ? AND statement_type = ? AND freq = ?
           ORDER BY report_date DESC LIMIT 4""",
        [ticker, stmt_type, freq],
    ).fetchall()
    conn.close()

    if not rows:
        return f"NO_DATA_AVAILABLE: No {stmt_type} data for '{ticker}'. Run: python -m data_eng {ticker}"

    # Reconstruct a DataFrame-like CSV
    records = {}
    for report_date, data_json in rows:
        records[str(report_date)] = json.loads(data_json)

    df = pd.DataFrame(records)
    csv_string = df.to_csv()

    label = stmt_type.replace("_", " ").title()
    header = f"# {label} data for {ticker} ({freq})\n"
    header += f"# Source: local DuckDB\n\n"
    return header + csv_string


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return news from DuckDB."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT title, summary, publisher, url, date FROM news
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date DESC LIMIT 20""",
        [ticker, start_date, end_date],
    ).fetchall()
    conn.close()

    if not rows:
        return f"No news found for {ticker} between {start_date} and {end_date}"

    news_str = ""
    for title, summary, publisher, url, pub_date in rows:
        news_str += f"### {title} (source: {publisher})\n"
        if summary:
            news_str += f"{summary}\n"
        if url:
            news_str += f"Link: {url}\n"
        news_str += "\n"

    return f"## {ticker} News, from {start_date} to {end_date}:\n\n{news_str}"


def get_global_news(curr_date: str, look_back_days: int = 7, limit: int = 15) -> str:
    """Return global news from DuckDB."""
    from dateutil.relativedelta import relativedelta

    start_dt = datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    conn = get_connection()
    rows = conn.execute(
        """SELECT title, summary, publisher, url, date FROM global_news
           WHERE date >= ? AND date <= ?
           ORDER BY date DESC LIMIT ?""",
        [start_date, curr_date, limit],
    ).fetchall()
    conn.close()

    if not rows:
        return f"No global news found between {start_date} and {curr_date}"

    news_str = ""
    for title, summary, publisher, url, pub_date in rows:
        news_str += f"### {title} (source: {publisher})\n"
        if summary:
            news_str += f"{summary}\n"
        if url:
            news_str += f"Link: {url}\n"
        news_str += "\n"

    return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"


def get_insider_transactions(ticker: str) -> str:
    """Return insider transactions (not stored locally, return graceful message)."""
    return f"No insider transaction data stored locally for '{ticker}'."


def get_market_sentiment(ticker: str) -> str:
    """Return Google Finance AI overview: summary, sentiment %, bull/bear points."""
    conn = get_connection()
    row = conn.execute(
        """SELECT summary, pct_bullish, pct_neutral, pct_bearish,
                  bull_points, bear_points, date_fetched
           FROM gfinance_overview
           WHERE ticker = ?
           ORDER BY date_fetched DESC LIMIT 1""",
        [ticker],
    ).fetchone()
    conn.close()

    if not row:
        return f"NO_DATA_AVAILABLE: No Google Finance overview for '{ticker}'."

    summary, pct_bull, pct_neut, pct_bear, bull_json, bear_json, fetched = row

    lines = [f"# Market Sentiment Overview for {ticker}"]
    lines.append(f"# Source: Google Finance (TipRanks), fetched {fetched}\n")

    if summary:
        lines.append(f"## AI Summary\n{summary}\n")

    if pct_bull is not None:
        lines.append(f"## News Sentiment\n{pct_bull}% bullish | {pct_neut}% neutral | {pct_bear}% bearish\n")

    if bull_json:
        points = json.loads(bull_json)
        if points:
            lines.append("## Bullish Case")
            for p in points:
                lines.append(f"- **{p['title']}**: {p['description']}")
            lines.append("")

    if bear_json:
        points = json.loads(bear_json)
        if points:
            lines.append("## Bearish Case")
            for p in points:
                lines.append(f"- **{p['title']}**: {p['description']}")
            lines.append("")

    return "\n".join(lines)
