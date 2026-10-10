"""The floating samurai widget + speech bubble + system tray.

Frameless, per-pixel transparent, always-on-top, pinned to the bottom-right
corner, draggable (snaps back on release), and (Windows) pinned across
virtual desktops. This is what lets the app "run in the background": the
widget + tray icon stay alive with no console window (see main.py /
run_background.pyw).
"""
from __future__ import annotations
# pyright: reportMissingImports=false
import glob
import math
import logging
from typing import Optional

try:
    from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, QObject, pyqtSlot
    from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QFontMetrics
    from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu,
                                  QGraphicsDropShadowEffect, QInputDialog, QLineEdit)
    try:
        from PyQt6.QtGui import QAction
    except ImportError:  # PyQt6 < 6.1 kept QAction in QtWidgets
        from PyQt6.QtWidgets import QAction
except ImportError:  # pragma: no cover - allow PySide6 fallback for missing PyQt6 runtime
    from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal as pyqtSignal, QObject, Slot as pyqtSlot
    from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon, QFontMetrics
    from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu,
                                  QGraphicsDropShadowEffect, QInputDialog, QLineEdit)
    try:
        from PySide6.QtGui import QAction
    except ImportError:  # PySide6 keeps QAction in QtWidgets for some versions
        from PySide6.QtWidgets import QAction

import config as C
import i18n

log = logging.getLogger("ui")


class Signals(QObject):
    mic_clicked = pyqtSignal()
    lang_cycle = pyqtSignal()
    quit_app = pyqtSignal()
    text_command = pyqtSignal(str)  # typed (non-voice) input path


def cm_to_px(cm: float) -> int:
    scr = QApplication.primaryScreen()
    dpi = scr.physicalDotsPerInch() if scr else 96.0
    dpi = dpi or 96.0
    return max(40, round(cm / 2.54 * dpi))


