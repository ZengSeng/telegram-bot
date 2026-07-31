"""
Shared data utilities for the analysis pipeline.
Includes JSON conversion and response cleaning helpers.
"""
import json
import re
from typing import Tuple

import numpy as np
import pandas as pd
import config


def dataframe_to_ai_json(df):
    """Convert DataFrame to JSON format suitable for AI prompting"""
    df = df.copy()

    for col in df.columns:
        df[col] = df[col].map(
            lambda x: x.isoformat() if hasattr(x, "isoformat") else x
        )

    return json.dumps(
        df.replace({np.nan: None}).to_dict(orient="records"),
        indent=2
    )


def clean_ollama_think_response(response: str) -> str:
    """
    Clean Ollama's deepseek-r1:7b response by removing think tags.

    Args:
        response: Raw response string from Ollama

    Returns:
        Cleaned response string without think tags
    """
    # Pattern to match <think> with optional whitespace
    pattern = r'<\s*think\s*>.*?<\s*/\s*think\s*>'
    # Handle None case
    if response is None:
        response = ""  # or some default value
    # Remove all think sections
    cleaned = re.sub(pattern, '', response, flags=re.DOTALL)

    # Clean up any extra blank lines left behind
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)  # Replace 3+ newlines with 2
    cleaned = cleaned.strip()

    return cleaned


def _fmt(val, decimals=2):
    """Format a value for display, returning 'N/A' for None/NaN."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    try:
        return f"{float(val):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def summarize_sector_data(row: dict) -> str:
    """Summarize a single sector/fundamentals row into a concise text block."""
    return (
        f"Company: {row.get('company_name', 'N/A')} | Ticker: {row.get('symbol', 'N/A')} "
        f"| Sector: {row.get('sector', 'N/A')} | Industry: {row.get('industry', 'N/A')}\n"
        f"Market Cap: ${_fmt(row.get('market_cap_intraday'))} | P/E: {_fmt(row.get('pe_ratio_ttm'))} | EPS: {_fmt(row.get('eps_ttm'))}\n"
        f"Business Summary: {str(row.get('yahoo_business_summary', 'N/A'))[:300]}\n"
        f"Stock vs Index (0q): {_fmt(row.get('stockOverIndex0q'))}% | (1q): {_fmt(row.get('stockOverIndex1q'))}%\n"
        f"Analyst Sentiment: {row.get('recommendations_strongBuy', 0)} strong buy, "
        f"{row.get('recommendations_buy', 0)} buy, "
        f"{row.get('recommendations_hold', 0)} hold, "
        f"{row.get('recommendations_sell', 0)} sell, "
        f"{row.get('recommendations_strongSell', 0)} strong sell\n"
        f"Price Target: mean=${_fmt(row.get('price_targets_mean'))}, "
        f"current=${_fmt(row.get('price_targets_current'))}, "
        f"upside={_fmt(row.get('price_targets_overMedian'))}%"
    )


def summarize_price_history(df: pd.DataFrame) -> str:
    """Summarize price history into key stats — no moving averages (covered in technical)."""
    if df.empty:
        return "No price data available."
    latest = df.iloc[-1]
    first = df.iloc[0]
    change = ((float(latest['close']) - float(first['close'])) / float(first['close'])) * 100
    period_low = df['low'].min()
    period_high = df['high'].max()
    avg_volume = df['volume'].mean()

    return (
        f"Current: ${_fmt(latest['close'])} | Range: ${_fmt(period_low)} - ${_fmt(period_high)}\n"
        f"Period Change: {'+' if change >= 0 else ''}{_fmt(change, 1)}% "
        f"from ${_fmt(first['close'])} to ${_fmt(latest['close'])}\n"
        f"Current Volume: {_fmt(latest['volume'], 0)} | Avg Volume: {_fmt(avg_volume, 0)}"
    )


def summarize_kronos_predictions(df: pd.DataFrame) -> str:
    """Summarize Kronos predictions — uses latest evaluation_date."""
    if df.empty:
        return "No Kronos predictions available."
    latest_eval = df['evaluation_date'].max()
    pred = df[df['evaluation_date'] == latest_eval]
    if pred.empty:
        return "No Kronos predictions available."

    n = len(pred)
    day1 = pred.iloc[0]
    day5 = pred.iloc[min(4, n - 1)]
    day10 = pred.iloc[-1]
    avg_close = pred['close'].mean()
    trend = "up" if float(day10['close']) > float(day1['close']) * 1.01 else "down" if float(day10['close']) < float(day1['close']) * 0.99 else "sideways"

    return (
        f"Evaluated: {latest_eval}\n"
        f"Day 1: ${_fmt(day1['close'])} | Day 5: ${_fmt(day5['close'])} | Day 10: ${_fmt(day10['close'])}\n"
        f"Trend: {trend.capitalize()} | Avg predicted close: ${_fmt(avg_close)}"
    )


def summarize_technical_analysis(df: pd.DataFrame) -> str:
    """Summarize technical indicators — latest row only with signal interpretation."""
    if df.empty:
        return "No technical analysis data available."
    latest = df.iloc[-1]

    rsi = float(latest.get('RSI_14', np.nan))
    rsi_label = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"

    macd = float(latest.get('MACD', np.nan))
    macd_sig = float(latest.get('MACD_Signal', np.nan))
    macd_label = "bullish crossover" if macd > macd_sig else "bearish crossover" if macd < macd_sig else "neutral"

    close = float(latest['close'])
    sma20 = float(latest.get('SMA_20', np.nan))
    sma50 = float(latest.get('SMA_50', np.nan))
    sma20_label = "above" if close > sma20 else "below"
    sma50_label = "above" if close > sma50 else "below"

    bb_upper = float(latest.get('BB_Upper', np.nan))
    bb_lower = float(latest.get('BB_Lower', np.nan))
    if not np.isnan(bb_lower) and close < bb_lower:
        bb_label = "below lower (potential buy)"
    elif not np.isnan(bb_upper) and close > bb_upper:
        bb_label = "above upper (potential sell)"
    else:
        bb_label = "within bands"

    obv = float(latest.get('OBV', np.nan))
    # Compare latest OBV to previous for trend
    obv_label = "stable"
    if len(df) >= 2:
        prev_obv = float(df.iloc[-2].get('OBV', np.nan))
        if not np.isnan(prev_obv):
            obv_label = "rising" if obv > prev_obv else "falling" if obv < prev_obv else "flat"

    combined = float(latest.get('Combined_Signal', np.nan))
    if not np.isnan(combined):
        combined_label = "BUY" if combined > 0.3 else "SELL" if combined < -0.3 else "HOLD"
    else:
        combined_label = "N/A"

    trade = latest.get('Trade_Signal', np.nan)
    if trade == 1:
        trade_label = "BUY"
    elif trade == -1:
        trade_label = "SELL"
    else:
        trade_label = "HOLD"

    return (
        f"RSI(14): {_fmt(rsi)} -> {rsi_label}\n"
        f"MACD: {_fmt(macd)}, Signal: {_fmt(macd_sig)} -> {macd_label}\n"
        f"Price vs SMA20: {sma20_label} (${_fmt(close)} vs ${_fmt(sma20)})\n"
        f"Price vs SMA50: {sma50_label} (${_fmt(close)} vs ${_fmt(sma50)})\n"
        f"Bollinger: {bb_label} (${_fmt(close)} vs ${_fmt(bb_lower)}-${_fmt(bb_upper)})\n"
        f"OBV: {_fmt(obv, 0)} -> {obv_label}\n"
        f"Combined Signal: {_fmt(combined, 3)} (weighted from RSI, MACD, trend signals) -> {combined_label}\n"
        f"Trade Signal: {trade_label} (derived from Combined Signal thresholds)"
    )


def get_top_stocks_sql() -> str:
    """
    Build SQL query to fetch top stock symbols based on configured thresholds.

    Returns:
        SQL query string
    """
    return """
        SELECT symbol
        FROM sector_company_daily
        WHERE date = (SELECT MAX(date) FROM sector_company_daily)
          AND {} > {}
          AND recommendations_strongBuy > {}
        ORDER BY {} DESC
        LIMIT {}
    """.format(
        config.price_targets_over_median_field,
        config.price_targets_over_median_threshold,
        config.recommendations_strong_buy_threshold,
        config.price_targets_over_median_field,
        config.top_stocks_limit
    )


def build_trading_prompt(
    symbol: str,
    sector_data_row: dict,
    price_df: pd.DataFrame,
    kronos_df: pd.DataFrame,
    tech_df: pd.DataFrame,
) -> Tuple[str, str]:
    """Build system + user prompt for a single ticker's trading analysis.

    Returns (system_prompt, user_prompt) tuple for OpenAI-compatible chat API.
    """
    system_prompt = (
        "You are an expert stock analyst. You will receive structured data for a single stock ticker. "
        "Produce a concise, professional stock summary covering: "
        "(1) company overview, (2) recent price action, "
        "(3) 10-day price prediction, (4) technical outlook, and (5) a clear buy/hold/sell recommendation. "
        "Be specific with numbers. Do NOT use markdown formatting (no asterisks, no backticks)."
    )

    user_prompt = f"""Analyze the following data for ticker: {symbol}

