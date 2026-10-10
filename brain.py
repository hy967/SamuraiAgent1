"""Optional LLM 'computer-use' brain.

Looks at a screenshot + the user's instruction, asks a vision LLM for a JSON
plan of ATOMIC actions, then executes them with pyautogui. Loops until the
model says it is finished or config.LLM_MAX_STEPS is reached.

This is the part that can act on arbitrary instructions. It is powerful and
risky:
 - off by default (config.ENABLE_LLM_BRAIN)
 - destructive verbs are intercepted for a spoken confirmation
 - every step is logged
 - pyautogui FAILSAFE (mouse to top-left corner) always aborts
Only enable this on a machine and account you trust.
"""
from __future__ import annotations
import base64
import io
import json
import logging
import time
from typing import Any

from importlib import import_module

# These dependencies are only needed when the optional LLM brain is enabled.
# Keep importing the main app possible when that feature is disabled.
try:
    pyautogui: Any = import_module("pyautogui")
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    pyautogui = None
try:
    mss: Any = import_module("mss")
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    mss = None
try:
    Image: Any = import_module("PIL.Image")
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    Image = None

import config as C
import i18n
import tasks

log = logging.getLogger("brain")

SYSTEM_PROMPT = """You are SamuraiAgent, a desktop-automation AI. You receive a screenshot \
of the user's screen and an instruction (which may be in English, Hindi, Urdu or Hinglish). \
Plan the MINIMAL sequence of ATOMIC actions to fulfil it. Reply ONLY with JSON:
{"thought": "...", "actions":[{"type":"click","x":0-1,"y":0-1},{"type":"type","text":"..."},
{"type":"key","keys":["ctrl","s"]},{"type":"scroll","dy":-3},{"type":"wait","ms":500},
{"type":"open","app":"chrome"},{"type":"search","query":"..."},{"type":"screenshot"},
{"type":"done"}]}
Coordinates are NORMALISED 0..1 relative to the screenshot. Use 'done' as the last action \
when the goal is achieved. NEVER perform payments, deletions, sends or shutdowns without \
the user having explicitly asked in this turn. Be concise."""


def _grab_b64() -> tuple[str, int, int]:
    with mss.mss() as sct:
        mon = sct.monitors[0]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    w, h = img.size
    img = img.resize((max(1, int(w * C.LLM_SCREEN_SCALE)), max(1, int(h * C.LLM_SCREEN_SCALE))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode(), w, h


def _call_llm(messages: list[dict]) -> str:
    p = C.LLM_PROVIDER
    if p == "openai":
        OpenAI = import_module("openai").OpenAI
        cli = OpenAI(api_key=C.LLM_API_KEY)
        r = cli.chat.completions.create(model=C.LLM_MODEL, messages=messages, temperature=0.0,
                                         response_format={"type": "json_object"})
        return r.choices[0].message.content
    if p == "anthropic":
        anthropic = import_module("anthropic")
        cli = anthropic.Anthropic(api_key=C.LLM_API_KEY)
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), SYSTEM_PROMPT)
        convo = [m for m in messages if m["role"] != "system"]
        r = cli.messages.create(model=C.LLM_MODEL, max_tokens=1024, system=sys_msg, messages=convo)
        return r.content[0].text
    if p == "gemini":
        genai = import_module("google.generativeai")
        genai.configure(api_key=C.LLM_API_KEY)
        m = genai.GenerativeModel(C.LLM_MODEL)
        parts = []
        for msg in messages:
            if isinstance(msg["content"], list):
                for c in msg["content"]:
                    if c.get("type") == "text":
                        parts.append(c["text"])
                    elif c.get("type") == "image_url":
                        b64 = c["image_url"]["url"].split(",", 1)[1]
                        parts.append(Image.open(io.BytesIO(base64.b64decode(b64))))
            else:
                parts.append(msg["content"])
        return m.generate_content(parts).text
    if p == "ollama":
        requests = import_module("requests")
        r = requests.post(f"{C.OLLAMA_BASE}/api/chat", json={
            "model": C.LLM_MODEL, "messages": messages, "stream": False,
            "format": "json", "options": {"temperature": 0}})
        return r.json()["message"]["content"]
    raise RuntimeError(f"unknown provider {p}")