class Bubble(QWidget):
    """Transient speech/thought bubble drawn to the LEFT of the samurai."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                          Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.lbl = QLabel(self)
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet(
            "color:#f5efe6; background:rgba(20,18,24,210); border:1px solid rgba(200,160,90,180);"
            "border-radius:10px; padding:8px 10px; font-size:13px;")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, text: str, anchor: QRect, ms: int = 4500):
        self.lbl.setText(text)
        fm = QFontMetrics(self.lbl.font())
        w = min(320, max(140, fm.boundingRect(text).width() + 24))
        self.lbl.setFixedWidth(w)
        self.lbl.adjustSize()
        h = self.lbl.sizeHint().height() + 4
        self.setFixedSize(w + 4, h + 4)
        x = anchor.left() - self.width() - 6
        y = anchor.top() + anchor.height() // 3
        scr = QApplication.primaryScreen().availableGeometry()
        x = max(scr.left() + 4, min(x, scr.right() - self.width() - 4))
        y = max(scr.top() + 4, min(y, scr.bottom() - self.height() - 4))
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(ms)


class SamuraiWidget(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool,
        )
        self.sig = Signals()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setWindowTitle(C.APP_NAME)

        self.w = cm_to_px(C.AGENT_CM_W)
        self.h = cm_to_px(C.AGENT_CM_H)
        self.setFixedSize(self.w, self.h)

        # ---- load art (single png OR sprite sheet) ----
        self.frames: list[QPixmap] = []
        sheet = sorted(glob.glob(C.SPRITE_GLOB))
        if sheet:
            for f in sheet:
                pm = QPixmap(f)
                if not pm.isNull():
                    self.frames.append(pm)
            log.info("sprite frames: %d", len(self.frames))
        if not self.frames:
            base = QPixmap(C.IDLE_IMAGE)
            if base.isNull():
                base = QPixmap(self.w, self.h)
                base.fill(QColor(0, 0, 0, 0))
                p = QPainter(base)
                p.setPen(QColor("#c8a05a"))
                p.drawText(base.rect(), Qt.AlignmentFlag.AlignCenter, "⚔\nsamurai\n(set asset)")
                p.end()
            self.frames = [base]

        self._i = 0
        self._phase = 0.0
        self._state = "idle"  # idle | listen | think | speak
        self._drag_off: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(33)

        self.bubble = Bubble()

        self._snap_to_corner()
        if C.FLOAT_ABOVE_TASKBAR and C.PLATFORM == "win32":
            QTimer.singleShot(300, self._pin_all_desktops)

    # ---------------- painting ----------------
    def _tick(self):
        self._phase += 0.06
        if self._state in ("listen", "think", "speak") and len(self.frames) > 1:
            self._i = (self._i + 1) % len(self.frames)
        elif len(self.frames) > 1 and int(self._phase * 2) % 6 == 0:
            self._i = (self._i + 1) % len(self.frames)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pm = self.frames[self._i % len(self.frames)]
        bob = math.sin(self._phase) * (self.h * 0.012)
        scale = 1.0
        if self._state == "listen":
            scale = 1.0 + 0.03 * math.sin(self._phase * 3)
        elif self._state == "think":
            scale = 1.0 + 0.02 * math.sin(self._phase * 1.5)
        dw = int(pm.width() * scale)
        dh = int(pm.height() * scale)
        x = (self.w - dw) // 2
        y = (self.h - dh) + int(bob)
        target = pm.scaled(dw, dh, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(x, y, target)
        col = {"idle": QColor(200, 160, 90, 90), "listen": QColor(90, 200, 140, 160),
               "think": QColor(120, 160, 230, 160), "speak": QColor(230, 120, 90, 160)}[self._state]
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(col)
        p.drawEllipse(2, 2, self.w - 4, self.h - 4)
        p.end()

    # ---------------- interaction ----------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._drag_off = self._press_pos - self.frameGeometry().topLeft()
        elif e.button() == Qt.MouseButton.RightButton:
            self.sig.lang_cycle.emit()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            moved = False
            if self._press_pos is not None:
                delta = e.globalPosition().toPoint() - self._press_pos
                moved = delta.manhattanLength() > 4
            self._drag_off = None
            self._press_pos = None
            if not moved:
                self.sig.mic_clicked.emit()
            self._snap_to_corner(keep_y=True)

    def mouseDoubleClickEvent(self, _):
        self.sig.mic_clicked.emit()

    # ---------------- placement ----------------
    def _geom(self):
        return QApplication.primaryScreen().geometry()

    def _snap_to_corner(self, keep_y: bool = False):
        g = self._geom()
        m = C.CORNER_MARGIN_PX
        x = g.right() - self.w - m
        y = self.y() if keep_y else g.bottom() - self.h - m
        y = max(g.top() + m, min(y, g.bottom() - self.h - m))
        self.move(x, y)

    def reflow(self):
        self.w = cm_to_px(C.AGENT_CM_W)
        self.h = cm_to_px(C.AGENT_CM_H)
        self.setFixedSize(self.w, self.h)
        self._snap_to_corner()

    # ---------------- typed (non-voice) input ----------------
    @pyqtSlot()
    def _open_type_prompt(self):
        """Text-input fallback: lets the agent take typed commands, not
        just spoken ones (Ctrl+Alt+T or the tray menu)."""
        text, ok = QInputDialog.getText(
            None, C.APP_NAME, "Command:", QLineEdit.EchoMode.Normal, ""
        )
        if ok and text.strip():
            self.sig.text_command.emit(text.strip())

    # ---------------- public API used by main ----------------
    def set_state(self, s: str):
        self._state = s
        if s == "idle":
            self.bubble.hide()

    def notify(self, text: str):
        self.bubble.show_text(text, self.frameGeometry())
        if self._state == "idle":
            self._state = "speak"
            QTimer.singleShot(2500, lambda: self.set_state("idle"))

    # ---------------- Windows: pin across all virtual desktops ------------
    def _pin_all_desktops(self):
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x80
            WS_EX_TOPMOST = 0x8
            WS_EX_NOACTIVATE = 0x08000000
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                   style | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            log.info("pinned topmost tool-window (cross-virtual-desktop friendly)")
        except Exception as e:
            log.debug("pin_all_desktops: %s", e)


def build_tray(app: QApplication, widget: SamuraiWidget, signals: Signals) -> QSystemTrayIcon:
    icon = QIcon(widget.frames[0]) if widget.frames and not widget.frames[0].isNull() else QIcon()
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu()
    a_listen = QAction("🎙  Talk  (Ctrl+Alt+S)", menu)
    a_listen.triggered.connect(signals.mic_clicked.emit)
    a_type = QAction("⌨  Type a command…  (Ctrl+Alt+T)", menu)
    a_type.triggered.connect(widget._open_type_prompt)
    a_lang = QAction("🌐  Cycle language  (Ctrl+Alt+L)", menu)
    a_lang.triggered.connect(signals.lang_cycle.emit)
    a_llm = QAction("🧠  LLM brain: " + ("ON" if C.ENABLE_LLM_BRAIN else "OFF"), menu)
    a_llm.setEnabled(False)
    a_quit = QAction("✖  Quit  (Ctrl+Alt+Q)", menu)
    a_quit.triggered.connect(signals.quit_app.emit)
    menu.addAction(a_listen)
    menu.addAction(a_type)
    menu.addAction(a_lang)
    menu.addSeparator()
    menu.addAction(a_llm)
    menu.addSeparator()
    menu.addAction(a_quit)
    tray.setContextMenu(menu)
    tray.setToolTip(f"{C.APP_NAME} — {i18n.say('greet', C.RT.lang)}")
    tray.activated.connect(
        lambda r: signals.mic_clicked.emit() if r == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    tray.show()
    return tray
