# Data Flow Diagrams

Mermaid diagrams illustrating the daily and nightly pipeline data flows.

---

### 1. Daily Pipeline (`--daily` / `--daily-smart`) — 8:00 AM NZT

**Tickers:** Step ① uses full `stock_universe`; steps ②–④ use `data/watchlist.json`; step ⑧ analyzes candidates + watchlist (event-gated)

```mermaid
flowchart LR
    subgraph ROW1["Per-ticker loop (steps ②–④)"]
        direction LR
        S1["① Batch Prices (universe)\n─── daily_prices ───\n~2700 tickers, incremental"]
        S2["② News\n─── news ───"]
        S3["③ Analyst Targets\n─── analyst_targets ───"]
        S4["④ Enriched\n─── ticker_enriched ───"]
        S1 --> S2 --> S3 --> S4
    end

    subgraph ROW2[" "]
        direction LR
        S5["⑤ Global News\n─── global_news ───"]
        S6["⑥ Google Finance\n─── gfinance_overview ───"]
        S6b["⑥b Yahoo Finance\n─── yfinance_overview ───"]
        S7["⑦ AI Summaries\n─── news_summaries ───"]
        S5 --> S6 --> S6b --> S7
    end

    subgraph ROW3[" "]
        direction LR
        S8["⑧ Screener\n─── screener_scores ───"]
        S9["⑨ Candidates\n─── candidates ───"]
        S10["⑩ TradingAgents\n─── trading_agent_decisions ───\nevent-gated, candidates+watchlist"]
        S11["⑪ Portfolio Engine\n─── portfolio_decisions ───"]
        S12["⑫ LLM Review\n─── portfolio_reviews ───"]
        S8 --> S9 --> S10 --> S11 --> S12
    end

    S4 --> S5
    S7 --> S8
```

### Smart Scheduling Staleness Thresholds

| Table | Date Column | Max Age | Refresh Rule |
|-------|------------|---------|--------------|
| `news` | `date` | 1 day | Changes daily |
| `ticker_enriched` | `date_fetched` | 3 days | Slow-changing estimates |
| `analyst_targets` | `date_fetched` | 3 days | Overlaps enrichment cycle |
| `financials` | `report_date` | report_date + 80 days | Quarterly (night pipeline) |

---

### 2. Night Pipeline (`--night`) — 3:00 PM NZT (automated in bot)

**Tickers:** Full `stock_universe` for scrape; watchlist for financials; enrich gated by rating (Buy/Strong Buy) + watchlist

```mermaid
flowchart LR
    subgraph NIGHT["🌙 Night Pipeline — 3:00 PM NZT (in-bot)"]
        direction LR
        N1["① Universe Scrape\n─── stock_universe ───\n\nYahoo Finance sectors\n4 groups, ~2700 tickers\nPK: (ticker, date_added)"]
        N2["② Watchlist Financials\n─── financials ───\n\n80-day cycle\nquarterly statements"]
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

    subgraph DUCKDB["data/market.duckdb (17 tables)"]
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
        T9b[("yfinance_overview")]
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
    YAHOO -->|"AI business summary"| T9b
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
    subgraph NIGHT["🌙 Night Pipeline (3 PM, in-bot)"]
        direction TB
        NU["stock_universe\nratings + sectors"]
        NF["financials\nwatchlist\n(80-day cycle)"]
        NE["fundamentals\nrolling 100/day\n(Buy/Strong Buy)"]
    end

    subgraph DAILY["☀️ Daily Pipeline (8 AM)"]
        direction TB
        DP["daily_prices\nuniverse (~2700)\n(incremental)"]
        DE["ticker_enriched\nnews, analysts\ngfinance"]
        YO["yfinance_overview\nAI summary"]
    end

    subgraph CONVERGE["Convergence Point"]
        direction TB
        SC["⑧ Screener\nscores ALL tickers\nwith fundamentals data"]
        CA["⑨ Candidates\nsector-balanced top-N\ncorrelation-filtered"]
    end

    subgraph ANALYSIS["Analysis Chain"]
        direction TB
        TA["⑩ TradingAgents\nevent-gated\ncandidates + watchlist"]
        PE["⑪ Portfolio Engine\nrules: 20% pos, 35% sector\n10% cash, stop loss"]
        RV["⑫ LLM Review\ninvestment committee"]
    end

    NU --> |"sector + rating\nfor allocation"| CA
    NF --> |"quality, value metrics\n(ROE, PE, margins)"| SC
    NE --> |"fundamentals refresh"| SC
    DP --> |"price history\n(momentum, volatility)"| SC
    DE --> |"sentiment, RSI\nearings trends"| SC

    SC --> CA
    CA --> TA
    DE --> |"events gate"| TA
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
        YO[("yfinance_overview")]
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
    subgraph NIGHT["🌙 Night Pipeline (3 PM NZT, in-bot)"]
        direction TB
        N1["1. Universe scrape\n(all 4 sector groups)"]
        N2["2. Watchlist financials\n(80-day quarterly cycle)"]
        N3["3. Enrich fundamentals\n(rolling 100/day)"]
        N1 --> N2 --> N3
    end

    subgraph DAILY["☀️ Daily Pipeline (8 AM NZT)"]
        direction TB
        D1["1. Batch prices\n(universe ~2700)"]
        D2["2. Per-ticker:\nnews, analysts, enriched"]
        D3["3. Global news"]
        D4["4. Google Finance overviews"]
        D4b["4b. Yahoo Finance overviews"]
        D5["5. AI news summaries (LLM)"]
        D6["6. Quantitative screener"]
        D7["7. Candidate selection"]
        D8["8. TradingAgents analysis\n(event-gated)"]
        D9["9. Portfolio engine"]
        D10["10. LLM portfolio review"]
        D1 --> D2 --> D3 --> D4 --> D4b --> D5 --> D6 --> D7 --> D8 --> D9 --> D10
    end

    subgraph SHARED["Shared DuckDB Tables"]
        direction TB
        T_DP[("daily_prices")]
        T_FU[("fundamentals")]
        T_FI[("financials")]
        T_TE[("ticker_enriched")]
        T_SC[("screener_scores")]
        T_PD[("portfolio_decisions")]
    end

    N2 --> T_FI
    N3 --> T_FU
    D1 --> T_DP
    D2 --> T_TE
    D6 --> T_SC
    D9 --> T_PD

    style NIGHT fill:#1a1a2e,stroke:#e94560,color:#fff
    style DAILY fill:#16213e,stroke:#0f3460,color:#fff
    style SHARED fill:#0f3460,stroke:#533483,color:#fff
```

---

## 6. Rolling Enrichment Detail

How the 100/day limit works in the nightly pipeline.

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
