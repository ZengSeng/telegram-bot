"""Ingest TradingAgents analysis decisions into DuckDB.

After analysis.runner.run_analysis() produces report files, this module
parses the structured decision data and stores it in the
trading_agent_decisions table. Also cleans up bulky state logs.
"""

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from .db import get_connection

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parent.parent / "data" / "analysis_reports"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_field(text: str, label: str) -> str | None:
    """Extract a **Label**: value from markdown text."""
    m = re.search(rf"\*\*{re.escape(label)}\*\*:\s*(.+)", text)
    return m.group(1).strip() if m else None


def _parse_decision_md(path: Path) -> dict:
    """Parse 5_portfolio/decision.md for rating, summary, price target, horizon."""
    text = path.read_text(encoding="utf-8")
    return {
        "rating": _extract_field(text, "Rating"),
        "price_target": _extract_field(text, "Price Target"),
        "time_horizon": _extract_field(text, "Time Horizon"),
        "summary": _extract_field(text, "Executive Summary"),
    }


def _parse_trader_md(path: Path) -> dict:
    """Parse 3_trading/trader.md for action, entry, stop loss."""
    text = path.read_text(encoding="utf-8")
    return {
        "action": _extract_field(text, "Action"),
        "entry_price": _extract_field(text, "Entry Price"),
        "stop_loss": _extract_field(text, "Stop Loss"),
    }


def _to_float(val: str | None) -> float | None:
    """Safely convert a string to float."""
    if not val:
        return None
    try:
        return float(val.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Report discovery
# ---------------------------------------------------------------------------


def _find_latest_report(ticker: str) -> Path | None:
    """Find the most recent report folder for a ticker.

    Report folders are named: reports/{TICKER}_{YYYYMMDD}_{HHMMSS}/
    """
    reports_root = REPORTS_DIR / "reports"
    if not reports_root.exists():
        return None

    prefix = f"{ticker}_"
    matches = sorted(
        (d for d in reports_root.iterdir() if d.is_dir() and d.name.startswith(prefix)),
        key=lambda d: d.name,
        reverse=True,
    )
    return matches[0] if matches else None


def _report_is_from_today(report_dir: Path) -> bool:
    """Check if a report folder name encodes today's date."""
    # Folder name: RKLB_20260725_182427
    parts = report_dir.name.split("_")
    if len(parts) >= 2:
        try:
            from datetime import datetime

            report_date = datetime.strptime(parts[-2], "%Y%m%d").date()
            return report_date == date.today()
        except ValueError:
            pass
    return False


# ---------------------------------------------------------------------------
# State log cleanup
# ---------------------------------------------------------------------------


def _cleanup_state_logs(ticker: str) -> None:
    """Delete the bulky TradingAgentsStrategy_logs JSON for a ticker."""
    logs_dir = REPORTS_DIR / ticker / "TradingAgentsStrategy_logs"
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
        log.info("Cleaned up state logs: %s", logs_dir)
    # Also remove the parent ticker dir if now empty
    ticker_dir = REPORTS_DIR / ticker
    if ticker_dir.exists() and not any(ticker_dir.iterdir()):
        ticker_dir.rmdir()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def has_decision_today(ticker: str) -> bool:
    """Check if a decision already exists in DuckDB for today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM trading_agent_decisions WHERE ticker = ? AND date = ?",
        [ticker, date.today()],
    ).fetchone()
    conn.close()
    return row is not None


def ingest_analysis_decision(ticker: str) -> bool:
    """Parse the latest report for a ticker and store decision in DuckDB.

    Returns True if a decision was ingested, False if no report found.
    """
    report_dir = _find_latest_report(ticker)
    if not report_dir:
        log.info("No report found for %s, skipping ingestion.", ticker)
        return False

    decision_md = report_dir / "5_portfolio" / "decision.md"
    trader_md = report_dir / "3_trading" / "trader.md"

    if not decision_md.exists():
        log.warning("decision.md missing in %s", report_dir)
        return False

    # Parse structured fields
    decision = _parse_decision_md(decision_md)
    trader = _parse_trader_md(trader_md) if trader_md.exists() else {}

    # Extract report date from folder name
    parts = report_dir.name.split("_")
    try:
        from datetime import datetime

        report_date = datetime.strptime(parts[-2], "%Y%m%d").date()
    except (ValueError, IndexError):
        report_date = date.today()

    # Relative path for reference
    rel_path = str(report_dir.relative_to(REPORTS_DIR.parent.parent))

    # Upsert into DuckDB
    conn = get_connection()
    conn.execute(
        """INSERT INTO trading_agent_decisions
           (ticker, date, action, rating, price_target, entry_price,
            stop_loss, time_horizon, summary, report_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (ticker, date) DO UPDATE SET
               action = EXCLUDED.action,
               rating = EXCLUDED.rating,
               price_target = EXCLUDED.price_target,
               entry_price = EXCLUDED.entry_price,
               stop_loss = EXCLUDED.stop_loss,
               time_horizon = EXCLUDED.time_horizon,
               summary = EXCLUDED.summary,
               report_path = EXCLUDED.report_path""",
        [
            ticker,
            report_date,
            trader.get("action"),
            decision.get("rating"),
            _to_float(decision.get("price_target")),
            _to_float(trader.get("entry_price")),
            _to_float(trader.get("stop_loss")),
            decision.get("time_horizon"),
            decision.get("summary"),
            rel_path,
        ],
    )
    conn.close()

    log.info("Ingested decision for %s (%s): %s / %s", ticker, report_date,
             trader.get("action"), decision.get("rating"))

    # Cleanup bulky state logs
    _cleanup_state_logs(ticker)

    return True
