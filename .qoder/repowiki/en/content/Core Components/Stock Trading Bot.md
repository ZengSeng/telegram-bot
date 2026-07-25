# Stock Trading Bot

<cite>
**Referenced Files in This Document**
- [bot.py](file://bot.py)
- [requirements.txt](file://requirements.txt)
- [SETUP.md](file://SETUP.md)
- [MyNotes.md](file://MyNotes.md)
- [stock_bot/__init__.py](file://stock_bot/__init__.py)
- [stock_bot/config.py](file://stock_bot/config.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [data_eng/__init__.py](file://data_eng/__init__.py)
- [data_eng/__main__.py](file://data_eng/__main__.py)
- [data_eng/db.py](file://data_eng/db.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [analysis/__init__.py](file://analysis/__init__.py)
- [analysis/duckdb_vendor.py](file://analysis/duckdb_vendor.py)
- [analysis/runner.py](file://analysis/runner.py)
- [voice_logger_bot.py](file://voice_logger_bot.py)
- [test.py](file://test.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document provides a comprehensive overview and technical deep dive into the Stock Trading Bot codebase. It explains the system architecture, core modules, data flows, integration points, and operational considerations. The goal is to make the project understandable for both technical and non-technical readers while providing actionable guidance for setup, usage, and maintenance.

## Project Structure
The repository is organized into feature-oriented packages:
- stock_bot: Telegram bot orchestration, configuration, handlers, LLM integration, portfolio management, and trade execution logic.
- data_eng: Data ingestion and database utilities for market data and trading records.
- analysis: Analytical tools and DuckDB vendor abstraction for querying and backtesting.
- Root-level files include the main bot entrypoint, dependencies, and documentation.

```mermaid
graph TB
subgraph "Root"
BOT["bot.py"]
REQ["requirements.txt"]
SETUP["SETUP.md"]
NOTES["MyNotes.md"]
TEST["test.py"]
VOICE["voice_logger_bot.py"]
end
subgraph "stock_bot"
SB_INIT["__init__.py"]
CFG["config.py"]
HND["handlers.py"]
LLM["llm.py"]
PORTFOLIO["portfolio.py"]
TRADES["trades.py"]
end
subgraph "data_eng"
DE_INIT["__init__.py"]
DE_MAIN["__main__.py"]
DB["db.py"]
INGEST["ingest.py"]
end
subgraph "analysis"
AN_INIT["__init__.py"]
DUCK["duckdb_vendor.py"]
RUN["runner.py"]
end
BOT --> SB_INIT
BOT --> HND
BOT --> CFG
BOT --> LLM
BOT --> PORTFOLIO
BOT --> TRADES
HND --> PORTFOLIO
HND --> TRADES
HND --> LLM
HND --> CFG
PORTFOLIO --> DB
TRADES --> DB
DE_MAIN --> INGEST
DE_MAIN --> DB
RUN --> DUCK
RUN --> DB
```

**Diagram sources**
- [bot.py](file://bot.py)
- [stock_bot/__init__.py](file://stock_bot/__init__.py)
- [stock_bot/config.py](file://stock_bot/config.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [data_eng/__main__.py](file://data_eng/__main__.py)
- [data_eng/db.py](file://data_eng/db.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [analysis/runner.py](file://analysis/runner.py)
- [analysis/duckdb_vendor.py](file://analysis/duckdb_vendor.py)

**Section sources**
- [bot.py](file://bot.py)
- [requirements.txt](file://requirements.txt)
- [SETUP.md](file://SETUP.md)
- [MyNotes.md](file://MyNotes.md)

## Core Components
- Bot Orchestrator (bot.py): Entry point that initializes the Telegram bot, registers command and message handlers, and starts polling or long-polling.
- Configuration (stock_bot/config.py): Centralized settings for API keys, database paths, and runtime flags.
- Handlers (stock_bot/handlers.py): Command routing and message processing for user interactions via Telegram.
- LLM Integration (stock_bot/llm.py): Abstraction for calling language models to generate insights or responses.
- Portfolio Management (stock_bot/portfolio.py): Tracks holdings, positions, and performance metrics.
- Trade Execution (stock_bot/trades.py): Encapsulates order placement, validation, and trade logging.
- Data Engineering (data_eng/*): Ingests market data and persists it using a local database.
- Analysis (analysis/*): Provides analytical queries and backtesting capabilities with DuckDB.

**Section sources**
- [bot.py](file://bot.py)
- [stock_bot/config.py](file://stock_bot/config.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [data_eng/__main__.py](file://data_eng/__main__.py)
- [data_eng/db.py](file://data_eng/db.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [analysis/runner.py](file://analysis/runner.py)
- [analysis/duckdb_vendor.py](file://analysis/duckdb_vendor.py)

## Architecture Overview
The system follows a modular design where the Telegram bot acts as the user interface layer. Handlers route commands to business logic modules (portfolio and trades), which interact with the database layer. LLM integration supports natural language insights. Data engineering pipelines ingest market data into the database, and the analysis module runs queries and backtests over stored data.

```mermaid
sequenceDiagram
participant User as "Telegram User"
participant Bot as "bot.py"
participant Handlers as "stock_bot/handlers.py"
participant Portfolio as "stock_bot/portfolio.py"
participant Trades as "stock_bot/trades.py"
participant LLM as "stock_bot/llm.py"
participant DB as "data_eng/db.py"
User->>Bot : Send command/message
Bot->>Handlers : Route to appropriate handler
alt Portfolio query
Handlers->>Portfolio : Fetch holdings/performance
Portfolio->>DB : Query positions and history
DB-->>Portfolio : Data rows
Portfolio-->>Handlers : Aggregated results
else Trade action
Handlers->>Trades : Validate and place order
Trades->>DB : Persist trade record
DB-->>Trades : Ack
Trades-->>Handlers : Confirmation
else LLM insight
Handlers->>LLM : Generate response
LLM-->>Handlers : Insight text
end
Handlers-->>Bot : Formatted reply
Bot-->>User : Telegram response
```

**Diagram sources**
- [bot.py](file://bot.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [data_eng/db.py](file://data_eng/db.py)

## Detailed Component Analysis

### Bot Orchestrator (bot.py)
Responsibilities:
- Initialize bot instance and configure error handling.
- Register command handlers and message callbacks.
- Start the polling loop to receive updates from Telegram.

Key behaviors:
- Graceful shutdown on signals.
- Logging and diagnostics for incoming messages and errors.

**Section sources**
- [bot.py](file://bot.py)

### Configuration (stock_bot/config.py)
Responsibilities:
- Load environment variables and defaults.
- Provide typed accessors for API keys, database paths, and toggles.

Design notes:
- Centralizes secrets and runtime options to avoid scattering across modules.
- Validates critical values at startup.

**Section sources**
- [stock_bot/config.py](file://stock_bot/config.py)

### Handlers (stock_bot/handlers.py)
Responsibilities:
- Parse Telegram commands and messages.
- Dispatch to portfolio queries, trade actions, or LLM prompts.
- Format responses for readability and safety.

Error handling:
- Catches invalid inputs and returns helpful messages.
- Logs unexpected exceptions without crashing the bot.

**Section sources**
- [stock_bot/handlers.py](file://stock_bot/handlers.py)

### LLM Integration (stock_bot/llm.py)
Responsibilities:
- Abstract calls to external language model APIs.
- Manage prompts, retries, and rate limiting.
- Return structured or natural language outputs.

Security:
- Ensures sensitive tokens are not logged.
- Sanitizes prompts to prevent injection.

**Section sources**
- [stock_bot/llm.py](file://stock_bot/llm.py)

### Portfolio Management (stock_bot/portfolio.py)
Responsibilities:
- Track current holdings, cost basis, and unrealized PnL.
- Aggregate historical performance metrics.
- Interface with the database for persistence and retrieval.

Data flow:
- Queries positions and trade history.
- Computes summaries and exposes them to handlers.

**Section sources**
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [data_eng/db.py](file://data_eng/db.py)

### Trade Execution (stock_bot/trades.py)
Responsibilities:
- Validate orders against portfolio constraints and risk rules.
- Place orders through configured brokers or simulators.
- Record trades and update portfolio state.

Validation:
- Checks available cash, position limits, and symbol validity.
- Prevents duplicate or malformed orders.

**Section sources**
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [data_eng/db.py](file://data_eng/db.py)

### Data Engineering (data_eng/*)
Responsibilities:
- Ingest market data from sources and normalize schemas.
- Persist data into a local database optimized for analytics.
- Provide CLI entrypoints for running ingestion jobs.

Key modules:
- db.py: Database connection, schema definitions, and utility functions.
- ingest.py: Data extraction, transformation, and loading routines.
- __main__.py: CLI interface to trigger ingestion workflows.

**Section sources**
- [data_eng/db.py](file://data_eng/db.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [data_eng/__main__.py](file://data_eng/__main__.py)

### Analysis (analysis/*)
Responsibilities:
- Provide analytical queries and backtesting scripts.
- Use DuckDB for fast in-process analytics.
- Vendor abstraction to switch or extend data engines.

Key modules:
- duckdb_vendor.py: DuckDB-specific implementation and helpers.
- runner.py: Orchestration of analysis tasks and result reporting.

**Section sources**
- [analysis/duckdb_vendor.py](file://analysis/duckdb_vendor.py)
- [analysis/runner.py](file://analysis/runner.py)

### Voice Logger Bot (voice_logger_bot.py)
Purpose:
- Experimental voice logging functionality separate from the main trading bot.
- Captures and stores voice messages for later processing.

Usage:
- Standalone script; not integrated into the primary bot pipeline.

**Section sources**
- [voice_logger_bot.py](file://voice_logger_bot.py)

### Test Harness (test.py)
Purpose:
- Unit or integration tests for selected components.
- Validates basic functionality and edge cases.

Scope:
- Focused on specific modules rather than full end-to-end flows.

**Section sources**
- [test.py](file://test.py)

## Dependency Analysis
The bot depends on several internal modules and external libraries. Dependencies are declared in requirements.txt and imported throughout the codebase.

```mermaid
graph LR
BOT["bot.py"] --> HANDLERS["stock_bot/handlers.py"]
BOT --> CFG["stock_bot/config.py"]
BOT --> LLM["stock_bot/llm.py"]
BOT --> PORTFOLIO["stock_bot/portfolio.py"]
BOT --> TRADES["stock_bot/trades.py"]
HANDLERS --> PORTFOLIO
HANDLERS --> TRADES
HANDLERS --> LLM
HANDLERS --> CFG
PORTFOLIO --> DB["data_eng/db.py"]
TRADES --> DB
DATA_MAIN["data_eng/__main__.py"] --> INGEST["data_eng/ingest.py"]
DATA_MAIN --> DB
ANALYSIS_RUNNER["analysis/runner.py"] --> DUCK["analysis/duckdb_vendor.py"]
ANALYSIS_RUNNER --> DB
```

**Diagram sources**
- [bot.py](file://bot.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [stock_bot/config.py](file://stock_bot/config.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [stock_bot/portfolio.py](file://stock_bot/portfolio.py)
- [stock_bot/trades.py](file://stock_bot/trades.py)
- [data_eng/__main__.py](file://data_eng/__main__.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [data_eng/db.py](file://data_eng/db.py)
- [analysis/runner.py](file://analysis/runner.py)
- [analysis/duckdb_vendor.py](file://analysis/duckdb_vendor.py)

**Section sources**
- [requirements.txt](file://requirements.txt)

## Performance Considerations
- Database choice: DuckDB enables fast analytical queries; ensure proper indexing and partitioning for large datasets.
- Polling strategy: Use efficient polling intervals and batch processing to reduce overhead.
- LLM calls: Implement caching and rate limiting to minimize latency and costs.
- Memory usage: Stream large datasets instead of loading entirely into memory.
- Concurrency: Avoid blocking operations in handlers; offload heavy tasks to background workers if needed.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Missing environment variables: Ensure all required keys and paths are set before starting the bot.
- Database connectivity: Verify file paths and permissions for local databases.
- LLM API failures: Check network connectivity, quotas, and retry policies.
- Handler errors: Inspect logs for malformed commands or unexpected payloads.
- Ingestion failures: Validate source formats and schema compatibility.

Operational tips:
- Enable verbose logging during development.
- Use test.py to validate component behavior in isolation.
- Keep dependencies updated and pinned to known-good versions.

**Section sources**
- [stock_bot/config.py](file://stock_bot/config.py)
- [data_eng/db.py](file://data_eng/db.py)
- [stock_bot/llm.py](file://stock_bot/llm.py)
- [stock_bot/handlers.py](file://stock_bot/handlers.py)
- [data_eng/ingest.py](file://data_eng/ingest.py)
- [test.py](file://test.py)

## Conclusion
The Stock Trading Bot is a modular system combining Telegram interaction, portfolio and trade management, LLM-powered insights, and robust data engineering and analysis capabilities. Its clear separation of concerns facilitates maintenance and extension. By following the setup instructions and adhering to best practices outlined here, users can operate and evolve the system effectively.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices
- Setup Instructions: Refer to SETUP.md for environment preparation and configuration steps.
- Notes and Ideas: See MyNotes.md for additional context and future enhancements.
- Dependencies: Review requirements.txt for library versions and installation commands.

**Section sources**
- [SETUP.md](file://SETUP.md)
- [MyNotes.md](file://MyNotes.md)
- [requirements.txt](file://requirements.txt)