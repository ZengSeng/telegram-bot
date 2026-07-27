"""
Yahoo Finance stats scraper: Scrapes detailed stats and business summary from Yahoo Finance.

This module scrapes the Yahoo Finance quote page for a given ticker symbol, extracting:
- Previous close, open, bid, ask, day's range, 52-week range
- Volume, average volume, market cap, beta, P/E ratio, EPS, earnings date
- 1-year target estimate
- Business summary (from the title/info section)

The scraping is rate-limited (5-8 second delay) to avoid being blocked.

Usage:
    scraper = YahooStatsScraper()
    stats, summary = scraper.fetch_stats(symbol)
"""
import requests
import random
import time
from bs4 import BeautifulSoup
import numpy as np
from typing import Tuple, Dict, Any


class YahooStatsScraper:
    """Scrapes Yahoo Finance quote page for detailed stats and business summary."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {"User-Agent": "Mozilla/5.0"}

    def fetch_stats(self, symbol: str) -> Tuple[Dict[str, Any], Any]:
        """
        Fetch Yahoo Finance stats and business summary for a ticker.

        Args:
            symbol: Stock ticker symbol (e.g., 'RKLB')

        Returns:
            Tuple of (stats_dict, summary_text)
            - stats_dict: Dictionary with keys prefixed by 'yahoo_' (e.g., yahoo_previous_close)
            - summary_text: Business summary string (or np.nan if not found)
        """
        url = f"https://finance.yahoo.com/quote/{symbol}/"

        try:
            r = requests.get(url, headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
        except Exception:
            return {}, np.nan

        soup = BeautifulSoup(r.text, "html.parser")

        # -------- Summary --------
        summary_text = np.nan
        summary = soup.select_one("h2.header span.titleInfo")
        if summary:
            summary_text = summary.get_text(strip=True)

        # -------- Key Stats --------
        stats = {}
        ul = soup.find("ul", class_="yf-6myrf1")
        if ul:
            for li in ul.find_all("li"):
                label = li.find("span", class_="label")
                value = li.find("span", class_="value")
                if label and value:
                    key = (
                        label.get_text(strip=True)
                        .lower()
                        .replace(" ", "_")
                        .replace("(", "")
                        .replace(")", "")
                        .replace("&", "and")
                    )
                    stats[f"yahoo_{key}"] = value.get_text(strip=True)
        stats["is_priority"] = 1
        return stats, summary_text

    def fetch_with_rate_limit(self, symbol: str) -> Tuple[Dict[str, Any], Any]:
        """
        Fetch stats with built-in rate limiting (5-8 second delay).

        Args:
            symbol: Stock ticker symbol

        Returns:
            Tuple of (stats_dict, summary_text)
        """
        stats, summary = self.fetch_stats(symbol)
        time.sleep(2 + random.random() * 3)  # Rate limit: 2-5 seconds
        
        return stats, summary
