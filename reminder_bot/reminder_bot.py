"""
Daily reminder bot.
Accepts voice or text messages as reminders, stores them per-user,
and sends the compiled list every day at 12:00 NZT.

Commands:
  /start   – register and show help
  /list    – view today's reminders
  /remove N – remove reminder #N
  /clear   – remove all reminders

Run:  python -m reminder_bot.reminder_bot
Stop: Ctrl+C
"""

import datetime as dt
import json
import logging
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN_REMINDER")  # from @BotFather

WHISPER_BIN = r"C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe"
WHISPER_MODEL = r"C:\repo\whisper.cpp\ggml-base.en.bin"

LLAMA_URL = "http://127.0.0.1:10000/v1/chat/completions"

REMINDER_HOUR = 12
REMINDER_MINUTE = 0
LOCAL_TIMEZONE = "Pacific/Auckland"

DATA_DIR = Path(__file__).parent / "data"
AUDIO_DIR = DATA_DIR / "audio"
REMINDERS_FILE = DATA_DIR / "reminders.json"

# ---------------------------------------------------------------------------

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("reminder_bot")

# ---------------------------------------------------------------------------
# Persistence helpers – one JSON file keyed by str(chat_id)
# Structure: { "12345": [ {"id": 1, "text": "...", "added": "ISO"}, ... ] }
# ---------------------------------------------------------------------------


def _load_store() -> dict[str, list[dict]]:
    if REMINDERS_FILE.exists():
        try:
            return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt reminders file, starting fresh.")
    return {}


def _save_store(store: dict[str, list[dict]]) -> None:
    REMINDERS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_reminders(chat_id: int) -> list[dict]:
    store = _load_store()
    return store.get(str(chat_id), [])


def add_reminder(chat_id: int, text: str) -> int:
    """Add a reminder; returns the new item number (1-based)."""
    store = _load_store()
    key = str(chat_id)
    items = store.setdefault(key, [])
    next_id = max((r["id"] for r in items), default=0) + 1
    items.append({
        "id": next_id,
        "text": text,
        "added": dt.datetime.now().isoformat(timespec="seconds"),
    })
    _save_store(store)
    return next_id


def remove_reminder(chat_id: int, item_id: int) -> str | None:
    """Remove reminder by id; returns removed text or None if not found."""
    store = _load_store()
    key = str(chat_id)
    items = store.get(key, [])
    for i, r in enumerate(items):
        if r["id"] == item_id:
            removed = items.pop(i)
            _save_store(store)
            return removed["text"]
    return None


def clear_reminders(chat_id: int) -> int:
    """Remove all reminders for user; returns count removed."""
    store = _load_store()
    key = str(chat_id)
    count = len(store.get(key, []))
    store[key] = []
    _save_store(store)
    return count


# ---------------------------------------------------------------------------
# AI / transcription helpers
# ---------------------------------------------------------------------------


