"""CLI entrypoint: python -m data_eng TICKER [TICKER2 ...]"""

import argparse
import logging

from .ingest import ingest_all

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def main():
    parser = argparse.ArgumentParser(description="Ingest market data into DuckDB")
    parser.add_argument("tickers", nargs="+", help="Ticker symbols to ingest (e.g. AAPL MSFT)")
    args = parser.parse_args()

    for ticker in args.tickers:
        ingest_all(ticker.upper())


if __name__ == "__main__":
    main()
