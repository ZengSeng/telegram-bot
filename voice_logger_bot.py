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
    app.add_handler(CommandHandler("summary", handlers.summary_command))
    app.add_handler(CommandHandler("gains", handlers.gains_command))
    app.add_handler(CommandHandler("analyze", handlers.analyze_command))
    app.add_handler(CommandHandler("report", handlers.report_command))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handlers.handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text))

    # Schedule daily portfolio summary (local time)
    tz = ZoneInfo(LOCAL_TIMEZONE)
    summary_time = dt.time(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(handlers.send_daily_summary, time=summary_time)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
