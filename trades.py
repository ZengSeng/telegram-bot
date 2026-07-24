"""Trade storage (CSV), duplicate detection, and watchlist persistence."""

import csv
import json

from config import TRADES_CSV, TRADES_CSV_COLUMNS, WATCHLIST_FILE


# ---------------------------------------------------------------------------
# CSV operations
# ---------------------------------------------------------------------------

def read_trades() -> list[dict]:
    """Read all trades from the CSV file."""
    if not TRADES_CSV.exists():
        return []
    with TRADES_CSV.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def append_trade(trade: dict) -> None:
    """Append a single trade row to the CSV file."""
    file_exists = TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({col: trade.get(col, "") for col in TRADES_CSV_COLUMNS})


def is_duplicate(trade: dict) -> bool:
    """Check if a trade is a duplicate based on stock, date, and amount."""
    existing = read_trades()
    new_stock = trade.get("stock", "").upper()
    new_date = trade.get("order_placed", "")[:10]
    try:
        new_amount = float(trade.get("amount_usd", 0))
    except (ValueError, TypeError):
        new_amount = 0.0

    for row in existing:
        row_stock = row.get("stock", "").upper()
        row_date = row.get("order_placed", "")[:10]
        try:
            row_amount = float(row.get("amount_usd", 0))
        except (ValueError, TypeError):
            row_amount = 0.0

        if row_stock == new_stock and row_date == new_date and abs(row_amount - new_amount) < 0.01:
            return True
    return False


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def load_watchlist() -> list[str]:
    """Load the watchlist from disk, defaulting to ['RKLB']."""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text())
            if isinstance(data, list):
                return [t.upper() for t in data]
        except (json.JSONDecodeError, TypeError):
            pass
    return ["RKLB"]


def save_watchlist(tickers: list[str]) -> None:
    """Persist the watchlist to disk."""
    WATCHLIST_FILE.write_text(json.dumps([t.upper() for t in tickers]))
