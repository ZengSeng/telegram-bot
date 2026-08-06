"""Telegram bot handlers: commands, voice, text, and photo processing."""

import datetime as dt
import json
import subprocess

from telegram import Update
from telegram.ext import ContextTypes

from . import llm
from .config import (
    AUDIO_DIR,
    CHAT_ID_FILE,
    DEFAULT_SYSTEM_PROMPT,
    IMAGES_DIR,
    SYSTEM_PROMPT_FILE,
    TRADE_EXTRACTION_PROMPT,
    log,
)
from .portfolio import (
    build_portfolio_summary,
    compute_fifo_details,
    extract_ticker,
    generate_price_chart,
    get_chart_tickers,
    get_current_prices,
)
from .trades import (
    append_trade,
    is_duplicate,
    load_watchlist,
    read_trades,
    save_watchlist,
)


# ---------------------------------------------------------------------------
# Chat ID persistence (for scheduled messages) — supports multiple users
# ---------------------------------------------------------------------------

def load_chat_ids() -> list[int]:
    if CHAT_ID_FILE.exists():
        ids = []
        for line in CHAT_ID_FILE.read_text().strip().splitlines():
            line = line.strip()
            if line:
                try:
                    ids.append(int(line))
                except ValueError:
                    pass
        return ids
    return []


def save_chat_id(chat_id: int) -> None:
    """Add a chat_id if not already registered."""
    existing = load_chat_ids()
    if chat_id not in existing:
        existing.append(chat_id)
        CHAT_ID_FILE.write_text("\n".join(str(i) for i in existing))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "Send me a voice note or text and I'll pass it to the AI.\n"
        "Send a photo of a trade confirmation and I'll log it.\n\n"
        "Commands:\n"
        "/system <prompt> - set system prompt\n"
        "/watch <TICKER> - add ticker to watchlist\n"
        "/unwatch <TICKER> - remove ticker\n"
        "/portfolio - portfolio summary\n"
        "/gains - realized/unrealized P&L\n"
        "/analyze <TICKER> - run trading agent analysis\n"
        "/summary [TICKER] - trading agent decisions\n"
        "/news - AI news summaries (from pipeline)"
    )


async def set_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    new_prompt = " ".join(context.args).strip()
    if not new_prompt:
        await update.message.reply_text(f"Current system prompt:\n\"{llm.system_prompt}\"")
        return
    llm.system_prompt = new_prompt
    SYSTEM_PROMPT_FILE.write_text(llm.system_prompt)
    await update.message.reply_text("System prompt updated.")


async def watch_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        watchlist = load_watchlist()
        await update.message.reply_text(f"Current watchlist: {', '.join(watchlist)}")
        return

    ticker = context.args[0].upper()
    watchlist = load_watchlist()
    if ticker in watchlist:
        await update.message.reply_text(f"{ticker} is already on the watchlist.")
        return
    watchlist.append(ticker)
    save_watchlist(watchlist)
    await update.message.reply_text(
        f"Added {ticker} to watchlist: {', '.join(watchlist)}\n"
        f"Starting full data ingestion for {ticker} (background)..."
    )

    # Trigger full ingestion in background; log + notify on failure so a
    # crash doesn't silently leave the user believing ingestion succeeded.
    import asyncio
    from data_eng.ingest import ingest_all

    async def _run_ingestion():
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, ingest_all, ticker)
        except Exception as e:
            log.error("Background ingestion failed for %s: %s", ticker, e)
            try:
                await update.message.reply_text(
                    f"Ingestion for {ticker} failed: {e}\n"
                    "It is on the watchlist and will be picked up by the pipeline."
                )
            except Exception:
                pass

    asyncio.ensure_future(_run_ingestion())


async def unwatch_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /unwatch <TICKER>")
        return

    ticker = context.args[0].upper()
    watchlist = load_watchlist()
    if ticker not in watchlist:
        await update.message.reply_text(f"{ticker} is not on the watchlist.")
        return
    watchlist.remove(ticker)
    save_watchlist(watchlist)
    await update.message.reply_text(f"Removed {ticker}. Watchlist: {', '.join(watchlist) if watchlist else '(empty)'}")


