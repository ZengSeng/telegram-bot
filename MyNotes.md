# bot.py
Daily motivation check-in bot.
Sends a reminder every day at a fixed time, accepts a voice-note reply,
transcribes it with whisper.cpp, and logs it to a local JSONL file.

# voice_logger_bot.py

Run:  python voice_logger_bot.py
Stop: Ctrl+C

## Code structure

| File | Role |
|------|------|
| `voice_logger_bot.py` | Entry point — wires handlers + starts bot |
| `stock_bot/config.py` | All constants, paths, prompts, logging setup |
| `stock_bot/llm.py` | Llama-server lifecycle, whisper transcription, AI requests |
| `stock_bot/trades.py` | CSV read/write, duplicate detection, watchlist |
| `stock_bot/portfolio.py` | FIFO matching, yfinance prices, summary builder |
| `stock_bot/handlers.py` | All Telegram command/message handlers |

## Running the AI separately

You can run llama-server in its own terminal (bot will detect it and skip startup):
```
llama-server -hf empero-ai/Qwythos-9B-v2-GGUF:Q8_0 --alias MyQwythos --port 10000
```

## What it does

- **Voice note** → transcribes with whisper.cpp → sends to local AI → replies with answer
- **Text message** → sends to local AI → replies with answer
- **Photo of a trade** (e.g. brokerage screenshot) → AI extracts trade data → saves to `data/trades.csv`
  - Automatically detects duplicate photos (same stock + date + amount) and skips them
- **Daily at 9 AM** → sends you a portfolio summary automatically

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/start` | Register this chat (needed for daily summaries) |
| `/system <prompt>` | Set/change the AI's system prompt |
| `/watch <TICKER>` | Add a stock ticker to monitor (e.g. `/watch AAPL`) |
| `/unwatch <TICKER>` | Remove a ticker from the watchlist |
| `/summary` | Show portfolio summary now (price, avg cost, shares, % change, totals) |
| `/gains` | Show detailed realized & unrealized P&L per stock (FIFO matching) |

## Data files

| File | Purpose |
|------|---------|
| `data/trades.csv` | All logged trades (from photos) |
| `data/watchlist.json` | Tickers being monitored (default: RKLB) |
| `data/voice_log.jsonl` | Log of all voice/text interactions |
| `data/audio/` | Saved voice notes + transcripts |
| `data/images/` | Saved trade photos |

## Quick workflow

1. Run `python voice_logger_bot.py`
2. Send `/start` in Telegram
3. Snap a screenshot of your trade confirmation → send as photo
4. Bot extracts the data and confirms it saved
5. Use `/summary` or `/gains` anytime to check your portfolio
6. Add more tickers with `/watch NVDA` etc.