"""Migration: split technical indicators out of ticker_enriched into technicals table.

Run once:  python migrate_technicals.py

Steps:
  1. Create the technicals table (if not exists).
  2. Copy technical indicator columns from ticker_enriched → technicals.
  3. Drop those columns from ticker_enriched.

Safe to re-run (idempotent).
"""

import duckdb
from pathlib import Path

DB_PATH = Path("data/market.duckdb")

TECHNICAL_COLUMNS = [
    "sma_20", "sma_50", "ema_12", "ema_26",
    "macd", "macd_signal", "macd_hist",
    "rsi_14",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "volume_sma_20", "obv", "volume_ratio",
    "signal_rsi", "signal_macd", "signal_trend", "signal_bb", "signal_volume",
    "combined_signal", "trade_signal",
]

CREATE_TECHNICALS = """
CREATE TABLE IF NOT EXISTS technicals (
    ticker              VARCHAR NOT NULL,
    date_fetched        DATE NOT NULL,
    sma_20              DOUBLE,
    sma_50              DOUBLE,
    ema_12              DOUBLE,
    ema_26              DOUBLE,
    macd                DOUBLE,
    macd_signal         DOUBLE,
    macd_hist           DOUBLE,
    rsi_14              DOUBLE,
    bb_upper            DOUBLE,
    bb_middle           DOUBLE,
    bb_lower            DOUBLE,
    bb_width            DOUBLE,
    volume_sma_20       DOUBLE,
    obv                 DOUBLE,
    volume_ratio        DOUBLE,
    signal_rsi          INTEGER,
    signal_macd         INTEGER,
    signal_trend        INTEGER,
    signal_bb           INTEGER,
    signal_volume       INTEGER,
    combined_signal     DOUBLE,
    trade_signal        INTEGER,
    PRIMARY KEY (ticker, date_fetched)
);
"""


def migrate():
    conn = duckdb.connect(str(DB_PATH))

    # 1. Create technicals table
    print("Creating technicals table...")
    conn.execute(CREATE_TECHNICALS)

    # 2. Check if ticker_enriched still has technical columns
    cols = [row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'ticker_enriched'"
    ).fetchall()]

    missing = [c for c in TECHNICAL_COLUMNS if c not in cols]
    if len(missing) == len(TECHNICAL_COLUMNS):
        print("ticker_enriched already has no technical columns — nothing to migrate.")
        conn.close()
        return

    if missing:
        print(f"Warning: columns already missing from ticker_enriched: {missing}")

    # 3. Copy data from ticker_enriched → technicals
    existing_cols = [c for c in TECHNICAL_COLUMNS if c in cols]
    if existing_cols:
        col_list = ", ".join(existing_cols)
        print(f"Copying {len(existing_cols)} technical columns from ticker_enriched -> technicals...")
        conn.execute(f"""
            INSERT OR REPLACE INTO technicals (ticker, date_fetched, {col_list})
            SELECT ticker, date_fetched, {col_list}
            FROM ticker_enriched
            WHERE {existing_cols[0]} IS NOT NULL
        """)
        count = conn.execute("SELECT COUNT(*) FROM technicals").fetchone()[0]
        print(f"  -> {count} rows in technicals table.")

    # 4. Drop columns from ticker_enriched
    for col in existing_cols:
        print(f"  Dropping {col} from ticker_enriched...")
        conn.execute(f"ALTER TABLE ticker_enriched DROP COLUMN {col}")

    print("Migration complete.")
    conn.close()


if __name__ == "__main__":
    migrate()
