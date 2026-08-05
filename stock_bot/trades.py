"""Trade storage (CSV), duplicate detection, and watchlist persistence."""

import csv

from data_eng.watchlist import load_watchlist as _read_watchlist, save_watchlist

from .config import TRADES_CSV, TRADES_CSV_COLUMNS

__all__ = [
    "read_trades",
    "append_trade",
    "is_duplicate",
    "load_watchlist",
    "save_watchlist",
]


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
    """Load the watchlist, defaulting to ['RKLB'] if the file is absent/corrupt.

    Parsing/uppercasing lives in the canonical data_eng.watchlist loader; this
    wrapper only adds the bot-friendly fallback so there is a single code path.
    """
    watchlist = _read_watchlist()
    return ["RKLB"] if watchlist is None else watchlist


# save_watchlist is re-exported from data_eng.watchlist above.

