"""DuckDB connection and schema management for market data."""

from pathlib import Path

import duckdb

# Database lives in data/ alongside other bot data
DB_PATH = Path(__file__).parent.parent / "data" / "market.duckdb"

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
"""


def get_connection() -> duckdb.DuckDBPyConnection:
    """Get a DuckDB connection with schema initialized."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(SCHEMA)
    return conn