async def charts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send 90-day price charts for all tracked tickers."""
    await update.message.reply_text("Generating charts...")
    tickers = get_chart_tickers()
    for ticker in tickers:
        chart = generate_price_chart(ticker)
        if chart:
            await update.message.reply_photo(photo=chart)


async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Fetching prices & charts...")

    # Send charts first (watchlist-only tickers appear at top)
    tickers = get_chart_tickers()
    for ticker in tickers:
        chart = generate_price_chart(ticker)
        if chart:
            await update.message.reply_photo(photo=chart)

    # Send portfolio summary text last (visible immediately)
    msg = build_portfolio_summary()
    await update.message.reply_text(msg, parse_mode="HTML")


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled daily portfolio summary — sent to all registered users."""
    chat_ids = load_chat_ids()
    if not chat_ids:
        log.warning("No chat_ids saved — no user has run /start")
        return

    tickers = get_chart_tickers()
    msg = build_portfolio_summary()

    for chat_id in chat_ids:
        try:
            for ticker in tickers:
                chart = generate_price_chart(ticker)
                if chart:
                    await context.bot.send_photo(chat_id=chat_id, photo=chart)
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        except Exception as e:
            log.warning("Failed to send summary to %s: %s", chat_id, e)


async def run_pipeline_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled daily data pipeline — refresh DuckDB for all watchlist tickers."""
    import asyncio

    from data_eng.pipeline import run_daily_pipeline

    watchlist = load_watchlist()
    if not watchlist:
        log.warning("Pipeline job: watchlist empty, skipping.")
        return

    log.info("Pipeline job started for: %s", watchlist)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_daily_pipeline, watchlist)
    log.info("Pipeline job finished.")


async def run_night_pipeline_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled night pipeline — universe + financials + bulk enrichment + TradingAgents batch."""
    import asyncio

    from data_eng.pipeline import run_night_pipeline

    watchlist = load_watchlist()
    log.info("Night pipeline job started.")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_night_pipeline, watchlist)
    log.info("Night pipeline job finished.")

    for chat_id in load_chat_ids():
        try:
            await context.bot.send_message(chat_id=chat_id, text="Used Matthew's 10 cents")
        except Exception as e:
            log.warning("Failed to send night pipeline notice to %s: %s", chat_id, e)


