"""
Voice + text logger bot with local AI replies and stock trade tracking.

Send a voice note OR a text message. The bot:
  1. (voice only) transcribes it with whisper.cpp
  2. sends the resulting text to a local LLM server (llama-server)
  3. replies to you with the AI's answer
  4. logs everything (your input + the AI's reply) to a local JSONL file

Send a photo of a trade confirmation. The bot:
  1. sends it to the local vision model for data extraction
  2. parses the JSON and saves to a CSV file
  3. detects accidental duplicates

Commands:
  /system <prompt>  - set the system prompt
  /watch <TICKER>   - add a ticker to the watchlist
  /unwatch <TICKER> - remove a ticker from the watchlist
  /summary          - show portfolio summary
  /gains            - show realized/unrealized P&L

Run:  python voice_logger_bot.py
Stop: Ctrl+C
"""

import atexit
import base64
import csv
import datetime as dt
import json
import logging
import subprocess
import time
from pathlib import Path

import os
import requests
import yfinance as yf
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ---------------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get('BOT_TOKEN_AI')  # from @BotFather

WHISPER_BIN = r"C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe"
WHISPER_MODEL = r"C:\repo\whisper.cpp\ggml-base.en.bin"

# Command used to spin up the local LLM server (llama.cpp)
LLAMA_CMD = [
    "llama-server",
    "-hf", "empero-ai/Qwythos-9B-v2-GGUF:Q8_0",
    "--alias", "MyQwythos",
    "--port", "10000",
]
LLAMA_URL = "http://127.0.0.1:10000/v1/chat/completions"
LLAMA_STARTUP_WAIT_SECONDS = 20

DEFAULT_SYSTEM_PROMPT = "Keep your answer within 50 words. Always encourage no matter what!"

DATA_DIR = Path(__file__).parent / "data"
AUDIO_DIR = DATA_DIR / "audio"
IMAGES_DIR = DATA_DIR / "images"
LOG_FILE = DATA_DIR / "voice_log.jsonl"
SYSTEM_PROMPT_FILE = DATA_DIR / "system_prompt.txt"
TRADES_CSV = DATA_DIR / "trades.csv"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"

SUMMARY_HOUR = 9
SUMMARY_MINUTE = 0

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

DATA_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)
IMAGES_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("voice_logger_bot")

# current system prompt, loaded from disk if it was set before
if SYSTEM_PROMPT_FILE.exists():
    system_prompt = SYSTEM_PROMPT_FILE.read_text().strip() or DEFAULT_SYSTEM_PROMPT
else:
    system_prompt = DEFAULT_SYSTEM_PROMPT

llama_process: subprocess.Popen | None = None


# ---------------------------------------------------------------------------
# CSV Helpers
# ---------------------------------------------------------------------------

def read_trades() -> list[dict]:
    """Read all trades from the CSV file."""
    if not TRADES_CSV.exists():
        return []
    with TRADES_CSV.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def append_trade(trade: dict) -> None:
    """Append a single trade row to the CSV file."""
    file_exists = TRADES_CSV.exists()
    with TRADES_CSV.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({col: trade.get(col, "") for col in TRADES_CSV_COLUMNS})


def is_duplicate(trade: dict) -> bool:
    """Check if a trade is a duplicate based on stock, date, and amount."""
    existing = read_trades()
    new_stock = trade.get("stock", "").upper()
    new_date = trade.get("order_placed", "")[:10]  # yyyy-MM-dd portion
    try:
        new_amount = float(trade.get("amount_usd", 0))
    except (ValueError, TypeError):
        new_amount = 0.0

    for row in existing:
        row_stock = row.get("stock", "").upper()
        row_date = row.get("order_placed", "")[:10]
        try:
            row_amount = float(row.get("amount_usd", 0))
        except (ValueError, TypeError):
            row_amount = 0.0

        if row_stock == new_stock and row_date == new_date and abs(row_amount - new_amount) < 0.01:
            return True
    return False


# ---------------------------------------------------------------------------
# Watchlist Helpers
# ---------------------------------------------------------------------------

def load_watchlist() -> list[str]:
    """Load the watchlist from disk, defaulting to ['RKLB']."""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text())
            if isinstance(data, list):
                return [t.upper() for t in data]
        except (json.JSONDecodeError, TypeError):
            pass
    return ["RKLB"]


