# Daily Motivation Check-in Bot

Sends you a daily Telegram reminder, accepts a voice-note reply, transcribes it
locally with whisper.cpp, and logs it to `data/checkins.jsonl`.

## 1. Create the bot

1. Open Telegram, message **@BotFather**.
2. `/newbot`, follow the prompts, copy the token it gives you.
3. Paste that token into `bot.py` as `BOT_TOKEN`.

## 2. Install whisper.cpp
See chat: https://claude.ai/chat/ff107af9-50af-4caa-92d0-0281452f6f86 (no need to refer this for development)

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -B build
cmake --build build --config Release
./models/download-ggml-model.sh base.en
```

## 2. What Actually Happened When Install whisper.cpp
```bash
C:\repo>git clone https://github.com/ggerganov/whisper.cpp
Cloning into 'whisper.cpp'...

C:\repo>cd whisper.cpp

C:\repo\whisper.cpp>models\download-ggml-model.cmd base.en
Downloading ggml model base.en...
Done! Model base.en saved in C:\repo\whisper.cpp\ggml-base.en.bin
You can now use it like this:
C:\repo\whisper.cpp\build\bin\Release\whisper-cli.exe -m C:\repo\whisper.cpp\ggml-base.en.bin -f samples\jfk.wav
```

This gives you two paths — put them into `bot.py`:
- `WHISPER_BIN` → e.g. `whisper.cpp/build/bin/whisper-cli`
- `WHISPER_MODEL` → e.g. `whisper.cpp/models/ggml-base.en.bin`

(`base.en` is a good speed/accuracy balance for short voice notes.
Use `small.en` if you want better accuracy and don't mind ~2x slower.)

## 3. Install ffmpeg

```bash
winget install ffmpeg
```

## 4. Install Python deps

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 5. Run it

```bash
python bot.py
```

Then in Telegram:
- Send `/start` once — this saves your chat ID so the bot knows where to send
  the daily reminder.
- Send `/checkin` any time to trigger a check-in manually (useful for testing).
- Every day at the time set by `REMINDER_HOUR` / `REMINDER_MINUTE` in `bot.py`,
  it'll message you automatically.
- Reply to any check-in with a voice note — it gets transcribed and logged.

## 6. Keep it running

Since your computer stays on, the simplest option is a terminal / tmux session:

```bash
tmux new -s checkinbot
python bot.py
# Ctrl+B then D to detach — it keeps running
```

Or set it up as a systemd service if you want it to survive reboots
automatically (happy to write that unit file if you want it).

## Data

- `data/checkins.jsonl` — one JSON object per line: timestamp, transcript, audio filename.
- `data/audio/` — raw voice notes (.ogg) and converted .wav files, kept for reference.

This log is exactly what next week's "weekly summary" script will read to
compare against your goals doc.
