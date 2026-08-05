"""TradingAgents runner: configures DuckDB data layer + local llama.cpp LLM."""

import json
import logging
import re
from datetime import date
from pathlib import Path

# Local llama.cpp endpoint + model alias (single source: stock_bot/config.py)
from stock_bot.config import LLAMA_BASE_URL as LLAMA_BACKEND_URL, LLAMA_MODEL

log = logging.getLogger(__name__)

# Where reports are saved
REPORTS_DIR = Path(__file__).parent.parent / "data" / "analysis_reports"


# ---------------------------------------------------------------------------
# Local-data patches: ground the debate in stored data, avoid live network
# ---------------------------------------------------------------------------


def _gfinance_bull_bear(ticker: str) -> tuple[str, str]:
    """Latest gfinance bull/bear points as text; scrape on demand if missing."""
    from data_eng.db import get_connection

    def fetch():
        conn = get_connection()
        row = conn.execute(
            "SELECT bull_points, bear_points FROM gfinance_overview "
            "WHERE ticker = ? ORDER BY date_fetched DESC LIMIT 1",
            [ticker],
        ).fetchone()
        conn.close()
        return row

    row = fetch()
    if row is None:
        try:
            from data_eng.gfinance import ingest_gfinance_overview
            if ingest_gfinance_overview(ticker):
                row = fetch()
        except Exception as e:
            log.warning("gfinance on-demand fetch failed for %s: %s", ticker, e)

    def fmt(points_json):
        try:
            points = json.loads(points_json or "[]")
        except Exception:
            return ""
        return "\n".join(
            f"- {p.get('title', '')}: {p.get('description', '')}" for p in points
        )

    if not row:
        return "", ""
    return fmt(row[0]), fmt(row[1])


# Degenerate-answer detection thresholds (see _is_degenerate_argument)
MIN_ARGUMENT_CHARS = 200   # anything shorter is unusable for the debate
TOOL_CALL_SCAN_WINDOW = 600  # only the head of the text is scanned


def _is_degenerate_argument(text: str) -> bool:
    """Detect the local model imitating a tool call instead of answering.

    Observed failures: one-liners like 'Tool call: Fundamental Analysis
    Report' or bare JSON blobs ({"name": "get_insider_activity", ...}).
    """
    stripped = (text or "").strip()
    if len(stripped) < MIN_ARGUMENT_CHARS:
        return True
    head = stripped[:TOOL_CALL_SCAN_WINDOW]
    return bool(re.search(r"tool[ _-]?call|\"arguments\"\s*:", head, re.I))


def _response_text(response) -> str:
    """Coerce an LLM response's content to a plain string."""
    return (
        response.content if isinstance(response.content, str)
        else str(response.content)
    )


