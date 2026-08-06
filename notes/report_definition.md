# Report Definition — where every number comes from

This doc maps every number you see in `/advice` and the 9:30 AM briefing
back to its source. Read top-down: **External sources → DuckDB tables →
what you see**.

---

## 1. The four external sources (everything starts here)

| Source | What it provides | Currency |
|--------|------------------|----------|
| **yfinance** (Yahoo Finance API) | Stock prices, company fundamentals, analyst targets, growth estimates, news articles | USD |
| **Web scrapes** | Stock universe + ratings (Yahoo screener pages), Google Finance AI overviews (bull/bear points), Yahoo Finance AI overviews | USD |
| **trades.csv** | Your actual executed trades (photo-logged via the bot) — only **share counts** are used, prices are ignored | n/a |
| **Local LLM** (llama-server, Qwythos-9B) | News summaries, TradingAgents analyses, committee reviews | n/a |

Key fact: **everything is USD**. yfinance returns USD prices, `TOTAL_CAPITAL`
is USD, and trades.csv only contributes share counts (shares have no
currency). Your NZD broker balance is the same portfolio in another currency.

---

## 2. The main DuckDB tables (the middle layer)

| Table | Built from | Refreshed by |
|-------|-----------|--------------|
| `daily_prices` | yfinance batch download | Daily 8 AM (all ~2,700 tickers) |
| `technicals` | Computed locally from `daily_prices` (no external call) | Daily 8 AM |
| `news` | yfinance news feed (~50 articles/ticker) | Daily 8 AM for watchlist + candidates |
| `news_summaries` | **LLM** summarizes the `news` rows (Catalysts/Sentiment/Risks) | Daily 8 AM |
| `fundamentals` | yfinance company snapshot (P/E, margins…) | Night runs, rolling 80/run |
| `ticker_enriched` | yfinance growth estimates, price targets, recommendations | Night runs, rolling 80/run |
| `analyst_targets` | yfinance analyst consensus | Night runs, rolling 120/run |
| `gfinance_overview` | Google Finance scrape: sentiment %, bull/bear points | Daily (watchlist) + night runs |
| `screener_scores` | **Computed** from fundamentals + ticker_enriched + technicals + daily_prices (see §3) | Daily 8 AM, full universe |
| `trading_agent_decisions` | **TradingAgents** (multi-agent LLM debate over the local tables above) | Night runs (up to 6/run) + `/analyze` |
| `portfolio_decisions` | **Rules engine** (no AI) combining decisions + scores + holdings | Daily 8 AM + `--portfolio` |
| `portfolio_reviews` | **LLM** committee reviewing `portfolio_decisions` | Daily 8 AM |

---

## 3. `/advice TICKER` — the buy card, line by line

| You see | Source table | Where it really comes from |
|---------|--------------|---------------------------|
| **Price $X.XX** | `daily_prices` (latest close) | yfinance, downloaded 8 AM |
| **(+1.2%)** day change | `daily_prices` (latest vs previous close) | Same — simple division |
| **Screener: NN/100** | `screener_scores.overall_score` | Percentile rank (0-100) across the whole universe, averaged over 5 equal categories: **quality** (ROE, ROA, margins), **value** (forward P/E, PEG, FCF yield), **risk** (debt/equity, beta, 63-day volatility) — all from `fundamentals` + `daily_prices`; **momentum** (3/6-month returns, RSI, above 200-day MA) from `daily_prices` + `technicals`; **sentiment** (analyst bullish ratio, price-target upside) from `ticker_enriched`. **It's a rank, not an absolute quality measure** — 80 means "better than 80% of scored tickers" |
| **TradingAgents (date): Buy / Overweight** | `trading_agent_decisions` | The LLM's final verdict after running market/news/fundamentals analysts + bull/bear debate + risk team + portfolio manager over the local tables. The date tells you how fresh the analysis is |
| **entry / stop / target** | `trading_agent_decisions` (entry_price, stop_loss, price_target) | LLM-generated during the analysis, grounded in stored price/fundamental data |
| **Horizon** | `trading_agent_decisions.time_horizon` | LLM-generated |
| **Summary paragraph** | `trading_agent_decisions.summary` | LLM-generated (trimmed to 400 chars in the card) |
| **News (date)** | `news_summaries` | LLM summary of yfinance headlines; today's if available, else the newest |

## 4. `/advice` — the list view

### Trade plan section (also the first message of the 9:30 push)

| You see | Where it comes from |
|---------|---------------------|
| **BUY ticker: N sh (X%)** | `portfolio_decisions` — the rules engine sized the position: `shares = position_value / latest price`, where position_value is an equal split of **deployable capital**, capped at 20% of `TOTAL_CAPITAL` ($28,000) per stock |
| **stop $X.XX** | TradingAgents' stop loss if it gave one, else default **-8% below current price** (engine rule) |
| **reason** | e.g. "Buy signal + score 85" = TA said Buy AND screener score passed the 80 gate |
| **SELL ticker: N sh** | TA's latest decision for a stock you hold is Sell; shares = your full holding from trades.csv |
| **HOLD: N held positions unchanged (M other signals suppressed by rules)** | N = tickers you actually own with no action; M = tickers the engine tracks (TA verdicts exist) but you don't own, or whose buy was blocked by a rule |

Inputs the engine mixes: your holdings (**trades.csv** share counts), latest
prices (**daily_prices**), latest TA verdicts (**trading_agent_decisions**),
screener scores (**screener_scores**), sector caps (**stock_universe**).

Rules: max 20% per stock, max 35% per sector, buy only if screener ≥ 80
(a ticker with **no score** — missing fundamentals — is blocked too),
always keep 10% cash. **Deployable = $28,000 × 90% − current holdings market
value.** The engine never forces sells of oversized positions — it only gates
new buys.

### Ideas section

| You see | Where it comes from |
|---------|---------------------|
| **Ideas list** | Top 3 `screener_scores` that **clear the same ≥80 buy gate** as the engine, excluding stocks you hold (trades.csv) and your watchlist. If nothing clears the gate you get an explicit "None today" message instead of weaker suggestions |
| **score NN** | Same screener percentile as above |
| **$price ±%** | `daily_prices` |
| **TA: Buy (date) target $X** | Latest `trading_agent_decisions` row if that ticker was ever analyzed; absent if never analyzed |

## 5. 9:30 AM briefing (rest of the push)

| Message | Source |
|---------|--------|
| Charts | `daily_prices` rendered locally |
| Portfolio summary | trades.csv holdings + `daily_prices` |
| Trade plan | §4 above |
| **Committee review** | `portfolio_reviews` — the LLM reading today's `portfolio_decisions` + TA summaries and flagging risks/contradictions. It does not see prices or news directly |
| **News briefing** | `news_summaries` for watchlist + today's candidates |

---

## 6. Trust guide — which numbers to lean on

- **Hard numbers** (prices, day change, your share counts): factual, straight from yfinance/trades.csv.
- **Screener score**: mechanical and reproducible, but a *relative* rank — it moves when the rest of the universe moves.
- **TradingAgents verdicts**: LLM judgement. Grounded in real stored data (not live hallucination of prices), but can flip day to day (e.g. MU: Buy → Sell → Buy). Treat as informed opinion.
- **Trade plan**: deterministic rules applied to the above — as good as its inputs. It's the most conservative layer by design.
- **Committee review**: LLM opinion about the trade plan; good for spotting blind spots, not a source of new facts.
