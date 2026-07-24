---
kind: error_handling
name: Ad-hoc Logging and Inline Exception Handling
category: error_handling
scope:
    - '**'
source_files:
    - bot.py
    - voice_logger_bot.py
---

This repository does not implement a structured error-handling system. Instead, both Telegram bots (`bot.py` and `voice_logger_bot.py`) rely on ad-hoc patterns:

1. **Logging-based error reporting**: External tool failures (whisper.cpp transcription, ffmpeg audio conversion, llama-server HTTP requests) are handled by checking `returncode` or catching broad `Exception`s, then logging via Python's `logging` module with `log.error(...)` and returning empty/default values rather than raising exceptions. There is no centralized error type hierarchy, sentinel errors, or error propagation.

2. **Inline try/except blocks**: Data parsing and numeric conversions use scattered `try/except (ValueError, TypeError)` blocks throughout the codebase to gracefully handle malformed input from JSONL files, CSV data, and user-provided text. Network calls to yfinance and the local LLM server catch generic `Exception` and log warnings/errors while returning fallback values.

3. **No custom exception types**: The codebase defines no custom exception classes, error codes, or error enums. Errors are represented implicitly through return values (empty strings, None, default lists) and logged messages.

4. **No middleware or global error handlers**: Each handler function manages its own error cases inline. There is no application-level error middleware, global exception hooks, or centralized error formatting for user-facing messages.

5. **User-facing error responses**: When operations fail, users receive simple text replies like "Couldn't convert the audio, sorry." or "Couldn't transcribe that — try again?" These are hardcoded strings rather than structured error responses.

6. **Process lifecycle management**: The voice logger bot uses `atexit.register(stop_llama_server)` for cleanup, but there's no robust process monitoring or restart logic for failed subprocesses.

The overall approach is pragmatic and functional for a small-scale project but lacks the architectural rigor needed for production reliability — no error categorization, no retry logic, no circuit breakers, and minimal observability beyond basic logging.