def _exec_action(a: dict, sw: int, sh: int, ui_notify) -> bool:
    t = a.get("type")
    if t == "done":
        return True
    if t == "click":
        pyautogui.click(int(a["x"] * sw), int(a["y"] * sh))
    elif t == "double_click":
        pyautogui.doubleClick(int(a["x"] * sw), int(a["y"] * sh))
    elif t == "right_click":
        pyautogui.rightClick(int(a["x"] * sw), int(a["y"] * sh))
    elif t == "move":
        pyautogui.moveTo(int(a["x"] * sw), int(a["y"] * sh))
    elif t == "drag":
        pyautogui.moveTo(int(a["x1"] * sw), int(a["y1"] * sh))
        pyautogui.dragTo(int(a["x2"] * sw), int(a["y2"] * sh), duration=a.get("duration", 0.4))
    elif t == "type":
        tasks.type_text(str(a.get("text", "")))
    elif t == "key":
        keys = a.get("keys", [])
        if keys:
            pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
    elif t == "scroll":
        pyautogui.scroll(int(a.get("dy", 0)))
    elif t == "wait":
        time.sleep(float(a.get("ms", 300)) / 1000.0)
    elif t == "open":
        tasks.open_app(str(a.get("app", "")))
    elif t == "search":
        ui_notify(tasks.web_search(str(a.get("query", ""))))
    elif t == "screenshot":
        ui_notify("📸 " + tasks.screenshot())
    elif t in {"shutdown", "restart", "delete", "send", "pay", "purchase", "format", "uninstall"}:
        ui_notify(i18n.say("confirm", C.RT.lang))
        if not tasks._confirm(ui_notify, C.RT.lang):
            ui_notify(i18n.say("cancelled", C.RT.lang))
            return True
        if t == "shutdown":
            tasks.shutdown_pc()
        elif t == "restart":
            tasks.restart_pc()
        else:
            log.warning("destructive verb %r has no dedicated handler; refusing", t)
    else:
        log.warning("unknown action %r", a)
    return False


def run(instruction: str, ui_notify) -> None:
    if not C.ENABLE_LLM_BRAIN:
        ui_notify(i18n.say("not_understood", C.RT.lang))
        return
    if pyautogui is None or mss is None or Image is None:
        ui_notify("LLM brain dependencies are missing. Install pyautogui, mss, and Pillow.")
        return
    if not C.LLM_API_KEY and C.LLM_PROVIDER != "ollama":
        ui_notify("LLM brain enabled but no API key set.")
        return

    lang = i18n.detect_lang(instruction)
    C.RT.lang = lang
    ui_notify(i18n.say("thinking", lang))
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": instruction}]}]

    for step in range(C.LLM_MAX_STEPS):
        b64, sw, sh = _grab_b64()
        messages.append({"role": "user", "content": [
            {"type": "text", "text": "Current screenshot (normalised coords 0..1):"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]})
        try:
            raw = _call_llm(messages)
        except Exception as e:
            log.exception("llm call")
            ui_notify(i18n.say("error", lang) + f": {e}")
            return

        log.info("brain step %d raw=%s", step, raw[:400])
        try:
            plan = json.loads(raw)
        except Exception:
            s = raw.strip()
            s = s[s.find("{"):s.rfind("}") + 1]
            plan = json.loads(s)

        acts = plan.get("actions", [])
        if plan.get("thought"):
            log.info("thought: %s", plan["thought"])

        finished = False
        for a in acts:
            if _exec_action(a, sw, sh, ui_notify):
                finished = True
                break
            time.sleep(0.25)

        messages.append({"role": "assistant", "content": raw})
        if finished:
            break

    ui_notify(i18n.say("done", lang))
