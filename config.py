"""Central configuration for SamuraiAgent.

This module was `import config as C`-ed from every other file in the
original code but never actually existed, which is the #1 reason nothing
ran. Fill in real values (especially LLM_API_KEY) before shipping.
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass, field

APP_NAME = "SamuraiAgent"
PLATFORM = sys.platform  # 'win32' | 'darwin' | 'linux'

LOG_PATH = os.path.join(os.path.expanduser("~"), f"{APP_NAME}.log")

# --------------------------------------------------------------------- UI --
AGENT_CM_W = 5.0
AGENT_CM_H = 5.0
CORNER_MARGIN_PX = 24
FLOAT_ABOVE_TASKBAR = True

# Point these at real assets. SPRITE_GLOB (e.g. "assets/frame_*.png") wins
# if it matches anything; otherwise IDLE_IMAGE is used; otherwise the app
# draws a text placeholder so it still runs with no art at all.
ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
SPRITE_GLOB = os.path.join(ASSET_DIR, "frame_*.png")
IDLE_IMAGE = os.path.join(ASSET_DIR, "idle.png")

# ------------------------------------------------------------------ hotkeys --
# pynput.keyboard.GlobalHotKeys format
HK_TOGGLE_LISTEN = "<ctrl>+<alt>+s"
HK_QUIT = "<ctrl>+<alt>+q"
HK_CYCLE_LANG = "<ctrl>+<alt>+l"
HK_TYPE_COMMAND = "<ctrl>+<alt>+t"

# ------------------------------------------------------------------ startup --
AUTO_START_AT_BOOT = False  # flip on once you've tested the app manually

# --------------------------------------------------------------------- LLM --
ENABLE_LLM_BRAIN = False  # off by default: this drives real mouse/keyboard input
LLM_PROVIDER = os.getenv("SAMURAI_LLM_PROVIDER", "anthropic")  # anthropic|openai|gemini|ollama
LLM_API_KEY = os.getenv("SAMURAI_LLM_API_KEY", "")
LLM_MODEL = os.getenv("SAMURAI_LLM_MODEL", "claude-sonnet-4-6")
LLM_SCREEN_SCALE = 0.5   # downscale screenshots before sending to the LLM
LLM_MAX_STEPS = 8
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")

# --------------------------------------------------------------- languages --
LANGUAGES = {
    "en-US": {"label": "English (US)", "stt": "en-US", "tts": "en-US-GuyNeural"},
    "en-GB": {"label": "English (UK)", "stt": "en-GB", "tts": "en-GB-RyanNeural"},
    "hi-IN": {"label": "हिन्दी",        "stt": "hi-IN", "tts": "hi-IN-MadhurNeural"},
    "ur-PK": {"label": "اردو",          "stt": "ur-PK", "tts": "ur-PK-AsadNeural"},
    "hinglish": {"label": "Hinglish",  "stt": "hi-IN", "tts": "hi-IN-MadhurNeural"},
}


@dataclass
class _Runtime:
    """Mutable process-wide state (was `C.RT` in the original code)."""
    lang: str = "en-US"
    busy: bool = False


RT = _Runtime()
