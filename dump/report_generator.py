"""
Report generation module.
Handles AI briefing prompts and subject line generation.
"""
import pandas as pd
from ollama_client import MyStockBot
from data_utils import clean_ollama_think_response


# AI Columns to include from sector_company_daily in the briefing
AI_COLUMNS = [
    "symbol", "company_name",
    # Trend
    "stockTrend_0q", "stockTrend_1q", "stockOverIndex_0q", "stockOverIndex_1q",
    # Valuation / targets
    "price_targets_low", "price_targets_current", "price_targets_high", "price_targets_overMean", "price_targets_overMedian",
    # Analyst sentiment
    "recommendations_strongBuy", "recommendations_buy", "recommendations_hold", "recommendations_sell", "recommendations_strongSell",
    # Risk / valuation fundamentals
    "yahoo_beta_5y_monthly", "yahoo_pe_ratio_ttm", "yahoo_eps_ttm",
    # qualitative
    "yahoo_business_summary"
]


def fetch_top_signals_with_sector(con, limit: int = 15) -> pd.DataFrame:
    """
    Fetch top trading signals joined with sector fundamentals.

    Args:
        con: DuckDB connection
        limit: Number of top signals to fetch

    Returns:
        DataFrame with combined signal and sector data
    """
    query = f"""
    WITH latest_ts AS (
        SELECT MAX(date) AS max_date
        FROM technical_signals
    ),

    top_signals AS (
        SELECT *
        FROM technical_signals
        WHERE date = (SELECT max_date FROM latest_ts)
        ORDER BY combined_signal DESC
        LIMIT {limit}
    ),

    latest_sector AS (
        SELECT *
        FROM sector_company_daily
        WHERE date = (SELECT MAX(date) FROM sector_company_daily)
    )

    SELECT
        t.*,
        s.company_name,
        s.stockTrend_0q,
        s.stockTrend_1q,
        s.stockOverIndex_0q,
        s.stockOverIndex_1q,
        s.price_targets_low,
        s.price_targets_current,
        s.price_targets_high,
        s.price_targets_overMean,
        s.price_targets_overMedian,
        s.recommendations_strongBuy,
        s.recommendations_buy,
        s.recommendations_hold,
        s.recommendations_sell,
        s.recommendations_strongSell,
        s.yahoo_beta_5y_monthly,
        s.yahoo_pe_ratio_ttm,
        s.yahoo_eps_ttm,
        s.yahoo_business_summary
    FROM top_signals t
    LEFT JOIN latest_sector s
        ON t.ticker = s.symbol;
    """
    return con.execute(query).fetchdf()


def build_briefing_prompt(top_stocks_df: pd.DataFrame) -> str:
    """
    Build the executive briefing prompt for the AI.

    Args:
        top_stocks_df: DataFrame with top stock signals and fundamentals

    Returns:
        Formatted prompt string
    """
    # Use AI_COLUMNS to select and order columns for the prompt
    available_cols = [col for col in AI_COLUMNS if col in top_stocks_df.columns]
    csv_string = top_stocks_df[available_cols].to_csv(index=False)

    prompt = f"""
You are the Chief Market Strategist. Provide an executive briefing with the TOP 5 BUY opportunities from the data.

**Context on Metrics:**
- **Trend:** `stockTrend_0q/1q` = model price trend (current/next quarter). `stockOverIndex_0q/1q` = expected vs market.
- **Targets/Upside:** `price_targets_overMean/Median` = % upside/downside to analyst consensus.
- **Sentiment:** `recommendations_strongBuy/buy/hold/sell/strongSell` = analyst rating counts.
- **Risk/Value:** `yahoo_beta` = market risk (1=market). `yahoo_pe_ratio` = valuation. `yahoo_eps` = earnings.
- **Signal:** `Combined_Signal` = weighted composite of RSI (25%), MACD (25%), Trend (20%), Bollinger Bands (15%), and Volume (15%).
- **Business:** `yahoo_business_summary` = Yahoo's AI generated summary
Don't use asterisk at all in your answer.
**Required Format:**

**EXECUTIVE BRIEFING: Top 5 Tactical Buys**
*Market Insight: [One-sentence synthesis of the overall opportunity]*

**1. [Ticker] — “[3-5 word strategic tagline]”**
*   **Action:** BUY
*   **Thesis:** One clear sentence on why *now*.
*   **Catalyst:** Key metric from Trend, Target, Sentiment, or Signal data.
*   **Risk Context:** Beta or P/E note.
*   **CEO Note:** Strategic fit in one sentence.

**[Repeat for 2-5]**

**Bottom Line:** One decisive sentence on capital allocation.

DATA:
{csv_string}
"""
    return prompt


def build_subject_prompt(briefing: str) -> str:
    """
    Build prompt for generating email subject line.

    Args:
        briefing: The generated executive briefing text

    Returns:
        Prompt string for subject generation
    """
    return f"""
Create a single strategic email subject line (10-15 words) for a CEO based on this report.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:
Subject: [Your subject line here]

Report:
{briefing}
"""


def generate_briefing(my_bot: MyStockBot, top_stocks_df: pd.DataFrame) -> tuple[str, str]:
    """
    Generate the executive briefing and email subject using AI.

    Args:
        my_bot: Initialized MyStockBot instance
        top_stocks_df: DataFrame with top stock data

    Returns:
        Tuple of (cleaned_briefing, cleaned_subject)
    """
    prompt = build_briefing_prompt(top_stocks_df)
    print(f"Briefing prompt length: {len(prompt)}")

    result = my_bot.query_ollama(prompt)
    clean_result = clean_ollama_think_response(result)

    prompt2 = build_subject_prompt(clean_result)
    result2 = my_bot.query_ollama(prompt2)
    clean_result2 = clean_ollama_think_response(result2)

    return clean_result, clean_result2
