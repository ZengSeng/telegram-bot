# Data Flow Diagrams

Mermaid diagrams illustrating the daily and nightly pipeline data flows.

---

### 1. Daily Pipeline (`--daily` / `--daily-smart`) — 8:00 AM NZT

**Tickers:** `data/watchlist.json` for steps ①–⑩; step ⑬ analyzes candidates + watchlist (event-gated)

```mermaid
flowchart LR
    WL["📋 watchlist.json"]

    subgraph ROW1["Per-ticker loop (steps ②–⑥)"]
        direction LR
        S1["① Batch Prices\n─── daily_prices ───"]
        S2["② News\n─── news ───"]
        S3["③ Analyst Targets\n─── analyst_targets ───"]
        S4["④ Enriched\n─── ticker_enriched ───"]
        S5["⑤ Fundamentals\n─── fundamentals ───"]
        S1 --> S2 --> S3 --> S4 --> S5
    end

    subgraph ROW2[" "]
        direction LR
        S6["⑥ Financials\n─── financials ───"]
        S7["⑦ Global News\n─── global_news ───"]
        S8["⑧ Google Finance\n─── gfinance_overview ───"]
        S9["⑨ AI Summaries\n─── news_summaries ───"]
        S10["⑩ Rolling Enrich\n─── fundamentals ───\n100 universe tickers"]
        S6 --> S7 --> S8 --> S9 --> S10
    end

    subgraph ROW3[" "]
        direction LR
        S11["⑪ Screener\n─── screener_scores ───"]
        S12["⑫ Candidates\n─── candidates ───"]
        S13["⑬ TradingAgents\n─── trading_agent_decisions ───\nevent-gated, candidates+watchlist"]
        S14["⑭ Portfolio Engine\n─── portfolio_decisions ───"]
        S15["⑮ LLM Review\n─── portfolio_reviews ───"]
        S11 --> S12 --> S13 --> S14 --> S15
    end

    WL --> S1
    S5 --> S6
    S10 --> S11
```

### Smart Scheduling Staleness Thresholds

| Table | Date Column | Max Age | Refresh Rule |
|-------|------------|---------|--------------|
| `news` | `date` | 1 day | Changes daily |
| `ticker_enriched` | `date_fetched` | 3 days | Slow-changing estimates |
| `analyst_targets` | `date_fetched` | 3 days | Overlaps enrichment cycle |
| `fundamentals` | `date_fetched` | 7 days | Company info snapshot |
| `financials` | `report_date` | report_date + 80 days | Quarterly filing cycle |

---

### 2. Night Pipeline (`--night`) — 3:00 PM NZT

**Tickers:** Full `stock_universe` (~2700+); enrich gated by rating (Buy/Strong Buy) + watchlist

```mermaid
flowchart LR
    subgraph NIGHT["🌙 Night Pipeline — 3:00 PM NZT"]
        direction LR
        N1["① Universe Scrape\n─── stock_universe ───\n\nYahoo Finance sectors\n4 groups, ~2700 tickers\nPK: (ticker, date_added)"]
        N2["② Batch Prices\n─── daily_prices ───\n\nIncremental (5-day cap)\nnew tickers backfilled\nindividually"]
        N3["③ Rolling Enrich\n─── fundamentals ───\n\n100 tickers/night\noldest first, ~2.5 min"]
        N1 --> N2 --> N3
    end
```

---

## 3. Data Source → DuckDB Table Mapping

```mermaid
flowchart LR
    subgraph SOURCES["External Data Sources"]
        direction TB
        YF["yfinance API\n(prices, fundamentals,\nanalysts, news, estimates)"]
        YAHOO["Yahoo Finance\nweb scrape\n(AI business summary)"]
        GF["Google Finance\nPlaywright scrape\n(AI overview, sentiment)"]
        LLM["Local LLM\nllama.cpp :10000\n(Qwythos-9B)"]
        TA["TradingAgents\nMulti-agent analysis\n(git submodule)"]
    end

    subgraph FILES["Local Files"]
        direction TB
        WL["data/watchlist.json"]
        TR["data/trades.csv"]
        RP["data/analysis_reports/"]
    end

    subgraph DUCKDB["data/market.duckdb (16 tables)"]
        direction TB
        T1[("daily_prices")]
        T2[("news")]
        T3[("global_news")]
        T4[("fundamentals")]
        T5[("financials")]
        T6[("analyst_targets")]
        T7[("ticker_enriched")]
        T8[("news_summaries")]
        T9[("gfinance_overview")]
        T10[("trading_agent_decisions")]
        T11[("screener_scores")]
        T12[("candidates")]
        T13[("events")]
        T14[("portfolio_decisions")]
        T15[("portfolio_reviews")]
        T16[("stock_universe")]
    end

    YF -->|"OHLCV + dividends + splits"| T1
    YF -->|"50 articles per ticker"| T2
    YF -->|"3 market queries × 15 articles"| T3
    YF -->|"Ticker.info (30 fields)"| T4
    YF -->|"Quarterly statements (JSON)"| T5
    YF -->|"Consensus targets + upgrades"| T6
    YF -->|"Growth estimates + recommendations"| T7
    YAHOO -->|"AI business summary"| T7
    GF -->|"Summary + bull/bear points"| T9
    LLM -->|"Per-ticker news digest"| T8
    TA -->|"Decision + trader parsing"| T10
    TR -->|"FIFO holdings"| T14

    WL -->|"Drives --daily, --batch"| T1
    RP -->|"Report file parsing"| T10

    style SOURCES fill:#1a1a2e,stroke:#e94560,color:#fff
    style FILES fill:#16213e,stroke:#0f3460,color:#fff
    style DUCKDB fill:#0f3460,stroke:#533483,color:#fff
```