def save_watchlist(tickers: list[str]) -> None:
    """Persist the watchlist to disk."""
    WATCHLIST_FILE.write_text(json.dumps([t.upper() for t in tickers]))


# ---------------------------------------------------------------------------
# yfinance Helpers
# ---------------------------------------------------------------------------

def get_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch the latest price for each ticker. Returns {ticker: price}."""
    prices = {}
    for t in tickers:
        try:
            ticker_obj = yf.Ticker(t)
            price = ticker_obj.fast_info.get("lastPrice") or ticker_obj.fast_info.get("last_price")
            if price:
                prices[t] = float(price)
        except Exception as e:
            log.warning("Failed to fetch price for %s: %s", t, e)
    return prices


def get_ticker_name(ticker: str) -> str:
    """Get the short name for a ticker."""
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortName", ticker)
    except Exception:
        return ticker


# ---------------------------------------------------------------------------
# Portfolio Calculation Helpers
# ---------------------------------------------------------------------------

def extract_ticker(stock_field: str) -> str:
    """Extract the ticker symbol from a stock field like 'Rocket Lab Corp (RKLB | NASDAQ)' or 'Micron Technology Inc | MU | NASDAQ'."""
    import re
    match = re.search(r"\(([A-Z]+)", stock_field.upper())
    if match:
        return match.group(1)
    match = re.search(r"\|\s*([A-Z]{1,5})\s*\|", stock_field.upper())
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Z]{1,5})\b", stock_field.upper())
    if match:
        return match.group(1)
    return stock_field.strip().upper()


def compute_portfolio(trades: list[dict]) -> dict[str, dict]:
    """
    Compute per-stock portfolio stats using FIFO for cost basis.
    Returns {ticker: {shares, avg_cost, total_invested, realized_gain}}
    """
    from collections import defaultdict

    # Group trades by ticker
    buys_by_ticker: dict[str, list[dict]] = defaultdict(list)
    sells_by_ticker: dict[str, list[dict]] = defaultdict(list)

    for trade in trades:
        ticker = extract_ticker(trade.get("stock", ""))
        if not ticker:
            continue
        ttype = trade.get("transaction_type", "").lower().strip()
        if ttype == "buy":
            buys_by_ticker[ticker].append(trade)
        elif ttype == "sell":
            sells_by_ticker[ticker].append(trade)

    # Sort buys by order_placed date (FIFO)
    for ticker in buys_by_ticker:
        buys_by_ticker[ticker].sort(key=lambda t: t.get("order_placed", ""))
    for ticker in sells_by_ticker:
        sells_by_ticker[ticker].sort(key=lambda t: t.get("order_placed", ""))

    all_tickers = set(list(buys_by_ticker.keys()) + list(sells_by_ticker.keys()))
    portfolio = {}

    for ticker in all_tickers:
        buys = buys_by_ticker.get(ticker, [])
        sells = sells_by_ticker.get(ticker, [])

        # Build FIFO lots from buys: each lot = {shares_remaining, price, fee_per_share}
        lots = []
        for b in buys:
            try:
                shares = float(b.get("shares", 0))
                price = float(b.get("price_per_share_usd", 0))
                fee = float(b.get("transaction_fee_usd", 0))
            except (ValueError, TypeError):
                continue
            if shares > 0:
                lots.append({
                    "shares_remaining": shares,
                    "price": price,
                    "fee_per_share": fee / shares if shares > 0 else 0,
                })

        # Process sells against FIFO lots
        realized_gain = 0.0
        for s in sells:
            try:
                sell_shares = float(s.get("shares", 0))
                sell_price = float(s.get("price_per_share_usd", 0))
                sell_fee = float(s.get("transaction_fee_usd", 0))
            except (ValueError, TypeError):
                continue

            fee_per_sell_share = sell_fee / sell_shares if sell_shares > 0 else 0
            remaining_to_sell = sell_shares

            for lot in lots:
                if remaining_to_sell <= 0:
                    break
                matched = min(lot["shares_remaining"], remaining_to_sell)
                if matched > 0:
                    buy_cost_per_share = lot["price"] + lot["fee_per_share"]
                    sell_net_per_share = sell_price - fee_per_sell_share
                    realized_gain += matched * (sell_net_per_share - buy_cost_per_share)
                    lot["shares_remaining"] -= matched
                    remaining_to_sell -= matched

        # Remaining open lots
        open_shares = sum(lot["shares_remaining"] for lot in lots)
        if open_shares > 0:
            total_cost = sum(
                lot["shares_remaining"] * (lot["price"] + lot["fee_per_share"])
                for lot in lots
            )
            avg_cost = total_cost / open_shares
        else:
            avg_cost = 0.0

        # Total invested (all buys, gross)
        total_invested = 0.0
        for b in buys:
            try:
                total_invested += float(b.get("amount_usd", 0))
            except (ValueError, TypeError):
                pass

        portfolio[ticker] = {
            "shares": open_shares,
            "avg_cost": avg_cost,
            "total_invested": total_invested,
            "realized_gain": realized_gain,
        }

    return portfolio


def compute_fifo_details(trades: list[dict], ticker: str) -> dict:
    """
    Compute detailed FIFO matching for a specific ticker.
    Returns {matched: [...], open_lots: [...], realized_total: float}
    """
    buys = []
    sells = []
    for trade in trades:
        t = extract_ticker(trade.get("stock", ""))
        if t != ticker:
            continue
        ttype = trade.get("transaction_type", "").lower().strip()
        if ttype == "buy":
            buys.append(trade)
        elif ttype == "sell":
            sells.append(trade)

    buys.sort(key=lambda t: t.get("order_placed", ""))
    sells.sort(key=lambda t: t.get("order_placed", ""))

    lots = []
    for b in buys:
        try:
            shares = float(b.get("shares", 0))
            price = float(b.get("price_per_share_usd", 0))
            fee = float(b.get("transaction_fee_usd", 0))
        except (ValueError, TypeError):
            continue
        if shares > 0:
            lots.append({
                "shares_remaining": shares,
                "price": price,
                "fee_per_share": fee / shares if shares > 0 else 0,
                "date": b.get("order_placed", "")[:10],
            })

    matched = []
    for s in sells:
        try:
            sell_shares = float(s.get("shares", 0))
            sell_price = float(s.get("price_per_share_usd", 0))
            sell_fee = float(s.get("transaction_fee_usd", 0))
        except (ValueError, TypeError):
            continue

        fee_per_sell_share = sell_fee / sell_shares if sell_shares > 0 else 0
        remaining_to_sell = sell_shares

        for lot in lots:
            if remaining_to_sell <= 0:
                break
            m = min(lot["shares_remaining"], remaining_to_sell)
            if m > 0:
                buy_cost = lot["price"] + lot["fee_per_share"]
                sell_net = sell_price - fee_per_sell_share
                gain = m * (sell_net - buy_cost)
                matched.append({
                    "shares": m,
                    "buy_price": lot["price"],
                    "sell_price": sell_price,
                    "buy_date": lot["date"],
                    "gain": gain,
                })
                lot["shares_remaining"] -= m
                remaining_to_sell -= m

    open_lots = [
        {"shares": lot["shares_remaining"], "price": lot["price"], "date": lot["date"]}
        for lot in lots if lot["shares_remaining"] > 0
    ]

    realized_total = sum(m["gain"] for m in matched)
    return {"matched": matched, "open_lots": open_lots, "realized_total": realized_total}


# ---------------------------------------------------------------------------
# LLM Helpers
# ---------------------------------------------------------------------------

def start_llama_server() -> None:
    """Launch the local LLM server as a background process."""
    global llama_process
    log.info("Starting llama server: %s", " ".join(LLAMA_CMD))
    llama_process = subprocess.Popen(
        LLAMA_CMD,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    atexit.register(stop_llama_server)
    log.info("Waiting %ss for the model to load...", LLAMA_STARTUP_WAIT_SECONDS)
    time.sleep(LLAMA_STARTUP_WAIT_SECONDS)


def stop_llama_server() -> None:
    if llama_process is not None and llama_process.poll() is None:
        log.info("Stopping llama server...")
        llama_process.terminate()


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


def ask_llm(user_text: str) -> str:
    """Send the text to the local llama-server and return its reply."""
    try:
        resp = requests.post(
            LLAMA_URL,
            json={
                "model": "MyQwythos",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error("LLM request failed: %s", e)
        return "(couldn't reach the AI server)"


def ask_llm_vision(image_path: Path, prompt: str) -> str:
    """Send an image + prompt to the local llama-server vision endpoint."""
    try:
        with open(image_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")

        resp = requests.post(
            LLAMA_URL,
            json={
                "model": "MyQwythos",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            },
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error("LLM vision request failed: %s", e)
        return ""


def log_entry(source: str, input_text: str, reply_text: str, audio_file: str | None = None) -> None:
    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "input": input_text,
        "reply": reply_text,
        "audio_file": audio_file,
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me a voice note or text and I'll pass it to the AI.\n"
        "Send a photo of a trade confirmation and I'll log it.\n\n"
        "Commands:\n"
        "/system <prompt> - set system prompt\n"
        "/watch <TICKER> - add ticker to watchlist\n"
        "/unwatch <TICKER> - remove ticker\n"
        "/summary - portfolio summary\n"
        "/gains - realized/unrealized P&L"
    )


async def set_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global system_prompt
    new_prompt = " ".join(context.args).strip()
    if not new_prompt:
        await update.message.reply_text(f"Current system prompt:\n\"{system_prompt}\"")
        return
    system_prompt = new_prompt
    SYSTEM_PROMPT_FILE.write_text(system_prompt)
    await update.message.reply_text("System prompt updated.")


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

    transcript = transcribe(wav_path)
    if not transcript:
        await update.message.reply_text("Couldn't transcribe that — try again?")
        return

    reply = ask_llm(transcript)
    log_entry("voice", transcript, reply, ogg_path.name)
    await update.message.reply_text(reply)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    if not user_text:
        return

    reply = ask_llm(user_text)
    log_entry("text", user_text, reply)
    await update.message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming photo: extract trade data via vision AI and save to CSV."""
    await update.message.reply_text("Processing trade image...")

    # Get the highest resolution photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = IMAGES_DIR / f"{stamp}.jpg"
    await file.download_to_drive(custom_path=str(img_path))

    # Send to vision model
    raw_response = ask_llm_vision(img_path, TRADE_EXTRACTION_PROMPT)
    if not raw_response:
        await update.message.reply_text("Couldn't extract data from the image. Try a clearer photo?")
        return

    # Parse JSON (strip potential markdown fences just in case)
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

    # Normalize numeric fields to plain numbers (strip $ and commas)
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

    # Save to CSV
    append_trade(trade_data)
    log_entry("image", json.dumps(trade_data), "Trade saved to CSV", img_path.name)

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


