"""LLM server management, transcription, and AI request helpers."""

import base64
import datetime as dt
import json
import subprocess
import time
from pathlib import Path

import requests

from .config import (
    LLAMA_CMD,
    LLAMA_MODEL,
    LLAMA_STARTUP_WAIT_SECONDS,
    LLAMA_URL,
    LOG_FILE,
    WHISPER_BIN,
    WHISPER_MODEL,
    log,
)

llama_process: subprocess.Popen | None = None

# Mutable system prompt (loaded/set at runtime by handlers)
system_prompt: str = ""


def _is_server_running() -> bool:
    """Check if the llama-server is already responding on its port."""
    try:
        resp = requests.get(LLAMA_URL.replace("/v1/chat/completions", "/health"), timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def start_llama_server() -> None:
    """Launch the local LLM server as a background process (skip if already running)."""
    global llama_process
    if _is_server_running():
        log.info("Llama server already running, skipping startup.")
        return
    log.info("Starting llama server: %s", " ".join(LLAMA_CMD))
    llama_process = subprocess.Popen(
        LLAMA_CMD,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # NOTE: the server is deliberately NOT stopped on bot exit — it is
    # expensive to reload the model, and start_llama_server() reuses a
    # running instance via _is_server_running(). No atexit hook on purpose.
    log.info("Waiting %ss for the model to load...", LLAMA_STARTUP_WAIT_SECONDS)
    time.sleep(LLAMA_STARTUP_WAIT_SECONDS)


def stop_llama_server() -> None:
    """Stop the server this process spawned (unused by default; kept for
    manual shutdown tooling)."""
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


def ask_llm(user_text: str) -> str | None:
    """Send text to the local llama-server and return its reply.

    Returns None if the server is unreachable, so callers can distinguish a
    real (possibly empty) reply from a transport failure.
    """
    try:
        resp = requests.post(
            LLAMA_URL,
            json={
                "model": LLAMA_MODEL,
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
        return None


def ask_llm_vision(image_path: Path, prompt: str) -> str:
    """Send an image + prompt to the local llama-server vision endpoint."""
    try:
        with open(image_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")

        resp = requests.post(
            LLAMA_URL,
            json={
                "model": LLAMA_MODEL,
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
    """Append an interaction entry to the JSONL log."""
    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "input": input_text,
        "reply": reply_text,
        "audio_file": audio_file,
    }
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")
