"""Event detection: gate expensive analysis on meaningful changes."""

import logging
from datetime import date

from .db import get_connection

log = logging.getLogger(__name__)

# Daily price move threshold to trigger re-analysis
PRICE_MOVE_THRESHOLD = 0.05  # 5%


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


def _check_price_move(ticker: str) -> dict | None:
    """Compare latest close to previous close. Trigger if abs > 5%."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT date, close FROM daily_prices
        WHERE ticker = ?
        ORDER BY date DESC
        LIMIT 2
        """,
        [ticker],
    ).fetchall()
    conn.close()

    if len(rows) < 2:
        return None

    latest_close = rows[0][1]
    prev_close = rows[1][1]

    if not prev_close or prev_close == 0:
        return None

    change = (latest_close - prev_close) / prev_close

    if abs(change) >= PRICE_MOVE_THRESHOLD:
        direction = "up" if change > 0 else "down"
        return {
            "event_type": "price_move",
            "details": f"{direction} {abs(change)*100:.1f}% ({prev_close:.2f} -> {latest_close:.2f})",
        }
    return None


def _check_technical_change(ticker: str) -> dict | None:
    """Compare trade_signal in latest vs previous technicals row."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT date_fetched, trade_signal FROM technicals
        WHERE ticker = ?
        ORDER BY date_fetched DESC
        LIMIT 2
        """,
        [ticker],
    ).fetchall()
    conn.close()

    if len(rows) < 2:
        return None

    latest_signal = rows[0][1]
    prev_signal = rows[1][1]

    if latest_signal is None or prev_signal is None:
        return None

    if latest_signal != prev_signal:
        return {
            "event_type": "technical_change",
            "details": f"trade_signal {prev_signal} -> {latest_signal}",
        }
    return None


def _check_earnings(ticker: str, since: date) -> dict | None:
    """Check if any financials report_date > since_date."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT MAX(report_date) FROM financials
        WHERE ticker = ? AND report_date > ?
        """,
        [ticker, since],
    ).fetchone()
    conn.close()

    if row and row[0]:
        report_date = row[0] if isinstance(row[0], date) else row[0].date()
        return {
            "event_type": "earnings",
            "details": f"new filing with report_date {report_date}",
        }
    return None


# ---------------------------------------------------------------------------
# Main detection
# ---------------------------------------------------------------------------


def _get_last_analysis_date(ticker: str) -> date | None:
    """Get the most recent trading_agent_decisions date for a ticker."""
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(date) FROM trading_agent_decisions WHERE ticker = ?",
        [ticker],
    ).fetchone()
    conn.close()

    if not row or not row[0]:
        return None
    return row[0] if isinstance(row[0], date) else row[0].date()


def _has_decision_today(ticker: str) -> bool:
    """Check if a decision already exists for today."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM trading_agent_decisions WHERE ticker = ? AND date = ?",
        [ticker, date.today()],
    ).fetchone()
    conn.close()
    return row is not None


def detect_events(ticker: str, since_date: date | None = None) -> list[dict]:
    """Detect all events for a ticker since a given date.

    If since_date is None, uses the last trading_agent_decisions date.
    Returns list of {event_type, details} dicts.
    """
    if since_date is None:
        since_date = _get_last_analysis_date(ticker)

    events: list[dict] = []

    # Price move (doesn't need since_date — always checks latest 2 days)
    try:
        ev = _check_price_move(ticker)
        if ev:
            events.append(ev)
    except Exception as e:
        log.debug("Events: price_move check failed for %s: %s", ticker, e)

    # Technical change (doesn't need since_date — compares latest 2 enrichments)
    try:
        ev = _check_technical_change(ticker)
        if ev:
            events.append(ev)
    except Exception as e:
        log.debug("Events: technical_change check failed for %s: %s", ticker, e)

    # Earnings needs a since_date (news event check removed: the daily
    # pipeline refreshes watchlist news every morning, so it would trigger
    # an analysis every single day)
    if since_date:
        try:
            ev = _check_earnings(ticker, since_date)
            if ev:
                events.append(ev)
        except Exception as e:
            log.debug("Events: earnings check failed for %s: %s", ticker, e)

    return events


def should_run_analysis(ticker: str) -> bool:
    """Main gate: should we run expensive TradingAgents analysis?

    Returns False if already analyzed today.
    Returns True if no previous analysis exists.
    Otherwise returns True only if events detected since last analysis.
    """
    # Already ran today — skip
    if _has_decision_today(ticker):
        return False

    # Never analyzed — always run
    last_date = _get_last_analysis_date(ticker)
    if last_date is None:
        log.info("Events: %s has no prior analysis, will analyze", ticker)
        return True

    # Check for events since last analysis
    events = detect_events(ticker, since_date=last_date)

    if events:
        _store_events(ticker, events)
        types = [e["event_type"] for e in events]
        log.info("Events: %s triggered by %s", ticker, types)
        return True

    return False


def detect_events_batch(tickers: list[str]) -> dict[str, list[dict]]:
    """Run detection for multiple tickers. Returns {ticker: [events]}."""
    results: dict[str, list[dict]] = {}
    for ticker in tickers:
        events = detect_events(ticker)
        if events:
            results[ticker] = events
    return results


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _store_events(ticker: str, events: list[dict]) -> None:
    """Persist detected events to DuckDB."""
    conn = get_connection()
    today = date.today()

    rows = [
        (ticker, today, ev["event_type"], ev.get("details"))
        for ev in events
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO events (ticker, date, event_type, details)
           VALUES (?, ?, ?, ?)""",
        rows,
    )
    conn.close()
