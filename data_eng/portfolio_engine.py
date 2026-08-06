"""Phase 7: Deterministic portfolio engine.

Applies hard rules to TradingAgents decisions + screener scores to produce
actionable trade proposals. No AI involved — pure rules.

Rules:
- max 20% portfolio in one stock
- max 35% in one sector
- don't buy if screener score < 80 (top 20%)
- keep 10% cash reserve
- stop loss mandatory on all buys
"""

import csv
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from stock_bot.portfolio import extract_ticker

from .db import get_connection
from .utils import safe_float

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOTAL_CAPITAL = 28000.0  # USD — adjust to your portfolio size
MAX_POSITION_PCT = 0.20   # max 20% in one stock
MAX_SECTOR_PCT = 0.35     # max 35% in one sector
MIN_SCREENER_SCORE = 80.0 # must be top 20% to buy
CASH_RESERVE_PCT = 0.10   # always keep 10% cash

TRADES_CSV = Path(__file__).parent.parent / "data" / "trades.csv"


# ---------------------------------------------------------------------------
# Holdings (net buys minus sells from trades.csv)
# ---------------------------------------------------------------------------

def _load_net_holdings() -> dict[str, float]:
    """Return {ticker: shares_held} from trades.csv.

    Nets total buys minus total sells per ticker (lot order doesn't matter
    when only the remaining share count is needed).
    """
    if not TRADES_CSV.exists():
        return {}

    with TRADES_CSV.open("r", newline="") as f:
        rows = list(csv.DictReader(f))

    holdings: dict[str, float] = defaultdict(float)
    for row in rows:
        ticker = extract_ticker(row.get("stock", ""))
        if not ticker:
            continue
        ttype = row.get("transaction_type", "").lower().strip()
        try:
            shares = float(row.get("shares", 0))
        except (ValueError, TypeError):
            continue
        if shares <= 0:
            continue
        if ttype == "buy":
            holdings[ticker] += shares
        elif ttype == "sell":
            holdings[ticker] -= shares

    return {t: s for t, s in holdings.items() if s > 0.001}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_latest_decisions() -> dict[str, dict]:
    """Load latest TradingAgents decision per ticker."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ticker, action, rating, price_target, entry_price, stop_loss, summary
        FROM trading_agent_decisions
        WHERE (ticker, date) IN (
            SELECT ticker, MAX(date) FROM trading_agent_decisions GROUP BY ticker
        )
    """).fetchall()
    conn.close()

    decisions = {}
    for r in rows:
        decisions[r[0]] = {
            "action": (r[1] or "").strip().lower(),
            "rating": (r[2] or "").strip(),
            "price_target": r[3],
            "entry_price": r[4],
            "stop_loss": r[5],
            "summary": r[6],
        }
    return decisions


def _load_screener_scores() -> dict[str, float]:
    """Load latest overall_score per ticker from screener."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ticker, overall_score
        FROM screener_scores
        WHERE (ticker, date_scored) IN (
            SELECT ticker, MAX(date_scored) FROM screener_scores GROUP BY ticker
        )
    """).fetchall()
    conn.close()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


