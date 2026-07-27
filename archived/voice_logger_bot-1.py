"""
Voice note logger bot.
Accepts any voice note you send, transcribes it with whisper.cpp,
and logs it (text + audio file) to a local JSONL file.

No reminders, no schedule — just send a voice note whenever you want.

Run:  python voice_logger_bot.py
Stop: Ctrl+C
"""

import datetime as dt
import json
import logging
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ---------------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------------

BOT_TOKEN = ""          # from @BotFather

WHISPER_BIN = r"C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe"   # path to compiled whisper.cpp binary
WHISPER_MODEL = r"C:\repo\whisper.cpp\ggml-base.en.bin"  # path to downloaded model

DATA_DIR = Path(__file__).parent / "data"
AUDIO_DIR = DATA_DIR / "audio"
LOG_FILE = DATA_DIR / "voice_log.jsonl"

# ---------------------------------------------------------------------------

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("voice_logger_bot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def log_entry(text: str, audio_file: str) -> None:
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
    await update.message.reply_text(
        "Send me a voice note any time and I'll transcribe and log it. That's all I do."
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice
    if voice is None:
        return

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

    log_entry(transcript, ogg_path.name)

    await update.message.reply_text("ok")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
