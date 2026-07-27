"""
Ticker enrichment service: Enriches priority tickers with yfinance API data.

This module handles enrichment of ticker data using yfinance.Ticker API:
- Growth estimates (stockTrend_*, indexTrend_LTG, stockOverIndex_*)
- Analyst price targets (low, mean, median, current, high, overMean, overMedian)
- Analyst recommendations (strongBuy, buy, hold, sell, strongSell)

Usage:
    enricher = TickerEnricher()
    enriched_dict = enricher.enrich_ticker(symbol)
    base_row.update(enriched_dict)
"""
import yfinance as yf
import pandas as pd
import numpy as np
import time
import random

from typing import Dict, Any, Optional


class TickerEnricher:
    """Enriches ticker data with yfinance growth estimates, price targets, and recommendations."""

    @staticmethod
    def safe_loc(df: Optional[pd.DataFrame], idx, col) -> float:
        """Safely locate a value in a DataFrame."""
        try:
            if df is None or df.empty:
                return np.nan
            return df.loc[idx, col]
        except Exception:
            return np.nan

    @staticmethod
    def safe_dict(d, key) -> float:
        """Safely get value from dictionary."""
        if isinstance(d, dict):
            return d.get(key, np.nan)
        return np.nan

    @staticmethod
    def safe_percentage(stock, index):
        """Calculate percentage difference safely."""
        if pd.isna(stock) or pd.isna(index) or index == 0:
            return np.nan
        return (stock / index - 1) * 100

    @staticmethod
    def normalize_recommendations(recs) -> Dict[str, float]:
        """
        Normalize recommendations to a standard dict with keys:
        { strongBuy, buy, hold, sell, strongSell }
        """
        if isinstance(recs, dict):
            return recs

        if isinstance(recs, pd.DataFrame) and not recs.empty:
            # yfinance usually has counts in first row
            row = recs.iloc[0]
            return {
                'strongBuy': row.get('strongBuy', np.nan),
                'buy': row.get('buy', np.nan),
                'hold': row.get('hold', np.nan),
                'sell': row.get('sell', np.nan),
                'strongSell': row.get('strongSell', np.nan),
            }

        return {}

    def enrich_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Enrich a ticker with growth estimates, price targets, and recommendations.

        Args:
            symbol: Stock ticker symbol (e.g., 'RKLB')

        Returns:
            Dictionary with enriched fields (all keys prefixed appropriately)
        """
        try:
            ticker = yf.Ticker(symbol)

            growth = ticker.growth_estimates
            targets = ticker.analyst_price_targets or {}
            recs = self.normalize_recommendations(ticker.recommendations_summary)

            enriched = {
                # Growth estimates
                'stockTrend_0q': self.safe_loc(growth, '0q', 'stockTrend'),
                'stockTrend_1q': self.safe_loc(growth, '+1q', 'stockTrend'),
                'stockTrend_0y': self.safe_loc(growth, '0y', 'stockTrend'),
                'stockTrend_1y': self.safe_loc(growth, '+1y', 'stockTrend'),
                'indexTrend_LTG': self.safe_loc(growth, 'LTG', 'indexTrend'),

                # Stock vs Index percentages
                'stockOverIndex_0q': self.safe_percentage(
                    self.safe_loc(growth, '0q', 'stockTrend'),
                    self.safe_loc(growth, '0q', 'indexTrend')
                ),
                'stockOverIndex_1q': self.safe_percentage(
                    self.safe_loc(growth, '+1q', 'stockTrend'),
                    self.safe_loc(growth, '+1q', 'indexTrend')
                ),
                'stockOverIndex_0y': self.safe_percentage(
                    self.safe_loc(growth, '0y', 'stockTrend'),
                    self.safe_loc(growth, '0y', 'indexTrend')
                ),
                'stockOverIndex_1y': self.safe_percentage(
                    self.safe_loc(growth, '+1y', 'stockTrend'),
                    self.safe_loc(growth, '+1y', 'indexTrend')
                ),

                # Price targets
                'price_targets_low': self.safe_dict(targets, 'low'),
                'price_targets_mean': self.safe_dict(targets, 'mean'),
                'price_targets_median': self.safe_dict(targets, 'median'),
                'price_targets_current': self.safe_dict(targets, 'current'),
                'price_targets_high': self.safe_dict(targets, 'high'),

                # Price target percentages
                'price_targets_overMean': self.safe_percentage(
                    self.safe_dict(targets, 'mean'),
                    self.safe_dict(targets, 'current')
                ),
                'price_targets_overMedian': self.safe_percentage(
                    self.safe_dict(targets, 'median'),
                    self.safe_dict(targets, 'current')
                ),

                # Recommendations
                'recommendations_strongBuy': self.safe_dict(recs, 'strongBuy'),
                'recommendations_buy': self.safe_dict(recs, 'buy'),
                'recommendations_hold': self.safe_dict(recs, 'hold'),
                'recommendations_sell': self.safe_dict(recs, 'sell'),
                'recommendations_strongSell': self.safe_dict(recs, 'strongSell'),
                'is_enriched': 1
            }

            # Rate limiting
            time.sleep(0.5 + random.random() * 0.25)

            return enriched

        except Exception as e:
            print(f"ERROR enriching ticker {symbol}: {e}")
            return {}
