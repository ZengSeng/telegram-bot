"""Candidate selection: sector-balanced top-N from screener scores with correlation filtering."""

import logging
from datetime import date

import numpy as np
import pandas as pd

from .db import get_connection

log = logging.getLogger(__name__)

# Max candidates per sector (from notes.md allocation)
SECTOR_ALLOCATION: dict[str, int] = {
    "technology": 5,
    "healthcare": 3,
    "financial-services": 3,
    "consumer-cyclical": 2,
    "consumer-defensive": 1,
    "industrials": 2,
    "energy": 1,
    "communication-services": 1,
    "utilities": 0,
    "basic-materials": 0,
    "real-estate": 0,
    "unknown": 2,  # watchlist tickers not in universe
}

CORRELATION_THRESHOLD = 0.85
CORRELATION_LOOKBACK = 126  # ~6 months of trading days


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------


def _fetch_scored_with_sector() -> pd.DataFrame:
    """Join latest screener_scores with stock_universe to get sector per ticker.

    Tickers not in universe get sector='unknown'.
    """
    conn = get_connection()
    df = conn.execute(
        """
        SELECT s.ticker, s.overall_score, COALESCE(u.sector, 'unknown') AS sector
        FROM screener_scores s
        INNER JOIN (
            SELECT ticker, MAX(date_scored) AS max_date
            FROM screener_scores
            GROUP BY ticker
        ) latest ON s.ticker = latest.ticker AND s.date_scored = latest.max_date
        LEFT JOIN stock_universe u ON s.ticker = u.ticker
        WHERE s.overall_score IS NOT NULL
        ORDER BY s.overall_score DESC
        """
    ).fetchdf()
    conn.close()
    return df