async def watch_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a ticker to the watchlist."""
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
    await update.message.reply_text(f"Added {ticker} to watchlist: {', '.join(watchlist)}")


async def unwatch_ticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a ticker from the watchlist."""
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


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show portfolio summary on demand."""
    await update.message.reply_text("Fetching prices...")
    msg = build_portfolio_summary()
    await update.message.reply_text(msg)


async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled daily portfolio summary."""
    chat_id = _load_chat_id()
    if chat_id is None:
        log.warning("No chat_id saved — user hasn't run /start")
        return
    msg = build_portfolio_summary()
    await context.bot.send_message(chat_id=chat_id, text=msg)


def _load_chat_id() -> int | None:
    """Load saved chat ID."""
    chat_id_file = DATA_DIR / "chat_id.txt"
    if chat_id_file.exists():
        try:
            return int(chat_id_file.read_text().strip())
        except ValueError:
            pass
    return None


def _save_chat_id(chat_id: int) -> None:
    """Save chat ID for scheduled messages."""
    (DATA_DIR / "chat_id.txt").write_text(str(chat_id))


def build_portfolio_summary() -> str:
    """Build the portfolio summary message."""
    trades = read_trades()
    if not trades:
        return "No trades recorded yet. Send a photo of a trade confirmation to get started!"

    watchlist = load_watchlist()
    prices = get_current_prices(watchlist)
    portfolio = compute_portfolio(trades)

    today = dt.date.today().strftime("%Y-%m-%d")
    lines = [f"📊 Portfolio Summary — {today}", ""]

    total_invested = 0.0
    total_market_value = 0.0

    for ticker in sorted(portfolio.keys()):
        stats = portfolio[ticker]
        shares = stats["shares"]
        avg_cost = stats["avg_cost"]
        current_price = prices.get(ticker)

        if shares <= 0:
            continue

        total_invested += shares * avg_cost
        market_value = shares * current_price if current_price else 0.0
        total_market_value += market_value

        change_pct = ((current_price - avg_cost) / avg_cost * 100) if (current_price and avg_cost > 0) else 0.0
        indicator = "🟢" if change_pct >= 0 else "🔴"
        change_str = f"+{change_pct:.2f}%" if change_pct >= 0 else f"{change_pct:.2f}%"
        price_str = f"${current_price:,.2f}" if current_price else "N/A"

        lines.append(f"{ticker}")
        lines.append(f"  Daily Price:    {price_str:>12}")
        lines.append(f"  Avg Cost:       ${avg_cost:>11,.2f}")
        lines.append(f"  Shares:         {shares:>12.0f}")
        lines.append(f"  Market Value:   ${market_value:>11,.2f}")
        lines.append(f"  Change:         {change_str:>12} {indicator}")
        lines.append("")

    unrealized = total_market_value - total_invested
    unrealized_pct = (unrealized / total_invested * 100) if total_invested > 0 else 0.0
    sign = "+" if unrealized >= 0 else ""
    total_indicator = "🟢" if unrealized >= 0 else "🔴"

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total Invested:   ${total_invested:>11,.2f}")
    lines.append(f"Market Value:     ${total_market_value:>11,.2f}")
    lines.append(f"Unrealized P&L:   {sign}${unrealized:>11,.2f} ({sign}{unrealized_pct:.2f}%) {total_indicator}")

    return "\n".join(lines)


