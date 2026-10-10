"""Speech I/O: mic -> text (STT) and text -> speaker (TTS).

Original bugs fixed here:
  - `log`, `_SR`, `_EDGE`, `queue`, `threading`, `io`, `asyncio`, `Optional`
    were all used but never imported/defined.
  - speech_recognition's Recognizer (`_SR`) was never constructed.
  - the speaker worker thread referenced `_EDGE` (undefined) instead of
    checking whether edge_tts actually imported.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import logging
import os
import queue
import threading
from typing import Optional

import config as C
import i18n

log = logging.getLogger("voice")

# ------------------------------------------------------------- optional deps
try:
    sr = importlib.import_module("speech_recognition")
    _SR = sr.Recognizer()
except Exception as e:  # pragma: no cover
    sr = None
    _SR = None
    log.warning("speech_recognition unavailable: %s", e)

try:
    import edge_tts  # type: ignore[import-not-found]
    _EDGE = True
except Exception as e:  # pragma: no cover
    edge_tts = None
    _EDGE = False
    log.warning("edge_tts unavailable, will fall back to pyttsx3: %s", e)

try:
    pyttsx3 = importlib.import_module("pyttsx3")
    _PYTTS = pyttsx3.init()
    _PYTTS.setProperty("rate", 175)
except Exception as e:  # pragma: no cover
    pyttsx3 = None
    _PYTTS = None
    log.warning("pyttsx3 unavailable: %s", e)

# Optional offline STT (Vosk)
_VOSK_MODEL = os.getenv("VOSK_MODEL_DIR")
_vosk_kaldi = None
try:
    from vosk import Model, KaldiRecognizer  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    Model = None
    KaldiRecognizer = None

if _VOSK_MODEL and Model is not None and KaldiRecognizer is not None:
    try:
        _vosk_kaldi = KaldiRecognizer(Model(_VOSK_MODEL), 16000)
    except Exception as e:
        log.warning("Vosk init failed: %s", e)


# --------------------------------------------------------------------- STT --
def listen_once(timeout: float = 6.0, phrase_limit: float = 8.0) -> Optional[str]:
    """Record one utterance and return transcript (or None). Blocks."""
    if _SR is None or sr is None:
        log.warning("listen_once called but speech_recognition is not installed")
        return None
    try:
        mic = sr.Microphone()
    except Exception as e:
        log.error("no microphone available: %s", e)
        return None

    try:
        with mic as source:
            _SR.adjust_for_ambient_noise(source, duration=0.4)
            audio = _SR.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
    except sr.WaitTimeoutError:
        return None
    except Exception as e:
        log.error("mic error: %s", e)
        return None

    locale = C.LANGUAGES[C.RT.lang]["stt"]

    # 1) online Google STT (handles hi/ur/en well)
    try:
        return _SR.recognize_google(audio, language=locale)
    except sr.UnknownValueError:
        pass
    except Exception as e:
        log.debug("google stt: %s", e)

    # 2) offline vosk fallback
    if _vosk_kaldi is not None:
        try:
            import json
            raw = audio.get_wav_data()
            _vosk_kaldi.AcceptWaveform(raw)
            res = json.loads(_vosk_kaldi.FinalResult())
            if res.get("text"):
                return res["text"]
        except Exception as e:
            log.debug("vosk stt: %s", e)
    return None


# --------------------------------------------------------------------- TTS --
def _play_edge(text: str, voice: str) -> None:
    if edge_tts is None:
        raise RuntimeError("edge_tts is not available")

    edge_mod = edge_tts

    async def _run():
        mp3 = io.BytesIO()
        comm = edge_mod.Communicate(text, voice)
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                mp3.write(chunk["data"])
        mp3.seek(0)
        _play_mp3(mp3.read())
    asyncio.run(_run())


def _play_mp3(data: bytes) -> None:
    # Prefer pygame if present (cross-platform mp3); else write temp + OS open
    try:
        pygame = importlib.import_module("pygame")
        import tempfile
        pygame.mixer.init()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(data)
            path = f.name
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.quit()
        os.unlink(path)
        return
    except Exception:
        pass

    import sys
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(data)
        path = f.name
    opener = {"win32": "start", "darwin": "afplay", "linux": "mpg123"}.get(sys.platform, "xdg-open")
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", path],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.error("tts playback: %s", e)


_speak_q: "queue.Queue[tuple[str, str]]" = queue.Queue()


def _speaker_worker():
    while True:
        text, lang = _speak_q.get()
        if not text:
            continue
        voice = C.LANGUAGES.get(lang, C.LANGUAGES["en-US"])["tts"]
        ok = False
        if _EDGE:
            try:
                _play_edge(text, voice)
                ok = True
            except Exception as e:
                log.debug("edge tts: %s", e)
        if not ok and _PYTTS is not None:
            try:
                _PYTTS.say(text)
                _PYTTS.runAndWait()
            except Exception as e:
                log.error("pyttsx3: %s", e)
        elif not ok:
            log.warning("no TTS backend available, dropping utterance: %r", text)


threading.Thread(target=_speaker_worker, daemon=True).start()


def speak(text: str, lang: Optional[str] = None) -> None:
    lang = lang or C.RT.lang
    if lang == "hinglish" or lang is None:
        lang = i18n.detect_lang(text) if text else C.RT.lang
    _speak_q.put((text, lang))


def cycle_language() -> str:
    keys = list(C.LANGUAGES.keys())
    C.RT.lang = keys[(keys.index(C.RT.lang) + 1) % len(keys)]
    return C.RT.lang
