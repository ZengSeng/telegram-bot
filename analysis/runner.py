"""TradingAgents runner: configures DuckDB data layer + local llama.cpp LLM."""

import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# Local llama.cpp config (matches stock_bot/config.py)
LLAMA_BACKEND_URL = "http://127.0.0.1:10000/v1"
LLAMA_MODEL = "MyQwythos"

# Where reports are saved
REPORTS_DIR = Path(__file__).parent.parent / "data" / "analysis_reports"


def _patch_dataflows():
    """Monkey-patch TradingAgents VENDOR_METHODS to use DuckDB vendor."""
    from tradingagents.dataflows import interface

    from . import duckdb_vendor

    # Register "duckdb" as a vendor for all methods
    duckdb_methods = {
        "get_stock_data": duckdb_vendor.get_stock_data,
        "get_indicators": duckdb_vendor.get_indicators,
        "get_fundamentals": duckdb_vendor.get_fundamentals,
        "get_balance_sheet": duckdb_vendor.get_balance_sheet,
        "get_cashflow": duckdb_vendor.get_cashflow,
        "get_income_statement": duckdb_vendor.get_income_statement,
        "get_news": duckdb_vendor.get_news,
        "get_global_news": duckdb_vendor.get_global_news,
        "get_insider_transactions": duckdb_vendor.get_insider_transactions,
        "get_market_sentiment": duckdb_vendor.get_market_sentiment,
    }

    for method, func in duckdb_methods.items():
        if method in interface.VENDOR_METHODS:
            interface.VENDOR_METHODS[method]["duckdb"] = func
        else:
            interface.VENDOR_METHODS[method] = {"duckdb": func}

    # Skip Reddit and StockTwits (avoid 429 rate limits, not stored locally)
    import tradingagents.dataflows.reddit as reddit_mod
    import tradingagents.dataflows.stocktwits as stocktwits_mod

    reddit_mod.fetch_reddit_posts = lambda *a, **kw: "(Reddit data not available — using local DB only)"
    stocktwits_mod.fetch_stocktwits_messages = lambda *a, **kw: "(StockTwits data not available — using local DB only)"

    # Suppress noisy structured-output retry warnings (expected with local 9B model)
    logging.getLogger("tradingagents.agents.utils.structured").setLevel(logging.ERROR)

    log.info("Patched TradingAgents dataflows with DuckDB vendor")


def run_analysis(ticker: str, analysis_date: str = None) -> str:
    """Run TradingAgents analysis for a ticker using local data + LLM.

    Args:
        ticker: Stock symbol (e.g. "AAPL")
        analysis_date: Date string YYYY-MM-DD. Defaults to today.

    Returns:
        Decision text from TradingAgents.
    """
    if analysis_date is None:
        analysis_date = date.today().strftime("%Y-%m-%d")

    # Patch data layer before importing the graph
    _patch_dataflows()

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()

    # LLM: use local llama.cpp via openai_compatible
    config["llm_provider"] = "openai_compatible"
    config["backend_url"] = LLAMA_BACKEND_URL
    config["deep_think_llm"] = LLAMA_MODEL
    config["quick_think_llm"] = LLAMA_MODEL

    # Data: route all categories to duckdb
    config["data_vendors"] = {
        "core_stock_apis": "duckdb",
        "technical_indicators": "duckdb",
        "fundamental_data": "duckdb",
        "news_data": "duckdb",
    }

    # Keep it lightweight for local model
    config["max_debate_rounds"] = 1
    config["results_dir"] = str(REPORTS_DIR)

    log.info("Starting TradingAgents analysis: %s on %s", ticker, analysis_date)

    ta = TradingAgentsGraph(
        selected_analysts=("market", "news", "fundamentals"),
        debug=False,
        config=config,
    )
    final_state, decision = ta.propagate(ticker, analysis_date)

    # Save full report tree (markdown files)
    report_path = ta.save_reports(final_state, ticker)
    log.info("Report saved: %s", report_path)

    log.info("Analysis complete for %s", ticker)
    return decision
