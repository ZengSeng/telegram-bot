"""
StockDataUpdater: Manages historical price data in DuckDB.
Handles batched downloads from yfinance and upsert operations.
"""
import duckdb
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import List


class StockDataUpdater:
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        """Initialize the stock data updater with DuckDB connection"""
        self.conn = conn
        self._ensure_table_exists()

    def _ensure_table_exists(self):
        """Create the stock prices table if it doesn't exist"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_prices (
                date DATE NOT NULL,
                ticker VARCHAR NOT NULL,
                open DECIMAL(18,4),
                high DECIMAL(18,4),
                low DECIMAL(18,4),
                close DECIMAL(18,4),
                adj_close DECIMAL(18,4),
                volume BIGINT,
                dividends DECIMAL(18,4),
                stock_splits DECIMAL(18,4),
                capital_gains DECIMAL(18,4),
                PRIMARY KEY (date, ticker)
            )
        """)

    def get_latest_date_for_ticker(self, ticker: str) -> datetime:
        """Get the latest date for a specific ticker in the database"""
        try:
            result = self.conn.execute("""
                SELECT MAX(date) as latest_date
                FROM stock_prices
                WHERE ticker = ?
            """, [ticker]).fetchone()

            if result and result[0]:
                # Convert to datetime and add 1 day to avoid duplicates
                latest_date = pd.to_datetime(result[0])
                return latest_date + timedelta(days=1)
        except Exception as e:
            print(f"Error getting latest date for {ticker}: {e}")

        # If no data exists, return date 3 years ago
        return datetime.now() - timedelta(days = 3 * 365)

    def insert_data(self, df: pd.DataFrame):
        """Insert data into DuckDB table"""
        if df.empty:
            return

        try:
            # Use upsert to handle duplicates
            self.conn.execute("""
                INSERT OR REPLACE INTO stock_prices
                SELECT 
                    Date,
                    Ticker,
                    Open,
                    High,
                    Low,
                    Close,
                    NULL,
                    Volume,
                    Dividends,
                    stock_splits,
                    NULL
                FROM df
            """)

            print(f"Inserted {len(df)} rows")

        except Exception as e:
            print(f"Error inserting data: {e}")

    def get_latest_dates(self, tickers: List[str]) -> dict:
        """Get latest date per ticker in one query"""
        placeholders = ",".join([f"'{t}'" for t in tickers])
        result = self.conn.execute(f"""
            SELECT ticker, MAX(date) as latest_date
            FROM stock_prices
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        """).fetchdf()
        return dict(zip(result['ticker'], pd.to_datetime(result['latest_date'])))

    def update_tickers(self, tickers: List[str]):
        """Update data for a list of tickers — batched download"""
        today = datetime.now()
        latest_dates = self.get_latest_dates(tickers)

        # Group tickers by start_date to batch where possible
        default_start = today - timedelta(days =  3 * 365)
        to_download = []
        for ticker in tickers:
            latest = latest_dates.get(ticker)
            start_date = (latest - timedelta(days=5)) if latest else default_start
            if start_date.date() > today.date():
                print(f"{ticker} already up to date, skipping")
                continue
            to_download.append((ticker, start_date))

        if not to_download:
            print("All tickers up to date.")
            return

        # Find earliest common start; batch download all at once
        min_start = min(s for _, s in to_download)
        batch_tickers = [t for t, _ in to_download]

        print(batch_tickers)

        print(f"Batch downloading {len(batch_tickers)} tickers from {min_start.date()}")
        try:
            data = yf.download(
                tickers=batch_tickers,
                start=min_start.strftime('%Y-%m-%d'),
                end=today.strftime('%Y-%m-%d'),
                interval="1d",
                auto_adjust=True,
                actions=True,
                rounding=True,
                progress=False,
                group_by='ticker'
            )
            print(data)
            if data is None or data.empty:
                print("No data returned.")
                return

            # Reshape multi-ticker download
            data = data.stack(level=0).reset_index()
            print(data.columns)
            data.columns = ['date', 'ticker', 'open', 'high', 'low', 'close', 'volume', 'dividends', 'stock_splits']
            data = data[['date', 'ticker', 'open', 'high', 'low', 'close', 'volume', 'dividends', 'stock_splits']]
            data['date'] = pd.to_datetime(data['date']).dt.date

            # Filter per-ticker to only new rows
            rows = []
            for ticker, start_date in to_download:
                mask = (data['ticker'] == ticker) & (data['date'] >= start_date.date())
                rows.append(data[mask])

            final_df = pd.concat(rows, ignore_index=True)
            self.insert_data(final_df)

        except Exception as e:
            print(f"Batch download failed: {e}")

    def get_summary(self) -> pd.DataFrame:
        """Get a summary of data in the database"""
        return self.conn.execute("""
            SELECT
                ticker,
                MIN(date) as first_date,
                MAX(date) as last_date,
                COUNT(*) as num_records
            FROM stock_prices
            GROUP BY ticker
            ORDER BY ticker
        """).fetchdf()
