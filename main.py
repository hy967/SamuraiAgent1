"""Entry point: wires UI + voice + deterministic tasks + optional LLM brain,
installs global hotkeys, and runs the listen -> think -> act -> speak loop on
a worker thread so the samurai widget never freezes the GUI thread.

Fixes vs. the original:
  1. `Q_ARG(str=f"...")` is invalid — Q_ARG takes positional args
     (type, value), not a `str=` keyword. This crashed the app the moment a
     transcript was heard. Fixed to `Q_ARG(str, f"...")`.
  2. The "bye" voice intent used to call `sys.exit(0)` from inside the
     worker thread, which only kills that thread — the Qt event loop (and
     the whole app) kept running. tasks.execute() now takes an `on_quit`
     callback wired to the real shutdown path below.
  3. Wrapped startup in try/except + logging so a background/console-less
     run (see run_background.pyw) leaves a trace in the log file instead of
     silently vanishing.
"""
from __future__ import annotations
import logging
import os
import sys
import threading
import queue
from importlib import import_module

try:
    # Resolve the installed Qt binding dynamically so static analyzers do not
    # report an error for the optional binding that is not installed.
    _qt_widgets = import_module("PyQt6.QtWidgets")
    _qt_core = import_module("PyQt6.QtCore")
except ModuleNotFoundError:  # pragma: no cover - fallback for environments with PyQt5
    _qt_widgets = import_module("PyQt5.QtWidgets")
    _qt_core = import_module("PyQt5.QtCore")

QApplication = _qt_widgets.QApplication
QMetaObject = _qt_core.QMetaObject
Qt = _qt_core.Qt
Q_ARG = _qt_core.Q_ARG
QObject = _qt_core.QObject
pyqtSlot = _qt_core.pyqtSlot
QTimer = _qt_core.QTimer

