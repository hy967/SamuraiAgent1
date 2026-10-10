"""Deterministic, offline-safe task engine.

Maps a transcript (any of the 5 languages) to concrete desktop actions using
keyword/regex intent tables + pyautogui + platform commands. This is the
SAFE default. The LLM brain (brain.py) is used only when this returns no
intent AND config.ENABLE_LLM_BRAIN is on.

Fix vs. the original: the "bye" intent used to call sys.exit(0) from inside
a background worker thread. That only kills the worker thread (raises
SystemExit there), it does NOT quit a Qt app — so the app looked "stuck".
execute() now takes an optional on_quit callback that main.py wires up to
the real app-quit path.
"""
from __future__ import annotations
import os
import re
import time
import logging
import subprocess
import datetime
from typing import Optional, Callable

try:
    import pyautogui  # type: ignore[import-not-found]
except ModuleNotFoundError:  # optional dependency; handle gracefully at runtime
    pyautogui = None  # type: ignore[assignment]
try:
    import requests  # type: ignore[import-not-found]
except ModuleNotFoundError:  # optional dependency; handle gracefully at runtime
    requests = None  # type: ignore[assignment]
try:
    from bs4 import BeautifulSoup  # type: ignore[import-not-found]
except ModuleNotFoundError:  # optional dependency; handle gracefully at runtime
    BeautifulSoup = None  # type: ignore[assignment]

import config as C
import i18n

try:
    from PIL import Image as PILImage  # type: ignore[import-not-found]
except ModuleNotFoundError:
    PILImage = None

if pyautogui is not None:
    pyautogui.FAILSAFE = True   # slam mouse to top-left corner to abort
    pyautogui.PAUSE = 0.05
log = logging.getLogger("tasks")


# ---------------------------------------------------------------- helpers --
def _run(cmd: list[str]) -> None:
    log.info("exec: %s", cmd)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_app(name: str) -> None:
    name = name.strip()
    if C.PLATFORM == "win32":
        _run(["cmd", "/c", "start", "", name])
    elif C.PLATFORM == "darwin":
        _run(["open", "-a", name])
    else:
        _run(["xdg-open", name] if ("." in name or "/" in name) else [name.lower()])


def web_search(q: str, max_results: int = 3) -> str:
    """Headless web search: fetches results over HTTP directly and returns a
    short text summary. Never opens a browser window/tab — nothing appears
    on screen; the summary is shown in the bubble and spoken aloud.

    Fix vs. the original: this used to call `webbrowser.open(...)`, which
    pops a visible browser tab — the opposite of what a background voice
    agent should do. Swapped for a direct HTTP fetch + HTML parse.
    """
    if requests is None or BeautifulSoup is None:
        raise RuntimeError(
            "Web search requires the optional packages 'requests' and 'beautifulsoup4'. "
            "Install them with: pip install requests beautifulsoup4"
        )

    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": q},
            headers={"User-Agent": "Mozilla/5.0 (SamuraiAgent voice assistant)"},
            timeout=8,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select(".result")[:max_results]:
            title_el = res.select_one(".result__a")
            snippet_el = res.select_one(".result__snippet")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            results.append(f"{title} — {snippet}" if snippet else title)
        if not results:
            return f"No results found for {q}."
        return "  |  ".join(results)
    except Exception as e:
        log.warning("web_search failed: %s", e)
        return f"Web search failed: {e}"


def screenshot(path: Optional[str] = None) -> str:
    try:
        import importlib
        mss = importlib.import_module("mss")
        Image = PILImage if PILImage is not None else importlib.import_module("PIL.Image")
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Screenshot support requires the optional packages 'mss' and 'Pillow'. "
            "Install them with: pip install mss pillow"
        ) from e

    with mss.mss() as sct:
        mon = sct.monitors[0]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    out = path or os.path.join(os.path.expanduser("~"), f"screenshot_{int(time.time())}.png")
    img.save(out)
    return out


def set_volume(pct: int) -> None:
    pct = max(0, min(100, pct))
    if C.PLATFORM == "win32":
        try:
            from ctypes import cast, POINTER
            try:
                import importlib
                comtypes = importlib.import_module("comtypes")
                CLSCTX_ALL = comtypes.CLSCTX_ALL
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "Volume control on Windows requires the optional package 'comtypes'. "
                    "Install it with: pip install comtypes"
                ) from e
            try:
                import importlib
                pycaw_module = importlib.import_module("pycaw.pycaw")
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "Volume control on Windows requires the optional package 'pycaw'. "
                    "Install it with: pip install pycaw"
                ) from e
            AudioUtilities = pycaw_module.AudioUtilities
            IAudioEndpointVolume = pycaw_module.IAudioEndpointVolume
            dev = AudioUtilities.GetSpeakers()
            iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(iface, POINTER(IAudioEndpointVolume))
            vol.SetMasterVolumeLevelScalar(pct / 100.0, None)
        except Exception as e:
            log.warning("volume (pycaw) failed: %s", e)
    elif C.PLATFORM == "darwin":
        _run(["osascript", "-e", f"set volume output volume {pct}"])
    else:
        _run(["amixer", "-D", "default", "sset", "Master", f"{pct}%"])


