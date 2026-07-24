---
kind: external_dependency
name: Local LLM Server (llama.cpp)
slug: llama-cpp-llama-server
category: external_dependency
category_hints:
    - framework_behavior
    - client_constraint
scope:
    - '**'
source_files:
    - voice_logger_bot.py
---

The bot launches a local llama-server process via subprocess, configured to serve the model `empero-ai/Qwythos-9B-v2-GGUF:Q8_0` on port 10000 with alias `MyQwythos`. It waits 20 seconds for startup before accepting requests. Both text chat completions and vision (image + base64 image_url) requests are sent to the OpenAI-compatible `/v1/chat/completions` endpoint. The server process is terminated on exit via atexit hook.