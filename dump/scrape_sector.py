"""
Sector scraping service: Crawls Yahoo Finance sector/industry mapping and top companies.

This module handles the core iteration through
1. sectors
2. industries
3. top companies.

It determines which tickers are "priority" (Strong Buy rating or in
watch list) and collects base data for all tickers.

For priority tickers, empty enrichment fields are added as placeholders; the actual
enrichment is performed by TickerEnricher.

For non-priority tickers, only base data is collected with NaN enrichment fields.
"""
import yfinance as yf
import numpy as np
import time
from datetime import date, datetime
from typing import List, Dict, Any

from config import config


class SectorScraper:
    """Scrapes sector/industry/company data from Yahoo Finance."""

    def __init__(self, watch_list: set = None):
        self.watch_list = watch_list or config.watch_list
        self.today = date.today()

    def scrape_sectors(self) -> List[Dict[str, Any]]:
        """
        Main entry point: iterate all sectors/industries and collect company data.

        Returns:
            List of row dictionaries, each representing a company with base fields
            and (empty) enrichment fields.
        """
        rows = []

        for sector_key in yf.const.SECTOR_INDUSTY_MAPPING_LC.keys():  # type: ignore
            print("")
            print(f"================== Sector: {sector_key} ==================", end=" ")

            sector = yf.Sector(sector_key)
            if sector.industries is None or sector.industries.empty:
                continue

            sector_weight = sector.overview.get("market_weight", np.nan)
            sector_cap = sector.overview.get("market_cap", np.nan)

            for industry_name in sector.industries.index:
                print("")
                print(f"====== Industry: {industry_name} ======", end=" ")
                try:
                    industry = yf.Industry(industry_name)

                    top = industry.top_companies
                    if top is None or top.empty:
                        continue

                    for symbol, row in top.iterrows():
                        base = {
                            'date': self.today,
                            'sector': sector_key,
                            'industry': industry_name,
                            'sector_market_cap': sector_cap,
                            'sector_weight': sector_weight,
                            'symbol': symbol,
                            'company_name': row.get("name"),
                            'rating': row.get("rating"),
                            'company_weight_in_sector': row.get("market weight"),
                            'company_weight_in_market': sector_weight * row.get("market weight", 0),
                            'extraction_date': datetime.now()
                        }

                        rows.append({**base, **self.empty_enrichment_fields()})
                    time.sleep(0.5)

                except Exception as e:
                    print(f"ERROR processing industry {industry_name}: {e}")
                    continue
        
        return rows

    @staticmethod
    def empty_enrichment_fields() -> Dict[str, Any]:
        """Return dict of all enrichment fields with NaN values."""
        return {
            'stockTrend_0q': np.nan,
            'stockTrend_1q': np.nan,
            'stockTrend_0y': np.nan,
            'stockTrend_1y': np.nan,
            'indexTrend_LTG': np.nan,
            'stockOverIndex_0q': np.nan,
            'stockOverIndex_1q': np.nan,
            'stockOverIndex_0y': np.nan,
            'stockOverIndex_1y': np.nan,
            'price_targets_overMean': np.nan,
            'price_targets_overMedian': np.nan,
            'price_targets_low': np.nan,
            'price_targets_mean': np.nan,
            'price_targets_median': np.nan,
            'price_targets_current': np.nan,
            'price_targets_high': np.nan,
            'recommendations_strongBuy': np.nan,
            'recommendations_buy': np.nan,
            'recommendations_hold': np.nan,
            'recommendations_sell': np.nan,
            'recommendations_strongSell': np.nan,
            'yahoo_previous_close': np.nan,
            'yahoo_open': np.nan,
            'yahoo_bid': np.nan,
            'yahoo_ask': np.nan,
            'yahoo_days_range': np.nan,
            'yahoo_52_week_range': np.nan,
            'yahoo_volume': np.nan,
            'yahoo_avg_volume': np.nan,
            'yahoo_market_cap_intraday': np.nan,
            'yahoo_beta_5y_monthly': np.nan,
            'yahoo_pe_ratio_ttm': np.nan,
            'yahoo_eps_ttm': np.nan,
            'yahoo_earnings_date': np.nan,
            'yahoo_1y_target_est': np.nan,
            'yahoo_business_summary': np.nan,
            'is_enriched': np.nan,            
            'is_priority': np.nan,
        }
