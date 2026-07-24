---
kind: logging_system
name: Standard Library Logging with Structured JSONL Application Logs
category: logging_system
scope:
    - '**'
source_files:
    - bot.py
    - voice_logger_bot.py
    - data/voice_log.jsonl
---

The repository uses Python's built-in `logging` module as its logging framework, configured identically in both bot entry points (`bot.py` and `voice_logger_bot.py`). There is no third-party logging library (no loguru, structlog, or similar). The system consists of two complementary layers: process-level console logging via `logging.basicConfig`, and structured application event logging to JSONL files.

**Console logging setup**
- Each bot initializes logging at the top of the file with `logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)`.
- A module-level logger instance is created via `logging.getLogger("checkin_bot")` or `logging.getLogger("voice_logger_bot")`.
- Log levels used are `info`, `warning`, and `error`; no debug-level logging is present.
- All external tool failures (whisper.cpp, ffmpeg, LLM requests) are logged via `log.error(...)` with the stderr or exception message as the formatted argument.

**Structured application logs (JSONL)**
- `bot.py` writes check-in entries to `data/checkins.jsonl` through a `log_checkin(text, audio_file)` helper that emits objects with fields: `timestamp` (ISO format), `text`, `audio_file`.
- `voice_logger_bot.py` writes conversation entries to `data/voice_log.jsonl` through a `log_entry(source, input_text, reply_text, audio_file=None)` helper that emits objects with fields: `timestamp`, `source` ("voice", "text", or "image"), `input`, `reply`, `audio_file`.
- Both files are appended line-by-line using `json.dumps(entry) + "\n"`, producing one JSON object per line — a standard JSONL pattern suitable for streaming ingestion.

**Architecture and conventions**
- Logging configuration is duplicated inline in each bot script rather than centralized in a shared module; there is no common logging package.
- Application events are written directly from handler functions rather than through an event bus or middleware layer.
- No log rotation, sinks to external services (e.g., ELK, CloudWatch), or async log handlers are implemented.
- Audio transcripts and trade data are persisted alongside their corresponding JSONL log entries, keeping related artifacts co-located under `data/audio/` and `data/images/`.

**Rules developers should follow**
- Use the module-level `log` variable (`log.info`, `log.warning`, `log.error`) for all runtime diagnostics; do not use `print()` for operational output.
- For persistent audit trails, write structured JSON objects via the existing `log_checkin` or `log_entry` helpers rather than ad-hoc file writes.
- Keep log field names consistent across bots (`timestamp`, `audio_file`, `source`, `input`, `reply`) so downstream consumers can parse uniformly.
- External tool errors must be captured and logged with `log.error(...)`, including the stderr or exception text for troubleshooting.