== SECTOR & FUNDAMENTALS ==
{summarize_sector_data(sector_data_row)}

== RECENT PRICE HISTORY (last ~60 trading days) ==
{summarize_price_history(price_df)}

== KRONOS 10-DAY PREDICTIONS ==
{summarize_kronos_predictions(kronos_df)}

== TECHNICAL INDICATORS ==
{summarize_technical_analysis(tech_df)}

CONTEXT for interpretation:
- stockOverIndex0q/1q: stock performance vs benchmark index over 0-quarter and 1-quarter periods. Positive = outperforming.
- Combined_Signal: weighted score from RSI, MACD, and trend signals (-1 to 1). >0.3 = BUY bias, <-0.3 = SELL bias.
- Trade_Signal: final signal derived from Combined_Signal thresholds (1=BUY, 0=HOLD, -1=SELL).

OUTPUT FORMAT:
[Company Name] ({symbol})
Sector: {sector_data_row.get('sector', 'N/A')} | Industry: {sector_data_row.get('industry', 'N/A')}

1. Current State: [2-3 sentences on price, valuation, analyst sentiment]
2. Prediction: [1-2 sentences on Kronos 10-day forecast with specific price levels]
3. Technical View: [2-3 sentences interpreting the key technical signals]
4. Recommendation: BUY / HOLD / SELL
5. Rationale: [3-4 bullet points supporting the recommendation]"""

    return system_prompt, user_prompt
