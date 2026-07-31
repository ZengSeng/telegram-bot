# You can fact check below, and when you refer to my scripts in dump, you can use it as reference, no need to follow 100%, just make sure it works and merge seamlessly

# Main objective
Turn this working pipeline from few tickers reporting to a bigger view stock universe shortlisting.
Pretty big project so want to plan well. I will initiate by telling you which stage I wanna be in. I will give details of phase we are in, but for future phases just a general title, use those to guide current plan.

## Phase 1: Build the stock universe

My idea is that this scraping can be broken down into 4 groups, see python code below, and can be run throughout the day (10am, 11am, 12pm, 1pm)

### Input
Yahoo Finance sectors, Industries, Top companies.
Rating is below, which can be quite useful??
- Buy
- Hold
- Sell
- Strong Buy
- Underperform

see dump\scrape_sector.py, ignore empty_enrichment_fields

```python
# Use this to have roughly similar total number of companies in each groups
# yf.const.SECTOR_INDUSTY_MAPPING_LC.keys()

sector_groups = [
    ["industrials", "utilities", "basic-materials"],
    ["financial-services", "real-estate", "communication-services"],
    ["technology", "energy", "consumer-defensive"],
    ["healthcare", "consumer-cyclical"]
]

Iterate:
for i, sectors in enumerate(sector_groups, 1):
    print(f"Group {i}: {sectors}")
```

## Phase 2: Data refresh see above

### Data that I have

#### News:
global_news - Scrape from yahoo
news - Scrape from yahoo, ticker by ticker
news_summary - Local LLM summary, ticker by ticker
gfinance_overview - Scrape from google, ticker by ticker

#### Daily:
daily_prices - see line 40 to 113 of dump\scrape_stock_prices.py

I want 
auto_adjust=True
actions=True

#### 3-days:
ticker_enriched - has 3 parts
- yahoo_summary which is Yahoo's AI news driven (preferably this can be daily like news, but I don't know how to go about this, maybe shortlisted tickers will have daily scrape)
- growth estimate and analyst price target, which is not changing too much day to day
- technical - computed, can be put in daily_prices and become daily?


#### Smart schedule (per ticker, I like this):
financials

## Unsure how to schedule this:
trading_agent_decisions - maybe 
analyst_targets - this is same as _fetch_enrichment? Can we merge this??
fundamentals

## Phase 3: Quantitative screener

Compute scores such as:

### Quality
ROE
ROIC
Gross Margin
Operating Margin
Revenue Growth
EPS Growth
Value
Forward PE
PEG
EV/EBITDA
FCF Yield
### Momentum
3M Return
6M Return
RSI
Above 200 MA
### Sentiment
Analyst revisions
Price target upside
News sentiment
### Risk
Debt
Beta
Volatility

Normalize every metric using percentile rank.

Then:

Quality Score
Value Score
Momentum Score
Sentiment Score
Risk Score

↓

Overall Score

Store every score.

## Phase 4: Candidate selection

Don't just take Top 10.

Apply filters.

Example:

Top 5 Technology

Top 3 Healthcare

Top 3 Financials

Top 2 Consumer

Top 2 Industrials

Remove duplicates.

Remove extremely correlated stocks.

Now maybe you have 12 candidates.

## Phase 5: Event detection  (Do we have the data for all???)

Only run expensive AI if something changed.

Examples:

✅ Earnings released

✅ New SEC filing

✅ News sentiment changed

✅ Price moved >5%

✅ Technical trend changed

Otherwise:

Reuse yesterday's analysis. This saves huge amounts of compute.

## Phase 6: TradingAgents

Now run TradingAgents only on those candidates.

Return structured data:

{
  "ticker": "NVDA",
  "decision": "BUY",
  "confidence": 82,
  "bull_points": [],
  "bear_points": [],
  "key_risks": [],
  "expected_catalysts": [],
  "summary": ""
}

Treat TradingAgents as an analyst, not a portfolio manager.

## Phase 7: Portfolio engine (deterministic)

This is your secret sauce.

Input:

current holdings
cash
TradingAgents output
screener score
sector exposure
position sizes

Rules like:

max 20% one stock
max 35% one sector
don't buy if confidence <70
don't buy if screener below top 20%
keep 10% cash
don't buy if thesis hasn't changed

Output:

BUY

HOLD

SELL

Position size

## Phase 8: Portfolio review (LLM)

Now let an LLM look at everything.

Prompt:

"Review today's portfolio decisions. Are there any contradictions, concentration risks, or important interactions across holdings?"

This is different from asking it to decide.

It's acting like an investment committee.

## Telegram!!!