def set_brightness(pct: int) -> None:
    pct = max(0, min(100, pct))
    try:
        import importlib
        try:
            sbc = importlib.import_module("screen_brightness_control")
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "Brightness control requires the optional package 'screen-brightness-control'. "
                "Install it with: pip install screen-brightness-control"
            ) from e
        sbc.set_brightness(pct)
    except Exception as e:
        log.warning("brightness failed: %s", e)


def lock_screen() -> None:
    if C.PLATFORM == "win32":
        _run(["rundll32.exe", "user32.dll,LockWorkStation"])
    elif C.PLATFORM == "darwin":
        _run(["pmset", "displaysleepnow"])
    else:
        _run(["loginctl", "lock-session"])


def sleep_pc() -> None:
    if C.PLATFORM == "win32":
        _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    elif C.PLATFORM == "darwin":
        _run(["pmset", "sleepnow"])
    else:
        _run(["systemctl", "suspend"])


def shutdown_pc() -> None:
    if C.PLATFORM == "win32":
        _run(["shutdown", "/s", "/t", "0"])
    elif C.PLATFORM == "darwin":
        _run(["osascript", "-e", 'tell app "System Events" to shut down'])
    else:
        _run(["shutdown", "-h", "now"])


def restart_pc() -> None:
    if C.PLATFORM == "win32":
        _run(["shutdown", "/r", "/t", "0"])
    elif C.PLATFORM == "darwin":
        _run(["osascript", "-e", 'tell app "System Events" to restart'])
    else:
        _run(["shutdown", "-r", "now"])


def _hotkey(*keys: str) -> None:
    if pyautogui is None:
        raise RuntimeError("pyautogui is not installed. Install it with: pip install pyautogui")
    pyautogui.hotkey(*keys)


def minimize_all() -> None:
    if C.PLATFORM == "win32":
        _hotkey("winleft", "d")
    elif C.PLATFORM == "darwin":
        _hotkey("command", "option", "w")
    else:
        _hotkey("super", "d")


def type_text(t: str) -> None:
    # non-ascii-safe (Devanagari/Arabic) via clipboard paste
    try:
        import importlib
        pyperclip = importlib.import_module("pyperclip")
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Clipboard typing requires the optional package 'pyperclip'. "
            "Install it with: pip install pyperclip"
        ) from e

    pyperclip.copy(t)
    time.sleep(0.05)
    if C.PLATFORM == "darwin":
        _hotkey("command", "v")
    else:
        _hotkey("ctrl", "v")


def current_time_str(lang: str) -> str:
    now = datetime.datetime.now()
    fmt = {"hi-IN": "%H:%M", "ur-PK": "%H:%M", "hinglish": "%I:%M %p",
           "en-US": "%I:%M %p", "en-GB": "%H:%M"}
    return now.strftime(fmt.get(lang, "%H:%M"))


# ------------------------------------------------------- intent patterns --
# _NAME_CHARS: app-name capture class. Plain \w does NOT match Devanagari/
# Urdu combining vowel signs (Unicode category Mn/Mc — e.g. the ु/ो/े matras
# that make up almost every Hindi word), only their base-letter characters.
# Without the explicit script ranges, "मुझे क्रोम खोलो" (open Chrome) would
# capture a single mangled character instead of "क्रोम" the moment a match
# started mid-word. Adding the full Devanagari + Arabic blocks fixes that.
_NAME_CHARS = r"[\w \u0900-\u097F\u0600-\u06FF.\-&]+"