# ---------------------------------------------------------------------------
# Trading agent commands
# ---------------------------------------------------------------------------


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run TradingAgents analysis for a ticker and store decision in DuckDB."""
    if not context.args:
        await update.message.reply_text("Usage: /analyze <TICKER>")
        return

    ticker = context.args[0].upper()
    await update.message.reply_text(f"Running analysis for {ticker}... (1-3 min)")

    try:
        import asyncio

        from analysis.runner import run_analysis
        from data_eng.analysis_ingest import ingest_analysis_decision

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_analysis, ticker)
        await loop.run_in_executor(None, ingest_analysis_decision, ticker)

        await update.message.reply_text(f"Done. Use /summary {ticker} to view the decision.")
    except Exception as e:
        log.error("Analysis failed for %s: %s", ticker, e)
        await update.message.reply_text(f"Analysis failed for {ticker}: {e}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show latest TradingAgents decisions from DuckDB."""
    from data_eng.db import get_connection

    conn = get_connection()

    if context.args:
        ticker = context.args[0].upper()
        # Latest decision regardless of date — the night pipeline only
        # re-analyzes on events/staleness, so "today" is often empty.
        rows = conn.execute(
            """SELECT ticker, date, action, rating, price_target, entry_price,
                      stop_loss, time_horizon, summary
               FROM trading_agent_decisions
               WHERE ticker = ?
               ORDER BY date DESC
               LIMIT 1""",
            [ticker],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ticker, date, action, rating, price_target, entry_price,
                      stop_loss, time_horizon, summary
               FROM trading_agent_decisions
               WHERE date = CURRENT_DATE
               ORDER BY ticker"""
        ).fetchall()

    conn.close()

    if not rows:
        if context.args:
            await update.message.reply_text(
                f"No trading agent decisions found for {context.args[0].upper()}.\n"
                f"Run /analyze {context.args[0].upper()} or wait for the night pipeline."
            )
        else:
            await update.message.reply_text(
                "No trading agent decisions found for today.\n"
                "Decisions are generated by the night pipeline.\n"
                "Tip: /summary TICKER shows a ticker's latest decision."
            )
        return

    for row in rows:
        ticker, dec_date, action, rating, pt, entry, stop, horizon, summary = row
        lines = [f"\U0001f916 {ticker} ({dec_date})"]
        if action:
            lines.append(f"Action: {action}")
        if rating:
            lines.append(f"Rating: {rating}")
        if pt:
            lines.append(f"Price Target: ${pt:.2f}")
        if entry:
            lines.append(f"Entry: ${entry:.2f}")
        if stop:
            lines.append(f"Stop Loss: ${stop:.2f}")
        if horizon:
            lines.append(f"Horizon: {horizon}")
        if summary:
            lines.append(f"\n{summary}")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# News command
# ---------------------------------------------------------------------------


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch stored AI news summaries from DuckDB (generated by daily pipeline)."""
    from data_eng.db import get_connection

    conn = get_connection()

    if context.args:
        ticker = context.args[0].upper()
        rows = conn.execute(
            """SELECT ticker, date, summary FROM news_summaries
               WHERE ticker = ? AND date = CURRENT_DATE""",
            [ticker],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT ticker, date, summary FROM news_summaries
               WHERE date = CURRENT_DATE
               ORDER BY ticker"""
        ).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "No news summaries found for today.\n"
            "Summaries are generated at the end of the daily pipeline.\n"
            "Run: python -m data_eng --daily"
        )
        return

    await update.message.reply_text(f"News summaries ({len(rows)} ticker-date entries):")
    for ticker, summary_date, summary in rows:
        text = f"📰 {ticker} ({summary_date})\n\n{summary}"
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        await update.message.reply_text(text)


async def gains_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trades = read_trades()
    if not trades:
        await update.message.reply_text("No trades recorded yet.")
        return

    watchlist = load_watchlist()
    prices = get_current_prices(watchlist)

    all_tickers = set()
    for trade in trades:
        t = extract_ticker(trade.get("stock", ""))
        if t:
            all_tickers.add(t)

    lines = ["Realized / Unrealized Gains", ""]

    for ticker in sorted(all_tickers):
        details = compute_fifo_details(trades, ticker)
        current_price = prices.get(ticker)

        lines.append(f"{ticker}")

        if details["matched"]:
            lines.append("  Realized (FIFO matched):")
            for m in details["matched"]:
                gain_sign = "+" if m["gain"] >= 0 else "-"
                lines.append(
                    f"    Sold {m['shares']:.0f} @ ${m['sell_price']:.2f} "
                    f"(bought @ ${m['buy_price']:.2f}) -> {gain_sign}${abs(m['gain']):.2f}"
                )
            total_sign = "+" if details["realized_total"] >= 0 else "-"
            lines.append(f"  Total Realized: {total_sign}${abs(details['realized_total']):.2f}")
        else:
            lines.append("  Realized: No sells yet")

        if details["open_lots"]:
            total_open_shares = sum(l["shares"] for l in details["open_lots"])
            total_open_cost = sum(l["shares"] * l["price"] for l in details["open_lots"])
            avg_open = total_open_cost / total_open_shares if total_open_shares > 0 else 0

            if current_price:
                unrealized = total_open_shares * (current_price - avg_open)
                unrealized_pct = ((current_price - avg_open) / avg_open * 100) if avg_open > 0 else 0
                u_sign = "+" if unrealized >= 0 else "-"
                lines.append(
                    f"  Unrealized: {total_open_shares:.0f} shares @ avg ${avg_open:.2f}, "
                    f"current ${current_price:.2f} -> {u_sign}${abs(unrealized):.2f} "
                    f"({u_sign}{abs(unrealized_pct):.1f}%)"
                )
            else:
                lines.append(
                    f"  Unrealized: {total_open_shares:.0f} shares @ avg ${avg_open:.2f} (price unavailable)"
                )
        else:
            lines.append("  Unrealized: Position fully closed")

        lines.append("")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    if voice is None:
        return

    file = await context.bot.get_file(voice.file_id)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ogg_path = AUDIO_DIR / f"{stamp}.ogg"
    wav_path = AUDIO_DIR / f"{stamp}.wav"

    await file.download_to_drive(custom_path=str(ogg_path))

    convert = subprocess.run(
        ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", str(wav_path)],
        capture_output=True,
        text=True,
    )
    if convert.returncode != 0:
        log.error("ffmpeg failed: %s", convert.stderr)
        await update.message.reply_text("Couldn't convert the audio, sorry.")
        return

    transcript = llm.transcribe(wav_path)
    if not transcript:
        await update.message.reply_text("Couldn't transcribe that — try again?")
        return

    reply = llm.ask_llm(transcript)
    if reply is None:
        await update.message.reply_text("Sorry, the AI server isn't responding right now.")
        return
    llm.log_entry("voice", transcript, reply, ogg_path.name)
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if not user_text:
        return

    reply = llm.ask_llm(user_text)
    if reply is None:
        await update.message.reply_text("Sorry, the AI server isn't responding right now.")
        return
    llm.log_entry("text", user_text, reply)
    await update.message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photo: extract trade data via vision AI and save to CSV."""
    await update.message.reply_text("Processing trade image...")

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = IMAGES_DIR / f"{stamp}.jpg"
    await file.download_to_drive(custom_path=str(img_path))

    raw_response = llm.ask_llm_vision(img_path, TRADE_EXTRACTION_PROMPT)
    if not raw_response:
        await update.message.reply_text("Couldn't extract data from the image. Try a clearer photo?")
        return

    # Strip potential markdown fences
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        trade_data = json.loads(cleaned)
    except json.JSONDecodeError:
        log.error("Failed to parse trade JSON: %s", raw_response)
        await update.message.reply_text(
            f"Couldn't parse the AI response as JSON.\nRaw output:\n{raw_response[:500]}"
        )
        return

    # Validate required fields
    required = ["stock", "transaction_type", "order_placed", "shares", "price_per_share_usd", "amount_usd"]
    missing = [f for f in required if not trade_data.get(f)]
    if missing:
        await update.message.reply_text(
            f"Extracted data is missing fields: {', '.join(missing)}\n"
            f"Raw: {json.dumps(trade_data, indent=2)}"
        )
        return

    # Normalize numeric fields
    for field in ["shares", "price_per_share_usd", "transaction_fee_usd", "amount_usd"]:
        val = str(trade_data.get(field, "0"))
        val = val.replace("$", "").replace(",", "").strip()
        trade_data[field] = val

    # Duplicate check
    if is_duplicate(trade_data):
        await update.message.reply_text(
            f"Duplicate detected! A trade with the same stock, date, and amount already exists.\n"
            f"Stock: {trade_data['stock']}\n"
            f"Date: {trade_data['order_placed']}\n"
            f"Amount: ${trade_data['amount_usd']}\n\n"
            f"Skipped saving."
        )
        return

    # Save
    append_trade(trade_data)
    llm.log_entry("image", json.dumps(trade_data), "Trade saved to CSV", img_path.name)

    await update.message.reply_text(
        f"Trade logged!\n\n"
        f"Stock: {trade_data['stock']}\n"
        f"Type: {trade_data['transaction_type']}\n"
        f"Date: {trade_data['order_placed']}\n"
        f"Shares: {trade_data['shares']}\n"
        f"Price: ${trade_data['price_per_share_usd']}\n"
        f"Fee: ${trade_data.get('transaction_fee_usd', '0')}\n"
        f"Amount: ${trade_data['amount_usd']}"
    )
