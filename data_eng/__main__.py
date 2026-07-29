"""CLI entrypoint: python -m data_eng TICKER [TICKER2 ...] | --batch | --daily"""

import argparse
import json
import logging
from pathlib import Path

from .ingest import batch_ingest_prices, ingest_all
from .pipeline import run_daily_pipeline

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"


def _load_watchlist() -> list[str]:
    """Load watchlist tickers from data/watchlist.json."""
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return []


def main():
    parser = argparse.ArgumentParser(description="Ingest market data into DuckDB")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to ingest (e.g. AAPL MSFT)")
    parser.add_argument(
        "--batch", action="store_true",
        help="Batch-download daily prices for all watchlist tickers (incremental)"
    )
    parser.add_argument(
        "--daily", action="store_true",
        help="Run full daily pipeline (prices, news, enriched, targets, financials)"
    )
    args = parser.parse_args()

    if args.daily:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty. Add tickers via /watch or edit data/watchlist.json")
            return
        print(f"Running daily pipeline for: {', '.join(tickers)}")
        run_daily_pipeline(tickers)
    elif args.batch:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty. Add tickers via /watch or edit data/watchlist.json")
            return
        print(f"Batch downloading prices for: {', '.join(tickers)}")
        batch_ingest_prices(tickers)
    elif args.tickers:
        for ticker in args.tickers:
            ingest_all(ticker.upper())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