def _compute_correlation_matrix(tickers: list[str]) -> pd.DataFrame:
    """Compute pairwise Pearson correlation of daily returns over lookback window."""
    if len(tickers) < 2:
        return pd.DataFrame()

    conn = get_connection()
    placeholders = ", ".join("?" for _ in tickers)
    df = conn.execute(
        f"""
        SELECT ticker, date, close
        FROM daily_prices
        WHERE ticker IN ({placeholders})
        ORDER BY ticker, date
        """,
        tickers,
    ).fetchdf()
    conn.close()

    if df.empty:
        return pd.DataFrame()

    # Pivot to wide format: index=date, columns=ticker, values=close
    pivot = df.pivot_table(index="date", columns="ticker", values="close")

    # Keep only tickers with enough data
    pivot = pivot.dropna(thresh=CORRELATION_LOOKBACK // 2, axis=1)

    # Take last N days
    pivot = pivot.tail(CORRELATION_LOOKBACK)

    # Daily returns
    returns = pivot.pct_change().dropna()

    if returns.empty or len(returns) < 20:
        return pd.DataFrame()

    return returns.corr()


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def _sector_top_n(scores_df: pd.DataFrame) -> pd.DataFrame:
    """Pick top-N tickers per sector based on SECTOR_ALLOCATION."""
    selected = []

    for sector, allocation in SECTOR_ALLOCATION.items():
        if allocation <= 0:
            continue
        sector_df = scores_df[scores_df["sector"] == sector].head(allocation)
        selected.append(sector_df)

    # Also handle any sectors in data not in allocation dict (default 1)
    known_sectors = set(SECTOR_ALLOCATION.keys())
    for sector in scores_df["sector"].unique():
        if sector not in known_sectors:
            sector_df = scores_df[scores_df["sector"] == sector].head(1)
            selected.append(sector_df)

    if not selected:
        return pd.DataFrame()

    return pd.concat(selected, ignore_index=True)


def _remove_correlated(
    candidates: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Remove highly correlated pairs. Returns (kept, removed).

    For each pair with corr > threshold, removes the lower-scored ticker.
    Greedy: processes highest-correlation pairs first.
    """
    if len(candidates) < 2:
        return candidates, pd.DataFrame()

    tickers = candidates["ticker"].tolist()
    corr_matrix = _compute_correlation_matrix(tickers)

    if corr_matrix.empty:
        return candidates, pd.DataFrame()

    # Build score lookup
    score_map = dict(zip(candidates["ticker"], candidates["overall_score"]))

    # Collect all pairs above threshold
    removed_tickers: set[str] = set()
    removal_reasons: dict[str, str] = {}

    # Get upper triangle pairs sorted by correlation (descending)
    pairs = []
    corr_tickers = [t for t in tickers if t in corr_matrix.columns]
    for i, t1 in enumerate(corr_tickers):
        for t2 in corr_tickers[i + 1:]:
            corr_val = corr_matrix.loc[t1, t2]
            if pd.notna(corr_val) and corr_val > CORRELATION_THRESHOLD:
                pairs.append((corr_val, t1, t2))

    # Sort by correlation descending (remove most correlated first)
    pairs.sort(reverse=True)

    for corr_val, t1, t2 in pairs:
        if t1 in removed_tickers or t2 in removed_tickers:
            continue
        # Remove the lower-scored ticker
        s1 = score_map.get(t1, 0)
        s2 = score_map.get(t2, 0)
        loser = t2 if s1 >= s2 else t1
        winner = t1 if loser == t2 else t2
        removed_tickers.add(loser)
        removal_reasons[loser] = f"correlation:{winner}({corr_val:.2f})"

    if not removed_tickers:
        return candidates, pd.DataFrame()

    kept = candidates[~candidates["ticker"].isin(removed_tickers)].copy()
    removed = candidates[candidates["ticker"].isin(removed_tickers)].copy()
    removed["removed_reason"] = removed["ticker"].map(removal_reasons)

    return kept, removed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def select_candidates() -> pd.DataFrame:
    """Full candidate selection pipeline.

    1. Join screener_scores with stock_universe (sector)
    2. Per-sector top-N selection
    3. Correlation filter
    4. Store results in DuckDB
    5. Return selected candidates
    """
    log.info("Candidates: loading scored tickers with sector...")
    scores_df = _fetch_scored_with_sector()

    if scores_df.empty:
        log.warning("Candidates: no scored tickers available.")
        return pd.DataFrame()

    log.info("Candidates: %d scored tickers available", len(scores_df))

    # Sector-balanced top-N
    pre_corr = _sector_top_n(scores_df)
    if pre_corr.empty:
        log.warning("Candidates: no tickers passed sector allocation.")
        return pd.DataFrame()

    log.info("Candidates: %d tickers after sector allocation", len(pre_corr))

    # Correlation filter
    kept, removed = _remove_correlated(pre_corr)

    if not removed.empty:
        log.info(
            "Candidates: removed %d correlated tickers: %s",
            len(removed),
            list(removed["ticker"]),
        )

    log.info("Candidates: final selection = %d tickers: %s", len(kept), list(kept["ticker"]))

    # Store results
    _store_candidates(kept, removed)

    return kept


def _store_candidates(kept: pd.DataFrame, removed: pd.DataFrame) -> None:
    """Upsert candidate selections into DuckDB."""
    conn = get_connection()
    today = date.today()

    rows = []
    for _, row in kept.iterrows():
        rows.append((
            row["ticker"],
            today,
            row["sector"],
            _safe_float(row["overall_score"]),
            None,  # selected
        ))

    if not removed.empty:
        for _, row in removed.iterrows():
            rows.append((
                row["ticker"],
                today,
                row["sector"],
                _safe_float(row["overall_score"]),
                row.get("removed_reason"),
            ))

    conn.executemany(
        """INSERT OR REPLACE INTO candidates
           (ticker, date_selected, sector, overall_score, removed_reason)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.close()
    log.info("Candidates: stored %d rows for %s", len(rows), today)


def _safe_float(val) -> float | None:
    """Convert to float, returning None for NaN/inf."""
    if val is None:
        return None
    f = float(val)
    if np.isnan(f) or np.isinf(f):
        return None
    return round(f, 2)


def get_analysis_tickers(watchlist: list[str]) -> list[str]:
    """Return deduplicated list of tickers to run TradingAgents on.

    Combines latest selected candidates (removed_reason IS NULL)
    with the watchlist. Falls back to watchlist-only if no candidates exist.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ticker FROM candidates
        WHERE removed_reason IS NULL
          AND date_selected = (SELECT MAX(date_selected) FROM candidates)
        """
    ).fetchall()
    conn.close()

    candidate_tickers = [r[0] for r in rows]

    # Union + deduplicate (preserve order: candidates first, then watchlist extras)
    seen: set[str] = set()
    result: list[str] = []
    for t in candidate_tickers + watchlist:
        if t not in seen:
            seen.add(t)
            result.append(t)

    if candidate_tickers:
        log.info("Analysis tickers: %d candidates + %d watchlist = %d total",
                 len(candidate_tickers), len(watchlist), len(result))
    else:
        log.info("Analysis tickers: no candidates, using watchlist only (%d)", len(result))

    return result