INTENTS: list[tuple[str, list[str]]] = [
    ("open_app", [rf"(?:open|kholo|khol|chalu karo|launch|खोलो|चालू करो|کھولو)\s+(?:the\s+)?({_NAME_CHARS})",
                  rf"(?:मुझे|मेरे लिए|مجھے)?\s*({_NAME_CHARS})\s*(?:खोलो|kholo|کھولو)"]),
    ("search_web", [r"(?:search|google|dhundo|khojo|ढूंढो|تلاش|खोजो|search for)\s+(?:for\s+)?(.+)",
                     r"(.+?)\s*(?:के बारे में|about|بارے میں|ke baare mein)"]),
    ("screenshot", [r"(screenshot|स्क्रीनशॉट|اسکرین شاٹ|photo of screen|screen capture|पर्दा की तस्वीर)"]),
    ("volume_set", [r"volume\s*(?:to\s*)?(\d{1,3})", r"आवाज़\s*(\d{1,3})", r"آواز\s*(\d{1,3})",
                     r"(?:volume|आवाज़|آواز)\s*(?:badha|ghata|increase|decrease|बढ़ा|घटा|اونی|نیچی)\s*(\d{1,3})?"]),
    ("brightness", [r"brightness\s*(?:to\s*)?(\d{1,3})", r"चमक|روشنی|brightness"]),
    ("lock", [r"(lock(?: the)? (?:pc|screen|computer)|स्क्रीन लॉक|تالا|pc lock|lock karo)"]),
    ("sleep", [r"(sleep|so jao|सो जाओ|سو جاؤ|hibernate)"]),
    ("shutdown", [r"(shutdown|shut down|बंद करो pc|pc बंद|بند کرو|band karo computer)"]),
    ("restart", [r"(restart|reboot|रीस्टार्ट|دوبارہ شروع)"]),
    ("minimize", [r"(minimize all|show desktop|सब छोटा|سب چھوٹا|desktop dikhao)"]),
    ("time", [r"(what(?:'s| is) the time|time kya hai|समय|وقت|kitne baje)"]),
    ("type", [r"(?:type|लिखो|likho|لکھو|write)\s*[\"']?(.+?)[\"']?$"]),
    ("bye", [r"(bye|goodbye|alvida|अलविदा|الوداع|chalo so jao|quit)"]),
]


def _match(transcript: str) -> Optional[tuple[str, Optional[str]]]:
    t = transcript.strip()
    for name, pats in INTENTS:
        for p in pats:
            m = re.search(p, t, re.IGNORECASE)
            if m:
                arg = m.group(1) if m.groups() and m.group(1) else None
                return name, arg
    return None


# --------------------------------------------------------- dispatcher -----
def execute(
    transcript: str,
    ui_notify: Callable[[str], None],
    on_quit: Optional[Callable[[], None]] = None,
) -> bool:
    """Returns True if an intent was handled (even partially)."""
    lang = i18n.detect_lang(transcript)
    C.RT.lang = lang if lang != "en-US" or C.RT.lang == "hinglish" else C.RT.lang
    hit = _match(transcript)
    if not hit:
        return False
    name, arg = hit
    log.info("intent=%s arg=%r lang=%s", name, arg, lang)
    try:
        if name == "open_app" and arg:
            ui_notify(i18n.say("thinking", lang)); open_app(arg); ui_notify(i18n.say("done", lang))
        elif name == "search_web" and arg:
            ui_notify(i18n.say("thinking", lang))
            ui_notify(web_search(arg))
        elif name == "screenshot":
            p = screenshot(); ui_notify(f"📸 {p}")
        elif name == "volume_set":
            set_volume(int(arg or 50)); ui_notify(i18n.say("done", lang))
        elif name == "brightness":
            set_brightness(int(arg or 70)); ui_notify(i18n.say("done", lang))
        elif name == "lock":
            lock_screen()
        elif name == "sleep":
            sleep_pc()
        elif name in ("shutdown", "restart"):
            if not _confirm(ui_notify, lang):
                ui_notify(i18n.say("cancelled", lang))
                return True
            (shutdown_pc if name == "shutdown" else restart_pc)()
        elif name == "minimize":
            minimize_all(); ui_notify(i18n.say("done", lang))
        elif name == "time":
            ui_notify(current_time_str(lang))
        elif name == "type" and arg:
            type_text(arg); ui_notify(i18n.say("done", lang))
        elif name == "bye":
            ui_notify(i18n.say("bye", lang))
            if on_quit:
                on_quit()
        else:
            return False
        return True
    except Exception as e:
        log.exception("task error")
        ui_notify(i18n.say("error", lang) + f" ({e})")
        return True


def _confirm(ui_notify, lang) -> bool:
    ui_notify(i18n.say("confirm", lang))
    txt = ""
    try:
        import voice
        txt = (voice.listen_once() or "").lower()
    except Exception:
        pass
    return any(k in txt for k in ("yes", "haan", "हाँ", "ہاں", "y", "ok", "ठीक", "theek"))
