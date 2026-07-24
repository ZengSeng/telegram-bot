---
kind: external_dependency
name: Telegram Bot Framework (python-telegram-bot)
slug: telegram-bot-api
category: external_dependency
category_hints:
    - sdk_real_api
scope:
    - '**'
source_files:
    - voice_logger_bot.py
    - requirements.txt
---

The bot uses the `python-telegram-bot` library (v21.6 with job-queue extra) to implement a long-polling Telegram bot. It registers command handlers (`/start`, `/system`, `/watch`, `/unwatch`, `/summary`, `/gains`) and message handlers for voice, text, and photo messages. The job queue is used to schedule daily portfolio summaries at 9:00 AM. The bot token is read from the `BOT_TOKEN_AI` environment variable.