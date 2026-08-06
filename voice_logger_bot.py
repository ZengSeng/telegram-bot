"""
Voice + text logger bot with local AI replies and stock trade tracking.

Run:  python voice_logger_bot.py
Stop: Ctrl+C
"""

import datetime as dt
from zoneinfo import ZoneInfo

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from stock_bot import handlers, llm
from stock_bot.config import (
    BOT_TOKEN,
    DEFAULT_SYSTEM_PROMPT,
    LOCAL_TIMEZONE,
    NIGHT_PIPELINE_TIMES,
    PIPELINE_HOUR,
    PIPELINE_MINUTE,
    SUMMARY_HOUR,
    SUMMARY_MINUTE,
    SYSTEM_PROMPT_FILE,
    log,
)


def main() -> None:
    # Load persisted system prompt
    if SYSTEM_PROMPT_FILE.exists():
        llm.system_prompt = SYSTEM_PROMPT_FILE.read_text().strip() or DEFAULT_SYSTEM_PROMPT
    else:
        llm.system_prompt = DEFAULT_SYSTEM_PROMPT

    llm.start_llama_server()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("system", handlers.set_system_prompt))
    app.add_handler(CommandHandler("watch", handlers.watch_ticker))
    app.add_handler(CommandHandler("unwatch", handlers.unwatch_ticker))
    app.add_handler(CommandHandler("portfolio", handlers.portfolio_command))
    app.add_handler(CommandHandler("charts", handlers.charts_command))
    app.add_handler(CommandHandler("gains", handlers.gains_command))
    app.add_handler(CommandHandler("analyze", handlers.analyze_command))
    app.add_handler(CommandHandler("summary", handlers.summary_command))
    app.add_handler(CommandHandler("news", handlers.news_command))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handlers.handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))

    # Schedule daily data pipeline (local time) — runs before summary
    tz = ZoneInfo(LOCAL_TIMEZONE)
    pipeline_time = dt.time(hour=PIPELINE_HOUR, minute=PIPELINE_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(handlers.run_pipeline_job, time=pipeline_time)

    # Schedule daily portfolio summary (local time)
    summary_time = dt.time(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(handlers.send_daily_summary, time=summary_time)

    # Schedule night pipeline 5x in the afternoon/evening (12 PM - 8 PM NZT)
    for hour, minute in NIGHT_PIPELINE_TIMES:
        night_time = dt.time(hour=hour, minute=minute, tzinfo=tz)
        app.job_queue.run_daily(handlers.run_night_pipeline_job, time=night_time)
        log.info("Night pipeline scheduled at %02d:%02d NZT", hour, minute)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
