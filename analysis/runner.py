"""TradingAgents runner: configures DuckDB data layer + local llama.cpp LLM."""

import json
import logging
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

# Local llama.cpp endpoint + model alias (single source: stock_bot/config.py)
from stock_bot.config import LLAMA_BASE_URL as LLAMA_BACKEND_URL, LLAMA_MODEL

log = logging.getLogger(__name__)

# Where reports are saved
REPORTS_DIR = Path(__file__).parent.parent / "data" / "analysis_reports"

# Hard cap on tokens per LLM generation. The GPU is generation-bound
# (~96% util), so runaway outputs are the biggest time sink; capping
# removes the long tail. Structured outputs (trader/PM JSON) are short
# and unaffected.
LLM_MAX_TOKENS_QUICK = 1536
LLM_MAX_TOKENS_DEEP = 2048


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


def _create_gfinance_evidence_node(side: str):
    """LLM-free Bull/Bear node: writes the stored Google Finance crowd points
    straight into the debate history.

    Replaces the upstream LLM researcher — no model call, just a DB read —
    saving two long generations per analysis. The debate stage keeps its
    shape (routing needs the 'Bull/Bear Analyst:' prefix and the count bump),
    and the Research Manager still evaluates this bull/bear text alongside
    the analyst reports.
    """
    label = "Bull" if side == "bull" else "Bear"

    def factory(llm):
        def node(state) -> dict:
            debate = state["investment_debate_state"]
            history = debate.get("history", "")
            side_history = debate.get(f"{side}_history", "")
            # AgentState stores the ticker as company_of_interest (there is
            # no "ticker" key); fall back to the first human message.
            ticker = state.get("company_of_interest") or (
                state["messages"][0][1] if state.get("messages") else ""
            )
            bull_points, bear_points = (
                _gfinance_bull_bear(ticker) if ticker else ("", "")
            )
            points = bull_points if side == "bull" else bear_points
            if points:
                body = (
                    f"Crowd-sourced {label.lower()} case from Google Finance:\n"
                    f"{points}"
                )
            else:
                body = (
                    f"No crowd-sourced {label.lower()} case available from "
                    "Google Finance; weigh the analyst reports accordingly."
                )
            argument = f"{label} Analyst: {body}"

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


def _create_rule_based_risk_node(role: str):
    """LLM-free risk debater: writes a fixed-perspective stance straight
    into the debate history.

    Same pattern as the gfinance bull/bear nodes — no model call, saving
    three long generations per analysis (each risk node otherwise receives
    all four reports plus the full debate context). The debate keeps its
    shape (routing needs latest_speaker + count), and the Portfolio
    Manager still weighs the trader plan against these three stances —
    the plan itself is passed to the PM separately, so the notes don't
    repeat it.
    """
    labels = {
        "aggressive": (
            "Aggressive",
            "Upside-first stance: the opportunity in the trader's plan "
            "stands; favor full participation and treat drawdowns as "
            "acceptable within the thesis. Challenge any overly cautious "
            "readings of the same data.",
        ),
        "neutral": (
            "Neutral",
            "Balanced stance: weigh the trader's plan on evidence only — "
            "accept what the data supports, flag what it does not, and "
            "size the position accordingly.",
        ),
        "conservative": (
            "Conservative",
            "Downside-first stance: capital preservation leads — honor the "
            "stop loss, question weak evidence in the trader's plan, and "
            "prefer smaller exposure when signals conflict.",
        ),
    }
    label, stance = labels[role]

    def factory(llm):
        def node(state) -> dict:
            risk = state["risk_debate_state"]
            argument = f"{label} Analyst: {stance}"

            new_risk = dict(risk)
            new_risk["history"] = risk.get("history", "") + "\n" + argument
            new_risk[f"{role}_history"] = (
                risk.get(f"{role}_history", "") + "\n" + argument
            )
            new_risk["latest_speaker"] = label
            new_risk[f"current_{role}_response"] = argument
            new_risk["count"] = risk["count"] + 1
            return {"risk_debate_state": new_risk}

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

    # Bull/bear debate without LLM calls: inject stored Google Finance
    # bull/bear points directly (saves ~2 generations per analysis)
    import tradingagents.graph.setup as setup_mod

    setup_mod.create_bull_researcher = _create_gfinance_evidence_node("bull")
    setup_mod.create_bear_researcher = _create_gfinance_evidence_node("bear")

    # Risk debate without LLM calls: fixed-perspective stances, trader
    # plan not echoed (PM receives it directly) — saves ~3 generations
    # per analysis
    setup_mod.create_aggressive_debator = _create_rule_based_risk_node("aggressive")
    setup_mod.create_neutral_debator = _create_rule_based_risk_node("neutral")
    setup_mod.create_conservative_debator = _create_rule_based_risk_node("conservative")

    # Verified-market-snapshot OHLCV from local DB, not live yfinance
    import tradingagents.dataflows.market_data_validator as validator_mod

    validator_mod.load_ohlcv = _local_load_ohlcv

    # Suppress noisy structured-output retry warnings (expected with local 9B model)
    logging.getLogger("tradingagents.agents.utils.structured").setLevel(logging.ERROR)

    log.info("Patched TradingAgents dataflows with DuckDB vendor")


def _save_single_report(final_state: dict, ticker: str) -> Path:
    """Keep only the consolidated report: data/analysis_reports/reports/
    TICKER_TIMESTAMP.md. The per-section tree the vendor writes is
    discarded via a temp dir."""
    from tradingagents.reporting import write_report_tree

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = Path(tempfile.mkdtemp(prefix="ta_report_"))
    try:
        complete = write_report_tree(final_state, ticker, tmp / "run")
        dest_dir = REPORTS_DIR / "reports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{ticker}_{stamp}.md"
        shutil.move(str(complete), dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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

    # Cap generation length on both tiers (not in vendor's passthrough
    # kwargs, so set on the constructed langchain models directly)
    ta.quick_thinking_llm.max_tokens = LLM_MAX_TOKENS_QUICK
    ta.deep_thinking_llm.max_tokens = LLM_MAX_TOKENS_DEEP

    final_state, decision = ta.propagate(ticker, analysis_date)

    # Save consolidated report only (TICKER_TIMESTAMP.md)
    report_path = _save_single_report(final_state, ticker)
    log.info("Report saved: %s", report_path)

    log.info("Analysis complete for %s", ticker)
    return decision