def _load_sectors() -> dict[str, str]:
    """Load sector per ticker from stock_universe (fallback: 'unknown')."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT ticker, sector FROM stock_universe").fetchall()
    except Exception:
        rows = []
    conn.close()
    return {r[0]: (r[1] or "unknown").lower() for r in rows}


def _load_current_prices(tickers: list[str]) -> dict[str, float]:
    """Get latest close price per ticker."""
    if not tickers:
        return {}
    conn = get_connection()
    placeholders = ", ".join(["?"] * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, close FROM daily_prices
            WHERE (ticker, date) IN (
                SELECT ticker, MAX(date) FROM daily_prices
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
            )""",
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_portfolio_engine(total_capital: float | None = None) -> list[dict]:
    """Generate portfolio decisions. Returns list of decision dicts.

    Each dict: {ticker, action, position_pct, shares, stop_loss, reason}
    """
    capital = total_capital or TOTAL_CAPITAL
    today = date.today()

    # Load inputs
    holdings = _load_net_holdings()
    decisions = _load_latest_decisions()
    scores = _load_screener_scores()
    sectors = _load_sectors()

    all_tickers = list(set(list(holdings.keys()) + list(decisions.keys())))
    prices = _load_current_prices(all_tickers)

    # Current portfolio state
    invested_value = sum(
        shares * prices.get(t, 0) for t, shares in holdings.items()
    )
    deployable = capital * (1 - CASH_RESERVE_PCT) - invested_value

    log.info("Portfolio engine: capital=$%.0f, invested=$%.0f, deployable=$%.0f",
             capital, invested_value, max(deployable, 0))

    # Sector exposure tracking (current)
    sector_value: dict[str, float] = defaultdict(float)
    for ticker, shares in holdings.items():
        sector = sectors.get(ticker, "unknown")
        sector_value[sector] += shares * prices.get(ticker, 0)

    results: list[dict] = []
    buy_queue: list[str] = []  # tickers approved for buy, sized later

    # --- Pass 1: classify each ticker ---
    for ticker in all_tickers:
        decision = decisions.get(ticker, {})
        action = decision.get("action", "")
        score = scores.get(ticker)
        price = prices.get(ticker)
        sector = sectors.get(ticker, "unknown")
        held_shares = holdings.get(ticker, 0)

        # SELL: TradingAgents says sell
        if action == "sell" and held_shares > 0:
            results.append({
                "ticker": ticker,
                "action": "SELL",
                "position_pct": 0.0,
                "shares": held_shares,
                "stop_loss": None,
                "reason": "TradingAgents SELL signal",
            })
            continue

        # BUY candidate: has a buy decision
        if action == "buy":
            # Rule: screener score must be top 20%
            if score is not None and score < MIN_SCREENER_SCORE:
                results.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "position_pct": (held_shares * price / capital * 100) if price else 0,
                    "shares": held_shares,
                    "stop_loss": None,
                    "reason": f"Screener score {score:.0f} < {MIN_SCREENER_SCORE:.0f}",
                })
                continue

            # Rule: don't exceed sector cap
            current_sector_val = sector_value.get(sector, 0)
            if current_sector_val / capital >= MAX_SECTOR_PCT:
                results.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "position_pct": (held_shares * price / capital * 100) if price else 0,
                    "shares": held_shares,
                    "stop_loss": None,
                    "reason": f"Sector '{sector}' at cap ({MAX_SECTOR_PCT:.0%})",
                })
                continue

            # Approved for buy
            buy_queue.append(ticker)
            continue

        # HOLD: everything else (no decision, or hold signal)
        if held_shares > 0:
            results.append({
                "ticker": ticker,
                "action": "HOLD",
                "position_pct": (held_shares * price / capital * 100) if price else 0,
                "shares": held_shares,
                "stop_loss": None,
                "reason": "No fresh buy/sell signal",
            })

    # --- Pass 2: size the buys ---
    if buy_queue and deployable > 0:
        # Equal weight among approved buys, capped at MAX_POSITION_PCT
        per_position = min(
            deployable / len(buy_queue),
            capital * MAX_POSITION_PCT,
        )

        for ticker in buy_queue:
            price = prices.get(ticker)
            decision = decisions.get(ticker, {})
            sector = sectors.get(ticker, "unknown")

            if not price or price <= 0:
                results.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "position_pct": 0,
                    "shares": 0,
                    "stop_loss": None,
                    "reason": "No price data",
                })
                continue

            # Check position cap (existing + new)
            existing_val = holdings.get(ticker, 0) * price
            max_new_val = capital * MAX_POSITION_PCT - existing_val
            position_val = min(per_position, max_new_val, deployable)

            if position_val < 50:  # skip dust amounts
                results.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "position_pct": (existing_val / capital * 100),
                    "shares": holdings.get(ticker, 0),
                    "stop_loss": None,
                    "reason": "Position already at/near cap",
                })
                continue

            # Check sector cap with new position
            if (sector_value.get(sector, 0) + position_val) / capital > MAX_SECTOR_PCT:
                position_val = max(0, capital * MAX_SECTOR_PCT - sector_value.get(sector, 0))
                if position_val < 50:
                    results.append({
                        "ticker": ticker,
                        "action": "HOLD",
                        "position_pct": (existing_val / capital * 100),
                        "shares": holdings.get(ticker, 0),
                        "stop_loss": None,
                        "reason": f"Sector '{sector}' cap reached after sizing",
                    })
                    continue

            shares = int(position_val / price)
            if shares <= 0:
                continue

            stop_loss = decision.get("stop_loss")
            # Mandatory stop loss: default to 8% below entry if not provided
            if not stop_loss:
                stop_loss = round(price * 0.92, 2)

            results.append({
                "ticker": ticker,
                "action": "BUY",
                "position_pct": round((existing_val + shares * price) / capital * 100, 1),
                "shares": shares,
                "stop_loss": stop_loss,
                "reason": f"Buy signal + score {scores.get(ticker, 0):.0f}",
            })

            # Update tracking
            sector_value[sector] += shares * price
            deployable -= shares * price

    # Store results
    _store_decisions(results, today)

    # Summary log
    buys = [r for r in results if r["action"] == "BUY"]
    sells = [r for r in results if r["action"] == "SELL"]
    holds = [r for r in results if r["action"] == "HOLD"]
    log.info("Portfolio engine: %d BUY, %d SELL, %d HOLD", len(buys), len(sells), len(holds))

    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _store_decisions(results: list[dict], decision_date: date) -> None:
    """Upsert portfolio decisions into DuckDB."""
    if not results:
        return

    conn = get_connection()
    for r in results:
        conn.execute(
            """INSERT INTO portfolio_decisions
               (ticker, date, action, position_pct, shares, stop_loss, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (ticker, date) DO UPDATE SET
                   action = EXCLUDED.action,
                   position_pct = EXCLUDED.position_pct,
                   shares = EXCLUDED.shares,
                   stop_loss = EXCLUDED.stop_loss,
                   reason = EXCLUDED.reason""",
            [
                r["ticker"],
                decision_date,
                r["action"],
                _safe_float(r.get("position_pct")),
                _safe_float(r.get("shares")),
                _safe_float(r.get("stop_loss")),
                r.get("reason", ""),
            ],
        )
    conn.close()
    log.info("Portfolio engine: stored %d decisions for %s", len(results), decision_date)


def _safe_float(val) -> float | None:
    """Portfolio values (shares, stops, positions) keep 4-decimal precision."""
    return safe_float(val, decimals=4)
