---
kind: external_dependency
name: Whisper Speech-to-Text (whisper.cpp)
slug: whisper-cpp
category: external_dependency
category_hints:
    - client_constraint
scope:
    - '**'
source_files:
    - voice_logger_bot.py
    - SETUP.md
---

Voice notes received by the bot are converted from .ogg to .wav via ffmpeg, then transcribed using the whisper.cpp CLI (`whisper-cli.exe`) with the `ggml-base.en.bin` model. The transcription output is written to a `.txt` file alongside the audio. This is an external native binary dependency that must be built separately and pointed to via `WHISPER_BIN` and `WHISPER_MODEL` paths.