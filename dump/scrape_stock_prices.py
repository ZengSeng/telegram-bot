import yfinance as yf
import duckdb
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Optional

class StockPriceDB:
    def __init__(self, db_path: str = 'stock_data.db', table_name: str = 'stock_prices'):
        self.db_path = db_path
        self.table_name = table_name
        self._init_db()
    
    def _init_db(self):
        """Initialize database and table"""
        with duckdb.connect(self.db_path) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    date DATE NOT NULL,
                    ticker VARCHAR NOT NULL,
                    open DECIMAL(18,4),
                    high DECIMAL(18,4),
                    low DECIMAL(18,4),
                    close DECIMAL(18,4),
                    volume BIGINT,
                    dividends DECIMAL(18,4),
                    stock_splits DECIMAL(18,4),
                    PRIMARY KEY (date, ticker)
                )
            """)
    
    def _get_existing_symbols(self) -> set:
        """Get symbols already in database"""
        with duckdb.connect(self.db_path) as conn:
            try:
                result = conn.execute(f"SELECT DISTINCT ticker FROM {self.table_name}").fetchdf()
                return set(result['ticker'].tolist()) if not result.empty else set()
            except:
                return set()
    
    def download(self, symbols: List[str], years_back: int = 2):
        """Download and store data for symbols"""
        # Filter out existing symbols
        existing = self._get_existing_symbols()
        new_symbols = [s for s in symbols if s not in existing]
        
        if not new_symbols:
            print(f"All {len(symbols)} symbols already exist")
            return
        
        print(f"Downloading {len(new_symbols)} symbols: {new_symbols}")
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=years_back * 365)
        
        # Download data
        data = yf.download(
            tickers=new_symbols,
            start=start_date.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d'),
            actions=True,
            auto_adjust=True,
            group_by='ticker',
            progress=True,
            threads=True
        )
        
        if data.empty:
            print("No data downloaded")
            return
        
        # Flatten data
        records = []
        for ticker in new_symbols:
            if ticker in data.columns.get_level_values(1):
                ticker_data = data.xs(ticker, axis=1, level=1)
                ticker_data = ticker_data.reset_index()
                ticker_data['ticker'] = ticker
                records.append(ticker_data)
        
        if not records:
            print("No records to insert")
            return
        
        # Combine and clean
        df = pd.concat(records, ignore_index=True)
        df.columns = df.columns.str.lower()
        
        # Fill missing values
        df['volume'] = df['volume'].fillna(0).astype('int64')
        df['dividends'] = df['dividends'].fillna(0)
        df['stock_splits'] = df['stock_splits'].fillna(0)
        
        # Insert into database
        with duckdb.connect(self.db_path) as conn:
            conn.register('temp_df', df)
            conn.execute(f"""
                INSERT OR REPLACE INTO {self.table_name}
                SELECT 
                    date::DATE,
                    ticker::VARCHAR,
                    open::DECIMAL(18,4),
                    high::DECIMAL(18,4),
                    low::DECIMAL(18,4),
                    close::DECIMAL(18,4),
                    volume::BIGINT,
                    dividends::DECIMAL(18,4),
                    stock_splits::DECIMAL(18,4)
                FROM temp_df
                WHERE date IS NOT NULL AND ticker IS NOT NULL
            """)
        
        print(f"✅ Inserted data for {len(new_symbols)} symbols")
    
    def query(self, symbols: Optional[List[str]] = None, 
              start_date: Optional[str] = None, 
              end_date: Optional[str] = None) -> pd.DataFrame:
        """Query data from database"""
        with duckdb.connect(self.db_path) as conn:
            query = f"SELECT * FROM {self.table_name}"
            conditions = []
            params = []
            
            if symbols:
                placeholders = ','.join(['?'] * len(symbols))
                conditions.append(f"ticker IN ({placeholders})")
                params.extend(symbols)
            
            if start_date:
                conditions.append("date >= ?")
                params.append(start_date)
            
            if end_date:
                conditions.append("date <= ?")
                params.append(end_date)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY ticker, date"
            
            return conn.execute(query, params).fetchdf()

# Usage
db = StockPriceDB('stock_data.db', 'stock_prices')

# Download last 2 years of data
db.download(['AAPL', 'GOOGL', 'MSFT', 'NVDA', 'META'])

# Query the data
df = db.query(symbols=['AAPL', 'GOOGL'], start_date='2024-01-01')
print(df.head())

# Get all data
all_data = db.query()
print(f"Total rows: {len(all_data)}")