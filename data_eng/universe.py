"""Stock universe scraper: builds a broad ticker universe from Yahoo Finance sectors."""

import logging
import time
from datetime import date

import numpy as np
import yfinance as yf

from .db import get_connection

log = logging.getLogger(__name__)

# Rate limit between industry API calls
INDUSTRY_PAUSE = 0.5

# 4 staggered groups (roughly equal company counts)
SECTOR_GROUPS: dict[int, list[str]] = {
    1: ["industrials", "utilities", "basic-materials"],
    2: ["financial-services", "real-estate", "communication-services"],
    3: ["technology", "energy", "consumer-defensive"],
    4: ["healthcare", "consumer-cyclical"],
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS stock_universe (
    ticker          VARCHAR PRIMARY KEY,
    company_name    VARCHAR,
    sector          VARCHAR,
    industry        VARCHAR,
    rating          VARCHAR,
    group_id        SMALLINT,
    sector_weight   DOUBLE,
    company_weight  DOUBLE,
    date_added      DATE,
    last_updated    DATE
);
"""


class UniverseScraper:
    """Scrapes Yahoo Finance sector/industry/company data into stock_universe."""

    def __init__(self):
        self.today = date.today()

    def scrape_sector_group(self, group_id: int) -> set[str]:
        """Scrape all sectors in a group, upsert companies to stock_universe.

        Returns the set of tickers discovered/updated.
        """
        sectors = SECTOR_GROUPS.get(group_id)
        if not sectors:
            log.warning("Unknown group_id=%s (valid: 1-4)", group_id)
            return set()

        conn = get_connection()
        conn.execute(_CREATE_TABLE)

        tickers: set[str] = set()

        for sector_key in sectors:
            log.info("Universe: scraping sector '%s' (group %d)", sector_key, group_id)
            try:
                sector = yf.Sector(sector_key)
                if sector.industries is None or sector.industries.empty:
                    log.warning("Universe: no industries for sector '%s'", sector_key)
                    continue

                sector_weight = sector.overview.get("market_weight", np.nan)
                if isinstance(sector_weight, np.generic):
                    sector_weight = float(sector_weight)

                for industry_name in sector.industries.index:
                    try:
                        industry = yf.Industry(industry_name)
                        top = industry.top_companies
                        if top is None or top.empty:
                            continue

                        for symbol, row in top.iterrows():
                            company_weight = row.get("market weight", np.nan)
                            if isinstance(company_weight, np.generic):
                                company_weight = float(company_weight)

                            rating = row.get("rating")
                            if isinstance(rating, np.generic):
                                rating = str(rating)

                            conn.execute(
                                """INSERT INTO stock_universe
                                   (ticker, company_name, sector, industry, rating,
                                    group_id, sector_weight, company_weight,
                                    date_added, last_updated)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                   ON CONFLICT (ticker) DO UPDATE SET
                                       company_name = EXCLUDED.company_name,
                                       sector = EXCLUDED.sector,
                                       industry = EXCLUDED.industry,
                                       rating = EXCLUDED.rating,
                                       group_id = EXCLUDED.group_id,
                                       sector_weight = EXCLUDED.sector_weight,
                                       company_weight = EXCLUDED.company_weight,
                                       last_updated = EXCLUDED.last_updated
                                """,
                                [
                                    symbol,
                                    row.get("name"),
                                    sector_key,
                                    industry_name,
                                    rating,
                                    group_id,
                                    sector_weight,
                                    company_weight,
                                    self.today,
                                    self.today,
                                ],
                            )
                            tickers.add(symbol)

                        time.sleep(INDUSTRY_PAUSE)

                    except Exception as e:
                        log.warning("Universe: error in industry '%s': %s", industry_name, e)
                        continue

            except Exception as e:
                log.warning("Universe: error in sector '%s': %s", sector_key, e)
                continue

        conn.close()
        log.info("Universe: group %d complete — %d tickers", group_id, len(tickers))
        return tickers

    def scrape_all(self) -> set[str]:
        """Scrape all 4 sector groups. Returns full set of tickers."""
        all_tickers: set[str] = set()
        for group_id in SECTOR_GROUPS:
            all_tickers |= self.scrape_sector_group(group_id)
        log.info("Universe: full scrape complete — %d total tickers", len(all_tickers))
        return all_tickers

    def get_universe_tickers(
        self, group_id: int | None = None, min_rating: str | None = None
    ) -> list[str]:
        """Query universe table with optional filters.

        Args:
            group_id: Filter by stagger group (1-4).
            min_rating: Minimum rating threshold. One of
                        "Strong Buy", "Buy", "Hold", "Sell", "Underperform".
        """
        conn = get_connection()
        conn.execute(_CREATE_TABLE)

        query = "SELECT ticker FROM stock_universe WHERE 1=1"
        params: list = []

        if group_id is not None:
            query += " AND group_id = ?"
            params.append(group_id)

        if min_rating is not None:
            rating_order = ["Underperform", "Sell", "Hold", "Buy", "Strong Buy"]
            if min_rating in rating_order:
                idx = rating_order.index(min_rating)
                allowed = rating_order[idx:]
                placeholders = ", ".join("?" for _ in allowed)
                query += f" AND rating IN ({placeholders})"
                params.extend(allowed)

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_shortlisted_tickers(self) -> list[str]:
        """Return Strong Buy + Buy rated tickers (for enrichment/analysis)."""
        conn = get_connection()
        conn.execute(_CREATE_TABLE)
        rows = conn.execute(
            "SELECT ticker FROM stock_universe WHERE rating IN ('Strong Buy', 'Buy')"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
