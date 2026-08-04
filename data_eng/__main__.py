"""CLI entrypoint: python -m data_eng TICKER [TICKER2 ...] | --batch | --daily"""

import argparse
import json
import logging
from pathlib import Path

from .ingest import batch_ingest_prices, ingest_all
from .pipeline import run_daily_pipeline, run_night_pipeline, run_universe_build, run_universe_group
from .screener import run_screener
from .candidates import select_candidates
from .events import detect_events_batch
from .portfolio_engine import run_portfolio_engine
from .portfolio_review import run_portfolio_review
from .enrich import run_enrich

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
    parser.add_argument(
        "--universe", action="store_true",
        help="Scrape full stock universe and batch-ingest prices for all tickers"
    )
    parser.add_argument(
        "--universe-group", type=int, choices=[1, 2, 3, 4], metavar="N",
        help="Scrape one sector group (1-4) and ingest its prices"
    )
    parser.add_argument(
        "--daily-smart", action="store_true",
        help="Run daily pipeline with smart scheduling (skip fresh data)"
    )
    parser.add_argument(
        "--screen", action="store_true",
        help="Run quantitative screener on all tickers with data"
    )
    parser.add_argument(
        "--candidates", action="store_true",
        help="Select sector-balanced candidates from screener scores"
    )
    parser.add_argument(
        "--events", action="store_true",
        help="Detect events for watchlist tickers (price moves, news, technicals)"
    )
    parser.add_argument(
        "--portfolio", action="store_true",
        help="Run deterministic portfolio engine (rules-based trade proposals)"
    )
    parser.add_argument(
        "--review", action="store_true",
        help="Run LLM portfolio review (investment committee)"
    )
    parser.add_argument(
        "--enrich", action="store_true",
        help="Bulk-enrich fundamentals for universe tickers (rolling N/day)"
    )
    parser.add_argument(
        "--sector", type=str, default=None,
        help="Filter --enrich by sector (e.g. technology)"
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max tickers per --enrich run (default: 100)"
    )
    parser.add_argument(
        "--night", action="store_true",
        help="Run night pipeline: universe + financials + bulk enrichment + TradingAgents batch"
    )
    parser.add_argument(
        "--analysis-limit", type=int, default=None,
        help="Max TradingAgents analyses for --night (default: NIGHT_ANALYSIS_LIMIT)"
    )
    parser.add_argument(
        "--batch-universe", action="store_true",
        help="Batch-download incremental prices for all universe tickers"
    )
    args = parser.parse_args()

    if args.night:
        print("Running night pipeline (universe + financials + bulk enrichment + analyses)...")
        watchlist = _load_watchlist()
        from .pipeline import NIGHT_ANALYSIS_LIMIT
        run_night_pipeline(
            tickers=watchlist,
            fundamentals_limit=args.limit,
            analyst_limit=args.limit,
            enriched_limit=args.limit,
            analysis_limit=args.analysis_limit if args.analysis_limit is not None else NIGHT_ANALYSIS_LIMIT,
        )
    elif args.universe:
        print("Building full stock universe...")
        run_universe_build()
    elif args.universe_group:
        print(f"Building universe group {args.universe_group}...")
        run_universe_group(args.universe_group)
    elif args.daily:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty. Add tickers via /watch or edit data/watchlist.json")
            return
        print(f"Running daily pipeline for: {', '.join(tickers)}")
        run_daily_pipeline(tickers)
    elif args.daily_smart:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty. Add tickers via /watch or edit data/watchlist.json")
            return
        print(f"Running smart daily pipeline for: {', '.join(tickers)}")
        run_daily_pipeline(tickers, use_smart_scheduling=True)
    elif args.screen:
        print("Running quantitative screener...")
        scores = run_screener()
        if not scores.empty:
            top = scores.sort_values("overall_score", ascending=False).head(10)
            print("\nTop 10 by overall score:")
            print(top[["quality_score", "value_score", "momentum_score",
                       "sentiment_score", "risk_score", "overall_score"]].to_string())
        else:
            print("No data available to screen.")
    elif args.candidates:
        print("Selecting candidates...")
        result = select_candidates()
        if not result.empty:
            print(f"\nSelected {len(result)} candidates:")
            print(result[["ticker", "sector", "overall_score"]].to_string(index=False))
        else:
            print("No candidates selected (run --screen first).")
    elif args.events:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty.")
            return
        print(f"Detecting events for: {', '.join(tickers)}")
        results = detect_events_batch(tickers)
        if results:
            for ticker, events in results.items():
                print(f"\n  {ticker}:")
                for ev in events:
                    print(f"    [{ev['event_type']}] {ev['details']}")
        else:
            print("No events detected.")
    elif args.portfolio:
        print("Running portfolio engine...")
        results = run_portfolio_engine()
        if results:
            print(f"\n{'Ticker':<8} {'Action':<6} {'Pos%':<7} {'Shares':<8} {'Stop':<9} Reason")
            print("-" * 70)
            for r in sorted(results, key=lambda x: x["action"]):
                stop = f"${r['stop_loss']:.2f}" if r.get("stop_loss") else "\u2014"
                pct = f"{r['position_pct']:.1f}%" if r.get("position_pct") else "\u2014"
                shares = f"{r['shares']:.0f}" if r.get("shares") else "\u2014"
                print(f"{r['ticker']:<8} {r['action']:<6} {pct:<7} {shares:<8} {stop:<9} {r.get('reason', '')}")
        else:
            print("No decisions generated.")
    elif args.review:
        print("Running portfolio review (LLM investment committee)...")
        review = run_portfolio_review()
        if review:
            print(f"\n{review}")
        else:
            print("No review generated (run --portfolio first).")
    elif args.enrich:
        print(f"Enriching fundamentals (sector={args.sector or 'all'}, limit={args.limit})...")
        count = run_enrich(sector=args.sector, limit=args.limit)
        print(f"Done: {count} tickers enriched.")
    elif args.batch:
        tickers = _load_watchlist()
        if not tickers:
            print("Watchlist is empty. Add tickers via /watch or edit data/watchlist.json")
            return
        print(f"Batch downloading prices for: {', '.join(tickers)}")
        batch_ingest_prices(tickers)
    elif args.batch_universe:
        from .universe import UniverseScraper
        scraper = UniverseScraper()
        tickers = scraper.get_universe_tickers()
        if not tickers:
            print("Universe is empty. Run --universe first.")
            return
        print(f"Batch downloading prices for {len(tickers)} universe tickers...")
        batch_ingest_prices(tickers)
    elif args.tickers:
        for ticker in args.tickers:
            ingest_all(ticker.upper())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