### How Night (1) and Daily (2) Converge

Night builds **breadth** (2700+ tickers with prices + fundamentals). Daily builds **depth** (news, enriched, technicals for watchlist). They meet at the screener.

```mermaid
flowchart TD
    subgraph NIGHT["🌙 Night Pipeline (3 PM)"]
        direction TB
        NP["daily_prices\n2700+ tickers\n(incremental)"]
        NF["fundamentals\nrolling 100/day\n(Buy/Strong Buy)"]
        NU["stock_universe\nratings + sectors"]
    end

    subgraph DAILY["☀️ Daily Pipeline (8 AM)"]
        direction TB
        DP["daily_prices\nwatchlist\n(+ NZDUSD)"]
        DF["fundamentals\nwatchlist\n(smart-scheduled)"]
        DE["ticker_enriched\nnews, analysts\ngfinance, summaries"]
    end

    subgraph CONVERGE["Convergence Point"]
        direction TB
        SC["⑪ Screener\nscores ALL tickers\nwith fundamentals data"]
        CA["⑫ Candidates\nsector-balanced top-N\ncorrelation-filtered"]
    end

    subgraph ANALYSIS["Analysis Chain"]
        direction TB
        TA["⑬ TradingAgents\nevent-gated\ncandidates + watchlist"]
        PE["⑭ Portfolio Engine\nrules: 20% pos, 35% sector\n10% cash, stop loss"]
        RV["⑮ LLM Review\ninvestment committee"]
    end

    NP -->|"price history\n(momentum, volatility)"| SC
    NF -->|"quality, value metrics\n(ROE, PE, margins)"| SC
    NU -->|"sector + rating\nfor allocation"| CA
    DP -->|"fresh prices"| SC
    DF -->|"fresh fundamentals"| SC
    DE -->|"sentiment, RSI\nearings trends"| SC

    SC --> CA
    CA --> TA
    DE -->|"events gate"| TA
    TA --> PE
    SC --> PE
    PE --> RV
    TA --> RV

    style NIGHT fill:#1a1a2e,stroke:#e94560,color:#fff
    style DAILY fill:#16213e,stroke:#0f3460,color:#fff
    style CONVERGE fill:#0f3460,stroke:#533483,color:#fff
    style ANALYSIS fill:#1a1a2e,stroke:#533483,color:#fff
```

---

## 4. Inter-Table Data Dependencies (Analysis Pipeline)

Shows how DuckDB tables feed into each other through the screening → analysis → portfolio chain.

```mermaid
flowchart LR
    subgraph INGEST["Ingestion Layer"]
        direction TB
        DP[("daily_prices")]
        NU[("news")]
        FU[("fundamentals")]
        FI[("financials")]
        AT[("analyst_targets")]
        TE[("ticker_enriched")]
        GO[("gfinance_overview")]
        NS[("news_summaries")]
    end

    subgraph ANALYSIS["Analysis Layer"]
        direction TB
        SC[("screener_scores")]
        CA[("candidates")]
        EV[("events")]
        TD[("trading_agent_decisions")]
    end

    subgraph PORTFOLIO["Portfolio Layer"]
        direction TB
        PD[("portfolio_decisions")]
        PR[("portfolio_reviews")]
    end

    %% Screener reads from ingestion
    DP --> SC
    TE --> SC
    FU --> SC
    AT --> SC

    %% Events read from ingestion
    DP --> EV
    NU --> EV
    TE --> EV

    %% Candidates read from screener
    SC --> CA

    %% TradingAgents gated by events + candidates
    CA --> TD
    EV --> TD
    FU --> TD
    TE --> TD
    DP --> TD

    %% Portfolio engine reads decisions + scores
    TD --> PD
    SC --> PD

    %% Portfolio review reads decisions + analysis
    PD --> PR
    TD --> PR
    SC --> PR

    style INGEST fill:#16213e,stroke:#0f3460,color:#fff
    style ANALYSIS fill:#1a1a2e,stroke:#533483,color:#fff
    style PORTFOLIO fill:#0f3460,stroke:#e94560,color:#fff
```

