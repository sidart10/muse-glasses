"""Beru's voice: Kokoro text-to-speech on the Mac, served on localhost only.

Runs as its own process (``python -m muse_bridge.voice``) so the model never
slows or crashes the bridge. The bridge proxies ``POST /api/speech`` to it.

POST /tts {"text": "...", "voice": "af_heart"} -> audio/mpeg (one sentence or short paragraph)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import subprocess
from pathlib import Path

import soundfile as sf
from aiohttp import web

log = logging.getLogger("beru-voice")

MODEL_DIR = Path(os.environ.get("BERU_VOICE_DIR")
                 or Path.home() / "Library" / "Application Support" / "muse-glasses" / "voice")
DEFAULT_VOICE = os.environ.get("BERU_VOICE", "af_heart")
VOICES = {"af_heart", "af_bella", "af_nicole", "af_sarah", "am_michael", "am_adam", "bf_emma", "bm_george"}
MAX_CHARS = 600
SPEED = float(os.environ.get("BERU_VOICE_SPEED", "1.15"))
MAX_WAITING = 2  # synthesis jobs allowed to queue behind the running one; more get 503 at once
_lock = asyncio.Lock()  # one synthesis at a time keeps latency predictable
_waiting = 0


def _mp3(samples, sample_rate: int) -> bytes:
    wav = io.BytesIO()
    sf.write(wav, samples, sample_rate, format="WAV")
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-ac", "1",
                          "-b:a", "64k", "-f", "mp3", "pipe:1"], input=wav.getvalue(),
                         capture_output=True, check=True, timeout=10)
    return out.stdout


async def _tts(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "expected JSON"}, status=400)
    text = body.get("text") if isinstance(body, dict) else None
    voice = body.get("voice") or DEFAULT_VOICE
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
        return web.json_response({"error": "text must be 1-600 characters"}, status=400)
    if voice not in VOICES:
        return web.json_response({"error": "unknown voice"}, status=400)
    global _waiting
    if _waiting >= MAX_WAITING:
        return web.json_response({"error": "busy"}, status=503)
    kokoro = request.app["kokoro"]
    _waiting += 1
    entered = False
    try:
        async with _lock:
            _waiting -= 1
            entered = True
            if request.transport is None or request.transport.is_closing():
                return web.json_response({"error": "client gone"}, status=499)  # don't burn CPU for nobody
            try:
                samples, rate = await asyncio.to_thread(kokoro.create, text.strip(), voice=voice, speed=SPEED,
                                                        lang="en-us")
                audio = await asyncio.to_thread(_mp3, samples, rate)
            except Exception as exc:  # odd input Kokoro can't say: report it, keep serving
                log.warning("synthesis failed: %s", type(exc).__name__)
                return web.json_response({"error": "could not synthesize"}, status=422)
    finally:
        if not entered:
            _waiting -= 1  # cancelled while queued
    return web.Response(body=audio, content_type="audio/mpeg", headers={"Cache-Control": "no-store"})


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="muse_bridge.voice")
    parser.add_argument("--port", type=int, default=8796)
    args = parser.parse_args()
    from kokoro_onnx import Kokoro

    app = web.Application(client_max_size=16 * 1024)
    app["kokoro"] = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
    app.router.add_post("/tts", _tts)
    app.router.add_get("/health", _health)
    log.info("voice ready on 127.0.0.1:%d (default %s)", args.port, DEFAULT_VOICE)
    web.run_app(app, host="127.0.0.1", port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
