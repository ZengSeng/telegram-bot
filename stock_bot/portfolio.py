"""Portfolio calculations: FIFO matching, price fetching, and summary builder."""

import datetime as dt
import io
import re
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from data_eng.db import get_connection

from .config import log
from .trades import load_watchlist, read_trades


# ---------------------------------------------------------------------------
# DuckDB price helpers
# ---------------------------------------------------------------------------

def get_usd_nzd_rate() -> float:
    """Get USD/NZD rate from DuckDB (NZDUSD=X close). Falls back to yfinance."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT close FROM daily_prices WHERE ticker = 'NZDUSD=X' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return 1.0 / float(row[0])  # NZDUSD is USD per 1 NZD; invert for NZD per USD
    except Exception as e:
        log.warning("DuckDB USD/NZD read failed: %s", e)

    # Fallback to yfinance if DB has no data
    try:
        import yfinance as yf
        ticker_obj = yf.Ticker("NZDUSD=X")
        rate = ticker_obj.fast_info.get("lastPrice") or ticker_obj.fast_info.get("last_price")
        if rate:
            return 1.0 / float(rate)
    except Exception as e:
        log.warning("Fallback USD/NZD fetch failed: %s", e)
    return 0.0


def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Get latest close price for each ticker from DuckDB. Returns {ticker: price}."""
    if not tickers:
        return {}
    prices = {}
    try:
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
        for row in rows:
            if row[1] is not None:
                prices[row[0]] = float(row[1])
    except Exception as e:
        log.warning("DuckDB price read failed: %s", e)
    return prices


def get_prices_n_days_ago(tickers: list[str], days: int = 30) -> dict[str, float]:
    """Get close price from ~N days ago for each ticker from DuckDB. Returns {ticker: price}."""
    if not tickers:
        return {}
    prices = {}
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    try:
        conn = get_connection()
        placeholders = ", ".join(["?"] * len(tickers))
        rows = conn.execute(
            f"""SELECT ticker, close FROM daily_prices
                WHERE (ticker, date) IN (
                    SELECT ticker, MAX(date) FROM daily_prices
                    WHERE ticker IN ({placeholders}) AND date <= ?
                    GROUP BY ticker
                )""",
            [*tickers, cutoff],
        ).fetchall()
        conn.close()
        for row in rows:
            if row[1] is not None:
                prices[row[0]] = float(row[1])
    except Exception as e:
        log.warning("DuckDB %d-day price read failed: %s", days, e)
    return prices


def get_price_targets(tickers: list[str]) -> dict[str, float]:
    """Get latest Consensus target_price from analyst_targets. Returns {ticker: target}."""
    if not tickers:
        return {}
    targets = {}
    try:
        conn = get_connection()
        placeholders = ", ".join(["?"] * len(tickers))
        rows = conn.execute(
            f"""SELECT ticker, target_price FROM analyst_targets
                WHERE analyst = 'Consensus'
                  AND (ticker, date_fetched) IN (
                      SELECT ticker, MAX(date_fetched) FROM analyst_targets
                      WHERE analyst = 'Consensus' AND ticker IN ({placeholders})
                      GROUP BY ticker
                  )""",
            tickers,
        ).fetchall()
        conn.close()
        for row in rows:
            if row[1] is not None:
                targets[row[0]] = float(row[1])
    except Exception as e:
        log.warning("DuckDB target read failed: %s", e)
    return targets


# ---------------------------------------------------------------------------
# Ticker extraction
# ---------------------------------------------------------------------------

def extract_ticker(stock_field: str) -> str:
    """Extract ticker from 'Rocket Lab Corp (RKLB | NASDAQ)' or 'Micron Technology Inc | MU | NASDAQ'."""
    match = re.search(r"\(([A-Z]+)", stock_field.upper())
    if match:
        return match.group(1)
    match = re.search(r"\|\s*([A-Z]{1,5})\s*\|", stock_field.upper())
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Z]{1,5})\b", stock_field.upper())
    if match:
        return match.group(1)
    return stock_field.strip().upper()


# ---------------------------------------------------------------------------
# Portfolio computation (FIFO)
# ---------------------------------------------------------------------------

def _build_lots(buys: list[dict]) -> list[dict]:
    """Convert buy trades (already sorted by order_placed) into FIFO lots."""
    lots = []
    for b in buys:
        try:
            shares = float(b.get("shares", 0))
            price = float(b.get("price_per_share_usd", 0))
            fee = float(b.get("transaction_fee_usd", 0))
        except (ValueError, TypeError):
            continue
        if shares > 0:
            lots.append({
                "shares_remaining": shares,
                "price": price,
                "fee_per_share": fee / shares,
                "date": b.get("order_placed", "")[:10],
            })
    return lots