def transcribe(wav_path: Path) -> str:
    """Call whisper.cpp on a wav file and return the transcript text."""
    result = subprocess.run(
        [
            WHISPER_BIN,
            "-m", WHISPER_MODEL,
            "-f", str(wav_path),
            "-nt",
            "-otxt",
            "-of", str(wav_path.with_suffix("")),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("whisper.cpp failed: %s", result.stderr)
        return ""

    txt_path = wav_path.with_suffix(".txt")
    if txt_path.exists():
        return txt_path.read_text().strip()

    return result.stdout.strip()


def parse_actions(raw_text: str, existing_items: list[dict]) -> list[dict] | None:
    """Use the LLM to parse a message into a list of add/remove actions.

    Returns a list like:
      [{"action": "add", "text": "Buy milk"},
       {"action": "remove", "id": 3}]
    or None if the LLM is unreachable / returns garbage.
    """
    numbered = "\n".join(f"{r['id']}. {r['text']}" for r in existing_items) or "(empty)"

    try:
        resp = requests.post(
            LLAMA_URL,
            json={
                "model": "MyQwythos",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a reminder assistant. Parse the user's message into "
                            "actions. The user may request multiple adds and/or removals "
                            "in one message.\n\n"
                            "Current reminders:\n"
                            f"{numbered}\n\n"
                            "Rules:\n"
                            "- For ADD: rewrite the item as a short clear reminder (max 15 words).\n"
                            "- For REMOVE: match by meaning to an existing reminder id.\n"
                            "- If the message is purely a new reminder with no remove intent, "
                            "treat it as an add.\n"
                            "- Output ONLY a JSON array. No markdown, no explanation.\n\n"
                            "Format:\n"
                            '[{"action":"add","text":"..."},{"action":"remove","id":N}]'
                        ),
                    },
                    {"role": "user", "content": raw_text},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # strip markdown fences if the model wraps them anyway
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        actions = json.loads(content)
        if isinstance(actions, list):
            return actions
        return None
    except Exception as e:
        log.warning("LLM parse_actions failed (%s), falling back to simple add.", e)
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Reminder Bot ready!\n\n"
        "• Send me a *text* or *voice* message and I'll add it to your daily list.\n"
        "• Every day at 12:00 NZT I'll send your reminders.\n\n"
        "Commands:\n"
        "/list – view your reminders\n"
        "/remove N – remove reminder #N\n"
        "/clear – remove all reminders\n\n"
        "You can mix adds & removes naturally, e.g.\n"
        "\"remove buying chocolate and add call the dentist\"",
        parse_mode="Markdown",
    )


async def process_natural_language(chat_id: int, raw_text: str, message) -> None:
    """Parse a message into add/remove actions and execute them."""
    items = get_reminders(chat_id)
    actions = parse_actions(raw_text, items)

    # Fallback: if LLM is down, treat the whole message as a single add
    if actions is None:
        num = add_reminder(chat_id, raw_text)
        await message.reply_text(f"✅ Added reminder #{num}: {raw_text}")
        return

    results: list[str] = []
    for act in actions:
        if act.get("action") == "add":
            text = act.get("text", "").strip()
            if text:
                num = add_reminder(chat_id, text)
                results.append(f"✅ Added #{num}: {text}")
        elif act.get("action") == "remove":
            rid = act.get("id")
            if rid is not None:
                removed_text = remove_reminder(chat_id, int(rid))
                if removed_text is not None:
                    results.append(f"🗑️ Removed #{rid}: {removed_text}")
                else:
                    results.append(f"⚠️ No reminder #{rid} found")

    if results:
        await message.reply_text("\n".join(results))
    else:
        await message.reply_text("Couldn't understand that. Try /list to see your reminders.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if not text:
        return
    await process_natural_language(update.effective_chat.id, text, update.message)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    if voice is None:
        return

    await update.message.reply_text("🎙️ Transcribing...")

    file = await context.bot.get_file(voice.file_id)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ogg_path = AUDIO_DIR / f"{stamp}.ogg"
    wav_path = AUDIO_DIR / f"{stamp}.wav"

    await file.download_to_drive(custom_path=str(ogg_path))

    # convert ogg -> wav (16 kHz mono for whisper.cpp)
    convert = subprocess.run(
        ["ffmpeg", "-y", "-i", str(ogg_path), "-ar", "16000", "-ac", "1", str(wav_path)],
        capture_output=True,
        text=True,
    )
    if convert.returncode != 0:
        log.error("ffmpeg failed: %s", convert.stderr)
        await update.message.reply_text("Couldn't convert the audio, sorry.")
        return

    transcript = transcribe(wav_path)
    if not transcript:
        await update.message.reply_text("Couldn't transcribe that — try again?")
        return

    await process_natural_language(update.effective_chat.id, transcript, update.message)


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = get_reminders(update.effective_chat.id)
    if not items:
        await update.message.reply_text("No reminders yet. Send me something!")
        return
    lines = [f"{r['id']}. {r['text']}" for r in items]
    await update.message.reply_text("📋 Your reminders:\n\n" + "\n".join(lines))


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /remove N (e.g. /remove 2)")
        return
    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a number, e.g. /remove 2")
        return

    removed_text = remove_reminder(update.effective_chat.id, item_id)
    if removed_text is None:
        await update.message.reply_text(f"No reminder #{item_id} found.")
    else:
        await update.message.reply_text(f"🗑️ Removed #{item_id}: {removed_text}")


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = clear_reminders(update.effective_chat.id)
    await update.message.reply_text(f"🗑️ Cleared {count} reminder(s).")


# ---------------------------------------------------------------------------
# Scheduled job – send each user their reminders at 12:00 NZT
# ---------------------------------------------------------------------------


async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _load_store()
    for chat_id_str, items in store.items():
        if not items:
            continue
        lines = [f"{r['id']}. {r['text']}" for r in items]
        text = "🔔 Your reminders for today:\n\n" + "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=int(chat_id_str), text=text)
        except Exception as e:
            log.error("Failed to send reminders to %s: %s", chat_id_str, e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set the BOT_TOKEN_REMINDER environment variable first.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Schedule daily reminder delivery at 12:00 NZT
    tz = ZoneInfo(LOCAL_TIMEZONE)
    reminder_time = dt.time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(send_daily_reminders, time=reminder_time)

    log.info("Reminder bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
