"""Quantitative screener: percentile-rank scoring across Quality, Value, Momentum, Sentiment, Risk."""

import logging
from datetime import date

import numpy as np
import pandas as pd

from .db import get_connection
from .utils import safe_float as _safe_float

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category definitions: metric column -> whether lower is better (inverted)
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, dict[str, bool]] = {
    "quality": {
        "roe": False,
        "roa": False,
        "gross_margin": False,
        "operating_margin": False,
        "earnings_trend_0y": False,
        "earnings_trend_1y": False,
    },
    "value": {
        "forward_pe": True,   # lower PE = cheaper = better
        "peg_ratio": True,    # lower PEG = better
        "fcf_yield": False,   # higher yield = better
    },
    "momentum": {
        "return_3m": False,
        "return_6m": False,
        "rsi_14": False,
        "above_200ma": False,
    },
    "sentiment": {
        "bullish_ratio": False,
        "target_upside": False,
    },
    "risk": {
        "debt_to_equity": True,   # lower debt = better
        "beta": True,             # lower beta = less risky = better
        "volatility_63d": True,   # lower vol = better
    },
}

# Flatten for quick lookup
_INVERTED_METRICS: set[str] = {
    metric
    for metrics in CATEGORIES.values()
    for metric, inverted in metrics.items()
    if inverted
}


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------


def _get_tickers_with_data() -> list[str]:
    """Return all tickers that have at least one fundamentals row."""
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT ticker FROM fundamentals").fetchall()
    conn.close()
    return [r[0] for r in rows]


def _fetch_latest_fundamentals(tickers: list[str]) -> pd.DataFrame:
    """Get the most recent fundamentals row per ticker."""
    conn = get_connection()
    placeholders = ", ".join("?" for _ in tickers)
    df = conn.execute(
        f"""
        SELECT f.*
        FROM fundamentals f
        INNER JOIN (
            SELECT ticker, MAX(date_fetched) AS max_date
            FROM fundamentals
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON f.ticker = latest.ticker AND f.date_fetched = latest.max_date
        """,
        tickers,
    ).fetchdf()
    conn.close()
    return df.set_index("ticker") if not df.empty else pd.DataFrame()


def _fetch_latest_enriched(tickers: list[str]) -> pd.DataFrame:
    """Get the most recent ticker_enriched row per ticker."""
    conn = get_connection()
    placeholders = ", ".join("?" for _ in tickers)
    df = conn.execute(
        f"""
        SELECT e.*
        FROM ticker_enriched e
        INNER JOIN (
            SELECT ticker, MAX(date_fetched) AS max_date
            FROM ticker_enriched
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON e.ticker = latest.ticker AND e.date_fetched = latest.max_date
        """,
        tickers,
    ).fetchdf()
    conn.close()
    return df.set_index("ticker") if not df.empty else pd.DataFrame()


def _fetch_latest_technicals(tickers: list[str]) -> pd.DataFrame:
    """Get the most recent technicals row per ticker."""
    conn = get_connection()
    placeholders = ", ".join("?" for _ in tickers)
    df = conn.execute(
        f"""
        SELECT t.*
        FROM technicals t
        INNER JOIN (
            SELECT ticker, MAX(date_fetched) AS max_date
            FROM technicals
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) latest ON t.ticker = latest.ticker AND t.date_fetched = latest.max_date
        """,
        tickers,
    ).fetchdf()
    conn.close()
    return df.set_index("ticker") if not df.empty else pd.DataFrame()


