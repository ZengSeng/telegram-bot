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
        "/news - AI news summaries (from pipeline)\n"
        "/advice [TICKER] - trade plan + ideas / full buy card"
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
    """Scheduled daily portfolio summary — sent to all registered users.

    Sends charts + portfolio state, then the morning briefing: today's
    trade plan (BUY/SELL proposals), the LLM committee review, and news
    summaries for watchlist + candidates.
    """
    chat_ids = load_chat_ids()
    if not chat_ids:
        log.warning("No chat_ids saved — no user has run /start")
        return

    tickers = get_chart_tickers()
    msg = build_portfolio_summary()
    briefing = _build_morning_briefing()

    for chat_id in chat_ids:
        try:
            for ticker in tickers:
                chart = generate_price_chart(ticker)
                if chart:
                    await context.bot.send_photo(chat_id=chat_id, photo=chart)
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            for text in briefing:
                await context.bot.send_message(chat_id=chat_id, text=text)
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
            await context.bot.send_message(chat_id=chat_id, text="Jesus loves you")
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
# Morning briefing (trade plan + committee review + news)
# ---------------------------------------------------------------------------


def _build_trade_plan() -> str:
    """Today's actionable BUY/SELL proposals from the portfolio engine."""
    from data_eng.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        """SELECT ticker, action, position_pct, shares, stop_loss, reason
           FROM portfolio_decisions WHERE date = CURRENT_DATE"""
    ).fetchall()
    conn.close()

    buys = [r for r in rows if r[1] == "BUY"]
    sells = [r for r in rows if r[1] == "SELL"]
    holds = [r for r in rows if r[1] == "HOLD"]

    lines = [f"\U0001f9ed Trade plan ({dt.date.today()})"]
    if not buys and not sells:
        lines.append("No buy/sell actions today — positions hold.")
        return "\n".join(lines)

    for t, _, pct, shares, stop, reason in buys:
        stop_txt = f", stop ${stop:.2f}" if stop else ""
        pct_txt = f" ({pct:.1f}%)" if pct else ""
        lines.append(f"\U0001f7e2 BUY {t}: {shares:.0f} sh{pct_txt}{stop_txt}"
                     + (f" — {reason}" if reason else ""))
    for t, _, _, shares, _, reason in sells:
        lines.append(f"\U0001f534 SELL {t}: {shares:.0f} sh"
                     + (f" — {reason}" if reason else ""))
    if holds:
        lines.append(f"\u26aa HOLD: {len(holds)} positions unchanged")
    return "\n".join(lines)


def _build_committee_review() -> str:
    """Today's LLM investment-committee review, or '' if none."""
    from data_eng.db import get_connection

    conn = get_connection()
    row = conn.execute(
        "SELECT review_text FROM portfolio_reviews WHERE date = CURRENT_DATE"
    ).fetchone()
    conn.close()
    if not row:
        return ""
    text = f"\U0001f3db Committee review:\n{row[0]}"
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    return text


def _briefing_news_tickers() -> list[str]:
    """Watchlist + today's selected candidates (deduped, ordered)."""
    from data_eng.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        """SELECT ticker FROM candidates
           WHERE removed_reason IS NULL
             AND date_selected = (SELECT MAX(date_selected) FROM candidates)"""
    ).fetchall()
    conn.close()
    return list(dict.fromkeys(load_watchlist() + [r[0] for r in rows]))


