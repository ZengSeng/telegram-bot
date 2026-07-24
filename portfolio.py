"""Portfolio calculations: FIFO matching, price fetching, and summary builder."""

import datetime as dt
import re
from collections import defaultdict

import yfinance as yf

from config import log
from trades import load_watchlist, read_trades


# ---------------------------------------------------------------------------
# yfinance helpers
# ---------------------------------------------------------------------------

def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch the latest price for each ticker. Returns {ticker: price}."""
    prices = {}
    for t in tickers:
        try:
            ticker_obj = yf.Ticker(t)
            price = ticker_obj.fast_info.get("lastPrice") or ticker_obj.fast_info.get("last_price")
            if price:
                prices[t] = float(price)
        except Exception as e:
            log.warning("Failed to fetch price for %s: %s", t, e)
    return prices


def get_ticker_name(ticker: str) -> str:
    """Get the short name for a ticker."""
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortName", ticker)
    except Exception:
        return ticker


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

def compute_portfolio(trades: list[dict]) -> dict[str, dict]:
    """
    Compute per-stock portfolio stats using FIFO for cost basis.
    Returns {ticker: {shares, avg_cost, total_invested, realized_gain}}
    """
    buys_by_ticker: dict[str, list[dict]] = defaultdict(list)
    sells_by_ticker: dict[str, list[dict]] = defaultdict(list)

    for trade in trades:
        ticker = extract_ticker(trade.get("stock", ""))
        if not ticker:
            continue
        ttype = trade.get("transaction_type", "").lower().strip()
        if ttype == "buy":
            buys_by_ticker[ticker].append(trade)
        elif ttype == "sell":
            sells_by_ticker[ticker].append(trade)

    for ticker in buys_by_ticker:
        buys_by_ticker[ticker].sort(key=lambda t: t.get("order_placed", ""))
    for ticker in sells_by_ticker:
        sells_by_ticker[ticker].sort(key=lambda t: t.get("order_placed", ""))

    all_tickers = set(list(buys_by_ticker.keys()) + list(sells_by_ticker.keys()))
    portfolio = {}

    for ticker in all_tickers:
        buys = buys_by_ticker.get(ticker, [])
        sells = sells_by_ticker.get(ticker, [])

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
                    "fee_per_share": fee / shares if shares > 0 else 0,
                })

        realized_gain = 0.0
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
                matched = min(lot["shares_remaining"], remaining_to_sell)
                if matched > 0:
                    buy_cost_per_share = lot["price"] + lot["fee_per_share"]
                    sell_net_per_share = sell_price - fee_per_sell_share
                    realized_gain += matched * (sell_net_per_share - buy_cost_per_share)
                    lot["shares_remaining"] -= matched
                    remaining_to_sell -= matched

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
        for b in buys:
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
    buys = []
    sells = []
    for trade in trades:
        t = extract_ticker(trade.get("stock", ""))
        if t != ticker:
            continue
        ttype = trade.get("transaction_type", "").lower().strip()
        if ttype == "buy":
            buys.append(trade)
        elif ttype == "sell":
            sells.append(trade)

    buys.sort(key=lambda t: t.get("order_placed", ""))
    sells.sort(key=lambda t: t.get("order_placed", ""))

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
                "fee_per_share": fee / shares if shares > 0 else 0,
                "date": b.get("order_placed", "")[:10],
            })

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
                gain = m * (sell_net - buy_cost)
                matched.append({
                    "shares": m,
                    "buy_price": lot["price"],
                    "sell_price": sell_price,
                    "buy_date": lot["date"],
                    "gain": gain,
                })
                lot["shares_remaining"] -= m
                remaining_to_sell -= m

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
    """Build the portfolio summary message."""
    trades = read_trades()
    if not trades:
        return "No trades recorded yet. Send a photo of a trade confirmation to get started!"

    watchlist = load_watchlist()
    prices = get_current_prices(watchlist)
    portfolio = compute_portfolio(trades)

    today = dt.date.today().strftime("%Y-%m-%d")
    lines = [f"📊 Portfolio Summary — {today}", ""]

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

        lines.append(f"{ticker}")
        lines.append(f"  Daily Price:    {price_str:>12}")
        lines.append(f"  Avg Cost:       ${avg_cost:>11,.2f}")
        lines.append(f"  Shares:         {shares:>12.0f}")
        lines.append(f"  Market Value:   ${market_value:>11,.2f}")
        lines.append(f"  Change:         {change_str:>12} {indicator}")
        lines.append("")

    unrealized = total_market_value - total_invested
    unrealized_pct = (unrealized / total_invested * 100) if total_invested > 0 else 0.0
    sign = "+" if unrealized >= 0 else ""
    total_indicator = "🟢" if unrealized >= 0 else "🔴"

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total Invested:   ${total_invested:>11,.2f}")
    lines.append(f"Market Value:     ${total_market_value:>11,.2f}")
    lines.append(f"Unrealized P&L:   {sign}${unrealized:>11,.2f} ({sign}{unrealized_pct:.2f}%) {total_indicator}")

    return "\n".join(lines)
