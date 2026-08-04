"""DuckDB connection and schema management for market data."""

from pathlib import Path

import duckdb

# Database lives in data/ alongside other bot data
DB_PATH = Path(__file__).parent.parent / "data" / "market.duckdb"

# Schema DDL runs once per process. Running it on every get_connection()
# caused DuckDB "Catalog write-write conflict on create" errors whenever two
# connections were open at once (e.g. TradingAgents tools + pipeline).
_schema_initialized = False

# No-data skip tracking: after SKIP_ATTEMPT_THRESHOLD consecutive empty
# results a ticker is skipped for SKIP_RETRY_DAYS before being retried.
SKIP_ATTEMPT_THRESHOLD = 2
SKIP_RETRY_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_prices (
    ticker      VARCHAR NOT NULL,
    date        DATE NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS news (
    ticker      VARCHAR NOT NULL,
    date        DATE NOT NULL,
    title       VARCHAR NOT NULL,
    summary     VARCHAR,
    publisher   VARCHAR,
    url         VARCHAR,
    PRIMARY KEY (ticker, date, title)
);

CREATE TABLE IF NOT EXISTS global_news (
    date        DATE NOT NULL,
    title       VARCHAR NOT NULL,
    summary     VARCHAR,
    publisher   VARCHAR,
    url         VARCHAR,
    PRIMARY KEY (date, title)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          VARCHAR NOT NULL,
    date_fetched    DATE NOT NULL,
    name            VARCHAR,
    sector          VARCHAR,
    industry        VARCHAR,
    market_cap      DOUBLE,
    pe_ratio        DOUBLE,
    forward_pe      DOUBLE,
    peg_ratio       DOUBLE,
    price_to_book   DOUBLE,
    eps_ttm         DOUBLE,
    forward_eps     DOUBLE,
    dividend_yield  DOUBLE,
    beta            DOUBLE,
    week52_high     DOUBLE,
    week52_low      DOUBLE,
    day50_avg       DOUBLE,
    day200_avg      DOUBLE,
    revenue_ttm     DOUBLE,
    gross_profit    DOUBLE,
    ebitda          DOUBLE,
    net_income      DOUBLE,
    profit_margin   DOUBLE,
    operating_margin DOUBLE,
    roe             DOUBLE,
    roa             DOUBLE,
    debt_to_equity  DOUBLE,
    current_ratio   DOUBLE,
    book_value      DOUBLE,
    free_cash_flow  DOUBLE,
    PRIMARY KEY (ticker, date_fetched)
);

CREATE TABLE IF NOT EXISTS financials (
    ticker      VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    freq        VARCHAR NOT NULL DEFAULT 'quarterly',
    statement_type VARCHAR NOT NULL,  -- 'balance_sheet', 'cashflow', 'income_statement'
    data_json   VARCHAR NOT NULL,      -- JSON blob of the statement row
    PRIMARY KEY (ticker, report_date, freq, statement_type)
);

CREATE TABLE IF NOT EXISTS analyst_targets (
    ticker          VARCHAR NOT NULL,
    date_fetched    DATE NOT NULL,
    analyst         VARCHAR,
    target_price    DOUBLE,
    rating          VARCHAR,
    PRIMARY KEY (ticker, date_fetched, analyst)
);

CREATE TABLE IF NOT EXISTS news_summaries (
    ticker      VARCHAR NOT NULL,
    date        DATE NOT NULL,
    summary     VARCHAR NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS trading_agent_decisions (
    ticker          VARCHAR NOT NULL,
    date            DATE NOT NULL,
    action          VARCHAR,        -- Buy / Sell / Hold
    rating          VARCHAR,        -- Overweight / Underweight / Neutral
    price_target    DOUBLE,
    entry_price     DOUBLE,
    stop_loss       DOUBLE,
    time_horizon    VARCHAR,
    summary         VARCHAR,        -- executive summary (~500 chars)
    report_path     VARCHAR,        -- relative path to full report folder
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS gfinance_overview (
    ticker          VARCHAR NOT NULL,
    date_fetched    DATE NOT NULL,
    summary         VARCHAR,
    pct_bullish     DOUBLE,
    pct_neutral     DOUBLE,
    pct_bearish     DOUBLE,
    bull_points     VARCHAR,    -- JSON array of {title, description}
    bear_points     VARCHAR,    -- JSON array of {title, description}
    PRIMARY KEY (ticker, date_fetched)
);

CREATE TABLE IF NOT EXISTS yfinance_overview (
    ticker          VARCHAR NOT NULL,
    date_fetched    DATE NOT NULL,
    overview        VARCHAR,
    PRIMARY KEY (ticker, date_fetched)
);

CREATE TABLE IF NOT EXISTS ticker_enriched (
    ticker              VARCHAR NOT NULL,
    date_fetched        DATE NOT NULL,

    -- Growth estimates (from yfinance growth_estimates)
    stock_trend_0q      DOUBLE,
    stock_trend_1q      DOUBLE,
    stock_trend_0y      DOUBLE,
    stock_trend_1y      DOUBLE,
    index_trend_ltg     DOUBLE,
    stock_over_index_0q DOUBLE,
    stock_over_index_1q DOUBLE,
    stock_over_index_0y DOUBLE,
    stock_over_index_1y DOUBLE,

    -- Analyst price targets
    target_low          DOUBLE,
    target_mean         DOUBLE,
    target_median       DOUBLE,
    target_current      DOUBLE,
    target_high         DOUBLE,
    target_over_mean    DOUBLE,
    target_over_median  DOUBLE,

    -- Recommendations
    rec_strong_buy      INTEGER,
    rec_buy             INTEGER,
    rec_hold            INTEGER,
    rec_sell            INTEGER,
    rec_strong_sell     INTEGER,

    PRIMARY KEY (ticker, date_fetched)
);

CREATE TABLE IF NOT EXISTS technicals (
    ticker              VARCHAR NOT NULL,
    date_fetched        DATE NOT NULL,

    -- Trend
    sma_20              DOUBLE,
    sma_50              DOUBLE,
    ema_12              DOUBLE,
    ema_26              DOUBLE,

    -- MACD
    macd                DOUBLE,
    macd_signal         DOUBLE,
    macd_hist           DOUBLE,

    -- RSI
    rsi_14              DOUBLE,

    -- Bollinger Bands
    bb_upper            DOUBLE,
    bb_middle           DOUBLE,
    bb_lower            DOUBLE,
    bb_width            DOUBLE,

    -- Volume
    volume_sma_20       DOUBLE,
    obv                 DOUBLE,
    volume_ratio        DOUBLE,

    -- Trading signals
    signal_rsi          INTEGER,
    signal_macd         INTEGER,
    signal_trend        INTEGER,
    signal_bb           INTEGER,
    signal_volume       INTEGER,
    combined_signal     DOUBLE,
    trade_signal        INTEGER,

    PRIMARY KEY (ticker, date_fetched)
);

CREATE TABLE IF NOT EXISTS screener_scores (
    ticker          VARCHAR NOT NULL,
    date_scored     DATE NOT NULL,
    quality_score   DOUBLE,
    value_score     DOUBLE,
    momentum_score  DOUBLE,
    sentiment_score DOUBLE,
    risk_score      DOUBLE,
    overall_score   DOUBLE,
    PRIMARY KEY (ticker, date_scored)
);

CREATE TABLE IF NOT EXISTS candidates (
    ticker          VARCHAR NOT NULL,
    date_selected   DATE NOT NULL,
    sector          VARCHAR,
    overall_score   DOUBLE,
    removed_reason  VARCHAR,
    PRIMARY KEY (ticker, date_selected)
);

CREATE TABLE IF NOT EXISTS events (
    ticker      VARCHAR NOT NULL,
    date        DATE NOT NULL,
    event_type  VARCHAR NOT NULL,
    details     VARCHAR,
    PRIMARY KEY (ticker, date, event_type)
);

CREATE TABLE IF NOT EXISTS portfolio_decisions (
    ticker          VARCHAR NOT NULL,
    date            DATE NOT NULL,
    action          VARCHAR NOT NULL,
    position_pct    DOUBLE,
    shares          DOUBLE,
    stop_loss       DOUBLE,
    reason          VARCHAR,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS portfolio_reviews (
    date            DATE NOT NULL PRIMARY KEY,
    review_text     VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_universe (
    ticker          VARCHAR NOT NULL,
    date_added      DATE NOT NULL,
    company_name    VARCHAR,
    sector          VARCHAR,
    industry        VARCHAR,
    rating          VARCHAR,
    group_id        SMALLINT,
    sector_weight   DOUBLE,
    company_weight  DOUBLE,
    last_updated    DATE,
    PRIMARY KEY (ticker, date_added)
);

-- Tracks tickers that repeatedly returned no data for a source, so bulk
-- steps skip them until the retry window passes.
CREATE TABLE IF NOT EXISTS skip_tickers (
    ticker          VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,  -- 'fundamentals' / 'analyst_targets' / 'ticker_enriched' / 'prices'
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_attempt    DATE NOT NULL,
    PRIMARY KEY (ticker, source)
);

-- Add corporate action columns to daily_prices (safe for existing databases)
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS dividends DECIMAL(18,4) DEFAULT 0;
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS stock_splits DECIMAL(18,4) DEFAULT 0;
"""


def get_connection() -> duckdb.DuckDBPyConnection:
    """Get a DuckDB connection with schema initialized."""
    global _schema_initialized
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    if not _schema_initialized:
        conn.execute(SCHEMA)
        _migrate_enrich_skips(conn)
        _schema_initialized = True
    return conn


def _migrate_enrich_skips(conn) -> None:
    """One-time rename of the old enrich_skips table to skip_tickers."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "enrich_skips" in tables:
        conn.execute(
            """INSERT INTO skip_tickers
               SELECT ticker, source, attempts, last_attempt FROM enrich_skips"""
        )
        conn.execute("DROP TABLE enrich_skips")


# ---------------------------------------------------------------------------
# No-data skip tracking (skip_tickers table)
# ---------------------------------------------------------------------------


def record_miss(ticker: str, source: str) -> int:
    """Record a no-result attempt for ticker/source. Returns attempt count."""
    conn = get_connection()
    row = conn.execute(
        "SELECT attempts FROM skip_tickers WHERE ticker = ? AND source = ?",
        [ticker, source],
    ).fetchone()
    attempts = (row[0] + 1) if row else 1
    conn.execute(
        """INSERT OR REPLACE INTO skip_tickers (ticker, source, attempts, last_attempt)
           VALUES (?, ?, ?, CURRENT_DATE)""",
        [ticker, source, attempts],
    )
    conn.close()
    return attempts


def clear_miss(ticker: str, source: str) -> None:
    """Remove a ticker's skip record once data comes back (resets attempts)."""
    conn = get_connection()
    conn.execute(
        "DELETE FROM skip_tickers WHERE ticker = ? AND source = ?",
        [ticker, source],
    )
    conn.close()


def get_skipped_tickers(source: str, retry_days: int = SKIP_RETRY_DAYS) -> set[str]:
    """Tickers currently skipped for a source (>= threshold misses, within
    the retry window)."""
    conn = get_connection()
    rows = conn.execute(
        f"""SELECT ticker FROM skip_tickers
            WHERE source = ? AND attempts >= {SKIP_ATTEMPT_THRESHOLD}
              AND last_attempt >= CURRENT_DATE - {int(retry_days)}""",
        [source],
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}