def _match_fifo(lots: list[dict], sells: list[dict]) -> list[dict]:
    """Match sells (sorted by order_placed) against lots, oldest first.

    Mutates the lots' shares_remaining in place and returns one record per
    partial/full lot match: {shares, buy_price, sell_price, buy_date, gain}.
    Fees are amortized per share on both sides.
    """
    matched = []
    for s in sells:
        try:
            sell_shares = float(s.get("shares", 0))
            sell_price = float(s.get("price_per_share_usd", 0))
            sell_fee = float(s.get("transaction_fee_usd", 0))
        except (ValueError, TypeError):
            continue

        fee_per_sell_share = sell_fee / sell_shares if sell_shares > 0 else 0
        remaining_to_sell = sell_shares

        for lot in lots:
            if remaining_to_sell <= 0:
                break
            m = min(lot["shares_remaining"], remaining_to_sell)
            if m > 0:
                buy_cost = lot["price"] + lot["fee_per_share"]
                sell_net = sell_price - fee_per_sell_share
                matched.append({
                    "shares": m,
                    "buy_price": lot["price"],
                    "sell_price": sell_price,
                    "buy_date": lot["date"],
                    "gain": m * (sell_net - buy_cost),
                })
                lot["shares_remaining"] -= m
                remaining_to_sell -= m
    return matched


def _sorted_trades(trades: list[dict], ticker: str | None = None):
    """Split trades into (buys, sells) for one ticker (or all), sorted by date."""
    buys, sells = [], []
    for trade in trades:
        t = extract_ticker(trade.get("stock", ""))
        if not t or (ticker is not None and t != ticker):
            continue
        ttype = trade.get("transaction_type", "").lower().strip()
        if ttype == "buy":
            buys.append((t, trade))
        elif ttype == "sell":
            sells.append((t, trade))
    key = lambda item: item[1].get("order_placed", "")
    buys.sort(key=key)
    sells.sort(key=key)
    return buys, sells


def compute_portfolio(trades: list[dict]) -> dict[str, dict]:
    """
    Compute per-stock portfolio stats using FIFO for cost basis.
    Returns {ticker: {shares, avg_cost, total_invested, realized_gain}}
    """
    buys, sells = _sorted_trades(trades)

    buys_by_ticker: dict[str, list[dict]] = defaultdict(list)
    sells_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t, trade in buys:
        buys_by_ticker[t].append(trade)
    for t, trade in sells:
        sells_by_ticker[t].append(trade)

    portfolio = {}
    for ticker in set(buys_by_ticker) | set(sells_by_ticker):
        lots = _build_lots(buys_by_ticker.get(ticker, []))
        matched = _match_fifo(lots, sells_by_ticker.get(ticker, []))
        realized_gain = sum(m["gain"] for m in matched)

        open_shares = sum(lot["shares_remaining"] for lot in lots)
        if open_shares > 0:
            total_cost = sum(
                lot["shares_remaining"] * (lot["price"] + lot["fee_per_share"])
                for lot in lots
            )
            avg_cost = total_cost / open_shares
        else:
            avg_cost = 0.0

        total_invested = 0.0
        for b in buys_by_ticker.get(ticker, []):
            try:
                total_invested += float(b.get("amount_usd", 0))
            except (ValueError, TypeError):
                pass

        portfolio[ticker] = {
            "shares": open_shares,
            "avg_cost": avg_cost,
            "total_invested": total_invested,
            "realized_gain": realized_gain,
        }

    return portfolio


def compute_fifo_details(trades: list[dict], ticker: str) -> dict:
    """
    Compute detailed FIFO matching for a specific ticker.
    Returns {matched: [...], open_lots: [...], realized_total: float}
    """
    buys, sells = _sorted_trades(trades, ticker)
    lots = _build_lots([trade for _, trade in buys])
    matched = _match_fifo(lots, [trade for _, trade in sells])

    open_lots = [
        {"shares": lot["shares_remaining"], "price": lot["price"], "date": lot["date"]}
        for lot in lots if lot["shares_remaining"] > 0
    ]

    realized_total = sum(m["gain"] for m in matched)
    return {"matched": matched, "open_lots": open_lots, "realized_total": realized_total}


# ---------------------------------------------------------------------------
# Summary message builder
# ---------------------------------------------------------------------------

