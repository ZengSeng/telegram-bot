# Setup

Fresh install from scratch. Skip sections you already have.

---

## 1. Telegram Bot Token

1. Message **@BotFather** on Telegram
2. `/newbot` → follow prompts → copy token
3. Set as system environment variable: `BOT_TOKEN`

---

## 2. Python Environment

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

## 3. whisper.cpp (voice transcription)

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -B build
cmake --build build --config Release
models\download-ggml-model.cmd base.en
```

Key paths (set in `stock_bot/config.py`):
- `WHISPER_BIN` → `whisper.cpp/build/bin/Release/whisper-cli.exe`
- `WHISPER_MODEL` → `whisper.cpp/ggml-base.en.bin`

---

## 4. ffmpeg (audio conversion)

```bash
winget install ffmpeg
```

---

## 5. llama-server (local LLM)

```bash
llama-server -hf empero-ai/Qwythos-9B-v2-GGUF:Q8_0 --alias MyQwythos --port 10000 --spec-type draft-mtp --ctx-size 96000
```

Bot auto-detects if already running. No API keys needed.

---

## 6. TradingAgents (analysis engine)

```bash
git submodule update --init
venv\Scripts\pip install -e vendor/TradingAgents
venv\Scripts\pip install duckdb
```

Verify:
```bash
venv\Scripts\python -c "import tradingagents; import duckdb; print('OK')"
```

### Upgrading later

```bash
cd vendor/TradingAgents
git fetch origin
git checkout v0.4.0
cd ../..
venv\Scripts\pip install -e vendor/TradingAgents
```

---

## 7. First Run

```bash
# Ingest data for your tickers
venv\Scripts\python -m data_eng RKLB

# Start bot
venv\Scripts\python voice_logger_bot.py

# In Telegram: /start
```
