---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### trade confirmation
- Definition：A screenshot or photo of a brokerage trade receipt that the bot processes via its vision AI to extract structured trade data (stock, shares, price, fees, amounts) into the trades CSV.
- Aliases：trade photo、trade image

### watchlist
- Definition：A user-managed list of stock tickers (default ['RKLB']) stored in `data/watchlist.json` against which the bot fetches live prices for portfolio summaries and P&L calculations.
- Aliases：ticker watchlist、monitored tickers

### FIFO matching
- Definition：First-In-First-Out lot accounting used to match sell transactions against the earliest buy lots for realized gain calculation. Buys are sorted by order date and sells consume lots in chronological order.
- Aliases：FIFO lot accounting、first-in-first-out

### portfolio summary
- Definition：A per-stock report showing daily price, average cost basis, shares held, market value, and percentage change, plus totals for invested amount, market value, and unrealized P&L. Generated on demand via `/summary` and automatically at 9:00 AM.
- Aliases：daily summary、portfolio report

### realized/unrealized gains
- Definition：P&L breakdown where realized gains come from FIFO-matched sell/buy pairs and unrealized gains reflect open positions valued at current market price. Accessed via the `/gains` command.
- Aliases：P&L report、gains breakdown

### chat ID
- Definition：The Telegram user's numeric chat identifier saved after `/start`, used to send scheduled daily portfolio summaries back to the correct recipient.
- Aliases：user chat id、effective_chat.id