def build_portfolio_summary() -> str:
    """Build the portfolio summary message (HTML parse mode)."""
    trades = read_trades()
    watchlist = load_watchlist()
    prices = get_current_prices(watchlist)
    targets = get_price_targets(watchlist)
    portfolio = compute_portfolio(trades) if trades else {}
    usd_nzd = get_usd_nzd_rate()

    today = dt.date.today().strftime("%Y-%m-%d")
    lines = [f"📊 Portfolio Summary — {today}", ""]

    # --- Watchlist-only tickers (monitored but no open position) ---
    portfolio_tickers = {t for t, s in portfolio.items() if s["shares"] > 0}
    watched_only = [t for t in sorted(watchlist) if t not in portfolio_tickers]

    if watched_only:
        prices_30d = get_prices_n_days_ago(watched_only, days=30)
        lines.append("👁 <b>Watchlist</b>")
        for ticker in watched_only:
            current_price = prices.get(ticker)
            price_str = f"${current_price:,.2f}" if current_price else "N/A"
            target = targets.get(ticker)
            target_str = f" | Target: ${target:,.0f}" if target else ""
            price_30d = prices_30d.get(ticker)
            if price_30d and current_price:
                chg_30d = (current_price - price_30d) / price_30d * 100
                chg_str = f" | 30D: {'+' if chg_30d >= 0 else ''}{chg_30d:.0f}%"
            else:
                chg_str = ""
            lines.append(f"<b>{ticker}</b>: {price_str}{target_str}{chg_str}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    # --- Portfolio holdings ---
    total_invested = 0.0
    total_market_value = 0.0

    for ticker in sorted(portfolio.keys()):
        stats = portfolio[ticker]
        shares = stats["shares"]
        avg_cost = stats["avg_cost"]
        current_price = prices.get(ticker)

        if shares <= 0:
            continue

        total_invested += shares * avg_cost
        market_value = shares * current_price if current_price else 0.0
        total_market_value += market_value

        change_pct = ((current_price - avg_cost) / avg_cost * 100) if (current_price and avg_cost > 0) else 0.0
        indicator = "🟢" if change_pct >= 0 else "🔴"
        change_str = f"+{change_pct:.2f}%" if change_pct >= 0 else f"{change_pct:.2f}%"
        price_str = f"${current_price:,.2f}" if current_price else "N/A"
        avg_str = f"${avg_cost:,.2f}"
        mv_str = f"${market_value:,.2f}"

        lines.append(f"<b>{ticker}'s</b> Price:   {price_str} ({change_str}) {indicator}")
        target = targets.get(ticker)
        target_str = f" | Target: ${target:,.2f}" if target else ""
        lines.append(f"  Avg Cost:         {avg_str}{target_str}")
        lines.append(f"  Market Val:      {mv_str} ({shares:.0f} shares)")
        lines.append("")

    unrealized = total_market_value - total_invested
    unrealized_pct = (unrealized / total_invested * 100) if total_invested > 0 else 0.0
    sign = "+" if unrealized >= 0 else "-"
    total_indicator = "🟢" if unrealized >= 0 else "🔴"

    inv_str = f"${total_invested:,.0f}"
    val_str = f"${total_market_value:,.0f}"
    pnl_str = f"{sign}${abs(unrealized):,.0f}"

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total Invested:   {inv_str} (NZD {total_invested * usd_nzd:,.0f})")
    lines.append(f"Market Value:     {val_str} (NZD {total_market_value * usd_nzd:,.0f})")
    lines.append(f"Unrealized P&L:   {pnl_str} ({sign}{abs(unrealized_pct):.2f}%) {total_indicator}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------

def get_chart_tickers() -> list[str]:
    """Return ordered tickers to chart: watchlist-only first, then portfolio."""
    trades = read_trades()
    watchlist = load_watchlist()
    portfolio = compute_portfolio(trades) if trades else {}
    portfolio_tickers = {t for t, s in portfolio.items() if s["shares"] > 0}
    watched_only = [t for t in sorted(watchlist) if t not in portfolio_tickers]
    held = [t for t in sorted(portfolio_tickers) if t in watchlist]
    # Also include portfolio tickers not in watchlist
    held += [t for t in sorted(portfolio_tickers) if t not in watchlist]
    return watched_only + held


def generate_price_chart(ticker: str) -> io.BytesIO | None:
    """Generate a 90-day price chart from DuckDB. Returns PNG as BytesIO or None."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT date, close FROM daily_prices
               WHERE ticker = ? AND date >= CURRENT_DATE - INTERVAL 90 DAY
               ORDER BY date""",
            [ticker],
        ).fetchall()
        conn.close()

        if not rows:
            log.warning("No price history in DB for %s", ticker)
            return None

        dates = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        last_close = closes[-1]

        fig, ax = plt.subplots(figsize=(7, 3))
        ax.plot(dates, closes, linewidth=1.2, color="#1a73e8")
        ax.fill_between(dates, closes, alpha=0.08, color="#1a73e8")

        # Red horizontal line at current price
        ax.axhline(y=last_close, color="red", linewidth=0.8, linestyle="--")
        ax.annotate(
            f"${last_close:.2f}",
            xy=(dates[-1], last_close),
            fontsize=7,
            color="red",
            va="bottom",
            ha="right",
        )

        ax.set_title(f"{ticker} — 90D", fontsize=12, loc="left", weight="bold")
        ax.set_ylabel("USD", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.set_xticklabels(ax.get_xticklabels(), weight='bold')
        ax.set_yticklabels(ax.get_yticklabels(), weight='bold')
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        fig.autofmt_xdate(rotation=0, ha="center")
        ax.grid(True, alpha=0.3)
        fig.tight_layout(pad=0.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        log.warning("Chart generation failed for %s: %s", ticker, e)
        return None
