---
kind: configuration_system
name: Inline Module-Level Configuration with Environment Variables and Local Files
category: configuration_system
scope:
    - '**'
source_files:
    - bot.py
    - voice_logger_bot.py
    - SETUP.md
    - requirements.txt
---

This repository uses a simple, inline configuration approach with no dedicated configuration framework or centralized config files. Each bot script (`bot.py` and `voice_logger_bot.py`) defines its configuration directly as module-level variables at the top of the file.

**Configuration sources (in priority order):**
- **Environment variables**: Bot tokens are loaded via `os.environ.get()` — `BOT_TOKEN_MOTIVATION` for the motivation bot and `BOT_TOKEN_AI` for the voice logger bot. This is the only mechanism used for secrets.
- **Hardcoded defaults**: All other settings (paths to whisper.cpp binary/model, ffmpeg, llama-server command, schedule times, data directories) are defined as Python constants directly in each module.
- **Local files for runtime state**: Chat IDs, system prompts, watchlists, trades CSV, and JSONL logs are stored under the `data/` directory as plain text/JSON/CSV files.

**Key patterns:**
- No `.env` files, no `config.yaml`, no `pydantic-settings`, no `python-dotenv` usage.
- Each bot independently manages its own configuration namespace — there is no shared config module.
- External tool paths (whisper.cpp, ffmpeg, llama-server) use absolute Windows paths hardcoded in the source.
- Runtime-varying configuration (system prompt, watchlist) is persisted to disk files under `data/` and reloaded on startup.

**Data storage conventions:**
- `data/chat_id.txt` — single integer per bot instance
- `data/checkins.jsonl` / `data/voice_log.jsonl` — append-only JSON lines for transcripts
- `data/system_prompt.txt` — editable system prompt for the AI bot
- `data/watchlist.json` — JSON array of ticker symbols
- `data/trades.csv` — CSV with fixed column schema for stock trade records
- `data/audio/` and `data/images/` — timestamped media files

**Rules developers should follow:**
- Keep sensitive values (bot tokens) in environment variables only — never hardcode them.
- New configuration constants should be added as module-level variables near the top of the relevant bot file.
- Use the `Path(__file__).parent / "data"` pattern for all file paths to keep them relative to the script location.
- Persist user-modifiable state (prompts, watchlists) to files under `data/` with sensible defaults if files don't exist.