import config as C
import i18n
import voice
import tasks
import brain
from ui import SamuraiWidget, build_tray

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(C.LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("main")


class Bridge(QObject):
    """Thread-safe hopper: worker threads call these via QMetaObject.invokeMethod
    so the actual widget mutation always happens on the GUI thread."""

    def __init__(self):
        super().__init__()
        self._widget: SamuraiWidget | None = None

    def attach(self, w: SamuraiWidget):
        self._widget = w

    @pyqtSlot(str)
    def notify(self, text: str):
        """Show the bubble AND speak it out loud. This is the agent's real
        'voice output' path — it was missing before: notify() only ever
        drew the text bubble, so nothing but the startup greeting was ever
        actually spoken. Every task result/confirmation/error now goes
        through here and is heard, not just read."""
        if self._widget:
            self._widget.notify(text)
        voice.speak(text)

    @pyqtSlot(str)
    def notify_silent(self, text: str):
        """Same as notify() but text-only (no TTS) — used for echoing back
        what the mic heard, so the agent doesn't parrot your own words."""
        if self._widget:
            self._widget.notify(text)

    @pyqtSlot(str)
    def set_state(self, s: str):
        if self._widget:
            self._widget.set_state(s)


def autostart_register(enable: bool) -> None:
    if not enable:
        return
    try:
        if C.PLATFORM == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            # pythonw.exe (no console) if available, else the current interpreter
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            exe = pyw if os.path.exists(pyw) else sys.executable
            winreg.SetValueEx(
                key, C.APP_NAME, 0, winreg.REG_SZ,
                f'"{exe}" "{os.path.abspath(__file__)}"',
            )
            winreg.CloseKey(key)
        elif C.PLATFORM == "darwin":
            plist = os.path.expanduser(f"~/Library/LaunchAgents/{C.APP_NAME}.plist")
            with open(plist, "w") as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>{C.APP_NAME}</string>
<key>ProgramArguments</key><array><string>{sys.executable}</string><string>{os.path.abspath(__file__)}</string></array>
<key>RunAtLoad</key><true/></dict></plist>""")
        else:
            d = os.path.expanduser("~/.config/autostart")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{C.APP_NAME}.desktop"), "w") as f:
                f.write(
                    "[Desktop Entry]\nType=Application\n"
                    f"Name={C.APP_NAME}\n"
                    f"Exec={sys.executable} {os.path.abspath(__file__)}\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
        log.info("autostart registered")
    except Exception as e:
        log.warning("autostart: %s", e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running in the tray after the widget closes
    app.setApplicationName(C.APP_NAME)

    widget = SamuraiWidget()
    bridge = Bridge()
    bridge.attach(widget)
    widget.show()
    tray = build_tray(app, widget, widget.sig)

    # ---- global hotkeys (pynput) ----
    hot = None
    try:
        # Import dynamically so the optional pynput dependency does not trigger
        # a static-import warning when it is not installed in the active venv.
        kb = __import__("pynput.keyboard", fromlist=["keyboard"])
        hot = kb.GlobalHotKeys({
            C.HK_TOGGLE_LISTEN: widget.sig.mic_clicked.emit,
            C.HK_QUIT: widget.sig.quit_app.emit,
            C.HK_CYCLE_LANG: widget.sig.lang_cycle.emit,
            C.HK_TYPE_COMMAND: lambda: QMetaObject.invokeMethod(
                widget, "_open_type_prompt", Qt.ConnectionType.QueuedConnection
            ),
        })
        hot.start()
    except Exception as e:
        log.warning("global hotkeys unavailable (%s); use tray / click samurai", e)

    # ---- worker loop plumbing ----
    cmd_q: "queue.Queue[str | None]" = queue.Queue()
    stop_flag = threading.Event()

    def request_quit():
        # Safe to call from any thread: emitting a Qt signal cross-thread is
        # thread-safe and Qt marshals it to the GUI thread automatically.
        stop_flag.set()
        cmd_q.put(None)
        widget.sig.quit_app.emit()

    def worker():
        while not stop_flag.is_set():
            instr = cmd_q.get()
            if instr is None:
                break
            QMetaObject.invokeMethod(bridge, "set_state", Qt.ConnectionType.QueuedConnection, Q_ARG(str, "think"))
            handled = tasks.execute(
                instr,
                lambda t: QMetaObject.invokeMethod(bridge, "notify", Qt.ConnectionType.QueuedConnection, Q_ARG(str, t)),
                on_quit=request_quit,
            )
            if not handled and C.ENABLE_LLM_BRAIN:
                brain.run(
                    instr,
                    lambda t: QMetaObject.invokeMethod(bridge, "notify", Qt.ConnectionType.QueuedConnection, Q_ARG(str, t)),
                )
            elif not handled:
                QMetaObject.invokeMethod(
                    bridge, "notify", Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, i18n.say("not_understood", C.RT.lang)),
                )
            QMetaObject.invokeMethod(bridge, "set_state", Qt.ConnectionType.QueuedConnection, Q_ARG(str, "idle"))
            cmd_q.task_done()

    threading.Thread(target=worker, daemon=True).start()

    # ---- mic click / hotkey => record one utterance ----
    def do_listen():
        if C.RT.busy:
            return
        C.RT.busy = True
        QMetaObject.invokeMethod(bridge, "set_state", Qt.ConnectionType.QueuedConnection, Q_ARG(str, "listen"))
        QMetaObject.invokeMethod(bridge, "notify", Qt.ConnectionType.QueuedConnection,
                                  Q_ARG(str, i18n.say("listening", C.RT.lang)))

        def _rec():
            try:
                txt = voice.listen_once()
            finally:
                C.RT.busy = False
            if txt:
                log.info("heard [%s]: %s", C.RT.lang, txt)
                det = i18n.detect_lang(txt)
                if det != "en-US" or C.RT.lang == "hinglish":
                    C.RT.lang = det
                QMetaObject.invokeMethod(bridge, "notify_silent", Qt.ConnectionType.QueuedConnection,
                                          Q_ARG(str, f"\u201c{txt}\u201d"))
                cmd_q.put(txt)
            else:
                QMetaObject.invokeMethod(bridge, "set_state", Qt.ConnectionType.QueuedConnection, Q_ARG(str, "idle"))

        threading.Thread(target=_rec, daemon=True).start()

    widget.sig.mic_clicked.connect(do_listen)
    # Typed commands (tray menu / Ctrl+Alt+T) feed the exact same
    # think -> act -> speak pipeline as voice, they just skip STT.
    widget.sig.text_command.connect(lambda t: cmd_q.put(t))
    widget.sig.lang_cycle.connect(lambda: (
        voice.cycle_language(),
        widget.notify(f"🌐 {C.LANGUAGES[C.RT.lang]['label']}"),
        voice.speak(C.LANGUAGES[C.RT.lang]['label'], C.RT.lang),
    ))
    # Small grace period before the real exit: on_quit()/"bye" already queued
    # a spoken "Goodbye." — without this delay app.quit() can tear the event
    # loop down before the TTS speaker thread finishes playing it.
    widget.sig.quit_app.connect(lambda: (
        stop_flag.set(), cmd_q.put(None), QTimer.singleShot(1600, app.quit()),
    ))

    autostart_register(C.AUTO_START_AT_BOOT)

    voice.speak(i18n.say("greet", C.RT.lang), C.RT.lang)
    widget.notify(i18n.say("greet", C.RT.lang))
    log.info("SamuraiAgent ready. Lang=%s  LLM-brain=%s  Log=%s",
              C.RT.lang, "ON" if C.ENABLE_LLM_BRAIN else "off", C.LOG_PATH)

    try:
        return app.exec()
    finally:
        if hot is not None:
            hot.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.getLogger("main").exception("fatal startup error")
        raise