def _fetch_price_metrics(tickers: list[str]) -> pd.DataFrame:
    """Compute 3M/6M returns, volatility, and above-200MA from daily_prices."""
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

    records = []
    for ticker, grp in df.groupby("ticker"):
        grp = grp.sort_values("date")
        # dropna: close is nullable — a NULL inside the window would carry
        # pd.NA through the math and crash float() (screener crash 2026-08-06)
        closes = grp["close"].dropna().to_numpy(dtype=float)

        n = len(closes)
        if n < 2:
            continue

        # Returns
        ret_3m = (closes[-1] / closes[-64] - 1) if n >= 64 else np.nan
        ret_6m = (closes[-1] / closes[-127] - 1) if n >= 127 else np.nan

        # Volatility: annualized std of daily returns over last 63 days
        window = closes[-64:] if n >= 64 else closes
        daily_returns = np.diff(window) / window[:-1]
        volatility = float(np.std(daily_returns) * np.sqrt(252)) if len(daily_returns) > 1 else np.nan

        records.append({
            "ticker": ticker,
            "return_3m": ret_3m,
            "return_6m": ret_6m,
            "volatility_63d": volatility,
        })

    return pd.DataFrame(records).set_index("ticker") if records else pd.DataFrame()


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_raw_metrics(tickers: list[str]) -> pd.DataFrame:
    """Pull latest data per ticker and assemble raw metric columns.

    Returns DataFrame indexed by ticker with one column per metric.
    """
    fund = _fetch_latest_fundamentals(tickers)
    enr = _fetch_latest_enriched(tickers)
    tech = _fetch_latest_technicals(tickers)
    price = _fetch_price_metrics(tickers)

    if fund.empty:
        log.warning("Screener: no fundamentals data available.")
        return pd.DataFrame()

    metrics = pd.DataFrame(index=fund.index)

    # --- Quality ---
    metrics["roe"] = fund.get("roe")
    metrics["roa"] = fund.get("roa")
    # Gross margin = gross_profit / revenue_ttm
    if "gross_profit" in fund.columns and "revenue_ttm" in fund.columns:
        metrics["gross_margin"] = fund["gross_profit"] / fund["revenue_ttm"].replace(0, np.nan)
    else:
        metrics["gross_margin"] = np.nan
    metrics["operating_margin"] = fund.get("operating_margin")

    # Earnings trend proxies from enriched
    if not enr.empty:
        metrics["earnings_trend_0y"] = enr.reindex(metrics.index).get("stock_trend_0y")
        metrics["earnings_trend_1y"] = enr.reindex(metrics.index).get("stock_trend_1y")
    else:
        metrics["earnings_trend_0y"] = np.nan
        metrics["earnings_trend_1y"] = np.nan

    # --- Value ---
    metrics["forward_pe"] = fund.get("forward_pe")
    metrics["peg_ratio"] = fund.get("peg_ratio")
    # FCF yield = free_cash_flow / market_cap
    if "free_cash_flow" in fund.columns and "market_cap" in fund.columns:
        metrics["fcf_yield"] = fund["free_cash_flow"] / fund["market_cap"].replace(0, np.nan)
    else:
        metrics["fcf_yield"] = np.nan

    # --- Momentum ---
    if not price.empty:
        price_aligned = price.reindex(metrics.index)
        metrics["return_3m"] = price_aligned.get("return_3m")
        metrics["return_6m"] = price_aligned.get("return_6m")
        metrics["volatility_63d"] = price_aligned.get("volatility_63d")
    else:
        metrics["return_3m"] = np.nan
        metrics["return_6m"] = np.nan
        metrics["volatility_63d"] = np.nan

    if not tech.empty:
        metrics["rsi_14"] = tech.reindex(metrics.index).get("rsi_14")
    else:
        metrics["rsi_14"] = np.nan

    # Above 200 MA: binary (latest close > day200_avg)
    if "day200_avg" in fund.columns and not price.empty:
        # Get latest close from price data
        conn = get_connection()
        placeholders = ", ".join("?" for _ in metrics.index.tolist())
        closes_df = conn.execute(
            f"""
            SELECT ticker, close FROM daily_prices
            WHERE (ticker, date) IN (
                SELECT ticker, MAX(date) FROM daily_prices
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
            )
            """,
            metrics.index.tolist(),
        ).fetchdf()
        conn.close()
        if not closes_df.empty:
            close_map = closes_df.set_index("ticker")["close"]
            metrics["above_200ma"] = (
                close_map.reindex(metrics.index) > fund["day200_avg"]
            ).astype(float)
        else:
            metrics["above_200ma"] = np.nan
    else:
        metrics["above_200ma"] = np.nan

    # --- Sentiment ---
    if not enr.empty:
        enr_aligned = enr.reindex(metrics.index)
        # Bullish ratio = (strong_buy + buy) / total recommendations
        rec_cols = ["rec_strong_buy", "rec_buy", "rec_hold", "rec_sell", "rec_strong_sell"]
        if all(c in enr_aligned.columns for c in rec_cols):
            total_recs = enr_aligned[rec_cols].sum(axis=1)
            bullish = enr_aligned["rec_strong_buy"].fillna(0) + enr_aligned["rec_buy"].fillna(0)
            metrics["bullish_ratio"] = bullish / total_recs.replace(0, np.nan)
        else:
            metrics["bullish_ratio"] = np.nan
        metrics["target_upside"] = enr_aligned.get("target_over_mean")
    else:
        metrics["bullish_ratio"] = np.nan
        metrics["target_upside"] = np.nan

    # --- Risk ---
    metrics["debt_to_equity"] = fund.get("debt_to_equity")
    metrics["beta"] = fund.get("beta")
    # volatility_63d already computed above

    # Coerce all to numeric
    for col in metrics.columns:
        metrics[col] = pd.to_numeric(metrics[col], errors="coerce")

    return metrics


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def percentile_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Rank each metric 0-100 across all tickers.

    Inverts 'lower is better' metrics so that higher rank = better.
    NaN values remain NaN (excluded from ranking).
    """
    ranked = pd.DataFrame(index=df.index)
    for col in df.columns:
        series = df[col]
        if col in _INVERTED_METRICS:
            # Invert: ascending=False means lowest value gets highest rank
            ranked[col] = series.rank(pct=True, ascending=False) * 100
        else:
            ranked[col] = series.rank(pct=True, ascending=True) * 100
    return ranked


def compute_category_scores(ranks: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight average of metric ranks within each category.

    Returns DataFrame with columns: quality, value, momentum, sentiment, risk.
    """
    scores = pd.DataFrame(index=ranks.index)
    for category, metrics_map in CATEGORIES.items():
        cols = [m for m in metrics_map if m in ranks.columns]
        if cols:
            scores[category] = ranks[cols].mean(axis=1)
        else:
            scores[category] = np.nan
    return scores


