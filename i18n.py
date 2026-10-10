"""Tiny i18n helper: stock phrases + very rough language detection.

`say(key, lang)` and `detect_lang(text)` were called throughout the
original code but this module didn't exist yet.
"""
from __future__ import annotations
import re
import config as C

_PHRASES = {
    "greet":          {"en-US": "Samurai Agent online.", "en-GB": "Samurai Agent online.",
                        "hi-IN": "समुराई एजेंट तैयार है।", "ur-PK": "سامورائی ایجنٹ تیار ہے۔",
                        "hinglish": "Samurai Agent ready hai."},
    "listening":      {"en-US": "Listening…", "en-GB": "Listening…",
                        "hi-IN": "सुन रहा हूँ…", "ur-PK": "سن رہا ہوں…",
                        "hinglish": "Sun raha hoon…"},
    "thinking":       {"en-US": "On it…", "en-GB": "On it…",
                        "hi-IN": "कर रहा हूँ…", "ur-PK": "کر رہا ہوں…",
                        "hinglish": "Kar raha hoon…"},
    "done":           {"en-US": "Done.", "en-GB": "Done.",
                        "hi-IN": "हो गया।", "ur-PK": "ہو گیا۔", "hinglish": "Ho gaya."},
    "cancelled":      {"en-US": "Cancelled.", "en-GB": "Cancelled.",
                        "hi-IN": "रद्द किया।", "ur-PK": "منسوخ کر دیا۔", "hinglish": "Cancel kar diya."},
    "error":          {"en-US": "Something went wrong.", "en-GB": "Something went wrong.",
                        "hi-IN": "कुछ गड़बड़ हो गई।", "ur-PK": "کچھ غلط ہو گیا۔",
                        "hinglish": "Kuch gadbad ho gayi."},
    "not_understood": {"en-US": "I didn't catch a command in that.",
                        "en-GB": "I didn't catch a command in that.",
                        "hi-IN": "मुझे कोई कमांड समझ नहीं आई।",
                        "ur-PK": "مجھے کوئی کمانڈ سمجھ نہیں آئی۔",
                        "hinglish": "Mujhe koi command samajh nahi aayi."},
    "confirm":        {"en-US": "Are you sure? Say yes to confirm.",
                        "en-GB": "Are you sure? Say yes to confirm.",
                        "hi-IN": "क्या आप निश्चित हैं? हाँ कहें।",
                        "ur-PK": "کیا آپ کو یقین ہے؟ ہاں کہیں۔",
                        "hinglish": "Pakka? Haan bolo confirm karne ke liye."},
    "bye":            {"en-US": "Goodbye.", "en-GB": "Goodbye.",
                        "hi-IN": "अलविदा।", "ur-PK": "الوداع۔", "hinglish": "Alvida."},
}

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_HINGLISH_HINTS = re.compile(
    r"\b(kya|hai|kholo|khol|band|karo|chalu|dikhao|mujhe|aap|nahi|haan|likho)\b",
    re.IGNORECASE,
)


def say(key: str, lang: str) -> str:
    table = _PHRASES.get(key, {})
    return table.get(lang) or table.get("en-US", key)


def detect_lang(text: str) -> str:
    if not text:
        return C.RT.lang
    if _DEVANAGARI.search(text):
        return "hi-IN"
    if _ARABIC.search(text):
        return "ur-PK"
    if _HINGLISH_HINTS.search(text):
        return "hinglish"
    return "en-US"
