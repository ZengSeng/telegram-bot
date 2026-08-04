"""Central configuration: paths, constants, prompts, and logging setup."""

import logging
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Tokens & external services
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN_AI")  # from @BotFather

WHISPER_BIN = r"C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe"
WHISPER_MODEL = r"C:\repo\whisper.cpp\ggml-base.en.bin"

LLAMA_CMD = [
    "llama-server",
    "-hf", "empero-ai/Qwythos-9B-v2-GGUF:Q8_0",
    "--alias", "MyQwythos",
    "--port", "10000",
    "--spec-type", "draft-mtp",
    "--ctx-size", "96000",
]
LLAMA_URL = "http://127.0.0.1:10000/v1/chat/completions"
LLAMA_STARTUP_WAIT_SECONDS = 1

# ---------------------------------------------------------------------------
# Paths (project root is one level up from this package)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
AUDIO_DIR = DATA_DIR / "audio"
IMAGES_DIR = DATA_DIR / "images"
LOG_FILE = DATA_DIR / "voice_log.jsonl"
SYSTEM_PROMPT_FILE = DATA_DIR / "system_prompt.txt"
TRADES_CSV = DATA_DIR / "trades.csv"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
CHAT_ID_FILE = DATA_DIR / "chat_id.txt"

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

SUMMARY_HOUR = 9
SUMMARY_MINUTE = 30
PIPELINE_HOUR = 8
PIPELINE_MINUTE = 0
# Night pipeline runs 3x in the afternoon/evening (hour, minute) — NZT
NIGHT_PIPELINE_TIMES = [(16, 0), (18, 0), (20, 0)]
LOCAL_TIMEZONE = "Pacific/Auckland"  # New Zealand

# ---------------------------------------------------------------------------
# Prompts & schemas
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = "Keep your answer within 50 words. Always encourage no matter what!"

TRADE_EXTRACTION_PROMPT = (
    "Extract trade data from this image. Output ONLY a single-line raw JSON object. "
    "No markdown, no code fences, no explanations.\n\n"
    "Rules:\n"
    "- All numeric fields must be plain numbers only (no $, no commas, no currency symbols)\n"
    "- stock format must be: Company Name (TICKER | EXCHANGE)\n"
    "- transaction_type must be exactly \"buy\" or \"sell\"\n"
    "- Dates in yyyy-MM-dd HH:mm:ss format\n\n"
    "Schema:\n"
    "{\"stock\":\"Name (TICKER | EXCHANGE)\",\"transaction_type\":\"buy|sell\","
    "\"order_placed\":\"yyyy-MM-dd HH:mm:ss\",\"order_filled\":\"yyyy-MM-dd HH:mm:ss or null\","
    "\"shares\":0.00,\"price_per_share_usd\":0.00,\"transaction_fee_usd\":0.00,\"amount_usd\":0.00}"
)

TRADES_CSV_COLUMNS = [
    "stock", "transaction_type", "order_placed", "order_filled",
    "shares", "price_per_share_usd", "transaction_fee_usd", "amount_usd",
]

# ---------------------------------------------------------------------------
# Initialise directories & logging
# ---------------------------------------------------------------------------

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)
IMAGES_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("voice_logger_bot")