def compute_overall_score(categories: pd.DataFrame) -> pd.Series:
    """Equal-weight average of 5 category scores (0-100)."""
    return categories.mean(axis=1)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_screener(tickers: list[str] | None = None) -> pd.DataFrame:
    """Full screener pipeline: metrics -> ranks -> category scores -> store.

    Args:
        tickers: Tickers to screen. If None, screens all tickers with fundamentals data.

    Returns:
        DataFrame with category scores + overall_score, indexed by ticker.
    """
    if tickers is None:
        tickers = _get_tickers_with_data()

    if not tickers:
        log.warning("Screener: no tickers with data to screen.")
        return pd.DataFrame()

    log.info("Screener: computing metrics for %d tickers...", len(tickers))

    # 1. Raw metrics
    raw = compute_raw_metrics(tickers)
    if raw.empty:
        log.warning("Screener: no metrics computed.")
        return pd.DataFrame()

    # 2. Percentile ranks
    ranks = percentile_rank(raw)

    # 3. Category scores
    categories = compute_category_scores(ranks)

    # 4. Overall score
    categories["overall_score"] = compute_overall_score(
        categories[["quality", "value", "momentum", "sentiment", "risk"]]
    )

    # Rename for storage
    result = categories.rename(columns={
        "quality": "quality_score",
        "value": "value_score",
        "momentum": "momentum_score",
        "sentiment": "sentiment_score",
        "risk": "risk_score",
    })

    # 5. Store in DuckDB
    _store_scores(result)

    # Log top 5
    top5 = result.sort_values("overall_score", ascending=False).head(5)
    log.info("Screener: top 5 tickers:\n%s", top5[["overall_score"]].to_string())

    return result


def _store_scores(scores: pd.DataFrame) -> None:
    """Upsert screener scores into DuckDB."""
    conn = get_connection()
    today = date.today()

    rows = []
    for ticker, row in scores.iterrows():
        rows.append((
            ticker,
            today,
            _safe_float(row.get("quality_score")),
            _safe_float(row.get("value_score")),
            _safe_float(row.get("momentum_score")),
            _safe_float(row.get("sentiment_score")),
            _safe_float(row.get("risk_score")),
            _safe_float(row.get("overall_score")),
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO screener_scores
           (ticker, date_scored, quality_score, value_score,
            momentum_score, sentiment_score, risk_score, overall_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.close()
    log.info("Screener: stored %d scores for %s", len(rows), today)