def _build_news_briefing() -> str:
    """Today's AI news summaries for watchlist + candidates, chunked text."""
    from data_eng.db import get_connection

    tickers = _briefing_news_tickers()
    if not tickers:
        return ""

    conn = get_connection()
    placeholders = ", ".join(["?"] * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, summary FROM news_summaries
            WHERE date = CURRENT_DATE AND ticker IN ({placeholders})
            ORDER BY ticker""",
        tickers,
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = ["\U0001f4f0 News briefing"]
    for ticker, summary in rows:
        lines.append(f"\n\U0001f4f0 {ticker}\n{summary}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated — /news for the full list)"
    return text


def _build_morning_briefing() -> list[str]:
    """Assemble the 9:30 AM push messages after the portfolio summary."""
    messages = []
    try:
        messages.append(_build_trade_plan())
    except Exception as e:
        log.warning("Briefing: trade plan failed: %s", e)
    try:
        review = _build_committee_review()
        if review:
            messages.append(review)
    except Exception as e:
        log.warning("Briefing: committee review failed: %s", e)
    try:
        news = _build_news_briefing()
        if news:
            messages.append(news)
    except Exception as e:
        log.warning("Briefing: news failed: %s", e)
    return messages


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


# ---------------------------------------------------------------------------
# Advice command — buy/sell guidance + new ideas
# ---------------------------------------------------------------------------


def _latest_price(ticker: str) -> tuple[float | None, float | None]:
    """(latest close, day-over-day % change) from daily_prices."""
    from data_eng.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT close FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 2",
        [ticker],
    ).fetchall()
    conn.close()
    if not rows or rows[0][0] is None:
        return None, None
    latest = float(rows[0][0])
    if len(rows) > 1 and rows[1][0]:
        prev = float(rows[1][0])
        return latest, (latest - prev) / prev * 100 if prev else None
    return latest, None


def _latest_ta_decision(ticker: str) -> dict | None:
    """Latest TradingAgents decision for a ticker, or None."""
    from data_eng.db import get_connection

    conn = get_connection()
    row = conn.execute(
        """SELECT date, action, rating, price_target, entry_price, stop_loss,
                  time_horizon, summary
           FROM trading_agent_decisions
           WHERE ticker = ? ORDER BY date DESC LIMIT 1""",
        [ticker],
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "date": row[0], "action": row[1], "rating": row[2],
        "price_target": row[3], "entry_price": row[4], "stop_loss": row[5],
        "time_horizon": row[6], "summary": row[7],
    }


def _latest_screener_score(ticker: str) -> float | None:
    from data_eng.db import get_connection

    conn = get_connection()
    row = conn.execute(
        """SELECT overall_score FROM screener_scores
           WHERE ticker = ? ORDER BY date_scored DESC LIMIT 1""",
        [ticker],
    ).fetchone()
    conn.close()
    return float(row[0]) if row and row[0] is not None else None


def _top_ideas(exclude: set[str], limit: int = 3) -> list[str]:
    """Highest-scored screener tickers not already held or watched."""
    from data_eng.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        """SELECT s.ticker
           FROM screener_scores s
           INNER JOIN (
               SELECT ticker, MAX(date_scored) AS max_date
               FROM screener_scores GROUP BY ticker
           ) latest ON s.ticker = latest.ticker AND s.date_scored = latest.max_date
           WHERE s.overall_score IS NOT NULL
           ORDER BY s.overall_score DESC"""
    ).fetchall()
    conn.close()

    ideas = []
    for (t,) in rows:
        if t in exclude:
            continue
        ideas.append(t)
        if len(ideas) >= limit:
            break
    return ideas


def _advice_card(ticker: str) -> str:
    """Full 'what do I need to know' card for one ticker."""
    lines = [f"\U0001f4a1 {ticker}"]

    price, change = _latest_price(ticker)
    if price:
        chg = f" ({change:+.1f}%)" if change is not None else ""
        lines.append(f"Price: ${price:.2f}{chg}")

    score = _latest_screener_score(ticker)
    if score is not None:
        lines.append(f"Screener: {score:.0f}/100")

    d = _latest_ta_decision(ticker)
    if d:
        verdict = d["action"] or "?"
        if d["rating"]:
            verdict += f" / {d['rating']}"
        lines.append(f"TradingAgents ({d['date']}): {verdict}")
        levels = []
        if d["entry_price"]:
            levels.append(f"entry ${d['entry_price']:.2f}")
        if d["stop_loss"]:
            levels.append(f"stop ${d['stop_loss']:.2f}")
        if d["price_target"]:
            levels.append(f"target ${d['price_target']:.2f}")
        if levels:
            lines.append("  " + " | ".join(levels))
        if d["time_horizon"]:
            lines.append(f"  Horizon: {d['time_horizon']}")
        if d["summary"]:
            summary = d["summary"]
            if len(summary) > 400:
                summary = summary[:400] + "…"
            lines.append(f"  {summary}")
    else:
        lines.append("TradingAgents: no analysis yet — /analyze " + ticker)

    # Latest news summary (today preferred, else most recent)
    from data_eng.db import get_connection

    conn = get_connection()
    row = conn.execute(
        """SELECT date, summary FROM news_summaries
           WHERE ticker = ? ORDER BY date DESC LIMIT 1""",
        [ticker],
    ).fetchone()
    conn.close()
    if row:
        lines.append(f"\n\U0001f4f0 News ({row[0]}):\n{row[1]}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    return text


async def advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buy/sell guidance.

    /advice TICKER — full card: price, TradingAgents verdict + levels,
    screener score, and latest news summary.
    /advice — today's trade plan (BUY/SELL proposals) plus the top screener
    ideas you don't hold and haven't watched.
    """
    if context.args:
        await update.message.reply_text(_advice_card(context.args[0].upper()))
        return

    messages = []
    try:
        messages.append(_build_trade_plan())
    except Exception as e:
        log.warning("Advice: trade plan failed: %s", e)

    # Ideas: top screener tickers not held and not on the watchlist
    try:
        from data_eng.portfolio_engine import _load_net_holdings

        exclude = set(_load_net_holdings()) | set(load_watchlist())
        ideas = _top_ideas(exclude)
        if ideas:
            lines = ["\n\U0001f4a1 Ideas (top screener, not held/watched):"]
            for i, ticker in enumerate(ideas, 1):
                score = _latest_screener_score(ticker)
                price, change = _latest_price(ticker)
                bits = []
                if score is not None:
                    bits.append(f"score {score:.0f}")
                if price:
                    chg = f" {change:+.1f}%" if change is not None else ""
                    bits.append(f"${price:.2f}{chg}")
                d = _latest_ta_decision(ticker)
                if d and d["action"]:
                    ta_bit = f"TA: {d['action']} ({d['date']})"
                    if d["price_target"]:
                        ta_bit += f" target ${d['price_target']:.2f}"
                    bits.append(ta_bit)
                lines.append(f"{i}. {ticker} — " + ", ".join(bits))
            lines.append("\n/advice TICKER for the full card")
            messages.append("\n".join(lines))
    except Exception as e:
        log.warning("Advice: ideas failed: %s", e)

    if not messages:
        await update.message.reply_text("Nothing to show yet — run the daily pipeline first.")
        return
    for text in messages:
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