def _create_grounded_researcher(side: str):
    """Bull/Bear researcher node grounded with Google Finance crowd points.

    Mirrors the upstream node (same state plumbing) but injects the stored
    gfinance bull/bear case as extra evidence, so the local 9B model debates
    from real current theses instead of only the analyst reports.
    """
    label = "Bull" if side == "bull" else "Bear"

    def factory(llm):
        def node(state) -> dict:
            from tradingagents.agents.utils.agent_utils import (
                get_instrument_context_from_state,
                get_language_instruction,
            )

            debate = state["investment_debate_state"]
            history = debate.get("history", "")
            side_history = debate.get(f"{side}_history", "")
            current_response = debate.get("current_response", "")
            # AgentState stores the ticker as company_of_interest (there is
            # no "ticker" key); fall back to the first human message.
            ticker = state.get("company_of_interest") or (
                state["messages"][0][1] if state.get("messages") else ""
            )
            bull_points, bear_points = (
                _gfinance_bull_bear(ticker) if ticker else ("", "")
            )
            gfinance_section = (
                f"Google Finance crowd-sourced bull case:\n{bull_points}\n"
                f"Google Finance crowd-sourced bear case:\n{bear_points}"
                if (bull_points or bear_points) else ""
            )

            no_tools_directive = (
                "\nIMPORTANT: Do not attempt any tool calls and do not output "
                "JSON. No tools are available at this stage — write your full "
                "argument directly as prose."
            )
            prompt = f"""You are a {label} Analyst advocating {'for' if side == 'bull' else 'against'} investing in the stock. Build a strong, evidence-based case. Leverage the provided research and data, and engage directly with the other side's latest argument rather than just listing data.

Resources available:
{get_instrument_context_from_state(state)}
Market research report: {state['market_report']}
Social media sentiment report: {state['sentiment_report']}
Latest world affairs news: {state['news_report']}
Company fundamentals report: {state['fundamentals_report']}
{gfinance_section}
Conversation history of the debate: {history}
Last opposing argument: {current_response}
""" + get_language_instruction() + no_tools_directive

            response = llm.invoke(prompt)
            content = _response_text(response)
            # The local 9B sometimes emits a fake tool call instead of prose
            # (e.g. 'Tool call: ...' or a JSON blob); retry once with an
            # explicit nudge before accepting it.
            if _is_degenerate_argument(content) or getattr(response, "tool_calls", None):
                log.warning("%s analyst produced a degenerate answer for %s — "
                            "retrying once", label, ticker)
                retry = llm.invoke(
                    prompt + no_tools_directive +  # repeated for emphasis
                    "\nYour previous attempt was unusable. Try again and write "
                    "several paragraphs of direct analysis."
                )
                retry_content = _response_text(retry)
                if (not _is_degenerate_argument(retry_content)
                        and not getattr(retry, "tool_calls", None)):
                    content = retry_content
                else:
                    log.warning("%s analyst retry also degenerate for %s — "
                                "using original answer", label, ticker)
            argument = f"{label} Analyst: {content}"

            new_debate = {
                "history": history + "\n" + argument,
                f"{side}_history": side_history + "\n" + argument,
                f"{'bear' if side == 'bull' else 'bull'}_history": debate.get(
                    f"{'bear' if side == 'bull' else 'bull'}_history", ""),
                "current_response": argument,
                "count": debate["count"] + 1,
            }
            return {"investment_debate_state": new_debate}

        return node

    return factory


def _local_load_ohlcv(symbol: str, curr_date: str):
    """Serve the verification snapshot's OHLCV from local daily_prices
    instead of a live 5-year yfinance download (network + rate-limit cost)."""
    import pandas as pd

    from data_eng.db import get_connection

    conn = get_connection()
    df = conn.execute(
        """SELECT date AS Date, open AS Open, high AS High, low AS Low,
                  close AS Close, volume AS Volume
           FROM daily_prices
           WHERE ticker = ? AND date <= CAST(? AS DATE)
           ORDER BY date""",
        [symbol, curr_date],
    ).fetchdf()
    conn.close()

    if df.empty:
        raise ValueError(f"No local price data for {symbol}")
    df["Date"] = pd.to_datetime(df["Date"])
    return df


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

    # No FRED API key: stub macro indicators instead of letting the fred
    # vendor warn and fall back on every analysis
    interface.VENDOR_METHODS["get_macro_indicators"] = {
        "duckdb": lambda *a, **kw: "(Macro data not available — no FRED API key)"
    }

    # No Polymarket integration: stub prediction markets the same way
    interface.VENDOR_METHODS["get_prediction_markets"] = {
        "duckdb": lambda *a, **kw: "(Prediction markets not available — using local DB only)"
    }

    # Ground bull/bear debate with stored Google Finance bull/bear points
    import tradingagents.graph.setup as setup_mod

    setup_mod.create_bull_researcher = _create_grounded_researcher("bull")
    setup_mod.create_bear_researcher = _create_grounded_researcher("bear")

    # Verified-market-snapshot OHLCV from local DB, not live yfinance
    import tradingagents.dataflows.market_data_validator as validator_mod

    validator_mod.load_ohlcv = _local_load_ohlcv

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
        "macro_data": "duckdb",
        "prediction_markets": "duckdb",
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