async def gains_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed realized/unrealized gains per stock."""
    trades = read_trades()
    if not trades:
        await update.message.reply_text("No trades recorded yet.")
        return

    watchlist = load_watchlist()
    prices = get_current_prices(watchlist)

    # Get all unique tickers from trades
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

        # Realized
        if details["matched"]:
            lines.append("  Realized (FIFO matched):")
            for m in details["matched"]:
                gain_sign = "+" if m["gain"] >= 0 else ""
                lines.append(
                    f"    Sold {m['shares']:.0f} @ ${m['sell_price']:.2f} "
                    f"(bought @ ${m['buy_price']:.2f}) -> {gain_sign}${m['gain']:.2f}"
                )
            total_sign = "+" if details["realized_total"] >= 0 else ""
            lines.append(f"  Total Realized: {total_sign}${details['realized_total']:.2f}")
        else:
            lines.append("  Realized: No sells yet")

        # Unrealized
        if details["open_lots"]:
            total_open_shares = sum(l["shares"] for l in details["open_lots"])
            total_open_cost = sum(l["shares"] * l["price"] for l in details["open_lots"])
            avg_open = total_open_cost / total_open_shares if total_open_shares > 0 else 0

            if current_price:
                unrealized = total_open_shares * (current_price - avg_open)
                unrealized_pct = ((current_price - avg_open) / avg_open * 100) if avg_open > 0 else 0
                u_sign = "+" if unrealized >= 0 else ""
                lines.append(
                    f"  Unrealized: {total_open_shares:.0f} shares @ avg ${avg_open:.2f}, "
                    f"current ${current_price:.2f} -> {u_sign}${unrealized:.2f} ({u_sign}{unrealized_pct:.1f}%)"
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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    start_llama_server()

    app = Application.builder().token(BOT_TOKEN).build()

    # Save chat ID on /start for scheduled messages
    async def start_with_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _save_chat_id(update.effective_chat.id)
        await start(update, context)

    app.add_handler(CommandHandler("start", start_with_chat_id))
    app.add_handler(CommandHandler("system", set_system_prompt))
    app.add_handler(CommandHandler("watch", watch_ticker))
    app.add_handler(CommandHandler("unwatch", unwatch_ticker))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("gains", gains_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Schedule daily portfolio summary
    summary_time = dt.time(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE)
    app.job_queue.run_daily(send_daily_summary, time=summary_time)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
