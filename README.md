# SamuraiAgent — fixed

## Second pass — gaps closed in this update

The previous pass fixed the crash bugs (below). This pass closes four
functional gaps against what the agent was actually supposed to do:

1. **Voice output was never wired up.** `notify()` only ever drew the
   speech-bubble text — nothing but the startup greeting was actually
   spoken. Every task result, confirmation and error now goes through
   `Bridge.notify()`, which both shows the bubble *and* calls
   `voice.speak()`. (The one exception: the live "heard `<transcript>`"
   echo now goes through a separate `notify_silent()` so the agent
   doesn't parrot your own words back at you.)
2. **Web search opened a visible browser tab** (`webbrowser.open(...)`).
   `tasks.web_search()` now fetches results directly over HTTP
   (`requests` + `BeautifulSoup`, no browser process at all) and returns
   a short text summary that's shown in the bubble and spoken aloud —
   nothing ever appears on screen.
3. **Typed input didn't exist** — voice was the only way in. Added a
   tray menu item ("⌨ Type a command…") and `Ctrl+Alt+T`, both feeding
   the exact same think → act → speak pipeline as voice (`tasks.py`
   doesn't care whether text came from STT or a dialog box).
4. **`assets/idle.png` was empty** — the widget fell back to a text
   placeholder. The samurai artwork you provided is now in
   `assets/idle.png` with the white background removed (border-connected
   flood fill, feathered edge), so it floats as a proper transparent
   cutout instead of a white box.
5. **Hindi/Urdu "open `<app>`" mangled the app name.** Python's `\w`
   doesn't match Devanagari/Urdu combining vowel signs (the ु/ो/े "matras"
   that make up almost every Hindi word) — only their base letters. The
   `open_app` capture group used `[\w .\-&]+`, so "मुझे क्रोम खोलो" (open
   Chrome) was captured as a single mangled character instead of "क्रोम".
   Fixed by widening the capture class to the full Devanagari + Arabic
   Unicode blocks. Verified against a battery of standalone regex tests
   (see below) — English, Hindi, and Urdu "open" phrases all now capture
   the full app name correctly.

All of the above — intent matching across languages, the new headless
search parser (success/error/empty-result paths), and the `execute()`
dispatcher (including its error handling and the quit callback) — were
run against real inputs with only the OS-automation libraries
(`pyautogui`/`pyperclip`/`mss`) stubbed out, since this sandbox has no
display and no network to install PyQt6 itself. Everything that could be
tested without a real display passed.

Also: `requirements.txt` was missing `PyAudio` (required by
`speech_recognition` for `sr.Microphone()` — without it, mic input always
silently failed) and now lists `requests` + `beautifulsoup4` for the new
headless search.

## What was wrong (first pass)

The pasted code was actually **six different files concatenated into one**
(a build script, `voice.py`, `tasks.py`, `brain.py`, `ui.py`, `main.py`),
plus two modules that were imported everywhere but never existed at all:
`config.py` and `i18n.py`. On top of that there was a real crash bug:

- `main.py` called `Q_ARG(str=f"“{txt}”")` — `Q_ARG` doesn't take a keyword
  argument, so this raised `TypeError` the instant you spoke a command.
  Fixed to `Q_ARG(str, f"...")` everywhere.
- The "bye" voice command called `sys.exit(0)` from inside a background
  worker thread. That only kills the thread, not the app — the widget would
  look frozen/alive with no way to quit by voice. `tasks.execute()` now
  takes an `on_quit` callback wired to the real Qt shutdown path.
- `voice.py` used `log`, `_SR` (a `speech_recognition.Recognizer`), and
  `_EDGE` without ever defining them — this crashed the module on import.
- The build script's `if __name__ == "__main__": ...` block was followed by
  more code that would still execute at import time and launched an
  unrelated Tk placeholder window. Split into its own `build.py`.

## Files

| File | Purpose |
|---|---|
| `config.py` | All tunables: hotkeys, languages, LLM settings, autostart flag |
| `i18n.py` | Stock phrases per language + language detection |
| `voice.py` | Mic → text (Google/Vosk) and text → speech (edge-tts/pyttsx3) |
| `tasks.py` | Deterministic voice-command → OS action mapping (safe path) |
| `brain.py` | Optional LLM "computer-use" loop — **off by default** |
| `ui.py` | Floating widget, speech bubble, system tray |
| `main.py` | Wires everything together, runs the Qt event loop |
| `build.py` | Packages a no-console `.exe` with PyInstaller |
| `run_background.pyw` | Double-click launcher with no console window (Windows) |

## Running in the background

- **Dev/test:** `python main.py` (still shows a console).
- **No console window:** double-click `run_background.pyw` (Windows routes
  `.pyw` through `pythonw.exe` automatically), or run
  `pythonw.exe main.py` directly.
- **Packaged app:** `python build.py` produces `dist/SamuraiAgent.exe`
  built with `--windowed`, so it never opens a console either.
- **Start at login:** set `AUTO_START_AT_BOOT = True` in `config.py`. This
  writes a registry Run key (Windows), a LaunchAgent plist (macOS), or an
  autostart `.desktop` file (Linux) pointing at `pythonw`/`python` + `main.py`.
- The app itself already keeps running after the widget is closed
  (`setQuitOnLastWindowClosed(False)`) and lives in the system tray, so the
  only "foreground" surface is the small draggable widget + tray icon —
  quit it from the tray menu or `Ctrl+Alt+Q`.

## Before you run it

1. `pip install -r requirements.txt`
2. Art is already in place: `assets/idle.png` is the samurai cutout you
   provided. Swap it for your own `assets/idle.png`, or a sprite sheet
   named `assets/frame_0.png`, `frame_1.png`, ... (glob pattern is
   `config.SPRITE_GLOB`), any time.
3. Leave `ENABLE_LLM_BRAIN = False` until you've tested the deterministic
   command set in `tasks.py`. When you do enable it, set
   `SAMURAI_LLM_API_KEY` — this feature lets an LLM click/type/run
   commands on your machine from a screenshot, so only turn it on for a
   provider/account you trust and test it on a throwaway account first.
4. `set_volume` needs `pycaw`/`comtypes` on Windows; autostart needs
   `pywin32` for `winreg` (stdlib on Windows already, no install needed).
   Mic input needs `PyAudio`, which sometimes needs a system package first
   (`sudo apt install portaudio19-dev` on Debian/Ubuntu, or
   `brew install portaudio` on macOS) before `pip install pyaudio` works.

## Voice *and* typed commands (deterministic path, `tasks.py`)

Open app, search the web (headlessly — no browser tab, results are
spoken/shown as a summary), screenshot, set volume/brightness, lock/sleep/
shutdown/restart (with a spoken confirmation), minimize all, tell the time,
type dictated text, and quit — in English, Hindi, Urdu, or Hinglish.

- **Voice:** click the samurai, `Ctrl+Alt+S`, or the tray's "🎙 Talk".
- **Typed:** tray → "⌨ Type a command…", or `Ctrl+Alt+T`.

Every response — task result, confirmation, or error — is both shown in
the speech bubble and spoken aloud through the TTS pipeline
(edge-tts online, pyttsx3 offline fallback).

## Compiling into a standalone app

`build.py` packages everything into a single windowed executable with
PyInstaller (`python build.py` → `dist/SamuraiAgent.exe` on Windows, or a
platform-native binary on macOS/Linux). Two things worth knowing:

- **PyInstaller builds natively** — run `build.py` *on* the OS you want
  the executable for (a Windows box for a `.exe`, a Mac for a `.app`,
  etc.). There's no reliable cross-compile from Linux to Windows without
  extra tooling (e.g. Wine), so this has to be run on your own machine,
  not in a sandboxed environment.
- Test with `python main.py` first — confirm the samurai shows up, mic
  input works, and a couple of voice/typed commands behave — *then* run
  `python build.py`. Debugging is much easier before PyInstaller wraps
  everything into one file.
