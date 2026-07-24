"""
Daily motivation check-in bot.
Sends a reminder every day at a fixed time, accepts a voice-note reply,
transcribes it with whisper.cpp, and logs it to a local JSONL file.

Run:  python bot.py
Stop: Ctrl+C
"""

import asyncio
import datetime as dt
import json
import logging
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get('BOT_TOKEN_MOTIVATION') # from @BotFather
CHAT_ID = None                                # filled in automatically on first /start

REMINDER_HOUR = 8      # 24h clock, local time of the machine running the bot
REMINDER_MINUTE = 0

WHISPER_BIN = r"C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe"   # path to compiled whisper.cpp binary
WHISPER_MODEL = r"C:\repo\whisper.cpp\ggml-base.en.bin"  # path to downloaded model

DATA_DIR = Path(__file__).parent / "data"
AUDIO_DIR = DATA_DIR / "audio"
LOG_FILE = DATA_DIR / "checkins.jsonl"
CHAT_ID_FILE = DATA_DIR / "chat_id.txt"

# ---------------------------------------------------------------------------

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("checkin_bot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def save_chat_id(chat_id: int) -> None:
    CHAT_ID_FILE.write_text(str(chat_id))


def load_chat_id() -> int | None:
    if CHAT_ID_FILE.exists():
        return int(CHAT_ID_FILE.read_text().strip())
    return None


def transcribe(wav_path: Path) -> str:
    """Call whisper.cpp on a wav file and return the transcript text."""
    result = subprocess.run(
        [
            WHISPER_BIN,
            "-m", WHISPER_MODEL,
            "-f", str(wav_path),
            "-nt",          # no timestamps
            "-otxt",        # also write a .txt file next to the wav
            "-of", str(wav_path.with_suffix("")),  # output file prefix
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

    # fallback: parse stdout if -otxt didn't produce a file for some reason
    return result.stdout.strip()


def log_checkin(text: str, audio_file: str) -> None:
    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "text": text,
        "audio_file": audio_file,
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    save_chat_id(chat_id)
    await update.message.reply_text(
        "You're set up. I'll send a daily check-in — just reply with a voice note.\n"
        "Try /checkin now to test it immediately."
    )


async def manual_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "How's your motivation today (1-10)? What's blocking you? Send a voice note 🎙️"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    if voice is None:
        return

    await update.message.reply_text("Got it, transcribing...")

    file = await context.bot.get_file(voice.file_id)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ogg_path = AUDIO_DIR / f"{stamp}.ogg"
    wav_path = AUDIO_DIR / f"{stamp}.wav"

    await file.download_to_drive(custom_path=str(ogg_path))

    # convert ogg -> wav (16kHz mono, what whisper.cpp expects)
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

    log_checkin(transcript, ogg_path.name)

    await update.message.reply_text(f"Logged ✅\n\nTranscript:\n\"{transcript}\"")


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = load_chat_id()
    if chat_id is None:
        log.warning("No chat_id saved yet — user hasn't run /start")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="Daily check-in 🎙️ How's your motivation today (1-10)? What's blocking you?",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("checkin", manual_checkin))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # schedule the daily reminder
    reminder_time = dt.time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE)
    app.job_queue.run_daily(send_daily_reminder, time=reminder_time)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