---

## 5. Night vs Daily — Side-by-Side Comparison

```mermaid
flowchart LR
    subgraph NIGHT["🌙 Night Pipeline (3 PM NZT)"]
        direction TB
        N1["1. Universe scrape\n(all 4 sector groups)"]
        N2["2. Batch prices\n(~2700 tickers)"]
        N3["3. Enrich fundamentals\n(rolling 100/day)"]
        N1 --> N2 --> N3
    end

    subgraph DAILY["☀️ Daily Pipeline (8 AM NZT)"]
        direction TB
        D1["1. Batch prices\n(watchlist only)"]
        D2["2. Per-ticker:\nnews, analysts, enriched,\nfundamentals, financials"]
        D3["3. Global news"]
        D4["4. Google Finance overviews"]
        D5["5. AI news summaries (LLM)"]
        D6["5b. Rolling enrich (100)"]
        D7["6. Quantitative screener"]
        D8["7. Candidate selection"]
        D9["8. TradingAgents analysis\n(event-gated)"]
        D10["9. Portfolio engine"]
        D11["10. LLM portfolio review"]
        D1 --> D2 --> D3 --> D4 --> D5 --> D6 --> D7 --> D8 --> D9 --> D10 --> D11
    end

    subgraph SHARED["Shared DuckDB Tables"]
        direction TB
        T_DP[("daily_prices")]
        T_FU[("fundamentals")]
        T_TE[("ticker_enriched")]
        T_SC[("screener_scores")]
        T_PD[("portfolio_decisions")]
    end

    N2 --> T_DP
    N3 --> T_FU
    D1 --> T_DP
    D2 --> T_FU
    D2 --> T_TE
    D7 --> T_SC
    D10 --> T_PD

    style NIGHT fill:#1a1a2e,stroke:#e94560,color:#fff
    style DAILY fill:#16213e,stroke:#0f3460,color:#fff
    style SHARED fill:#0f3460,stroke:#533483,color:#fff
```

---

## 6. Rolling Enrichment Detail

How the 100/day limit works across nightly and daily runs.

```mermaid
flowchart TD
    subgraph ELIGIBLE["Eligible Ticker Pool"]
        direction LR
        E1["Watchlist tickers\n(always eligible\nregardless of rating)"]
        E2["stock_universe tickers\nWHERE rating IN\n('Strong Buy', 'Buy')"]
        E1 & E2 --> POOL["Combined eligible set\n(~500-800 tickers)"]
    end

    subgraph PRIORITY["Priority Queue"]
        direction TB
        PQ["LEFT JOIN fundamentals\nORDER BY date_fetched ASC NULLS FIRST"]
        PQ --> P1["Priority 1: Never loaded\n(NULL date_fetched)"]
        PQ --> P2["Priority 2: Oldest loaded\n(earliest date_fetched)"]
    end

    subgraph EXECUTION["Execution"]
        direction TB
        EX["Take top 100 from queue"]
        EX --> LOOP["For each ticker:\n  ingest_fundamentals(ticker)\n  sleep(1.5s)"]
        LOOP --> CHECKPOINT["Progress log every 50 tickers"]
    end

    subgraph STEADY["Steady-State Behavior"]
        direction TB
        SS1["Initial backlog:\n~800 tickers / 100 per day\n= ~8 days to clear"]
        SS2["After backlog clears:\n100 stalest refreshed daily\n= full rotation every ~8 days"]
        SS3["Estimated runtime:\n~2.5 minutes per run"]
        SS1 --> SS2 --> SS3
    end

    POOL --> PRIORITY
    PRIORITY --> EXECUTION
    EXECUTION --> STEADY

    style ELIGIBLE fill:#1a1a2e,stroke:#e94560,color:#fff
    style PRIORITY fill:#16213e,stroke:#0f3460,color:#fff
    style EXECUTION fill:#1a1a2e,stroke:#533483,color:#fff
    style STEADY fill:#0f3460,stroke:#533483,color:#fff
```
