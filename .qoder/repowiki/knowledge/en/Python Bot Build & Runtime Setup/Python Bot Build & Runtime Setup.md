---
kind: build_system
name: Python Bot Build & Runtime Setup
category: build_system
scope:
    - '**'
source_files:
    - requirements.txt
    - SETUP.md
    - bot.py
    - voice_logger_bot.py
---

This repository contains two independent Python Telegram bots with no formal build system (no Makefile, Dockerfile, CI pipeline, or packaging scripts). The project relies on a minimal, manual setup process:

**Runtime Dependencies**
- `requirements.txt` declares only `python-telegram-bot[job-queue]==21.6` and `yfinance` as Python dependencies.
- External native binaries are required but not managed by the project: `whisper.cpp` (compiled locally via cmake) for speech-to-text transcription and `ffmpeg` for audio format conversion.
- A local LLM server (`llama-server`) is launched at runtime by `voice_logger_bot.py` to provide AI responses.

**Environment Setup**
- Developers create a Python virtual environment (`python -m venv venv`) and install deps via `pip install -r requirements.txt`.
- `whisper.cpp` must be cloned, built with cmake, and its model downloaded manually — paths are hardcoded in both bot scripts (`WHISPER_BIN`, `WHISPER_MODEL`).
- `ffmpeg` is installed via the system package manager (`winget install ffmpeg` on Windows).
- Configuration is done by editing constants directly in `bot.py` and `voice_logger_bot.py` (BOT_TOKEN, REMINDER_HOUR/MINUTE, SUMMARY_HOUR/MINUTE, WHISPER_BIN, WHISPER_MODEL).

**Execution Model**
- Each bot runs as a standalone Python script: `python bot.py` and `python voice_logger_bot.py`.
- No process supervisor is included; `SETUP.md` suggests running inside `tmux` or configuring a systemd service for persistence.
- Data persists in plain files under `data/`: JSONL logs (`checkins.jsonl`, `voice_log.jsonl`), CSV trades (`trades.csv`), JSON watchlist (`watchlist.json`), and raw audio/images in subdirectories.

**No Automated Build/Deploy**
- There is no automated testing, linting, containerization, release packaging, or CI configuration.
- Version pinning exists only for `python-telegram-bot` in `requirements.txt`; other dependencies have no version constraints.
- Hardcoded absolute Windows paths (`C:\repo\whisper.cpp\...`) indicate this is a developer workstation setup rather than a portable deployment.