"""Canonical watchlist file I/O — single source of truth.

The watchlist lives at data/watchlist.json. Both stock_bot and data_eng read
through these helpers so there is exactly one parsing/uppercasing code path.
"""

import json
from pathlib import Path

WATCHLIST_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"


def load_watchlist() -> list[str] | None:
    """Return the uppercased watchlist, or None if the file is missing/corrupt.

    Callers choose their own fallback for the None case (e.g. [] in the data
    pipeline, ['RKLB'] in the bot), so an intentionally-empty watchlist is
    preserved as [] rather than being replaced.
    """
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text())
            if isinstance(data, list):
                return [str(t).upper() for t in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return None


def save_watchlist(tickers: list[str]) -> None:
    """Persist the watchlist to disk (uppercased)."""
    WATCHLIST_FILE.write_text(json.dumps([str(t).upper() for t in tickers]))
