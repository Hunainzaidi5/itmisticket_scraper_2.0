import sys
import os
import webbrowser
import pandas as pd
import json
import re
from datetime import datetime, timedelta
import psutil
import time
from threading import Event, Lock, Thread
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                           QHBoxLayout, QPushButton, QLabel, QProgressBar,
                           QFileDialog, QTextEdit, QFrame, QSplitter, QSpinBox,
                           QCheckBox, QGroupBox, QGridLayout, QLineEdit, QComboBox,
                           QMessageBox, QTabWidget, QFormLayout, QToolBar,
                           QGraphicsDropShadowEffect, QScrollArea, QSystemTrayIcon,
                           QToolButton, QSizeGrip)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QSize, QTimer, QEvent, QObject
from PyQt6.QtGui import (QFont, QTextCursor, QIcon, QShortcut, QKeySequence, QAction,
                          QPalette, QColor, QPixmap, QPainter, QImage)

try:
    from PIL import Image, ImageFilter
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, UnexpectedAlertPresentException, WebDriverException

if getattr(sys, "frozen", False):
    _app_cache_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "ITMIS_Ticket_Scraper")
    os.makedirs(_app_cache_dir, exist_ok=True)
    os.environ.setdefault("WDM_LOCAL", "1")
    os.environ.setdefault("WDM_CACHE_DIR", os.path.join(_app_cache_dir, "wdm_cache"))
    os.environ.setdefault("SE_CACHE_PATH", os.path.join(_app_cache_dir, "selenium_cache"))


def apply_soft_shadow(widget: QWidget, blur_radius: int = 28, y_offset: int = 4, alpha: int = 40) -> None:
    """Apply a subtle drop shadow to make borders feel softer/more premium."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur_radius)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)

def _enable_acrylic_blur_windows(widget: QWidget, tint_rgba) -> bool:
    """Windows: try the modern DWM backdrop API (Win11 22H2+ Mica/Acrylic)
    first, then fall back to the undocumented but battle-tested
    SetWindowCompositionAttribute accent-policy call (Win10 1803+/Win11)."""
    import ctypes
    from ctypes import wintypes

    hwnd = wintypes.HWND(int(widget.winId()))

    try:
        dwmapi = ctypes.windll.dwmapi
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_TRANSIENTWINDOW = 3
        backdrop = ctypes.c_int(DWMSBT_TRANSIENTWINDOW)
        hr = dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )

        if hr == 0:
            class MARGINS(ctypes.Structure):
                _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                            ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]
            margins = MARGINS(-1, -1, -1, -1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

            try:
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                use_dark = ctypes.c_int(0)
                dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(use_dark), ctypes.sizeof(use_dark)
                )
            except Exception:
                pass

            try:
                DWMWA_BORDER_COLOR = 34
                DWMWA_CAPTION_COLOR = 35
                DWMWA_COLOR_NONE = ctypes.c_int(-2)
                dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_CAPTION_COLOR,
                    ctypes.byref(DWMWA_COLOR_NONE), ctypes.sizeof(DWMWA_COLOR_NONE)
                )
                dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_BORDER_COLOR,
                    ctypes.byref(DWMWA_COLOR_NONE), ctypes.sizeof(DWMWA_COLOR_NONE)
                )
            except Exception:
                pass
            return True
    except Exception:
        pass

    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_int),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        r, g, b, a = tint_rgba
        gradient_color = (a << 24) | (b << 16) | (g << 8) | r

        accent = ACCENT_POLICY()
        accent.AccentFlags = 2
        accent.GradientColor = gradient_color
        accent.AnimationId = 0

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19
        data.SizeOfData = ctypes.sizeof(accent)

        set_attr = ctypes.windll.user32.SetWindowCompositionAttribute
        for accent_state in (4, 3):
            accent.AccentState = accent_state
            data.Data = ctypes.pointer(accent)
            if set_attr(hwnd, ctypes.pointer(data)):
                return True
        return False
    except Exception:
        return False

def _enable_acrylic_blur_macos(widget: QWidget, tint_rgba) -> bool:
    """macOS: drop a real NSVisualEffectView (the same frosted-glass layer
    used by Finder/Control Center) behind the Qt content view, in
    .underWindowBackground blending mode so it samples whatever is
    actually behind the window. Pure ctypes + the Objective-C runtime, no
    PyObjC dependency. Best-effort: any failure just leaves the window as
    plain alpha-transparent (still see-through, just not blurred)."""
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        def cls(name):
            return objc.objc_getClass(name.encode())

        def sel(name):
            return objc.sel_registerName(name.encode())

        def send(receiver, selector, *args, restype=ctypes.c_void_p, argtypes=None):
            fn = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p)
            proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *(argtypes or [type(a) for a in args]))
            caller = ctypes.cast(fn, proto)
            return caller(receiver, selector, *args)

        nsview = ctypes.c_void_p(int(widget.winId()))
        nswindow = send(nsview, sel("window"))

        content_view = send(nswindow, sel("contentView"))

        class NSRect(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                        ("w", ctypes.c_double), ("h", ctypes.c_double)]
        bounds = send(content_view, sel("bounds"), restype=NSRect, argtypes=[])

        effect_cls = cls("NSVisualEffectView")
        effect_view = send(effect_cls, sel("alloc"))
        effect_view = send(effect_view, sel("initWithFrame:"), bounds, restype=ctypes.c_void_p, argtypes=[NSRect])

        NSVisualEffectBlendingModeBehindWindow = 0
        NSVisualEffectStateActive = 1

        NSVisualEffectMaterialPopover = 9

        send(effect_view, sel("setMaterial:"), ctypes.c_long(NSVisualEffectMaterialPopover),
             restype=None, argtypes=[ctypes.c_long])
        send(effect_view, sel("setBlendingMode:"), ctypes.c_long(NSVisualEffectBlendingModeBehindWindow),
             restype=None, argtypes=[ctypes.c_long])
        send(effect_view, sel("setState:"), ctypes.c_long(NSVisualEffectStateActive),
             restype=None, argtypes=[ctypes.c_long])
        NSViewWidthSizable, NSViewHeightSizable = 2, 16
        send(effect_view, sel("setAutoresizingMask:"),
             ctypes.c_ulong(NSViewWidthSizable | NSViewHeightSizable),
             restype=None, argtypes=[ctypes.c_ulong])

        send(content_view, sel("addSubview:positioned:relativeTo:"),
             effect_view, ctypes.c_long(-1), ctypes.c_void_p(0),
             restype=None, argtypes=[ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p])

        try:
            nsstring_cls = cls("NSString")
            name = send(nsstring_cls, sel("stringWithUTF8String:"),
                        b"NSAppearanceNameVibrantLight",
                        restype=ctypes.c_void_p, argtypes=[ctypes.c_char_p])
            appearance_cls = cls("NSAppearance")
            appearance = send(appearance_cls, sel("appearanceNamed:"), name,
                               restype=ctypes.c_void_p, argtypes=[ctypes.c_void_p])
            if appearance:
                send(nswindow, sel("setAppearance:"), appearance,
                     restype=None, argtypes=[ctypes.c_void_p])
        except Exception:
            pass

        send(nswindow, sel("setOpaque:"), ctypes.c_bool(False), restype=None, argtypes=[ctypes.c_bool])
        return True
    except Exception:
        return False

def enable_acrylic_blur(widget: QWidget, tint_rgba=(255, 255, 255, 1)) -> bool:
    """
    Make `widget` (a top-level window) genuinely see-through with a blurred
    backdrop, like Windows 11's Mica/Acrylic or macOS's frosted panels.
    This is what makes the glassmorphism real instead of a QSS illusion:
    QSS alone can only fake glass with translucent colors painted over
    whatever the window happened to have; actual "see-through blur" needs
    the OS compositor to blur the live desktop/content behind the window.

    tint_rgba: (r, g, b, a) tint painted over the blur, 0-255 each. Kept
    low-alpha by default so more of the real blurred backdrop shows
    through — a heavier tint looks like a painted panel, not glass.

    Returns True if native OS blur-behind was applied, False if we only
    fell back to plain alpha transparency (still see-through, no blur —
    happens on Linux compositors without a blur effect, or if the native
    call fails for any reason).
    """
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    if sys.platform == "win32":
        return _enable_acrylic_blur_windows(widget, tint_rgba)
    if sys.platform == "darwin":
        return _enable_acrylic_blur_macos(widget, tint_rgba)
    return False

TICKET_REGEX = re.compile(r'^[A-Z]{2,5}\.[A-Z0-9]{2,8}\.\d{4}\.\d{2}\.\d{6,10}$', re.IGNORECASE)
TICKET_REGEX_PARTIAL = re.compile(r'\d{4}\.\d{2}\.\d{6,10}', re.IGNORECASE)
LIVE_MONITOR_TICKET_URL_RE = re.compile(r'service-tickets/details/([^/?#]+)', re.IGNORECASE)
LIVE_MONITOR_TIME_RE = re.compile(r'\d{1,2}:\d{2}(:\d{2})?\s*[AP]M', re.IGNORECASE)


GLASS_BG          = "rgba(255, 255, 255, 0.13)"
GLASS_BG_HOVER    = "rgba(255, 255, 255, 0.20)"
GLASS_BORDER      = "1px solid rgba(255, 255, 255, 0.32)"
GLASS_BORDER_SOFT = "1px solid rgba(255, 255, 255, 0.18)"
GLASS_RADIUS      = "16px"
GLASS_SHADOW      = "0 4px 24px rgba(0, 0, 0, 0.08)"

_LM_CARD_DEFAULT = f"""
    QFrame#lmTicketCard {{
        background: {GLASS_BG};
        border: {GLASS_BORDER};
        border-radius: {GLASS_RADIUS};
    }}
"""

_LM_CARD_NEW_FLASH = """
    QFrame#lmTicketCard {
        background: rgba(255, 255, 255, 0.22);
        border: 1px solid rgba(100, 180, 255, 0.50);
        border-radius: 16px;
    }
"""

GLASS_MESSAGEBOX_QSS = """
    QMessageBox {
        background: rgba(255, 255, 255, 0.20);
        border: 1px solid rgba(255, 255, 255, 0.35);
        border-radius: 12px;
    }
    QMessageBox QLabel {
        color: #1C140F;
        background: transparent;
        font-family: "Segoe UI";
        font-size: 10pt;
    }
    QMessageBox QLabel#qt_msgbox_label {
        font-weight: 600;
    }
    QMessageBox QLabel#qt_msgbox_informativelabel {
        color: #5C4638;
        font-weight: 500;
    }
    QMessageBox QPushButton {
        background-color: rgba(255, 255, 255, 0.15);
        color: #1C140F;
        border: 2px solid #FF6200;
        border-radius: 6px;
        padding: 6px 18px;
        min-width: 76px;
        font-weight: 700;
        font-family: "Segoe UI";
        font-size: 9.5pt;
        letter-spacing: 0.4px;
    }
    QMessageBox QPushButton:hover {
        background-color: rgba(255, 98, 0, 0.55);
        color: #ffffff;
    }
    QMessageBox QPushButton:default {
        background-color: rgba(255, 98, 0, 0.75);
        color: #ffffff;
    }
    QMessageBox QPushButton:default:hover {
        background-color: #E84C00;
        border-color: #E84C00;
    }
"""

class GlassDialogBlurFilter(QObject):
    """App-wide event filter that gives every QMessageBox real OS
    blur-behind (acrylic/vibrancy) the moment it's shown, so popups raised
    anywhere - including QMessageBox.information/warning/critical/question(...)
    calls that never expose a widget instance to style manually - match the
    glassmorphism look used by ConfigDialog and the rest of the app.
    """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QMessageBox):
            if not obj.property("_glass_blur_applied"):
                obj.setProperty("_glass_blur_applied", True)
                enable_acrylic_blur(obj)
                obj.setStyleSheet(GLASS_MESSAGEBOX_QSS)
        return False


_LM_CARD_DUPE_FLASH = """
    QFrame#lmTicketCard {
        background: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 200, 80, 0.45);
        border-radius: 16px;
    }
"""

_LM_STATUS_IDLE = f"""
    QFrame#lmStatusBanner {{
        background: {GLASS_BG};
        border: none;
        border-radius: {GLASS_RADIUS};
    }}
"""

_LM_STATUS_ACTIVE = """
    QFrame#lmStatusBanner {
        background: rgba(255, 255, 255, 0.15);
        border: none;
        border-radius: 16px;
    }
"""


BTN_PRIMARY = f"""
    QPushButton {{
        background: rgba(255, 98, 0, 0.75);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.25);
        border-radius: {GLASS_RADIUS};
        padding: 9px 18px;
        font-weight: 700;
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QPushButton:hover {{
        background: rgba(255, 98, 0, 0.88);
    }}
    QPushButton:pressed {{
        background: rgba(255, 98, 0, 0.65);
    }}
    QPushButton:disabled {{
        background: rgba(255, 255, 255, 0.02);
        color: rgba(255, 255, 255, 0.35);
        border: {GLASS_BORDER_SOFT};
    }}
"""

BTN_PRIMARY_OUTLINE = f"""
    QPushButton {{
        background: {GLASS_BG};
        color: #FF6200;
        border: 1.5px solid rgba(255, 98, 0, 0.55);
        border-radius: {GLASS_RADIUS};
        padding: 8px 17px;
        font-weight: 700;
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QPushButton:hover {{
        background: rgba(255, 98, 0, 0.12);
        border-color: rgba(255, 98, 0, 0.75);
    }}
    QPushButton:disabled {{
        color: rgba(255, 98, 0, 0.30);
        border-color: rgba(255, 98, 0, 0.20);
        background: transparent;
    }}
"""

BTN_SUCCESS = BTN_PRIMARY.replace("255, 98, 0", "0, 166, 81")
BTN_SUCCESS_OUTLINE = BTN_PRIMARY_OUTLINE.replace("255, 98, 0", "0, 166, 81").replace("#FF6200", "#00A651")

BTN_DANGER = BTN_PRIMARY.replace("255, 98, 0", "211, 47, 47")
BTN_DANGER_OUTLINE = BTN_PRIMARY_OUTLINE.replace("255, 98, 0", "211, 47, 47").replace("#FF6200", "#D32F2F")

BTN_NEUTRAL = f"""
    QPushButton {{
        background: {GLASS_BG};
        color: #3A2E26;
        border: {GLASS_BORDER};
        border-radius: {GLASS_RADIUS};
        padding: 9px 18px;
        font-weight: 700;
        font-family: "Segoe UI";
        font-size: 10pt;
    }}
    QPushButton:hover {{
        background: {GLASS_BG_HOVER};
    }}
"""

BTN_NEUTRAL_OUTLINE = BTN_NEUTRAL

BTN_FILTER_VALID = f"""
    QPushButton {{
        background: {GLASS_BG};
        color: #007A3B;
        border: 1px solid rgba(0, 166, 81, 0.35);
        border-radius: {GLASS_RADIUS};
        padding: 8px 16px;
        font-weight: 700;
    }}
    QPushButton:checked {{
        background: rgba(0, 166, 81, 0.18);
        border-color: rgba(0, 166, 81, 0.55);
        color: #007A3B;
    }}
"""

BTN_FILTER_INVALID = BTN_FILTER_VALID.replace("0, 166, 81", "211, 47, 47").replace("#007A3B", "#D32F2F")
BTN_FILTER_ALL = f"""
    QPushButton {{
        background: {GLASS_BG};
        color: #5C4638;
        border: {GLASS_BORDER};
        border-radius: {GLASS_RADIUS};
        padding: 8px 16px;
        font-weight: 700;
    }}
    QPushButton:checked {{
        background: rgba(255, 255, 255, 0.05);
    }}
"""

def is_valid_ticket(ticket: str) -> tuple:
    """Check if a ticket number matches the valid format.

    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    ticket = ticket.strip().upper()

    if not TICKET_REGEX.match(ticket):
        return (False, "Invalid format: Expected format LHR.L2SP.YYYY.MM.XXXXXXXX")


    segments = ticket.split('.')

    if len(segments) != 5:
        return (False, f"Invalid format: Expected 5 segments, got {len(segments)}")


    if not segments[0].isalpha() or len(segments[0]) < 2 or len(segments[0]) > 5:
        return (False, f"Invalid location code: '{segments[0]}' - Must be 2-5 letters")


    if not segments[1].isalnum() or len(segments[1]) < 2 or len(segments[1]) > 8:
        return (False, f"Invalid line/system code: '{segments[1]}' - Must be 2-8 alphanumeric characters")


    try:
        year = int(segments[2])
        if year < 2000 or year > 2099:
            return (False, f"Invalid year: '{segments[2]}' - Must be between 2000-2099")
    except ValueError:
        return (False, f"Invalid year: '{segments[2]}' - Must be 4 digits")


    try:
        month = int(segments[3])
        if month < 1 or month > 12:
            return (False, f"Invalid month: '{segments[3]}' - Must be between 01-12")
    except ValueError:
        return (False, f"Invalid month: '{segments[3]}' - Must be 2 digits")


    if not segments[4].isdigit() or len(segments[4]) < 6 or len(segments[4]) > 10:
        return (False, f"Invalid reference number: '{segments[4]}' - Must be 6-10 digits")

    return (True, "")

def make_link(ticket: str, base_url: str) -> str:
    """Convert a ticket number to a full URL."""
    return f"{base_url}{ticket.strip().upper()}"

def parse_tickets(raw: str) -> list:
    """Parse ticket numbers from raw text input."""
    if not raw or not str(raw).strip():
        return []
    raw = str(raw).replace('(', '').replace(')', '').upper()
    TICKET_SCAN = re.compile(r'[A-Z]{2,5}\.[A-Z0-9]{2,8}\.\d{4}\.\d{2}\.\d{6,10}', re.IGNORECASE)
    chunks = re.split(r'[,;|]+|\r\n|\n|\r|\t+', raw)
    chunks = [t.strip() for t in chunks if t.strip()]
    tokens = []
    for chunk in chunks:
        matches = [m[0].upper() for m in TICKET_SCAN.finditer(chunk)]
        if matches:
            tokens.extend(matches)
        else:
            tokens.append(chunk)
    seen = set()
    return [t for t in tokens if not (t in seen or seen.add(t))]

class Config:
    """Configuration management class"""
    def __init__(self):
        self.settings = QSettings("TicketScraper", "Settings")
        self.load_defaults()

    def load_defaults(self):
        """Load default configuration values"""
        self.LOGIN_URL = self.settings.value("login_url", "https://itmis.olmrts.com.pk/#/login")
        self.DASHBOARD_URL = self.settings.value("dashboard_url", "https://itmis.olmrts.com.pk/#/app/dashboard")
        self.BASE_TICKET_URL = self.settings.value("base_ticket_url", "https://itmis.olmrts.com.pk/#/app/service-tickets/details/")
        self.LAST_FILE_PATH = self.settings.value("last_file_path", "")


        self.LOGIN_TIMEOUT = int(self.settings.value("login_timeout", 30))
        self.PAGE_LOAD_TIMEOUT = int(self.settings.value("page_load_timeout", 15))
        self.ELEMENT_WAIT_TIMEOUT = int(self.settings.value("element_wait_timeout", 5))
        self.DELAY_BETWEEN_TICKETS = int(self.settings.value("delay_between_tickets", 1))


        self.MAX_RETRIES = int(self.settings.value("max_retries", 3))
        self.RETRY_DELAY = int(self.settings.value("retry_delay", 5))


        self.CHROME_BINARY_PATH = self.settings.value("chrome_binary", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        self.HEADLESS_MODE = self.settings.value("headless_mode", False, type=bool)


        raw_keywords = self.settings.value("keyword_list", "") or ""
        self.KEYWORD_LIST = [
            kw.strip() for kw in str(raw_keywords).split(",") if kw.strip()
        ]

        self.CONTENT_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[1]/div[2]/p'
        self.COMMENT_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[2]/div/div/ul'
        self.STATION_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[1]/div/div[2]/span[2]'
        self.RESOLUTION_TIME_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[3]/span[2]'
        self.TICKET_START_TIME_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[1]/span[2]'
        self.RESOLVED_DATETIME_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[4]/span[2]'
        self.TICKET_CATEGORY_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[1]/div/div[1]/span[2]'
        self.LAST_COMMENT_TIME_XPATH = '/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[2]/div/div/ul/li/div/span'


        self.DASHBOARD_FIRST_TICKET_XPATH = '/html/body/app-root/app-init-app/div/div/app-dashboard/div/div[4]/div/div/div/p-scrollpanel/div/div[1]/div/p-table/div/div/table/tbody/tr[1]'
        self.NOTIFICATION_BELL_ICON_XPATH = '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/a/em'
        self.NOTIFICATION_BELL_DROPDOWN_XPATH = '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div'
        self.NOTIFICATION_COUNT_XPATH = '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div/div[1]/span/span'
        self.NOTIFICATION_TICKET_NUMBER_XPATH = '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div/div[2]/a[1]/div/div[1]/span[1]'
        self.NOTIFICATION_LINK_XPATH = '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div/div[2]/a[1]'

    def save_settings(self):
        """Save current configuration"""
        self.settings.setValue("login_url", self.LOGIN_URL)
        self.settings.setValue("dashboard_url", self.DASHBOARD_URL)
        self.settings.setValue("base_ticket_url", self.BASE_TICKET_URL)
        self.settings.setValue("login_timeout", self.LOGIN_TIMEOUT)
        self.settings.setValue("page_load_timeout", self.PAGE_LOAD_TIMEOUT)
        self.settings.setValue("element_wait_timeout", self.ELEMENT_WAIT_TIMEOUT)
        self.settings.setValue("delay_between_tickets", self.DELAY_BETWEEN_TICKETS)
        self.settings.setValue("max_retries", self.MAX_RETRIES)
        self.settings.setValue("retry_delay", self.RETRY_DELAY)
        self.settings.setValue("chrome_binary", self.CHROME_BINARY_PATH)
        self.settings.setValue("headless_mode", self.HEADLESS_MODE)
        self.settings.setValue("keyword_list", ",".join(self.KEYWORD_LIST))
        self.settings.setValue("content_xpath", self.CONTENT_XPATH)
        self.settings.setValue("comment_xpath", self.COMMENT_XPATH)
        self.settings.setValue("station_xpath", self.STATION_XPATH)
        self.settings.setValue("resolution_time_xpath", self.RESOLUTION_TIME_XPATH)
        self.settings.setValue("ticket_start_time_xpath", self.TICKET_START_TIME_XPATH)
        self.settings.setValue("resolved_datetime_xpath", self.RESOLVED_DATETIME_XPATH)
        self.settings.setValue("ticket_category_xpath", self.TICKET_CATEGORY_XPATH)
        if hasattr(self, "LAST_FILE_PATH"):
            self.settings.setValue("last_file_path", self.LAST_FILE_PATH)

class ScraperThread(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    validation_errors = pyqtSignal(list)

    def __init__(self, links_file, config):
        super().__init__()
        self.links_file = links_file
        self.config = config
        self.driver = None
        self.is_running = True
        self.results = []
        self.current_ticket = 0
        self.start_time = None

    def stop(self):
        """Stop the scraping process"""
        self.is_running = False
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

    def run(self):
        try:
            if not self.is_running:
                return



            self.log.emit("Starting Chrome setup without closing existing Chrome instances...")


            if not self.setup_chrome():
                self.error.emit("Failed to setup Chrome browser")
                return


            if not self.login():
                self.error.emit("Failed to login to the system")
                return


            if not self.read_links_file():
                self.error.emit("Failed to read links from file")
                return


            self.process_all_links()


            if self.results:
                self.save_results(self.results)
                self.log.emit("Scraping completed successfully!")
                self.finished.emit(self.results)
            else:
                self.log.emit("No results to save.")
                self.finished.emit([])

        except Exception as e:
            self.log.emit(f"Critical error: {str(e)}")
            self.error.emit(f"Critical error: {str(e)}")
        finally:
            self.cleanup()

    def kill_chrome_processes(self):
        """Kill existing Chrome processes - ONLY call manually if needed"""
        try:
            self.log.emit("⚠️ Manual Chrome cleanup requested...")
            killed_count = 0
            for proc in psutil.process_iter(['name', 'pid']):
                if proc.info['name'] in ['chrome.exe', 'chromedriver.exe']:
                    try:
                        proc.kill()
                        killed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

            if killed_count > 0:
                self.log.emit(f"Killed {killed_count} Chrome processes")
                time.sleep(1)
            else:
                self.log.emit("No Chrome processes found to kill.")

        except Exception as e:
            self.log.emit(f"Warning: Could not clean Chrome processes: {str(e)}")

    def _build_chrome_options(self, profile_dir):
        """Build Chrome options, trying a named profile first then falling back to a temp profile."""
        chrome_options = Options()
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        chrome_options.add_argument("--profile-directory=Default")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        if self.config.HEADLESS_MODE:
            chrome_options.add_argument("--headless=new")
        if os.path.exists(self.config.CHROME_BINARY_PATH):
            chrome_options.binary_location = self.config.CHROME_BINARY_PATH
        return chrome_options

    def _try_launch_driver(self, service, chrome_options):
        """Launch webdriver and apply anti-detection script."""
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.set_page_load_timeout(self.config.PAGE_LOAD_TIMEOUT)
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def setup_chrome(self):
        """Setup Chrome browser with robust configuration."""
        try:
            self.log.emit("Setting up Chrome browser...")

            base_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "ITMIS_Ticket_Scraper")
            profile_dir = os.path.join(base_dir, "chrome_profile")
            os.makedirs(profile_dir, exist_ok=True)


            self.log.emit("Using Selenium Manager for automatic ChromeDriver management...")
            for attempt, p_dir in enumerate([profile_dir, None], start=1):
                try:
                    opts = self._build_chrome_options(
                        p_dir if p_dir else os.path.join(base_dir, f"chrome_profile_tmp_{attempt}")
                    )
                    if p_dir is None:


                        args = [a for a in opts.arguments if not a.startswith("--user-data-dir") and not a.startswith("--profile-directory")]
                        opts.arguments.clear()
                        for a in args:
                            opts.add_argument(a)
                        self.log.emit("Retrying with a temporary Chrome profile (profile lock detected)...")
                    self._try_launch_driver(Service(), opts)
                    self.log.emit("✅ Chrome browser setup successful with Selenium Manager!")
                    return True
                except Exception as e:
                    last_err = e
                    if attempt == 1:
                        self.log.emit(f"Selenium Manager attempt {attempt} failed: {e}")

            self.log.emit(f"Selenium Manager failed: {last_err}")


            self.log.emit("Falling back to webdriver-manager...")
            try:
                driver_path = ChromeDriverManager().install()



                if not driver_path.lower().endswith(".exe"):
                    import glob
                    exe_candidates = glob.glob(
                        os.path.join(os.path.dirname(driver_path), "**", "chromedriver.exe"),
                        recursive=True,
                    )
                    if not exe_candidates:
                        raise FileNotFoundError(
                            f"chromedriver.exe not found near '{driver_path}'. "
                            "Please download ChromeDriver manually from https://chromedriver.chromium.org/downloads "
                            "and place it in your PATH."
                        )
                    driver_path = exe_candidates[0]
                    self.log.emit(f"Resolved chromedriver.exe at: {driver_path}")

                opts = self._build_chrome_options(profile_dir)
                self._try_launch_driver(Service(driver_path), opts)
                self.log.emit("✅ Chrome browser setup successful with webdriver-manager fallback!")
                return True
            except Exception as e:
                self.log.emit(f"webdriver-manager fallback failed: {e}")


            self.log.emit("Trying chromedriver from system PATH...")
            import shutil
            path_driver = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
            if path_driver:
                opts = self._build_chrome_options(profile_dir)
                self._try_launch_driver(Service(path_driver), opts)
                self.log.emit(f"✅ Chrome browser setup successful using PATH driver: {path_driver}")
                return True
            else:
                self.log.emit(
                    "chromedriver not found on PATH. "
                    "Download it from https://chromedriver.chromium.org/downloads and add it to your PATH."
                )

        except Exception as e:
            self.log.emit(f"Error setting up Chrome: {str(e)}")
        return False

    def login(self):
        """Enhanced login with retry mechanism"""
        for attempt in range(self.config.MAX_RETRIES):
            try:
                if not self.is_running:
                    return False

                self.log.emit(f"Login attempt {attempt + 1}/{self.config.MAX_RETRIES}")
                self.driver.get(self.config.LOGIN_URL)

                self.log.emit("Please enter login credentials and press Sign In")
                self.log.emit("Waiting for successful login...")


                WebDriverWait(self.driver, self.config.LOGIN_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "app-dashboard"))
                )


                try:
                    user_info = self.driver.execute_script("return localStorage.getItem('user');")
                    if user_info:
                        self.log.emit("Login verification successful")
                    else:
                        self.log.emit("Warning: Could not verify user session")
                except Exception as e:
                    self.log.emit(f"Could not access localStorage: {str(e)}")

                time.sleep(1)
                self.log.emit("Login successful, ready for ticket processing...")
                return True

            except TimeoutException:
                self.log.emit(f"Login timeout on attempt {attempt + 1}")
                if attempt < self.config.MAX_RETRIES - 1:
                    self.log.emit(f"Retrying in {self.config.RETRY_DELAY} seconds...")
                    time.sleep(self.config.RETRY_DELAY)

            except Exception as e:
                self.log.emit(f"Login error on attempt {attempt + 1}: {str(e)}")
                if attempt < self.config.MAX_RETRIES - 1:
                    time.sleep(self.config.RETRY_DELAY)

        self.log.emit("All login attempts failed")
        return False

    def read_links_file(self):
        """Enhanced file reading with better error handling - handles both ticket numbers and URLs"""
        try:
            if not os.path.exists(self.links_file):
                self.log.emit(f"File not found: {self.links_file}")
                return False

            self.log.emit(f"Reading links from: {self.links_file}")


            df = pd.read_excel(self.links_file)
            self.log.emit(f"Excel file loaded successfully. Shape: {df.shape}")


            if 'Link' not in df.columns:
                self.log.emit(f"Available columns: {list(df.columns)}")
                self.log.emit("Error: 'Link' column not found in Excel file")
                return False


            link_data = df['Link'].dropna().astype(str).tolist()

            if not link_data:
                self.log.emit("No valid ticket numbers found in the Link column")
                return False


            self.links = []
            invalid_entries = []
            for item in link_data:
                clean_item = item.strip()
                if clean_item.startswith('http'):

                    self.links.append(clean_item)
                else:
                    valid, error_msg = is_valid_ticket(clean_item)
                    if valid:

                        full_url = make_link(clean_item, self.config.BASE_TICKET_URL)
                        self.links.append(full_url)
                    else:

                        if '/' in clean_item:
                            clean_num = clean_item.split('/')[-1]
                            valid, error_msg = is_valid_ticket(clean_num)
                            if valid:
                                full_url = make_link(clean_num, self.config.BASE_TICKET_URL)
                                self.links.append(full_url)
                            else:
                                invalid_entries.append((clean_item, error_msg))
                        else:
                            invalid_entries.append((clean_item, error_msg))

            if invalid_entries:
                self.validation_errors.emit(invalid_entries)

            if not self.links:
                self.log.emit("No valid ticket numbers or URLs found after processing")
                return False

            self.log.emit(f"Successfully processed {len(self.links)} ticket URLs")
            return True

        except Exception as e:
            self.log.emit(f"Error reading links file: {str(e)}")
            return False

    def process_all_links(self):
        """Process all links with enhanced error handling"""
        if not hasattr(self, 'links') or not self.links:
            self.log.emit("No links to process")
            return

        total_links = len(self.links)
        self.log.emit(f"Starting to process {total_links} tickets...")
        self.start_time = time.time()

        for i, link in enumerate(self.links, 1):
            if not self.is_running:
                self.log.emit("Scraping stopped by user")
                break

            self.log.emit(f"\n--- Processing ticket {i}/{total_links} ---")


            if i > 1:
                self.log.emit(f"Waiting {self.config.DELAY_BETWEEN_TICKETS} seconds before next ticket...")
                time.sleep(self.config.DELAY_BETWEEN_TICKETS)


            result = self.process_link_with_retry(link, i, total_links)
            self.results.append(result)


            self.current_ticket = i
            progress = int((i / total_links) * 100)
            self.progress.emit(progress)

    def process_link_with_retry(self, link, current_index, total_links):
        """Process a single link with retry mechanism"""
        for attempt in range(self.config.MAX_RETRIES):
            try:
                self.log.emit(f"Attempt {attempt + 1}/{self.config.MAX_RETRIES} for ticket {current_index}")


                if attempt > 0:
                    self.clear_browser_state()

                result = self.process_single_ticket(link)

                if result.get('Error Type') != 'Critical':
                    self.log.emit(f"Successfully processed ticket {current_index}")
                    return result
                else:
                    raise Exception(result.get('Error', 'Unknown error'))

            except Exception as e:
                self.log.emit(f"Attempt {attempt + 1} failed: {str(e)}")

                if attempt < self.config.MAX_RETRIES - 1:
                    self.log.emit(f"Retrying in {self.config.RETRY_DELAY} seconds...")
                    time.sleep(self.config.RETRY_DELAY)


                    if 'login' in str(e).lower() or 'session' in str(e).lower():
                        self.log.emit("Session might have expired, attempting re-login...")
                        if not self.login():
                            self.log.emit("Re-login failed, continuing with next ticket")
                            break


        self.log.emit(f"⚠️ Ticket {current_index} failed after {self.config.MAX_RETRIES} attempts. Continuing with next ticket.")
        return {
            "Contains Keywords": False,
            "URL": link,
            "Check Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Error": f"Failed after {self.config.MAX_RETRIES} attempts",
            "Error Type": "Critical",
            "Ticket Index": current_index
        }

    def clear_browser_state(self):
        """Clear browser cache and state"""
        try:
            self.driver.delete_all_cookies()
            self.driver.execute_script('window.localStorage.clear();')
            self.driver.execute_script('window.sessionStorage.clear();')
        except Exception as e:
            self.log.emit(f"Warning: Could not clear browser state: {str(e)}")

    def handle_alerts(self):
        """Handle any alert popups"""
        try:
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            self.log.emit(f"Alert detected: {alert_text}")
            alert.accept()
            return True
        except:
            return False

    def _locate_by_xpath_instant(self, xpath, fallback_xpath, field_name):
        try:
            elements = self.driver.find_elements(By.XPATH, xpath)
            if elements:
                return elements[0]
        except:
            pass
        if fallback_xpath:
            try:
                elements = self.driver.find_elements(By.XPATH, fallback_xpath)
                if elements:
                    return elements[0]
            except:
                pass
        return None

    def _locate_by_xpath(self, wait, xpath, fallback_xpath, field_name):
        """Locate element with visibility, presence, then fallback XPath."""
        self.log.emit(f"Attempting to locate {field_name} using XPath: {xpath}")
        for check_name, condition in (
            ("visibility", EC.visibility_of_element_located),
            ("presence", EC.presence_of_element_located),
        ):
            try:
                element = wait.until(condition((By.XPATH, xpath)))
                self.log.emit(f"{field_name} element found with {check_name} check")
                return element
            except Exception as e:
                self.log.emit(f"{field_name} {check_name} check failed: {str(e)}")
        if fallback_xpath:
            try:
                element = wait.until(
                    EC.presence_of_element_located((By.XPATH, fallback_xpath))
                )
                self.log.emit(f"{field_name} element found with fallback XPath")
                return element
            except Exception as e:
                self.log.emit(f"{field_name} fallback XPath check failed: {str(e)}")
        return None

    def _text_from_element(self, element, field_name, default="N/A"):
        if not element:
            return default
        try:
            text = (element.text or element.get_attribute("innerText") or "").strip()
            if text:
                self.log.emit(f"{field_name}: {text}")
                return text
        except Exception as e:
            self.log.emit(f"Error extracting {field_name}: {str(e)}")
        return default

    def process_single_ticket(self, link):
        """Process a single ticket with comprehensive error handling"""
        try:

            self.log.emit(f"Loading ticket: {link}")
            self.driver.get(link)


            current_url = self.driver.current_url
            if "login" in current_url.lower():
                self.log.emit("Session expired or redirected to login, attempting to re-login...")
                if not self.login():
                    raise Exception("Failed to re-login after redirection")

                self.driver.get(link)


            wait_short = WebDriverWait(self.driver, 3)
            self.log.emit("Waiting for ticket details to load...")

            content_element = None
            try:
                content_element = wait_short.until(
                    EC.presence_of_element_located((By.XPATH, self.config.CONTENT_XPATH))
                )
                self.log.emit("Content element loaded successfully.")
            except:

                try:
                    content_element = wait_short.until(
                        EC.presence_of_element_located((
                            By.XPATH,
                            "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[1]/div[2]/p"
                        ))
                    )
                    self.log.emit("Content element loaded using fallback XPath.")
                except Exception as e:
                    self.log.emit("Warning: Content element load timed out. Proceeding with instant extraction.")


            station_element = self._locate_by_xpath_instant(
                self.config.STATION_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[1]/div/div[2]/span[2]",
                "Station"
            )
            comment_element = self._locate_by_xpath_instant(
                self.config.COMMENT_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[2]/div/div/ul",
                "Comments"
            )
            resolution_time_element = self._locate_by_xpath_instant(
                self.config.RESOLUTION_TIME_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[3]/span[2]",
                "Resolution Time"
            )
            ticket_start_time_element = self._locate_by_xpath_instant(
                self.config.TICKET_START_TIME_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[1]/span[2]",
                "Ticket Start Time"
            )
            resolved_datetime_element = self._locate_by_xpath_instant(
                self.config.RESOLVED_DATETIME_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[2]/div/div[4]/span[2]",
                "Resolved Date Time"
            )
            ticket_category_element = self._locate_by_xpath_instant(
                self.config.TICKET_CATEGORY_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/aside/section[1]/div/div[1]/span[2]",
                "Ticket Category"
            )
            last_comment_time_element = self._locate_by_xpath_instant(
                self.config.LAST_COMMENT_TIME_XPATH,
                "/html/body/app-root/app-init-app/div/div/app-ticket-details/div/div[3]/div/section[2]/div/div/ul/li/div/span",
                "Last Comment Time"
            )


            content_text = ""
            if content_element:
                try:
                    content_text = content_element.get_attribute("innerText").strip()
                    self.log.emit(f"Content text: {content_text}")
                    self.log.emit(f"Content length: {len(content_text)} characters")
                except Exception as e:
                    self.log.emit(f"Error extracting content text: {str(e)}")


            station_number = "N/A"
            if station_element:
                try:
                    station_number = station_element.text.strip()
                    self.log.emit(f"Station Number: {station_number}")
                except Exception as e:
                    self.log.emit(f"Error extracting station number: {str(e)}")

            resolution_time = self._text_from_element(resolution_time_element, "Resolution Time")
            ticket_start_time = self._text_from_element(ticket_start_time_element, "Ticket Start Time")
            resolved_datetime = self._text_from_element(resolved_datetime_element, "Resolved Date Time")
            ticket_category = self._text_from_element(ticket_category_element, "Ticket Category")


            comment_text = ""
            if comment_element:
                try:
                    comment_text = comment_element.get_attribute("innerText").strip()
                    self.log.emit(f"Comment text: {comment_text[:200]}{'...' if len(comment_text) > 200 else ''}")
                    self.log.emit(f"Comment length: {len(comment_text)} characters")
                except Exception as e:
                    self.log.emit(f"Error extracting comment text: {str(e)}")


            combined_text = content_text
            if comment_text:
                if combined_text:
                    combined_text = f"{combined_text}\n\n--- Comments ---\n{comment_text}"
                else:
                    combined_text = comment_text
            all_text_lower = combined_text.lower()


            keywords_found = []
            if all_text_lower:
                for keyword in self.config.KEYWORD_LIST:
                    if keyword.lower() in all_text_lower:
                        keywords_found.append(keyword)

            has_keywords = len(keywords_found) > 0

            last_comment_time = "N/A"
            closed_within_time = False

            if last_comment_time_element:
                try:
                    last_comment_time = last_comment_time_element.text.strip()
                    date_matches = re.findall(r'(\d{2}/\d{2}/\d{4} \d{2}:\d{2} [AP]M)', last_comment_time)
                    if date_matches:
                        last_comment_time = date_matches[-1]
                except Exception as e:
                    self.log.emit(f"Error extracting last comment time: {str(e)}")
            elif comment_text:
                matches = re.findall(r'\((\d{2}/\d{2}/\d{4} \d{2}:\d{2} [AP]M)\)', comment_text)
                if matches:
                    last_comment_time = matches[-1]

            if ticket_start_time != "N/A" and last_comment_time != "N/A":
                try:
                    start_dt = datetime.strptime(ticket_start_time, "%d/%m/%Y %I:%M %p")
                    com_dt = datetime.strptime(last_comment_time, "%d/%m/%Y %I:%M %p")
                    diff = com_dt - start_dt
                    if diff <= timedelta(hours=2):
                        closed_within_time = True
                except Exception as e:
                    self.log.emit(f"Error calculating time difference: {str(e)}")
                    closed_within_time = False

            record = {
                "Contains Keywords": has_keywords,
                "URL": link,
                "Check Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Ticket Category": ticket_category,
                "Content Length": len(content_text),
                "Comment Length": len(comment_text),
                "Combined Length": len(combined_text),
                "Keywords Found": keywords_found,
                "Keyword Count": len(keywords_found),
                "Ticket Content": content_text[:1000] + "..." if len(content_text) > 1000 else content_text,
                "Ticket Comments": comment_text[:1000] + "..." if len(comment_text) > 1000 else comment_text,
                "Station Number": station_number,
                "Resolution Time": resolution_time,
                "Ticket Start Time": ticket_start_time,
                "Resolved Date Time": resolved_datetime,
                "Last Comment Time": last_comment_time,
                "Closed Within Time": closed_within_time,
                "Processing Status": "Success" if content_element and station_element else "Partial Success"
            }

            self.log.emit(f"Final record: {json.dumps(record, indent=2)}")

            return record
        except TimeoutException as e:
            self.log.emit(f"Timeout loading ticket content: {str(e)}")
            self.log.emit(f"Current page URL: {self.driver.current_url}")
            self.log.emit(f"Page source: {self.driver.page_source[:1000]}")
            return {
                "Contains Keywords": False,
                "URL": link,
                "Check Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Error": f"Timeout: {str(e)}",
                "Error Type": "Timeout",
                "Processing Status": "Failed"
            }

        except Exception as e:
            self.log.emit(f"Error processing ticket: {str(e)}")
            self.log.emit(f"Current page URL: {self.driver.current_url}")
            self.log.emit(f"Page source: {self.driver.page_source[:1000]}")
            return {
                "Contains Keywords": False,
                "URL": link,
                "Check Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Error": str(e),
                "Error Type": "Critical",
                "Processing Status": "Failed"
            }

    def save_results(self, results):
        """Enhanced results saving with backup"""
        try:
            if not results:
                self.log.emit("No results to save")
                return

            df = pd.DataFrame(results)


            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


            output_file = "rechecked.xlsx"
            df.to_excel(output_file, index=False)
            self.log.emit(f"Results saved to: {output_file}")


            backup_file = f"rechecked_backup_{timestamp}.xlsx"
            df.to_excel(backup_file, index=False)
            self.log.emit(f"Backup saved to: {backup_file}")


            summary = self.generate_summary(results)
            summary_file = f"summary_{timestamp}.json"
            with open(summary_file, 'w') as f:
                json.dump(summary, f, indent=2)
            self.log.emit(f"Summary saved to: {summary_file}")

        except Exception as e:
            self.log.emit(f"Error saving results: {str(e)}")

    def generate_summary(self, results):
        """Generate summary statistics"""
        total_tickets = len(results)
        successful_tickets = len([r for r in results if r.get('Processing Status') == 'Success'])
        keyword_tickets = len([r for r in results if r.get('Contains Keywords', False)])
        failed_tickets = total_tickets - successful_tickets

        return {
            "processing_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_tickets": total_tickets,
            "successful_tickets": successful_tickets,
            "failed_tickets": failed_tickets,
            "keyword_tickets": keyword_tickets,
            "success_rate": (successful_tickets / total_tickets * 100) if total_tickets > 0 else 0,
            "keyword_percentage": (keyword_tickets / successful_tickets * 100) if successful_tickets > 0 else 0
        }

    def cleanup(self):
        """Clean up resources"""
        try:
            if self.driver:
                self.driver.quit()
                self.log.emit("Browser closed successfully")
        except Exception as e:
            self.log.emit(f"Error during cleanup: {str(e)}")


class LiveMonitorThread(QThread):
    """Background thread: Chrome patrol mode across all open browser tabs."""

    status_update = pyqtSignal(str)
    ticket_captured = pyqtSignal(dict)
    duplicate_detected = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    monitoring_stopped = pyqtSignal()
    monitor_stats = pyqtSignal(int, int)

    def __init__(self, config, session_start=None):
        super().__init__()
        self.config = config
        self.driver = None
        self._stop_event = Event()
        self._driver_lock = Lock()
        self.processed_urls = set()
        self._processed_ids = set()
        self.tab_last_url = {}
        self.tab_pending = {}
        self._extracting_handles = set()
        self._dup_notify_times = {}
        self._duplicate_reported_ids = set()
        self._last_tab_count = 0
        self._all_tabs_closed_announced = False
        self.captured_tickets = []
        self._target_handle_map = {}
        self._last_handle_count = 0
        self._last_notification_count = None
        self._last_dashboard_ticket_count = 0
        self._dashboard_monitoring_enabled = True
        self._last_dashboard_refresh_time = 0
        self._dashboard_refresh_interval = 0.20
        self._silent_api_request = None
        self._silent_api_installed = False
        self._silent_api_discovery_attempts = 0
        self._silent_api_last_discovery_time = 0.0
        self._silent_api_discovery_interval = 1.0
        self._silent_api_poll_interval = 0.50
        self._silent_api_last_state_check = 0.0
        self._silent_api_state_check_interval = 0.20
        self._silent_api_last_seq = -1
        self._silent_api_baseline_initialized = False
        self._silent_api_baseline_ids = set()
        self._notification_poll_interval = 0.50
        self._last_notification_poll_time = 0.0
        self._tab_scan_interval = 0.50
        self._last_tab_scan_time = 0.0
        self._dashboard_handle = None
        self._last_known_dashboard_tickets = []
        self._dashboard_baseline_initialized = False
        self._baseline_ticket_ids = set()
        self._last_dashboard_scan_was_loading = False
        self.session_start = session_start or datetime.now()
        self._monitoring_cutoff = None

        self.pending_reextract = []

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            if self._stop_event.is_set():
                return
            self.status_update.emit("Setting up Chrome browser...")
            if not self._setup_chrome():
                self.error_occurred.emit("Failed to setup Chrome browser for live monitor.")
                return

            self.driver.get(self.config.LOGIN_URL)
            self.status_update.emit("Please log in manually — waiting for session...")
            if not self._wait_for_manual_login():
                if not self._stop_event.is_set():
                    self.error_occurred.emit("Login timeout — URL did not leave the login page.")
                return

            self.status_update.emit("Login successful — establishing monitoring baseline...")
            self._initialize_monitoring_baseline()
            if self._stop_event.is_set():
                return

            self.status_update.emit(
                "Baseline established — existing tickets are ignored; "
                "only post-start tickets will be captured."
            )
            self.status_update.emit(
                "Logged in — silent ultra-fast patrol (200 ms DOM scan + background ticket API polling; no page reload)"
            )
            self._patrol_loop()
        except Exception as e:
            if not self._stop_event.is_set():
                self.error_occurred.emit(str(e))
        finally:
            self.monitoring_stopped.emit()

    def _build_chrome_options(self, profile_dir):
        chrome_options = Options()
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        chrome_options.add_argument("--profile-directory=Default")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)


        chrome_options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        if self.config.HEADLESS_MODE:
            chrome_options.add_argument("--headless=new")
        if os.path.exists(self.config.CHROME_BINARY_PATH):
            chrome_options.binary_location = self.config.CHROME_BINARY_PATH
        return chrome_options

    def _try_launch_driver(self, service, chrome_options):
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.set_page_load_timeout(self.config.PAGE_LOAD_TIMEOUT)
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def _setup_chrome(self):
        try:
            base_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.getcwd()), "ITMIS_Ticket_Scraper")
            profile_dir = os.path.join(base_dir, "chrome_profile_live_monitor")
            os.makedirs(profile_dir, exist_ok=True)

            for attempt, p_dir in enumerate([profile_dir, None], start=1):
                try:
                    opts = self._build_chrome_options(
                        p_dir if p_dir else os.path.join(base_dir, f"chrome_profile_live_tmp_{attempt}")
                    )
                    if p_dir is None:
                        args = [
                            a for a in opts.arguments
                            if not a.startswith("--user-data-dir")
                            and not a.startswith("--profile-directory")
                        ]
                        opts.arguments.clear()
                        for a in args:
                            opts.add_argument(a)
                    self._try_launch_driver(Service(), opts)
                    return True
                except Exception:
                    pass

            try:
                driver_path = ChromeDriverManager().install()
                if not driver_path.lower().endswith(".exe"):
                    import glob
                    exe_candidates = glob.glob(
                        os.path.join(os.path.dirname(driver_path), "**", "chromedriver.exe"),
                        recursive=True,
                    )
                    if exe_candidates:
                        driver_path = exe_candidates[0]
                opts = self._build_chrome_options(profile_dir)
                self._try_launch_driver(Service(driver_path), opts)
                return True
            except Exception:
                pass

            import shutil
            path_driver = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
            if path_driver:
                opts = self._build_chrome_options(profile_dir)
                self._try_launch_driver(Service(path_driver), opts)
                return True
        except Exception as e:
            self.status_update.emit(f"Chrome setup error: {e}")
        return False

    def _wait_for_manual_login(self):
        deadline = time.time() + self.config.LOGIN_TIMEOUT
        while time.time() < deadline and not self._stop_event.is_set():
            try:
                with self._driver_lock:
                    url = self.driver.current_url or ""
                if "#/login" not in url and "/login" not in url.split("#")[-1].lower():
                    return True
            except WebDriverException:
                pass
            time.sleep(0.2)
        return False

    def _cleanup_stale_handles(self, handles):
        live = set(handles)
        for stale in [h for h in self.tab_last_url if h not in live]:
            self.tab_last_url.pop(stale, None)
            self.tab_pending.pop(stale, None)
            self._extracting_handles.discard(stale)

    def _get_all_tab_urls(self) -> dict[str, str]:
        """Returns {targetId: url} for all open tabs WITHOUT switching focus."""
        result = {}
        try:
            targets = self.driver.execute_cdp_cmd("Target.getTargets", {})
            for target in targets.get("targetInfos", []):
                if target.get("type") == "page":
                    result[target["targetId"]] = target.get("url", "")
        except Exception:
            pass
        return result

    def _build_target_to_handle_map(self) -> dict[str, str]:
        """Map CDP targetId → Selenium window handle."""
        mapping = {}
        with self._driver_lock:
            original = self.driver.current_window_handle
            for handle in self.driver.window_handles:
                self.driver.switch_to.window(handle)
                try:
                    target_id = self.driver.execute_cdp_cmd(
                        "Target.getTargetInfo", {}
                    ).get("targetInfo", {}).get("targetId", "")
                    if target_id:
                        mapping[target_id] = handle
                except Exception:
                    pass
            self.driver.switch_to.window(original)
        return mapping

    def _initialize_monitoring_baseline(self):
        """Snapshot existing ITMIS state so old tickets are never treated as new."""

        self._last_notification_count = self._get_notification_count()
        self.status_update.emit(
            f"Notification baseline recorded: {self._last_notification_count} existing notification(s)."
        )

        try:
            current_tickets, dashboard_ready = self._wait_for_stable_dashboard_baseline(
                max_rows=15, timeout=12.0
            )
        except Exception as e:
            self.status_update.emit(f"Dashboard baseline warning: {e}")
            current_tickets, dashboard_ready = [], False

        self._last_known_dashboard_tickets = list(current_tickets or [])
        self._baseline_ticket_ids.update(self._last_known_dashboard_tickets)
        self._dashboard_baseline_initialized = bool(dashboard_ready)
        if dashboard_ready:
            self.status_update.emit(
                f"Dashboard baseline recorded: {len(self._last_known_dashboard_tickets)} existing ticket(s)."
            )
        else:

            self.status_update.emit(
                "Dashboard was still loading; baseline deferred until the first real ticket snapshot."
            )

        try:
            current_handles = list(self.driver.window_handles)
            self._target_handle_map = self._build_target_to_handle_map() if current_handles else {}
            self._last_handle_count = len(current_handles)
            for _target_id, url in self._get_all_tab_urls().items():
                match = LIVE_MONITOR_TICKET_URL_RE.search(url or "")
                if match:
                    ticket_id = match.group(1).strip().upper()
                    if ticket_id:
                        self._baseline_ticket_ids.add(ticket_id)
                        self.processed_urls.add(url)
        except Exception as e:
            self.status_update.emit(f"Open-tab baseline warning: {e}")

        self._monitoring_cutoff = datetime.now()
        self.status_update.emit(
            f"Live capture cutoff set to {self._monitoring_cutoff.strftime('%d/%m/%Y %I:%M:%S %p')}."
        )

    def _parse_live_ticket_datetime(self, raw_value):
        """Best-effort parser for ITMIS ticket start datetime strings.

        IMPORTANT: preserve AM/PM before trying 24-hour formats.  The previous
        parser also extracted ``18/08/2026 01:07`` from ``01:07 PM`` and tried
        that candidate first, which converted a 1:07 PM ticket into 1:07 AM.
        """
        if not raw_value:
            return None

        text = re.sub(r"\s+", " ", str(raw_value)).strip().upper()

        ampm_patterns = (
            (r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M", "%d/%m/%Y"),
            (r"\d{1,2}/\d{1,2}/\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M", "%d/%m/%y"),
        )
        for pattern, date_fmt in ampm_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group(0)).strip().upper()
            for time_fmt in ("%I:%M:%S %p", "%I:%M %p"):
                try:
                    return datetime.strptime(candidate, f"{date_fmt} {time_fmt}")
                except ValueError:
                    pass

        hour24_patterns = (
            (r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?(?!\s*[AP]M)", "%d/%m/%Y"),
            (r"\d{1,2}/\d{1,2}/\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?(?!\s*[AP]M)", "%d/%m/%y"),
        )
        for pattern, date_fmt in hour24_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group(0)).strip()
            for time_fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(candidate, f"{date_fmt} {time_fmt}")
                except ValueError:
                    pass

        return None

    def _is_pre_session_ticket(self, raw_time):
        """Return True only when a parsed ticket time is definitely before monitoring began."""
        if self._monitoring_cutoff is None:
            return False
        ticket_dt = self._parse_live_ticket_datetime(raw_time)
        if ticket_dt is None:


            return False



        cutoff_minute = self._monitoring_cutoff.replace(second=0, microsecond=0)
        ticket_minute = ticket_dt.replace(second=0, microsecond=0)
        is_old = ticket_minute < cutoff_minute




        self.status_update.emit(
            "Ticket time check: "
            f"raw='{raw_time}' -> {ticket_minute.strftime('%d/%m/%Y %I:%M %p')}; "
            f"cutoff={cutoff_minute.strftime('%d/%m/%Y %I:%M %p')}; "
            f"result={'OLD' if is_old else 'NEW'}"
        )
        return is_old

    def _patrol_loop(self):
        """Run a dashboard-first patrol without letting tab/CDP work delay detection."""
        while not self._stop_event.is_set():
            try:
                try:
                    with self._driver_lock:
                        current_handles = list(self.driver.window_handles)
                except WebDriverException as e:
                    self.error_occurred.emit(f"Browser disconnected: {e}")
                    self._stop_event.set()
                    break

                if not current_handles:
                    if not self._all_tabs_closed_announced:
                        self.status_update.emit("All tabs closed — waiting for new tabs...")
                        self._all_tabs_closed_announced = True
                    self._last_handle_count = 0
                    self.monitor_stats.emit(0, len(self.captured_tickets))
                    self._stop_event.wait(0.05)
                    continue

                self._all_tabs_closed_announced = False



                self._monitor_dashboard_and_notifications()




                now = time.time()
                if now - self._last_tab_scan_time >= self._tab_scan_interval:
                    self._last_tab_scan_time = now
                    extractions_to_start = []

                    self._cleanup_stale_handles(current_handles)

                    if len(current_handles) != self._last_handle_count:
                        self._target_handle_map = self._build_target_to_handle_map()
                        self._last_handle_count = len(current_handles)

                    tab_urls = self._get_all_tab_urls()

                    for target_id, url in tab_urls.items():
                        handle = self._target_handle_map.get(target_id)
                        if not handle:
                            continue

                        match = LIVE_MONITOR_TICKET_URL_RE.search(url)
                        if not match:
                            continue

                        ticket_id = match.group(1).strip().upper()

                        if (ticket_id in self._baseline_ticket_ids or
                                ticket_id in self._processed_ids or url in self.processed_urls):
                            if ticket_id not in self._duplicate_reported_ids:
                                self._duplicate_reported_ids.add(ticket_id)
                                self.duplicate_detected.emit(ticket_id)
                            continue

                        self.processed_urls.add(url)
                        self._processed_ids.add(ticket_id)
                        self.tab_pending[handle] = 3
                        self._extracting_handles.add(handle)

                        try:
                            tab_index = current_handles.index(handle) + 1
                        except ValueError:
                            tab_index = 1

                        extractions_to_start.append((handle, url, ticket_id, tab_index))

                    self.monitor_stats.emit(len(current_handles), len(self.captured_tickets))

                    for handle, url, ticket_id, tab_index in extractions_to_start:
                        Thread(
                            target=self._extract_from_handle,
                            args=(handle, url, ticket_id, tab_index),
                            daemon=True,
                        ).start()

            except WebDriverException as e:
                self.error_occurred.emit(f"Browser disconnected: {e}")
                self._stop_event.set()
                break
            except Exception as e:
                self.status_update.emit(f"Patrol loop error: {e}")



            self._stop_event.wait(0.05)

    def _wait_page_settle(self, handle, timeout=1.5):
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stop_event.is_set():
            with self._driver_lock:
                try:
                    if handle not in self.driver.window_handles:
                        return False
                    self.driver.switch_to.window(handle)
                    ready = self.driver.execute_script("return document.readyState")
                    if ready == "complete":
                        return True
                except WebDriverException:
                    return False
            time.sleep(0.05)
        return True

    def _extract_from_handle(self, handle, ticket_url, ticket_id, tab_index):
        """Extract a newly opened ticket quickly without monopolizing Selenium.

        The previous version held the driver lock while performing three separate
        WebDriverWait(..., 3) XPath lookups.  In the worst case that delayed a
        ticket by many seconds and also blocked the dashboard watcher.  This
        version reads all three fields in one browser-side JavaScript call and
        polls briefly while Angular fills the page.
        """
        tab_title = f"Tab {tab_index}"
        self.tab_pending[handle] = 1
        deadline = time.time() + 6.0
        first_partial = None
        wrong_url_since = None

        try:
            while time.time() < deadline and not self._stop_event.is_set():
                previous_handle = None
                time_val = station_val = desc_val = ""
                try:
                    with self._driver_lock:
                        handles = list(self.driver.window_handles)
                        if handle not in handles:
                            return

                        try:
                            previous_handle = self.driver.current_window_handle
                        except Exception:
                            previous_handle = None

                        self.driver.switch_to.window(handle)
                        current_url = self.driver.current_url or ""



                        if ticket_id.upper() not in current_url.upper():
                            if current_url and current_url != "about:blank":
                                if wrong_url_since is None:
                                    wrong_url_since = time.time()
                                elif time.time() - wrong_url_since > 1.0:
                                    if previous_handle and previous_handle in handles:
                                        self.driver.switch_to.window(previous_handle)
                                    self.status_update.emit(
                                        f"Ticket tab changed before extraction: {ticket_id}"
                                    )
                                    return
                            if previous_handle and previous_handle in handles:
                                self.driver.switch_to.window(previous_handle)
                        else:
                            wrong_url_since = None
                            values = self.driver.execute_script(
                                """
                                const textAtXPath = (xp) => {
                                    try {
                                        const node = document.evaluate(
                                            xp, document, null,
                                            XPathResult.FIRST_ORDERED_NODE_TYPE, null
                                        ).singleNodeValue;
                                        if (!node) return '';
                                        return (node.innerText || node.textContent || '').trim();
                                    } catch (e) {
                                        return '';
                                    }
                                };
                                return [
                                    textAtXPath(arguments[0]),
                                    textAtXPath(arguments[1]),
                                    textAtXPath(arguments[2]),
                                    document.title || ''
                                ];
                                """,
                                self.config.TICKET_START_TIME_XPATH,
                                self.config.STATION_XPATH,
                                self.config.CONTENT_XPATH,
                            ) or ["", "", "", ""]
                            time_val = (values[0] or "").strip()
                            station_val = (values[1] or "").strip()
                            desc_val = (values[2] or "").strip()
                            tab_title = (values[3] or tab_title).strip() or tab_title

                            if previous_handle and previous_handle in handles:
                                self.driver.switch_to.window(previous_handle)

                    if any((time_val, station_val, desc_val)):
                        if first_partial is None:
                            first_partial = time.time()




                        complete = bool(time_val and station_val and desc_val)
                        partial_ready = first_partial and (time.time() - first_partial >= 0.25)
                        if complete or partial_ready:
                            if self._is_pre_session_ticket(time_val):
                                self.status_update.emit(
                                    f"Ignored old ticket {ticket_id}: ticket start time {time_val} "
                                    "is before the live-monitor cutoff."
                                )
                                return

                            captured_at = datetime.now()
                            statement_time = time_val or "Unknown time"
                            station_info = station_val or "Unknown station"
                            description = desc_val or "No description"
                            formatted = (
                                f"{statement_time} PMA issued a ticket ({ticket_id}) "
                                f"at {station_info} - {description}"
                            )
                            record = {
                                "ticket_id": ticket_id,
                                "ticket_url": ticket_url,
                                "time": statement_time,
                                "station": station_info,
                                "description": description,
                                "formatted_statement": formatted,
                                "formatted": formatted,
                                "captured_at": captured_at.isoformat(),
                                "tab_index": tab_index,
                                "tab_title": tab_title,
                                "tab_label": tab_title or f"Tab {tab_index}",
                            }
                            self.captured_tickets.append(record)
                            self.ticket_captured.emit(record)
                            self.status_update.emit(
                                f"Captured new ticket {ticket_id} at {captured_at.strftime('%H:%M:%S')}"
                            )
                            return

                except Exception as e:
                    self.status_update.emit(f"Ticket extraction retry for {ticket_id}: {e}")

                self._stop_event.wait(0.12)

            self.status_update.emit(f"Failed to extract {ticket_id} within 6 seconds.")
        finally:
            self._extracting_handles.discard(handle)
            self.tab_pending.pop(handle, None)

    def _safe_xpath(self, xpath) -> str:
        try:
            el = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            return (el.text or "").strip()
        except Exception:
            return ""

    def _extract_field_locked(self, handle, xpath) -> str:
        """Extract a single field via XPath using a brief lock per poll."""
        if not xpath:
            return ""
        deadline = time.time() + 6
        while time.time() < deadline and not self._stop_event.is_set():
            with self._driver_lock:
                try:
                    if handle not in self.driver.window_handles:
                        return ""
                    els = self.driver.find_elements(By.XPATH, xpath)
                    if els:
                        text = (els[0].text or
                                els[0].get_attribute("innerText") or "").strip()
                        if text:
                            return text
                except Exception:
                    return ""
            time.sleep(0.3)
        return ""

    def _extract_time_fallback(self, handle) -> str:
        """Broad label search + regex fallback for time field."""
        with self._driver_lock:
            try:
                if handle not in self.driver.window_handles:
                    return ""

                labels = self.driver.find_elements(
                    By.XPATH,
                    "//label[contains(translate(text(),'time','TIME'),'TIME')]"
                    "/following-sibling::label"
                )
                for lbl in labels:
                    text = (lbl.text or "").strip()
                    if text:
                        return text

                src = self.driver.page_source or ""
                m = LIVE_MONITOR_TIME_RE.search(src)
                if m:
                    return m.group(0)
            except Exception:
                pass
        return ""

    def _extract_desc_fallback(self, handle) -> str:
        """Find the longest fieldset text block as description fallback."""
        with self._driver_lock:
            try:
                if handle not in self.driver.window_handles:
                    return ""
                best = ""
                for fs in self.driver.find_elements(By.TAG_NAME, "fieldset"):
                    block = (fs.get_attribute("innerText") or "").strip()
                    if 50 < len(block) < 1000 and len(block) > len(best):
                        best = block
                return best
            except Exception:
                pass
        return ""

    def _element_text(self, element):
        if not element:
            return ""
        try:
            return (element.text or element.get_attribute("innerText") or "").strip()
        except Exception:
            return ""

    def _try_find(self, xpath, timeout=None):
        if not xpath:
            return None
        try:
            wait = WebDriverWait(self.driver, timeout or self.config.ELEMENT_WAIT_TIMEOUT)
            return wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
        except Exception:
            return None

    def _check_dashboard_for_new_tickets(self):
        """Check dashboard for newly issued tickets and return ticket count."""
        try:
            with self._driver_lock:

                current_url = self.driver.current_url or ""
                if "dashboard" not in current_url.lower():
                    self.driver.get(self.config.DASHBOARD_URL)
                    time.sleep(1)


                ticket_rows = self.driver.find_elements(By.XPATH,
                    '/html/body/app-root/app-init-app/div/div/app-dashboard/div/div[4]/div/div/div/p-scrollpanel/div/div[1]/div/p-table/div/div/table/tbody/tr')
                ticket_count = len(ticket_rows)

                return ticket_count
        except Exception as e:
            self.status_update.emit(f"Dashboard check error: {e}")
            return 0

    def _extract_ticket_from_dashboard_first_row(self):
        """Extract ticket number from the first row of dashboard table."""
        try:
            with self._driver_lock:

                current_url = self.driver.current_url or ""
                if "dashboard" not in current_url.lower():
                    self.driver.get(self.config.DASHBOARD_URL)
                    time.sleep(2)


                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, self.config.DASHBOARD_FIRST_TICKET_XPATH))
                    )
                except:
                    self.status_update.emit("Dashboard table not loaded within timeout")
                    return None, None


                first_row = self.driver.find_elements(By.XPATH, self.config.DASHBOARD_FIRST_TICKET_XPATH)
                if not first_row:
                    self.status_update.emit("No first row found in dashboard table")
                    return None, None


                row_text = first_row[0].text
                self.status_update.emit(f"First row text: {row_text[:100]}...")

                match = TICKET_REGEX.search(row_text)
                if match:
                    ticket_number = match.group(0)
                    self.status_update.emit(f"Found ticket number from text: {ticket_number}")


                    link_elements = first_row[0].find_elements(By.TAG_NAME, 'a')
                    if link_elements:
                        ticket_url = link_elements[0].get_attribute('href')
                        self.status_update.emit(f"Found ticket URL from link: {ticket_url}")
                    else:

                        ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
                        self.status_update.emit(f"Constructed ticket URL: {ticket_url}")
                    return ticket_number, ticket_url


                link_elements = first_row[0].find_elements(By.TAG_NAME, 'a')
                for link in link_elements:
                    link_text = link.text
                    link_href = link.get_attribute('href')
                    self.status_update.emit(f"Link text: {link_text}, Href: {link_href}")


                    if link_href:
                        match = TICKET_REGEX.search(link_href)
                        if match:
                            ticket_number = match.group(0)
                            ticket_url = link_href
                            self.status_update.emit(f"Found ticket number from link href: {ticket_number}")
                            return ticket_number, ticket_url


                    match = TICKET_REGEX_PARTIAL.search(link_text)
                    if match:
                        partial_ticket = match.group(0)

                        if link_href:
                            href_match = TICKET_REGEX.search(link_href)
                            if href_match:
                                ticket_number = href_match.group(0)
                                ticket_url = link_href
                                self.status_update.emit(f"Found full ticket from href using partial match: {ticket_number}")
                                return ticket_number, ticket_url

                        self.status_update.emit(f"Found partial ticket from link text: {partial_ticket}")

                        if link_href:
                            ticket_url = link_href

                            url_match = TICKET_REGEX.search(link_href)
                            if url_match:
                                ticket_number = url_match.group(0)
                                self.status_update.emit(f"Extracted full ticket from URL: {ticket_number}")
                                return ticket_number, ticket_url
                        return None, None


                inner_html = first_row[0].get_attribute('innerHTML')
                match = TICKET_REGEX.search(inner_html)
                if match:
                    ticket_number = match.group(0)
                    self.status_update.emit(f"Found ticket number from innerHTML: {ticket_number}")
                    ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
                    return ticket_number, ticket_url

                self.status_update.emit("No ticket number found in first row using any method")
                return None, None

        except Exception as e:
            self.status_update.emit(f"Extract ticket from dashboard error: {e}")
            return None, None

    def _wait_for_stable_dashboard_baseline(self, max_rows=15, timeout=12.0):
        """Return (tickets, ready) after the dashboard has reached a stable real state.

        A stable baseline prevents the Angular loading placeholder from being
        mistaken for an empty dashboard.  Two matching real snapshots are required
        for a non-empty dashboard.  A genuinely empty dashboard is accepted after a
        short stable period.  If loading never finishes, ready=False causes the
        monitor loop to adopt the first real snapshot as its baseline instead of
        opening those rows.
        """
        deadline = time.time() + max(2.0, float(timeout))
        first_real_seen = None
        previous = None
        stable_matches = 0
        last_tickets = []

        while time.time() < deadline and not self._stop_event.is_set():
            tickets, _details = self._scan_all_dashboard_rows(max_rows=max_rows)
            tickets = list(tickets or [])
            last_tickets = tickets

            if self._last_dashboard_scan_was_loading:
                previous = None
                stable_matches = 0
                self.status_update.emit("Dashboard is still loading — waiting before baseline capture...")
                self._stop_event.wait(0.5)
                continue

            if first_real_seen is None:
                first_real_seen = time.time()

            if previous == tickets:
                stable_matches += 1
            else:
                previous = list(tickets)
                stable_matches = 0




            if tickets and stable_matches >= 1:
                return tickets, True
            if not tickets and stable_matches >= 2 and time.time() - first_real_seen >= 2.0:
                return [], True

            self._stop_event.wait(0.5)

        return last_tickets, False

    def _scan_all_dashboard_rows(self, max_rows=10):
        """Read the dashboard table in one browser-side snapshot.

        This avoids dozens of Selenium round-trips per scan.  It also uses a
        dedicated dashboard handle, so background ticket extraction can never
        accidentally turn the ticket tab back into the dashboard.
        """
        self._last_dashboard_scan_was_loading = False
        raw_rows = []
        try:
            with self._driver_lock:
                handles = list(self.driver.window_handles)
                if not handles:
                    return [], {}

                try:
                    original_handle = self.driver.current_window_handle
                except Exception:
                    original_handle = handles[0]


                if self._dashboard_handle not in handles:
                    self._dashboard_handle = None

                    try:
                        if "dashboard" in (self.driver.current_url or "").lower():
                            self._dashboard_handle = original_handle
                    except Exception:
                        pass

                    if self._dashboard_handle is None:
                        for candidate in handles:
                            try:
                                self.driver.switch_to.window(candidate)
                                if "dashboard" in (self.driver.current_url or "").lower():
                                    self._dashboard_handle = candidate
                                    break
                            except Exception:
                                continue



                    if self._dashboard_handle is None:
                        self._dashboard_handle = original_handle
                        self.driver.switch_to.window(self._dashboard_handle)
                        self.driver.get(self.config.DASHBOARD_URL)

                self.driver.switch_to.window(self._dashboard_handle)
                if "dashboard" not in (self.driver.current_url or "").lower():
                    self.driver.get(self.config.DASHBOARD_URL)

                raw_rows = self.driver.execute_script(
                    """
                    const maxRows = arguments[0];
                    let rows = Array.from(document.querySelectorAll('app-dashboard p-table tbody tr'));

                    // Exact-path fallback for the current ITMIS layout.
                    if (!rows.length) {
                        try {
                            const xp = '/html/body/app-root/app-init-app/div/div/app-dashboard/div/div[4]/div/div/div/p-scrollpanel/div/div[1]/div/p-table/div/div/table/tbody/tr';
                            const snap = document.evaluate(
                                xp, document, null,
                                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
                            );
                            rows = [];
                            for (let i = 0; i < snap.snapshotLength; i++) {
                                rows.push(snap.snapshotItem(i));
                            }
                        } catch (e) {}
                    }

                    return rows.slice(0, maxRows).map(row => ({
                        text: (row.innerText || row.textContent || '').trim(),
                        cells: Array.from(row.querySelectorAll('td')).map(td =>
                            (td.innerText || td.textContent || '').trim()
                        ),
                        hrefs: Array.from(row.querySelectorAll('a')).map(a =>
                            a.href || a.getAttribute('href') || ''
                        )
                    }));
                    """,
                    int(max_rows),
                ) or []


                if original_handle in self.driver.window_handles:
                    self.driver.switch_to.window(original_handle)

            if not raw_rows:
                return [], {}

            current_tickets = []
            ticket_details = {}

            for i, row_data in enumerate(raw_rows[:max_rows]):
                row_text = str((row_data or {}).get("text", "") or "").strip()
                if i == 0 and row_text:


                    self.status_update.emit(f"Row 1 text: {row_text[:100]}...")

                row_text_lower = row_text.lower()
                if ("loading ticket" in row_text_lower or
                        "fetching the latest service tickets" in row_text_lower):
                    self._last_dashboard_scan_was_loading = True
                    continue

                match = TICKET_REGEX_PARTIAL.search(row_text)
                if not match:
                    continue

                partial_ticket = match.group(0)
                ticket_number = None
                for link_href in (row_data or {}).get("hrefs", []) or []:
                    if link_href and link_href != "javascript:void(0)":
                        href_match = TICKET_REGEX.search(str(link_href))
                        if href_match:
                            ticket_number = href_match.group(0).upper()
                            break

                if not ticket_number:
                    candidate = f"LHR.L2SP.{partial_ticket}".upper()
                    if TICKET_REGEX.match(candidate):
                        ticket_number = candidate

                if not ticket_number or ticket_number in current_tickets:
                    continue

                current_tickets.append(ticket_number)
                words = row_text.split()
                details = {
                    "assignee": "Unknown",
                    "station": "Unknown",
                    "description": "Unknown",
                    "priority": "Unknown",
                    "date": "Unknown",
                }



                cells = [
                    str(v or "").strip()
                    for v in ((row_data or {}).get("cells", []) or [])
                ]
                if len(cells) >= 4:
                    if len(cells) > 1 and cells[1]:
                        details["assignee"] = cells[1]
                    if len(cells) > 2 and cells[2]:
                        details["station"] = cells[2]
                    if len(cells) > 3 and cells[3]:
                        details["description"] = cells[3]

                    for cell in cells:
                        if cell in {"Critical", "High", "Medium", "Low", "Non-Critical"}:
                            details["priority"] = cell
                            break
                    for cell in cells:
                        if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", cell):
                            details["date"] = cell
                            break
                elif len(words) >= 3:

                    idx = 1
                    if idx < len(words):
                        details["assignee"] = words[idx]
                        idx += 1
                    if idx < len(words):
                        details["station"] = words[idx]
                        idx += 1
                    known_keywords = {
                        "Assigned", "Unassigned", "Fault", "Rectification",
                        "Critical", "High", "Medium", "Low", "Non-Critical",
                    }
                    desc_words = []
                    while idx < len(words) and words[idx] not in known_keywords:
                        desc_words.append(words[idx])
                        idx += 1
                    if desc_words:
                        details["description"] = " ".join(desc_words)
                    for word in words:
                        if word in {"Critical", "High", "Medium", "Low", "Non-Critical"}:
                            details["priority"] = word
                            break
                    for word in words:
                        if "/" in word and len(word) >= 8:
                            details["date"] = word
                            break

                ticket_details[ticket_number] = details

            return current_tickets, ticket_details

        except Exception as e:
            self.status_update.emit(f"Scan dashboard rows error: {e}")
            return [], {}

    def _normalize_api_ticket_ids(self, body_text):
        """Return normalized full ticket IDs found anywhere in an API response."""
        text = str(body_text or "")
        ids = []
        seen = set()


        prefix = "LHR.L2SP"
        for existing in (self._last_known_dashboard_tickets or list(self._baseline_ticket_ids)):
            if TICKET_REGEX.match(str(existing or "")):
                parts = str(existing).upper().split(".")
                if len(parts) >= 2:
                    prefix = ".".join(parts[:2])
                    break


        full_re = re.compile(
            r'\b[A-Z]{2,5}\.[A-Z0-9]{2,8}\.\d{4}\.\d{2}\.\d{6,10}\b',
            re.IGNORECASE,
        )
        for match in full_re.findall(text):
            ticket_id = str(match).upper()
            if ticket_id not in seen and TICKET_REGEX.match(ticket_id):
                seen.add(ticket_id)
                ids.append(ticket_id)


        for partial in TICKET_REGEX_PARTIAL.findall(text):
            ticket_id = f"{prefix}.{str(partial).upper()}"
            if ticket_id not in seen and TICKET_REGEX.match(ticket_id):
                seen.add(ticket_id)
                ids.append(ticket_id)

        return ids

    def _extract_api_details(self, body_text, ticket_number):
        """Best-effort details from the JSON object containing a ticket ID."""
        details = {
            "description": "PMA Issued a NEW Ticket - Click to view details",
        }
        try:
            data = json.loads(str(body_text or ""))
        except Exception:
            return details

        partial = ".".join(str(ticket_number).split(".")[-3:]).lower()
        full = str(ticket_number).lower()
        target = None

        def walk(node):
            nonlocal target
            if target is not None:
                return
            if isinstance(node, dict):
                try:
                    blob = json.dumps(node, ensure_ascii=False, default=str).lower()
                except Exception:
                    blob = str(node).lower()
                if full in blob or partial in blob:
                    target = node
                    return
                for value in node.values():
                    walk(value)
                    if target is not None:
                        return
            elif isinstance(node, list):
                for value in node:
                    walk(value)
                    if target is not None:
                        return

        walk(data)
        if not isinstance(target, dict):
            return details

        def pick(key_words, default):

            lowered = {str(k).lower(): v for k, v in target.items()}
            for wanted in key_words:
                for key, value in lowered.items():
                    if key == wanted and value not in (None, "", [], {}):
                        if not isinstance(value, (dict, list)):
                            return str(value).strip()
            for wanted in key_words:
                for key, value in lowered.items():
                    if wanted in key and value not in (None, "", [], {}):
                        if not isinstance(value, (dict, list)):
                            return str(value).strip()
            return default

        details["description"] = pick(
            ["description", "faultdescription", "complaint", "subject", "title"],
            details["description"],
        )
        return details

    def _discover_silent_ticket_api(self):
        """Identify the dashboard ticket XHR/fetch from Chrome performance events.

        Discovery is read-only: it does not navigate, reload, click, or switch the
        user's page to another URL.  A response is accepted only when its body
        contains one of the already-known dashboard baseline ticket IDs, which
        sharply reduces the chance of selecting an unrelated API endpoint.
        """
        now = time.time()
        if self._silent_api_request or (
            now - self._silent_api_last_discovery_time < self._silent_api_discovery_interval
        ):
            return bool(self._silent_api_request)
        self._silent_api_last_discovery_time = now
        self._silent_api_discovery_attempts += 1

        try:
            with self._driver_lock:
                try:
                    entries = self.driver.get_log("performance") or []
                except Exception as e:
                    if self._silent_api_discovery_attempts <= 2:
                        self.status_update.emit(f"Silent API discovery unavailable: {e}")
                    return False

                requests = {}
                responses = []
                for entry in entries:
                    try:
                        msg = json.loads(entry.get("message", "{}"))["message"]
                        method = msg.get("method")
                        params = msg.get("params", {})
                        request_id = params.get("requestId")
                        if method == "Network.requestWillBeSent" and request_id:
                            req = params.get("request", {}) or {}
                            requests[request_id] = {
                                "url": str(req.get("url", "")),
                                "method": str(req.get("method", "GET") or "GET").upper(),
                                "headers": req.get("headers", {}) or {},
                                "postData": req.get("postData"),
                            }
                        elif method == "Network.responseReceived" and request_id:
                            resp = params.get("response", {}) or {}
                            resource_type = str(params.get("type", ""))
                            if resource_type in {"XHR", "Fetch"}:
                                responses.append((request_id, str(resp.get("url", ""))))
                    except Exception:
                        continue

                baseline_ids = [
                    str(t).upper() for t in self._baseline_ticket_ids
                    if TICKET_REGEX.match(str(t or ""))
                ]
                baseline_needles = set()
                for tid in baseline_ids:
                    baseline_needles.add(tid.lower())
                    baseline_needles.add(".".join(tid.split(".")[-3:]).lower())



                for request_id, response_url in reversed(responses):
                    req = requests.get(request_id, {})
                    url = req.get("url") or response_url
                    if not url or "itmis.olmrts.com.pk" not in url.lower():
                        continue
                    lowered_url = url.lower()
                    if any(x in lowered_url for x in (".js", ".css", ".png", ".jpg", ".svg", "favicon")):
                        continue
                    try:
                        payload = self.driver.execute_cdp_cmd(
                            "Network.getResponseBody", {"requestId": request_id}
                        ) or {}
                        body = str(payload.get("body", "") or "")
                    except Exception:
                        continue
                    if not body:
                        continue

                    low_body = body.lower()
                    found_ids = self._normalize_api_ticket_ids(body)
                    baseline_hit = any(n in low_body for n in baseline_needles)
                    if not baseline_hit or len(found_ids) < 2:
                        continue



                    safe_headers = {}
                    for key, value in (req.get("headers") or {}).items():
                        lk = str(key).lower()
                        if lk in {"authorization", "content-type", "accept"} or lk.startswith("x-"):
                            safe_headers[str(key)] = str(value)

                    self._silent_api_request = {
                        "url": url,
                        "method": str(req.get("method") or "GET").upper(),
                        "headers": safe_headers,
                        "postData": req.get("postData"),
                    }
                    self.status_update.emit(
                        "Silent ticket API discovered — switching to background server polling; visible page will not refresh."
                    )
                    return True

        except Exception as e:
            if self._silent_api_discovery_attempts <= 3:
                self.status_update.emit(f"Silent API discovery error: {e}")
        return False

    def _install_silent_api_poller(self):
        """Install an invisible browser-side fetch loop for the discovered API."""
        if self._silent_api_installed or not self._silent_api_request:
            return self._silent_api_installed
        req = self._silent_api_request
        try:
            with self._driver_lock:
                handles = list(self.driver.window_handles)
                if not handles or self._dashboard_handle not in handles:
                    return False
                original_handle = self.driver.current_window_handle
                self.driver.switch_to.window(self._dashboard_handle)
                result = self.driver.execute_script(
                    r"""
                    const cfg = arguments[0];
                    const intervalMs = arguments[1];
                    try {
                        if (window.__itmisSilentTicketPoller && window.__itmisSilentTicketPoller.timer) {
                            clearInterval(window.__itmisSilentTicketPoller.timer);
                        }
                    } catch (e) {}

                    const state = {
                        seq: 0,
                        body: '',
                        at: 0,
                        error: '',
                        busy: false,
                        timer: null
                    };
                    window.__itmisSilentTicketPoller = state;

                    const poll = async () => {
                        if (state.busy) return;
                        state.busy = true;
                        try {
                            const opts = {
                                method: cfg.method || 'GET',
                                credentials: 'include',
                                cache: 'no-store',
                                headers: cfg.headers || {}
                            };
                            if (cfg.postData != null && String(cfg.method || 'GET').toUpperCase() !== 'GET') {
                                opts.body = cfg.postData;
                            }
                            const resp = await fetch(cfg.url, opts);
                            const text = await resp.text();
                            if (resp.ok) {
                                state.body = text;
                                state.at = Date.now();
                                state.error = '';
                                state.seq += 1;
                            } else {
                                state.error = 'HTTP ' + resp.status;
                            }
                        } catch (e) {
                            state.error = String(e && e.message ? e.message : e);
                        } finally {
                            state.busy = false;
                        }
                    };
                    poll();
                    state.timer = setInterval(poll, intervalMs);
                    return true;
                    """,
                    req,
                    int(self._silent_api_poll_interval * 1000),
                )
                if original_handle in self.driver.window_handles:
                    self.driver.switch_to.window(original_handle)
            self._silent_api_installed = bool(result)
            if self._silent_api_installed:
                self.status_update.emit(
                    f"Silent ticket API poller active every {int(self._silent_api_poll_interval * 1000)} ms — no visible dashboard reload."
                )
            return self._silent_api_installed
        except Exception as e:
            self.status_update.emit(f"Silent API poller install error: {e}")
            return False

    def _poll_silent_ticket_api(self):
        """Read the latest invisible API snapshot and capture newly appearing IDs."""
        now = time.time()
        if now - self._silent_api_last_state_check < self._silent_api_state_check_interval:
            return
        self._silent_api_last_state_check = now

        if not self._silent_api_request:
            self._discover_silent_ticket_api()
        if self._silent_api_request and not self._silent_api_installed:
            self._install_silent_api_poller()
        if not self._silent_api_installed:
            return

        try:
            with self._driver_lock:
                handles = list(self.driver.window_handles)
                if not handles or self._dashboard_handle not in handles:
                    self._silent_api_installed = False
                    return
                original_handle = self.driver.current_window_handle
                self.driver.switch_to.window(self._dashboard_handle)
                state = self.driver.execute_script(
                    """
                    const s = window.__itmisSilentTicketPoller;
                    if (!s) return null;
                    return {seq:s.seq || 0, body:s.body || '', at:s.at || 0, error:s.error || ''};
                    """
                )
                if original_handle in self.driver.window_handles:
                    self.driver.switch_to.window(original_handle)
        except Exception as e:
            self.status_update.emit(f"Silent API state read error: {e}")
            self._silent_api_installed = False
            return

        if not state:
            self._silent_api_installed = False
            return
        seq = int(state.get("seq", 0) or 0)
        if seq <= self._silent_api_last_seq:
            return
        self._silent_api_last_seq = seq
        body = str(state.get("body", "") or "")
        if not body:
            return

        ids = self._normalize_api_ticket_ids(body)
        if not ids:
            return

        if not self._silent_api_baseline_initialized:
            self._silent_api_baseline_ids.update(ids)
            self._silent_api_baseline_ids.update(self._baseline_ticket_ids)
            self._silent_api_baseline_initialized = True
            self.status_update.emit(
                f"Silent API baseline recorded: {len(ids)} ticket ID(s); future server-side arrivals will be captured immediately."
            )
            return

        new_ids = [
            tid for tid in ids
            if tid not in self._silent_api_baseline_ids
            and tid not in self._baseline_ticket_ids
            and tid not in self._processed_ids
        ]
        if not new_ids:
            return

        detected = datetime.now()
        self.status_update.emit(
            f"Silent API detected {len(new_ids)} new ticket(s) at "
            f"{detected.strftime('%H:%M:%S.%f')[:-3]}: {new_ids}"
        )
        for ticket_number in new_ids:


            self._silent_api_baseline_ids.add(ticket_number)
            ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
            details = self._extract_api_details(body, ticket_number)
            self._emit_immediate_dashboard_record(ticket_number, ticket_url, details)
            self.status_update.emit(
                f"Opening full details in background: {ticket_number} (Silent API)"
            )
            self._capture_ticket_via_new_tab(
                ticket_number, ticket_url, source_label="Silent API"
            )

    def _get_notification_count(self):
        """Get the current notification count from the bell icon."""
        try:
            with self._driver_lock:
                count_element = self.driver.find_elements(By.XPATH, self.config.NOTIFICATION_COUNT_XPATH)
                if count_element:
                    count_text = count_element[0].text.strip()

                    import re
                    match = re.search(r'\d+', count_text)
                    if match:
                        return int(match.group())
                return 0
        except Exception as e:
            self.status_update.emit(f"Notification count error: {e}")
            return 0

    def _open_notification_dropdown(self):
        """Open the notification bell dropdown."""
        try:
            with self._driver_lock:
                bell_icon = self.driver.find_elements(By.XPATH, self.config.NOTIFICATION_BELL_ICON_XPATH)
                if bell_icon:
                    bell_icon[0].click()
                    time.sleep(0.5)
                    return True
                return False
        except Exception as e:
            self.status_update.emit(f"Open notification dropdown error: {e}")
            return False

    def _extract_ticket_from_notification(self):
        """Extract ticket number and link from the first notification."""
        try:
            with self._driver_lock:

                try:
                    WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, self.config.NOTIFICATION_LINK_XPATH))
                    )
                except:
                    self.status_update.emit("Notification dropdown not loaded within timeout")
                    return None, None


                ticket_number_element = self.driver.find_elements(By.XPATH, self.config.NOTIFICATION_TICKET_NUMBER_XPATH)
                if ticket_number_element:
                    ticket_number = ticket_number_element[0].text.strip()
                    self.status_update.emit(f"Notification ticket number element text: {ticket_number}")

                    valid, _ = is_valid_ticket(ticket_number)
                    if valid:

                        link_element = self.driver.find_elements(By.XPATH, self.config.NOTIFICATION_LINK_XPATH)
                        if link_element:
                            ticket_url = link_element[0].get_attribute('href')

                            if not ticket_url or ticket_url == 'javascript:void(0)':
                                ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
                            self.status_update.emit(f"Found ticket from notification xpath: {ticket_number}")
                            return ticket_number, ticket_url


                link_element = self.driver.find_elements(By.XPATH, self.config.NOTIFICATION_LINK_XPATH)
                if link_element:
                    link_text = link_element[0].text
                    link_href = link_element[0].get_attribute('href')
                    self.status_update.emit(f"Notification link text: {link_text}, Href: {link_href}")

                    match = TICKET_REGEX.search(link_text)
                    if match:
                        ticket_number = match.group(0)

                        if not link_href or link_href == 'javascript:void(0)':
                            ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
                        else:
                            ticket_url = link_href
                        self.status_update.emit(f"Found ticket from notification link text: {ticket_number}")
                        return ticket_number, ticket_url


                    if link_href and link_href != 'javascript:void(0)':
                        match = TICKET_REGEX.search(link_href)
                        if match:
                            ticket_number = match.group(0)
                            self.status_update.emit(f"Found ticket from notification link href: {ticket_number}")
                            return ticket_number, link_href


                all_links = self.driver.find_elements(By.XPATH,
                    '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div/div[2]/a')
                for i, link in enumerate(all_links):
                    link_text = link.text
                    link_href = link.get_attribute('href')
                    self.status_update.emit(f"Notification link {i}: text={link_text[:50]}, href={link_href}")

                    match = TICKET_REGEX.search(link_text)
                    if match:
                        ticket_number = match.group(0)

                        if not link_href or link_href == 'javascript:void(0)':
                            ticket_url = f"{self.config.BASE_TICKET_URL}{ticket_number}"
                        else:
                            ticket_url = link_href
                        self.status_update.emit(f"Found ticket from notification link {i}: {ticket_number}")
                        return ticket_number, ticket_url

                    if link_href and link_href != 'javascript:void(0)':
                        match = TICKET_REGEX.search(link_href)
                        if match:
                            ticket_number = match.group(0)
                            self.status_update.emit(f"Found ticket from notification link {i} href: {ticket_number}")
                            return ticket_number, link_href


                all_links = self.driver.find_elements(By.XPATH,
                    '/html/body/app-root/app-init-app/div/app-bar-menu/div/div/div[2]/div/app-notification-bell/div/div/div[2]/a')
                for i, link in enumerate(all_links):
                    link_text = link.text
                    link_href = link.get_attribute('href')

                    match = TICKET_REGEX_PARTIAL.search(link_text)
                    if match:
                        partial_ticket = match.group(0)
                        self.status_update.emit(f"Found partial ticket from notification link {i}: {partial_ticket}")


                        try:
                            link.click()
                            time.sleep(1)
                            current_url = self.driver.current_url
                            if "service-tickets/details" in current_url:
                                url_match = TICKET_REGEX.search(current_url)
                                if url_match:
                                    ticket_number = url_match.group(0)
                                    self.status_update.emit(f"Got full ticket from navigation: {ticket_number}")
                                    return ticket_number, current_url

                            self.driver.get(self.config.DASHBOARD_URL)
                            time.sleep(1)
                        except:
                            pass
                        return None, None

                self.status_update.emit("No ticket number found in notification using any method")
                return None, None
        except Exception as e:
            self.status_update.emit(f"Extract ticket from notification error: {e}")
            return None, None

    def _open_ticket_in_new_tab(self, ticket_url):
        """Open a ticket in a background tab without blocking on full page load."""
        try:
            with self._driver_lock:
                before = set(self.driver.window_handles)
                original_handle = self.driver.current_window_handle



                self.driver.execute_script(
                    "window.open(arguments[0], '_blank');", ticket_url
                )

                deadline = time.time() + 1.0
                new_handle = None
                while time.time() < deadline and not self._stop_event.is_set():
                    handles = list(self.driver.window_handles)
                    created = [h for h in handles if h not in before]
                    if created:
                        new_handle = created[-1]
                        break
                    time.sleep(0.03)



                if original_handle in self.driver.window_handles:
                    self.driver.switch_to.window(original_handle)

                return new_handle
        except Exception as e:
            self.status_update.emit(f"Open ticket in new tab error: {e}")
            return None

    def _capture_ticket_via_new_tab(self, ticket_number, ticket_url, source_label="Auto"):
        """Open a ticket in a new tab and extract its FULL details directly (time/station/description),
        instead of relying on the patrol loop's opportunistic CDP tab-scan to notice it later.

        This is used by both the dashboard-scan watcher and the notification-bell watcher so that
        auto-detected tickets end up with the same complete record as manually opened tabs, rather
        than being stuck on the row/notification-derived placeholder text.
        """
        handle = self._open_ticket_in_new_tab(ticket_url)
        if not handle:
            self.status_update.emit(f"Failed to open ticket {ticket_number} in new tab ({source_label}).")
            return False



        self.processed_urls.add(ticket_url)
        self._processed_ids.add(ticket_number)
        self.tab_pending[handle] = 3
        self._extracting_handles.add(handle)

        try:
            with self._driver_lock:
                tab_index = self.driver.window_handles.index(handle) + 1
        except Exception:
            tab_index = 0

        self.status_update.emit(
            f"Opened ticket {ticket_number} in new tab for full details extraction ({source_label})"
        )
        Thread(
            target=self._extract_from_handle,
            args=(handle, ticket_url, ticket_number, tab_index),
            daemon=True,
        ).start()
        return True

    def _reextract_pending(self):
        """Upgrade restored preliminary records (dashboard-row/notification placeholders left over
        from a previous session) by opening each ticket and pulling full page details, same as any
        newly detected ticket. Runs once, right after login, before the patrol loop starts."""
        for ticket_id, ticket_url in self.pending_reextract:
            if self._stop_event.is_set():
                return
            if not ticket_id or not ticket_url:
                continue
            if ticket_id in self._processed_ids:
                continue
            self._capture_ticket_via_new_tab(ticket_id, ticket_url, source_label="Restored")
            time.sleep(1.5)

    def _emit_immediate_dashboard_record(self, ticket_number, ticket_url, details):
        """Surface a newly visible dashboard ticket immediately.

        Full ticket-page extraction still runs in the background and replaces this
        preliminary record. This removes the page-load delay from what the operator
        sees in Live Monitor.
        """
        now = datetime.now()
        details = details or {}
        station = str(details.get("station") or "Unknown station").strip()
        description = str(details.get("description") or "No description").strip()
        row_time = str(details.get("date") or "Dashboard time pending").strip()

        formatted = (
            f"{row_time} PMA issued a ticket ({ticket_number}) "
            f"at {station} - {description}"
        )
        record = {
            "ticket_id": ticket_number,
            "ticket_url": ticket_url,
            "time": row_time,
            "station": station,
            "description": description,
            "priority": str(details.get("priority") or "Unknown"),
            "assignee": str(details.get("assignee") or "Unknown"),
            "formatted_statement": formatted,
            "formatted": formatted,
            "captured_at": now.isoformat(),
            "tab_index": 0,
            "tab_title": "Dashboard",
            "tab_label": "Dashboard",
            "preliminary": True,
        }
        self.ticket_captured.emit(record)
        self.status_update.emit(
            f"Immediate dashboard capture {ticket_number} at {now.strftime('%H:%M:%S.%f')[:-3]}"
        )

    def _monitor_dashboard_and_notifications(self):
        """Monitor the dashboard at high frequency and notifications independently."""
        if not self._dashboard_monitoring_enabled:
            return

        try:
            current_time = time.time()

            self._poll_silent_ticket_api()

            if current_time - self._last_dashboard_refresh_time >= self._dashboard_refresh_interval:
                self._last_dashboard_refresh_time = current_time

                current_dashboard_tickets, ticket_details = self._scan_all_dashboard_rows(max_rows=15)
                current_dashboard_tickets = current_dashboard_tickets or []
                ticket_details = ticket_details or {}

                if not self._dashboard_baseline_initialized:
                    self._last_known_dashboard_tickets = list(current_dashboard_tickets)
                    self._baseline_ticket_ids.update(current_dashboard_tickets)
                    self._dashboard_baseline_initialized = True
                    self.status_update.emit(
                        f"Dashboard baseline initialized with {len(current_dashboard_tickets)} ticket(s)."
                    )
                else:
                    new_tickets = [
                        t for t in current_dashboard_tickets
                        if t not in self._last_known_dashboard_tickets
                        and t not in self._baseline_ticket_ids
                    ]

                    if new_tickets:
                        detected_at = datetime.now()
                        self.status_update.emit(
                            f"Found {len(new_tickets)} newly appearing dashboard ticket(s) "
                            f"at {detected_at.strftime('%H:%M:%S.%f')[:-3]}: {new_tickets}"
                        )

                        for new_ticket in new_tickets:
                            if new_ticket in self._processed_ids:
                                continue

                            details = ticket_details.get(new_ticket, {})
                            ticket_url = f"{self.config.BASE_TICKET_URL}{new_ticket}"

                            row_time = details.get("date")
                            if row_time and self._is_pre_session_ticket(row_time):
                                self._baseline_ticket_ids.add(new_ticket)
                                self.status_update.emit(
                                    f"Ignored old/reordered dashboard ticket {new_ticket}: {row_time}"
                                )
                                continue

                            self._emit_immediate_dashboard_record(
                                new_ticket, ticket_url, details
                            )

                            self.status_update.emit(
                                f"Opening full details in background: {new_ticket}"
                            )
                            self._capture_ticket_via_new_tab(
                                new_ticket, ticket_url, source_label="Dashboard"
                            )

                    self._last_known_dashboard_tickets = list(current_dashboard_tickets)

            if current_time - self._last_notification_poll_time >= self._notification_poll_interval:
                self._last_notification_poll_time = current_time
                current_notification_count = self._get_notification_count()

                if self._last_notification_count is None:
                    self._last_notification_count = current_notification_count
                    self.status_update.emit(
                        f"Notification baseline initialized at {current_notification_count}."
                    )
                elif current_notification_count > self._last_notification_count:
                    increase = current_notification_count - self._last_notification_count
                    self.status_update.emit(
                        f"New notification count increased by {increase} "
                        f"({self._last_notification_count} → {current_notification_count})."
                    )

                    if self._open_notification_dropdown():
                        ticket_number, ticket_url = self._extract_ticket_from_notification()
                        if ticket_number and ticket_url:
                            ticket_number = ticket_number.strip().upper()
                            if ticket_number in self._baseline_ticket_ids:
                                self.status_update.emit(
                                    f"Ignored pre-existing notification ticket: {ticket_number}"
                                )
                            elif ticket_number not in self._processed_ids:
                                self.status_update.emit(
                                    f"New ticket detected from notification: {ticket_number}"
                                )
                                self._capture_ticket_via_new_tab(
                                    ticket_number, ticket_url, source_label="Notification"
                                )

                    self._last_notification_count = current_notification_count
                elif current_notification_count != self._last_notification_count:
                    self._last_notification_count = current_notification_count

        except Exception as e:
            self.status_update.emit(f"Dashboard/notification monitoring error: {e}")

class ConfigDialog(QWidget):
    """Configuration dialog for advanced settings"""
    finished = pyqtSignal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.init_ui()
        enable_acrylic_blur(self)

    def showEvent(self, event):
        """Re-apply the acrylic backdrop on every show, not just the first.

        MainWindow keeps a single ConfigDialog instance alive and reuses it
        (show()/raise_()/activateWindow() on subsequent opens instead of
        recreating it). Windows' DWMWA_SYSTEMBACKDROP_TYPE "transient"
        backdrop only stays attached while the window remains continuously
        mapped - once it's closed/hidden and shown again, DWM drops the
        blur association but WA_TranslucentBackground stays set, so the
        window paints solid black instead of glass. Re-running
        enable_acrylic_blur() here re-establishes the backdrop each time
        the dialog reappears.
        """
        super().showEvent(event)
        enable_acrylic_blur(self)

    def init_ui(self):
        self.setWindowTitle("Configuration Settings")
        self.setGeometry(200, 200, 660, 540)
        self.setMinimumSize(660, 540)

        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet("""
            QWidget {
                background: transparent;
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QTabWidget::pane {
                border: 1px solid rgba(255, 255, 255, 0.25);
                background: rgba(255, 255, 255, 0.09);
                border-radius: 6px;
            }
            QTabBar::tab {
                background: rgba(255, 255, 255, 0.19);
                color: #5C4638;
                padding: 8px 16px;
                border: 1px solid rgba(255, 255, 255, 0.25);
                border-bottom: none;
                margin-right: 2px;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QTabBar::tab:selected {
                background: rgba(255, 255, 255, 0.19);
                color: #1C140F;
                border-top: 2px solid rgba(255, 255, 255, 0.55);
            }
            QTabBar::tab:hover:!selected {
                color: #1C140F;
                background: rgba(255, 255, 255, 0.13);
            }
            QLabel {
                color: #5C4638;
                font-size: 10pt;
                font-weight: 600;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: rgba(255, 255, 255, 0.07);
                border: 1px solid rgba(255, 255, 255, 0.17);
                border-radius: 6px;
                padding: 8px 16px;
                color: #1C140F;
                font-weight: 600;
                selection-background-color: rgba(255, 98, 0, 0.49);
                selection-color: #ffffff;
            }
            QLineEdit:hover, QSpinBox:hover, QComboBox:hover { border: 2px solid #FFD1B3; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 2px solid #FF6200;
            }
            QCheckBox {
                color: #1C140F;
                spacing: 8px;
                font-weight: 600;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid rgba(255, 255, 255, 0.19);
                background: rgba(255, 255, 255, 0.1);
                border-radius: 6px;
            }
            QCheckBox::indicator:checked {
                background: rgba(255, 98, 0, 0.49);
                border-color: #FF6200;
            }
            QPushButton {
                border: 2px solid #FF6200;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-family: "Segoe UI";
                font-size: 10pt;
                letter-spacing: 0.6px;
                text-transform: uppercase;
            }
            QPushButton#primary {
                background-color: rgba(255, 98, 0, 0.49);
                color: #ffffff;
                border: none;
            }
            QPushButton#primary:hover { background-color: rgba(232, 76, 0, 0.8); }
            QPushButton#secondary {
                background-color: transparent;
                color: #FF6200;
                border: 2px solid #FF6200;
            }
            QPushButton#secondary:hover {
                background-color: rgba(255, 98, 0, 0.49);
                color: #ffffff;
            }
            QPushButton#danger {
                background-color: transparent;
                color: #D32F2F;
                border: 2px solid #D32F2F;
            }
            QPushButton#danger:hover {
                background-color: rgba(211, 47, 47, 0.8);
                color: #ffffff;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.19);
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(12)


        tabs = QTabWidget()


        general_tab = QWidget()
        general_layout = QFormLayout()

        self.login_url_edit = QLineEdit(self.config.LOGIN_URL)
        self.dashboard_url_edit = QLineEdit(self.config.DASHBOARD_URL)
        self.base_ticket_url_edit = QLineEdit(self.config.BASE_TICKET_URL)

        general_layout.addRow("Login URL:", self.login_url_edit)
        general_layout.addRow("Dashboard URL:", self.dashboard_url_edit)
        general_layout.addRow("Base Ticket URL:", self.base_ticket_url_edit)

        general_tab.setLayout(general_layout)
        tabs.addTab(general_tab, "General")


        timing_tab = QWidget()
        timing_layout = QFormLayout()

        self.login_timeout_spin = QSpinBox()
        self.login_timeout_spin.setRange(10, 300)
        self.login_timeout_spin.setValue(self.config.LOGIN_TIMEOUT)

        self.page_load_timeout_spin = QSpinBox()
        self.page_load_timeout_spin.setRange(5, 120)
        self.page_load_timeout_spin.setValue(self.config.PAGE_LOAD_TIMEOUT)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(self.config.DELAY_BETWEEN_TICKETS)

        timing_layout.addRow("Login Timeout (seconds):", self.login_timeout_spin)
        timing_layout.addRow("Page Load Timeout (seconds):", self.page_load_timeout_spin)
        timing_layout.addRow("Delay Between Tickets (seconds):", self.delay_spin)

        timing_tab.setLayout(timing_layout)
        tabs.addTab(timing_tab, "Timing")


        retry_tab = QWidget()
        retry_layout = QFormLayout()

        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(1, 10)
        self.max_retries_spin.setValue(self.config.MAX_RETRIES)

        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(1, 60)
        self.retry_delay_spin.setValue(self.config.RETRY_DELAY)

        retry_layout.addRow("Max Retries:", self.max_retries_spin)
        retry_layout.addRow("Retry Delay (seconds):", self.retry_delay_spin)

        retry_tab.setLayout(retry_layout)
        tabs.addTab(retry_tab, "Retry")


        chrome_tab = QWidget()
        chrome_layout = QFormLayout()

        self.chrome_binary_edit = QLineEdit(self.config.CHROME_BINARY_PATH)
        self.headless_checkbox = QCheckBox()
        self.headless_checkbox.setChecked(self.config.HEADLESS_MODE)

        chrome_layout.addRow("Chrome Binary Path:", self.chrome_binary_edit)
        chrome_layout.addRow("Headless Mode:", self.headless_checkbox)

        chrome_tab.setLayout(chrome_layout)
        tabs.addTab(chrome_tab, "Chrome")


        content_tab = QWidget()
        content_layout = QFormLayout()

        self.sensor_keywords_edit = QLineEdit(",".join(self.config.KEYWORD_LIST))
        self.content_xpath_edit = QLineEdit(self.config.CONTENT_XPATH)
        self.comment_xpath_edit = QLineEdit(self.config.COMMENT_XPATH)
        self.station_xpath_edit = QLineEdit(self.config.STATION_XPATH)
        self.resolution_time_xpath_edit = QLineEdit(self.config.RESOLUTION_TIME_XPATH)
        self.ticket_start_time_xpath_edit = QLineEdit(self.config.TICKET_START_TIME_XPATH)
        self.resolved_datetime_xpath_edit = QLineEdit(self.config.RESOLVED_DATETIME_XPATH)
        self.ticket_category_xpath_edit = QLineEdit(self.config.TICKET_CATEGORY_XPATH)

        content_layout.addRow("Keywords (comma-separated):", self.sensor_keywords_edit)
        content_layout.addRow("Content XPath:", self.content_xpath_edit)
        content_layout.addRow("Comment XPath:", self.comment_xpath_edit)
        content_layout.addRow("Station XPath:", self.station_xpath_edit)
        content_layout.addRow("Resolution Time XPath:", self.resolution_time_xpath_edit)
        content_layout.addRow("Ticket Start Time XPath:", self.ticket_start_time_xpath_edit)
        content_layout.addRow("Resolved Date Time XPath:", self.resolved_datetime_xpath_edit)
        content_layout.addRow("Ticket Category XPath:", self.ticket_category_xpath_edit)


        content_tab.setLayout(content_layout)
        tabs.addTab(content_tab, "Content")

        layout.addWidget(tabs)


        button_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        cancel_button = QPushButton("Cancel")
        reset_button = QPushButton("Reset to Defaults")

        save_button.setObjectName("primary")
        cancel_button.setObjectName("secondary")
        reset_button.setObjectName("danger")

        save_button.clicked.connect(self.save_config)
        cancel_button.clicked.connect(self.close)
        reset_button.clicked.connect(self.reset_defaults)

        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(reset_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def save_config(self):
        """Save configuration changes"""
        self.config.LOGIN_URL = self.login_url_edit.text()
        self.config.DASHBOARD_URL = self.dashboard_url_edit.text()
        self.config.BASE_TICKET_URL = self.base_ticket_url_edit.text()
        self.config.LOGIN_TIMEOUT = self.login_timeout_spin.value()
        self.config.PAGE_LOAD_TIMEOUT = self.page_load_timeout_spin.value()
        self.config.DELAY_BETWEEN_TICKETS = self.delay_spin.value()
        self.config.MAX_RETRIES = self.max_retries_spin.value()
        self.config.RETRY_DELAY = self.retry_delay_spin.value()
        self.config.CHROME_BINARY_PATH = self.chrome_binary_edit.text()
        self.config.HEADLESS_MODE = self.headless_checkbox.isChecked()
        self.config.KEYWORD_LIST = [
            kw.strip() for kw in self.sensor_keywords_edit.text().split(",") if kw.strip()
        ]
        self.config.CONTENT_XPATH = self.content_xpath_edit.text()
        self.config.COMMENT_XPATH = self.comment_xpath_edit.text()
        self.config.STATION_XPATH = self.station_xpath_edit.text()
        self.config.RESOLUTION_TIME_XPATH = self.resolution_time_xpath_edit.text()
        self.config.TICKET_START_TIME_XPATH = self.ticket_start_time_xpath_edit.text()
        self.config.RESOLVED_DATETIME_XPATH = self.resolved_datetime_xpath_edit.text()
        self.config.TICKET_CATEGORY_XPATH = self.ticket_category_xpath_edit.text()

        self.config.save_settings()
        QMessageBox.information(self, "Settings Saved", "Configuration has been saved successfully!")
        self.finished.emit()
        self.close()

    def reset_defaults(self):
        """Reset to default values"""
        reply = QMessageBox.question(self, "Reset Settings",
                                   "Are you sure you want to reset all settings to defaults?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:

            self.config.settings.clear()
            self.config.load_defaults()


            self.login_url_edit.setText(self.config.LOGIN_URL)
            self.dashboard_url_edit.setText(self.config.DASHBOARD_URL)
            self.base_ticket_url_edit.setText(self.config.BASE_TICKET_URL)
            self.login_timeout_spin.setValue(self.config.LOGIN_TIMEOUT)
            self.page_load_timeout_spin.setValue(self.config.PAGE_LOAD_TIMEOUT)
            self.delay_spin.setValue(self.config.DELAY_BETWEEN_TICKETS)
            self.max_retries_spin.setValue(self.config.MAX_RETRIES)
            self.retry_delay_spin.setValue(self.config.RETRY_DELAY)
            self.chrome_binary_edit.setText(self.config.CHROME_BINARY_PATH)
            self.headless_checkbox.setChecked(self.config.HEADLESS_MODE)
            self.sensor_keywords_edit.setText(",".join(self.config.KEYWORD_LIST))
            self.content_xpath_edit.setText(self.config.CONTENT_XPATH)
            self.comment_xpath_edit.setText(self.config.COMMENT_XPATH)
            self.station_xpath_edit.setText(self.config.STATION_XPATH)
            self.resolution_time_xpath_edit.setText(self.config.RESOLUTION_TIME_XPATH)
            self.ticket_start_time_xpath_edit.setText(self.config.TICKET_START_TIME_XPATH)
            self.resolved_datetime_xpath_edit.setText(self.config.RESOLVED_DATETIME_XPATH)
            self.ticket_category_xpath_edit.setText(self.config.TICKET_CATEGORY_XPATH)

def resource_path(relative):
    """Return absolute path to resource — works for dev and PyInstaller EXE."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def load_blurred_background(path: str, blur_radius: int = 40) -> QPixmap | None:
    """
    Load an image and bake a real Gaussian blur into it (once, at startup),
    returning it as a QPixmap ready to be scaled/painted per-frame.

    Baking the blur once here — instead of blurring on every resize/paint —
    keeps window resizing smooth. If Pillow isn't installed, falls back to
    the sharp image rather than failing outright.
    """
    if not os.path.exists(path):
        return None
    try:
        if _HAS_PIL:
            img = Image.open(path).convert("RGB")
            img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            data = img.tobytes("raw", "RGB")
            qimage = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimage.copy())
        else:
            return QPixmap(path)
    except Exception:
        try:
            return QPixmap(path)
        except Exception:
            return None


class CustomTitleBar(QWidget):
    """Compact frameless title bar with Pin beside Minimize."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._drag_global_pos = None
        self.setObjectName("customTitleBar")
        self.setFixedHeight(36)
        self.setStyleSheet("""
            QWidget#customTitleBar {
                background: rgba(255, 255, 255, 0.12);
                border-bottom: 1px solid rgba(255, 255, 255, 0.18);
            }
            QLabel#customTitleLabel {
                color: #3A2E26;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: 700;
                background: transparent;
            }
            QToolButton {
                background: transparent;
                color: #3A2E26;
                border: none;
                border-radius: 5px;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.22);
            }
            QToolButton#pinButton:checked {
                background: rgba(255, 98, 0, 0.20);
                color: #D64D00;
            }
            QToolButton#closeButton:hover {
                background: rgba(211, 47, 47, 0.85);
                color: white;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 4, 0)
        layout.setSpacing(2)

        self.title_label = QLabel(window.windowTitle() or "ITMIS Ticket Scraper")
        self.title_label.setObjectName("customTitleLabel")
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.pin_button = QToolButton()
        self.pin_button.setObjectName("pinButton")
        self.pin_button.setText("📌")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(bool(getattr(window, "_always_on_top", False)))
        self.pin_button.setToolTip("Keep this window always on top")
        self.pin_button.setFixedSize(38, 30)
        self.pin_button.toggled.connect(window._toggle_always_on_top)
        layout.addWidget(self.pin_button)

        self.min_button = QToolButton()
        self.min_button.setText("—")
        self.min_button.setToolTip("Minimize")
        self.min_button.setFixedSize(42, 30)
        self.min_button.clicked.connect(window.showMinimized)
        layout.addWidget(self.min_button)

        self.max_button = QToolButton()
        self.max_button.setText("□")
        self.max_button.setToolTip("Maximize / Restore")
        self.max_button.setFixedSize(42, 30)
        self.max_button.clicked.connect(window._toggle_maximize_restore)
        layout.addWidget(self.max_button)

        self.close_button = QToolButton()
        self.close_button.setObjectName("closeButton")
        self.close_button.setText("×")
        self.close_button.setToolTip("Close")
        self.close_button.setFixedSize(44, 30)
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.close_button)

    def set_maximized(self, maximized):
        self.max_button.setText("❐" if maximized else "□")
        self.max_button.setToolTip("Restore" if maximized else "Maximize")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QToolButton):
                self._drag_global_pos = event.globalPosition().toPoint()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_global_pos is not None and
                event.buttons() & Qt.MouseButton.LeftButton):
            if self._window.isMaximized():

                ratio = max(0.05, min(0.95, event.position().x() / max(1, self.width())))
                old_width = self._window.width()
                self._window.showNormal()
                new_x = int(event.globalPosition().x() - self._window.width() * ratio)
                self._window.move(new_x, int(event.globalPosition().y() - 16))
                self._drag_global_pos = event.globalPosition().toPoint()
            else:
                current = event.globalPosition().toPoint()
                delta = current - self._drag_global_pos
                self._window.move(self._window.pos() + delta)
                self._drag_global_pos = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_global_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QToolButton):
                self._window._toggle_maximize_restore()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class TicketNotificationToast(QFrame):
    """Persistent in-app alert for a newly detected live ticket."""
    dismissed = pyqtSignal(str)

    def __init__(self, record, parent=None):
        super().__init__(parent)
        self.record = dict(record or {})
        self.ticket_id = self.record.get("ticket_id", "")
        self.setObjectName("ticketNotificationToast")
        self.setFixedWidth(430)
        self.setMinimumHeight(170)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#ticketNotificationToast {
                background: rgba(255, 255, 255, 0.96);
                border: 2px solid rgba(255, 98, 0, 0.90);
                border-radius: 14px;
            }
            QLabel { background: transparent; color: #241912; }
            QLabel#toastBadge {
                color: #ffffff;
                background: #FF6200;
                border-radius: 7px;
                padding: 4px 9px;
                font-size: 9pt;
                font-weight: 800;
            }
            QLabel#toastPriority {
                color: #8A3210;
                background: rgba(255, 98, 0, 0.10);
                border: 1px solid rgba(255, 98, 0, 0.25);
                border-radius: 7px;
                padding: 3px 8px;
                font-size: 9pt;
                font-weight: 800;
            }
            QLabel#toastTicket {
                color: #20150F;
                font-size: 12pt;
                font-weight: 800;
            }
            QLabel#toastMeta {
                color: #665247;
                font-size: 9.5pt;
                font-weight: 600;
            }
            QLabel#toastDesc {
                color: #34251D;
                font-size: 10pt;
                font-weight: 600;
            }
            QPushButton {
                min-height: 28px;
                border-radius: 7px;
                padding: 5px 12px;
                font-weight: 700;
            }
            QPushButton#toastOpen {
                color: white;
                background: #FF6200;
                border: 1px solid #E84C00;
            }
            QPushButton#toastOpen:hover { background: #E84C00; }
            QPushButton#toastDismiss {
                color: #4B392F;
                background: rgba(28, 20, 15, 0.05);
                border: 1px solid rgba(28, 20, 15, 0.15);
            }
            QPushButton#toastDismiss:hover { background: rgba(28, 20, 15, 0.10); }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(8)
        badge = QLabel("NEW TICKET")
        badge.setObjectName("toastBadge")
        header.addWidget(badge, 0)
        header.addStretch(1)
        self.priority_label = QLabel()
        self.priority_label.setObjectName("toastPriority")
        header.addWidget(self.priority_label, 0)
        layout.addLayout(header)

        self.ticket_label = QLabel()
        self.ticket_label.setObjectName("toastTicket")
        self.ticket_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.ticket_label)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("toastMeta")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.description_label = QLabel()
        self.description_label.setObjectName("toastDesc")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        self.open_button = QPushButton("Open Ticket")
        self.open_button.setObjectName("toastOpen")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self._open_ticket)
        buttons.addWidget(self.open_button)
        dismiss_button = QPushButton("Dismiss")
        dismiss_button.setObjectName("toastDismiss")
        dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_button.clicked.connect(self.dismiss)
        buttons.addWidget(dismiss_button)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(15000)
        self.update_record(record)

        apply_soft_shadow(self, blur_radius=24, y_offset=5, alpha=55)

    def update_record(self, record):
        self.record = dict(record or {})
        self.ticket_id = self.record.get("ticket_id", self.ticket_id)
        priority = str(self.record.get("priority") or "Unknown").strip()
        station = str(self.record.get("station") or "Unknown station").strip()
        time_text = str(self.record.get("time") or "Time pending").strip()
        description = str(self.record.get("description") or "Ticket details are loading...").strip()
        if len(description) > 220:
            description = description[:217].rstrip() + "..."

        self.ticket_label.setText(self.ticket_id or "New ITMIS ticket")
        self.priority_label.setText(priority.upper())
        self.meta_label.setText(f"{station}  •  {time_text}")
        self.description_label.setText(description)
        self.open_button.setEnabled(bool(self.record.get("ticket_url")))
        self.adjustSize()

    def _open_ticket(self):
        url = self.record.get("ticket_url", "")
        if url:
            webbrowser.open(url)
        self.dismiss()

    def dismiss(self):
        if not self.isVisible():
            return
        self._timer.stop()
        self.hide()
        self.dismissed.emit(self.ticket_id)
        self.deleteLater()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self._always_on_top = self.config.settings.value("always_on_top", False, type=bool)
        window_flags = self.windowFlags() | Qt.WindowType.FramelessWindowHint
        if self._always_on_top:
            window_flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(window_flags)
        self.scraper_thread = None
        self.live_monitor_thread = None
        self.config_dialog = None
        self.selected_file_path = ""
        self.live_monitor_tickets = []
        self.live_monitor_ticket_ids = set()
        self.live_monitor_card_widgets = {}
        self.live_monitor_status_messages = []
        self._lm_dupe_count = 0
        self.live_monitor_session_start = None
        self.notification_tray = None
        self._notification_toasts = {}
        self._notification_toast_order = []
        self._notified_ticket_ids = set()
        self._last_notification_record = None
        self._live_monitor_session_path = os.path.join(
            os.environ.get("LOCALAPPDATA", os.getcwd()),
            "ITMIS_Ticket_Scraper",
            "live_monitor_session.json",
        )
        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))


        bg_path = os.path.join(os.path.dirname(__file__), "background.png")
        self._bg_pixmap = QPixmap(bg_path) if os.path.exists(bg_path) else None


        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.init_ui()
        self._setup_notification_system()
        enable_acrylic_blur(self, tint_rgba=(255, 255, 255, 10))
        self.init_live_monitor()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._bg_pixmap and not self._bg_pixmap.isNull():

            scaled = self._bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )


            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2

            painter.drawPixmap(x, y, scaled)
        else:

            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))

        super().paintEvent(event)


    def init_ui(self):
        self.setWindowTitle("ITMIS Ticket Scraper v2.0")
        self.setMinimumSize(1000, 780)
        self.resize(1120, 960)


        self.menuBar().setVisible(False)


        self.setFont(QFont("Segoe UI", 10))


        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(f"""
            QMainWindow, QWidget#centralWidget, QTabWidget::pane {{
                font-family: "Segoe UI";
                background: transparent;
                border: none;
                color: #1C140F;
                font-size: 10pt;
                font-weight: 500;
            }}
            QWidget {{
                font-family: "Segoe UI";
                background: transparent;
                color: #1C140F;
                font-size: 10pt;
                font-weight: 500;
            }}

            /* ── Group boxes (pure glass) ── */
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 18px;
                padding: 14px 12px 10px 12px;
            }}
            QGroupBox:hover {{
                background: {GLASS_BG_HOVER};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #5C4638;
                font-weight: 700;
                font-size: 10pt;
                letter-spacing: 0.7px;
                text-transform: uppercase;
                background: transparent;
            }}

            /* ── Inputs (glass) ── */
            QLineEdit, QSpinBox, QComboBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER_SOFT};
                border-radius: 12px;
                padding: 8px 16px;
                color: #1C140F;
                font-weight: 600;
                selection-background-color: rgba(255, 98, 0, 0.49);
                selection-color: #ffffff;
            }}
            QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{
                border: 1px solid rgba(255, 98, 0, 0.26);
                background: {GLASS_BG_HOVER};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border: 2px solid rgba(255, 98, 0, 0.33);
                background: {GLASS_BG_HOVER};
            }}

            /* ── Tabs (pure glass) ── */
            QTabBar::tab {{
                background: rgba(255, 255, 255, 0.02);
                color: #3A2E26;
                padding: 8px 18px;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-bottom: none;
                margin-right: 4px;
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }}
            QTabBar::tab:selected {{
                background: rgba(255, 255, 255, 0.19);
                color: #1C140F;
                border-top: 2px solid rgba(255, 255, 255, 0.55);
            }}
            QTabBar::tab:hover:!selected {{
                background: rgba(255, 255, 255, 0.04);
            }}

            /* ── Scrollbars ── */
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.19);
                border-radius: 6px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 0.28); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}

            /* ── Labels ── */
            QLabel {{
                color: #1C140F;
                background-color: transparent;
                font-weight: 600;
            }}

            /* ── Splitter handle ── */
            QSplitter::handle {{
                background-color: rgba(28, 20, 15, 0.12);
                height: 2px;
            }}

            /* ── Toolbar (glass) ── */
            QToolBar {{
                background-color: {GLASS_BG};
                border: none;
                border-bottom: {GLASS_BORDER_SOFT};
                spacing: 4px;
                padding: 8px 16px;
            }}
            QToolBar QToolButton {{
                background-color: transparent;
                color: #1C140F;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 8px 16px;
                font-family: "Segoe UI";
                font-size: 10pt;
                letter-spacing: 0.6px;
                font-weight: 800;
            }}
            QToolBar QToolButton:hover {{
                background-color: {GLASS_BG_HOVER};
                border-color: rgba(255, 255, 255, 0.22);
            }}
            QToolBar QToolButton:pressed {{
                background-color: rgba(255, 255, 255, 0.22);
            }}
            QToolBar QToolButton:disabled {{
                color: rgba(28, 20, 15, 0.4);
            }}
        """)


        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(self.select_file)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.start_scraping)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(lambda: self.log_area.clear())
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(QApplication.quit)


        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        root_layout = QVBoxLayout(main_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)




        self.custom_title_bar = CustomTitleBar(self)
        root_layout.addWidget(self.custom_title_bar)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setToolTip("Drag to resize")
        self.size_grip.raise_()


        header = QWidget()
        header.setFixedHeight(62)
        header.setStyleSheet("""
            QWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 255, 255, 0.17),
                    stop:0.5 rgba(255, 255, 255, 0.02),
                    stop:1 rgba(255, 255, 255, 0.17)
                );
                border-bottom: 1px solid rgba(255, 255, 255, 0.19);
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 0, 20, 0)
        header_layout.setSpacing(14)


        logo_path = resource_path("logo.ico")
        logo_lbl = QLabel()
        logo_lbl.setFixedSize(38, 38)
        logo_lbl.setStyleSheet("background: transparent;")
        if os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaled(38, 38,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(pix)
        else:
            logo_lbl.setText("🔍")
            logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo_lbl.setStyleSheet("font-size:22px; background:transparent;")

        title_lbl = QLabel("ITMIS  TICKET  SCRAPER")
        title_lbl.setStyleSheet("""
            QLabel {
                color: #3A2E26;
                font-family: "Segoe UI";
                font-size: 16px;
                font-weight: 800;
                letter-spacing: 2.5px;
                background: transparent;
            }
        """)

        shortcuts_lbl = QLabel("Ctrl+O  Open  │  Ctrl+S  Start  │  Ctrl+L  Clear  │  Ctrl+Q  Quit")
        shortcuts_lbl.setStyleSheet("""
            QLabel {
                color: rgba(0, 0, 0, 0.8);
                font-family: "Segoe UI";
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.6px;
                background: transparent;
            }
        """)

        header_layout.addWidget(logo_lbl)
        header_layout.addWidget(title_lbl)
        header_layout.addSpacing(12)
        header_layout.addWidget(shortcuts_lbl)
        header_layout.addStretch()
        root_layout.addWidget(header)


        dashboard_widget = QWidget()
        dash_layout = QVBoxLayout(dashboard_widget)
        dash_layout.setContentsMargins(14, 10, 14, 6)
        dash_layout.setSpacing(8)


        file_row = QWidget()
        file_row.setStyleSheet("background: transparent;")
        file_row_h = QHBoxLayout(file_row)
        file_row_h.setContentsMargins(0, 0, 0, 0)
        file_row_h.setSpacing(8)

        self.file_button = QPushButton("▲  LOAD FILE")
        self.file_button.clicked.connect(self.select_file)
        self.file_button.setMinimumHeight(36)
        self.file_button.setFixedWidth(140)
        self.file_button.setStyleSheet(BTN_PRIMARY_OUTLINE)

        self.file_label = QLabel("NO FILE SELECTED")
        self.file_label.setWordWrap(True)
        self.file_label.setStyleSheet("""
            QLabel {
                padding: 8px 16px;
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.33),
                    stop:1 rgba(255, 255, 255, 0.22)
                );
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 12px;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 600;
            }
        """)
        apply_soft_shadow(self.file_label)

        file_row_h.addWidget(self.file_button)
        file_row_h.addWidget(self.file_label, 1)
        dash_layout.addWidget(file_row)




        cards_grid_widget = QWidget()
        cards_grid_widget.setStyleSheet("background: transparent;")
        cards_grid = QGridLayout(cards_grid_widget)
        cards_grid.setContentsMargins(0, 0, 0, 0)
        cards_grid.setSpacing(12)
        cards_grid.setColumnStretch(0, 7)
        cards_grid.setColumnStretch(1, 7)
        cards_grid.setColumnStretch(2, 16)
        cards_grid.setRowStretch(0, 1)
        cards_grid.setRowStretch(1, 1)
        cards_grid.setRowStretch(2, 1)
        cards_grid.setRowStretch(3, 2)



        config_section = self.create_config_section()
        cards_grid.addWidget(config_section, 0, 0, 1, 1)

        stats_section = self.create_stats_section()
        cards_grid.addWidget(stats_section, 0, 1, 1, 1)

        link_generator_section = self.create_link_generator_section()
        cards_grid.addWidget(link_generator_section, 0, 2, 4, 1)


        quick_actions_section = self.create_quick_actions_section()
        cards_grid.addWidget(quick_actions_section, 1, 0, 1, 1)

        system_status_section = self.create_system_status_section()
        cards_grid.addWidget(system_status_section, 1, 1, 1, 1)


        help_section = self.create_help_section()
        cards_grid.addWidget(help_section, 2, 0, 1, 1)

        recent_activity_section = self.create_recent_activity_section()
        cards_grid.addWidget(recent_activity_section, 2, 1, 1, 1)


        live_console_section = self.create_live_console_section()
        cards_grid.addWidget(live_console_section, 3, 0, 1, 2)

        dash_layout.addWidget(cards_grid_widget)


        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(dashboard_widget, "Dashboard")
        self.live_monitor_tab = self.create_live_monitor_tab()
        self.main_tabs.addTab(self.live_monitor_tab, "Live Monitor")
        root_layout.addWidget(self.main_tabs)


        self.log("ITMIS Ticket Scraper initialized")
        self.log(f"Configuration loaded — Max retries: {self.config.MAX_RETRIES}, Delay: {self.config.DELAY_BETWEEN_TICKETS}s")



    def _toggle_maximize_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        if hasattr(self, "custom_title_bar"):
            self.custom_title_bar.set_maximized(self.isMaximized())

    def _toggle_always_on_top(self, enabled):
        """Toggle top-most state while preserving size/maximized state."""
        enabled = bool(enabled)
        if enabled == self._always_on_top:
            return
        self._always_on_top = enabled
        self.config.settings.setValue("always_on_top", enabled)

        was_maximized = self.isMaximized()
        old_geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()
        if was_maximized:
            self.showMaximized()
        else:
            self.setGeometry(old_geometry)
        if enabled:
            self.raise_()
            self.activateWindow()


        QTimer.singleShot(0, lambda: enable_acrylic_blur(self, tint_rgba=(255, 255, 255, 10)))

    def _setup_notification_system(self):
        """Create OS + in-app notification channels for new live tickets."""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.notification_tray = QSystemTrayIcon(self)
            icon = self.windowIcon()
            if not icon.isNull():
                self.notification_tray.setIcon(icon)
            self.notification_tray.setToolTip("ITMIS Ticket Scraper — Live Monitor")
            self.notification_tray.messageClicked.connect(self._on_notification_message_clicked)
            self.notification_tray.activated.connect(self._on_tray_activated)
            self.notification_tray.show()
        except Exception:
            self.notification_tray = None

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._bring_window_to_front(show_live_monitor=True)

    def _on_notification_message_clicked(self):
        """Bring the operator straight to Live Monitor when the OS alert is clicked."""
        self._bring_window_to_front(show_live_monitor=True)

    def _bring_window_to_front(self, show_live_monitor=False):
        if self.isMinimized():
            self.showNormal()
        self.show()
        if show_live_monitor and hasattr(self, "main_tabs") and hasattr(self, "live_monitor_tab"):
            self.main_tabs.setCurrentWidget(self.live_monitor_tab)
        self.raise_()
        self.activateWindow()

    def _play_new_ticket_sound(self):
        """Two short alert tones without blocking the live-monitor worker."""
        try:
            QApplication.beep()
            QTimer.singleShot(220, QApplication.beep)
        except Exception:
            pass

    def _on_ticket_toast_dismissed(self, ticket_id):
        toast = self._notification_toasts.pop(ticket_id, None)
        if ticket_id in self._notification_toast_order:
            self._notification_toast_order.remove(ticket_id)
        self._reposition_notification_toasts()

    def _show_ticket_toast(self, record):
        """Show a persistent, actionable alert inside the application."""
        ticket_id = record.get("ticket_id", "")
        if not ticket_id:
            return

        existing = self._notification_toasts.get(ticket_id)
        if existing is not None:
            existing.update_record(record)
            existing.show()
            existing.raise_()
            self._reposition_notification_toasts()
            return

        toast = TicketNotificationToast(record, self)
        toast.dismissed.connect(self._on_ticket_toast_dismissed)
        self._notification_toasts[ticket_id] = toast
        self._notification_toast_order.append(ticket_id)


        while len(self._notification_toast_order) > 3:
            oldest_id = self._notification_toast_order[0]
            oldest = self._notification_toasts.get(oldest_id)
            if oldest is not None:
                oldest.dismiss()
            else:
                self._notification_toast_order.pop(0)

        toast.show()
        toast.raise_()
        self._reposition_notification_toasts()

    def _update_ticket_notification(self, record):
        """Upgrade the visible in-app alert when full ticket details arrive."""
        ticket_id = record.get("ticket_id", "")
        toast = self._notification_toasts.get(ticket_id)
        if toast is not None:
            toast.update_record(record)
            toast.raise_()
            self._reposition_notification_toasts()

    def _reposition_notification_toasts(self):
        if not self._notification_toast_order:
            return
        top_margin = 14
        if hasattr(self, "custom_title_bar"):
            top_margin += max(0, self.custom_title_bar.height())
        y = top_margin
        right_margin = 16
        for ticket_id in list(self._notification_toast_order):
            toast = self._notification_toasts.get(ticket_id)
            if toast is None:
                continue
            toast.adjustSize()
            x = max(8, self.width() - toast.width() - right_margin)
            toast.move(x, y)
            toast.raise_()
            y += toast.height() + 10

    def _show_new_ticket_notification(self, record):
        """Issue one strong alert per new ticket, immediately on dashboard detection."""
        ticket_id = record.get("ticket_id", "New ticket")
        if ticket_id in self._notified_ticket_ids:
            self._update_ticket_notification(record)
            return
        self._notified_ticket_ids.add(ticket_id)
        self._last_notification_record = dict(record or {})

        station = record.get("station", "Unknown station") or "Unknown station"
        time_text = record.get("time", "") or ""
        priority = str(record.get("priority", "Unknown") or "Unknown").strip()
        description = (record.get("description", "") or "").strip()
        if len(description) > 160:
            description = description[:157].rstrip() + "..."

        body_parts = [part for part in (station, time_text, f"Priority: {priority}") if part]
        body = " • ".join(body_parts)
        if description:
            body = f"{body}\n{description}" if body else description

        priority_upper = priority.upper()
        prefix = "🚨" if priority_upper in {"CRITICAL", "HIGH"} else "🔔"

        self._show_ticket_toast(record)
        self._play_new_ticket_sound()
        try:
            QApplication.alert(self, 7000)
        except Exception:
            pass

        try:
            if self.notification_tray and self.notification_tray.isVisible():
                self.notification_tray.showMessage(
                    f"{prefix} New ITMIS Ticket — {ticket_id}",
                    body or "A new live ticket was detected.",
                    QSystemTrayIcon.MessageIcon.Warning
                    if priority_upper in {"CRITICAL", "HIGH"}
                    else QSystemTrayIcon.MessageIcon.Information,
                    12000,
                )
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "size_grip"):
            self.size_grip.move(
                max(0, self.width() - self.size_grip.sizeHint().width()),
                max(0, self.height() - self.size_grip.sizeHint().height()),
            )
            self.size_grip.raise_()
        if hasattr(self, "_notification_toasts"):
            self._reposition_notification_toasts()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "custom_title_bar"):
            self.custom_title_bar.set_maximized(self.isMaximized())

    def create_config_section(self):
        """Create configuration preview section"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("ACTIVE  CONFIGURATION")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 10, 16, 12)

        self.config_preview = QLabel()
        self.config_preview.setWordWrap(True)
        self.config_preview.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 9.5pt;
                font-weight: 500;
                letter-spacing: 0.2px;
            }
        """)
        self.update_config_preview()
        body_layout.addWidget(self.config_preview)
        outer.addWidget(body)

        return section

    def create_stats_section(self):
        """Create statistics section"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("SESSION  STATISTICS")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(16, 10, 16, 12)

        self.stats_label = QLabel("AWAITING FIRST RUN")
        self.stats_label.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 9.5pt;
                font-weight: 500;
                letter-spacing: 0.2px;
            }
        """)
        body_layout.addWidget(self.stats_label)
        outer.addWidget(body)

        return section

    def create_link_generator_section(self):
        """Create Link Generator section (Chrome extension integration)"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("LINK  GENERATOR")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(10)


        stats_bar = QWidget()
        stats_bar.setStyleSheet("background: transparent;")
        stats_layout = QHBoxLayout(stats_bar)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(12)

        self.stat_total = QLabel("0")
        self.stat_total.setStyleSheet("""
            QLabel {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.07),
                    stop:1 rgba(255, 255, 255, 0.03)
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 8px 16px;
                color: #1C140F;
                font-weight: 700;
                font-family: "Segoe UI";
                font-size: 11pt;
            }
        """)

        self.stat_valid = QLabel("0")
        self.stat_valid.setStyleSheet("""
            QLabel {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(233, 248, 239, 0.18),
                    stop:1 rgba(233, 248, 239, 0.1)
                );
                border: 1px solid rgba(0, 166, 81, 0.4);
                border-radius: 12px;
                padding: 8px 16px;
                color: #007A3B;
                font-weight: 700;
                font-family: "Segoe UI";
                font-size: 11pt;
            }
        """)

        self.stat_invalid = QLabel("0")
        self.stat_invalid.setStyleSheet("""
            QLabel {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 235, 238, 0.18),
                    stop:1 rgba(255, 235, 238, 0.1)
                );
                border: 1px solid rgba(211, 47, 47, 0.4);
                border-radius: 12px;
                padding: 8px 16px;
                color: #D32F2F;
                font-weight: 700;
                font-family: "Segoe UI";
                font-size: 11pt;
            }
        """)

        stats_layout.addWidget(QLabel("Total:"))
        stats_layout.addWidget(self.stat_total)
        stats_layout.addWidget(QLabel("Valid:"))
        stats_layout.addWidget(self.stat_valid)
        stats_layout.addWidget(QLabel("Invalid:"))
        stats_layout.addWidget(self.stat_invalid)
        stats_layout.addStretch()
        body_layout.addWidget(stats_bar)


        input_container = QWidget()
        input_container.setStyleSheet("background: transparent;")
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)


        file_row = QWidget()
        file_row.setStyleSheet("background: transparent;")
        file_row_h = QHBoxLayout(file_row)
        file_row_h.setContentsMargins(0, 0, 0, 0)
        file_row_h.setSpacing(8)

        self.load_example_btn = QPushButton("🧪  EXAMPLE")
        self.load_example_btn.clicked.connect(self.load_example_tickets)
        self.load_example_btn.setMinimumHeight(28)
        self.load_example_btn.setStyleSheet(BTN_NEUTRAL_OUTLINE)

        self.file_name_label = QLabel("No file loaded  —  use LOAD FILE above or paste tickets below")
        self.file_name_label.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 500;
            }
        """)

        file_row_h.addWidget(self.load_example_btn)
        file_row_h.addWidget(self.file_name_label)
        file_row_h.addStretch()
        input_layout.addWidget(file_row)


        self.ticket_input = QTextEdit()
        self.ticket_input.setPlaceholderText("Paste ticket numbers here (one per line):\nLHR.L2SP.2024.08.00074528\nLHR.L2SP.2024.01.00012345")
        self.ticket_input.setMaximumHeight(80)
        self.ticket_input.setStyleSheet("""
            QTextEdit {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.07),
                    stop:1 rgba(255, 255, 255, 0.03)
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 8px 16px;
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: 500;
            }
            QTextEdit:focus {
                border: 2px solid rgba(0, 166, 81, 0.5);
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.11),
                    stop:1 rgba(255, 255, 255, 0.07)
                );
            }
        """)

        input_layout.addWidget(self.ticket_input)
        body_layout.addWidget(input_container)


        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent;")
        btn_row_h = QHBoxLayout(btn_row)
        btn_row_h.setContentsMargins(0, 0, 0, 0)
        btn_row_h.setSpacing(8)

        self.generate_links_btn = QPushButton("⚡  GENERATE")
        self.generate_links_btn.clicked.connect(self.generate_links)
        self.generate_links_btn.setMinimumHeight(32)
        self.generate_links_btn.setStyleSheet(BTN_SUCCESS)

        self.download_excel_btn = QPushButton("📥  EXCEL")
        self.download_excel_btn.clicked.connect(self.download_generated_links_excel)
        self.download_excel_btn.setEnabled(False)
        self.download_excel_btn.setMinimumHeight(32)
        self.download_excel_btn.setStyleSheet(BTN_SUCCESS_OUTLINE)

        self.download_txt_btn = QPushButton("📄  TXT")
        self.download_txt_btn.clicked.connect(self.download_generated_links_txt)
        self.download_txt_btn.setEnabled(False)
        self.download_txt_btn.setMinimumHeight(32)
        self.download_txt_btn.setStyleSheet(BTN_PRIMARY_OUTLINE)

        self.scrape_links_btn = QPushButton("▶  SCRAPE")
        self.scrape_links_btn.clicked.connect(self.scrape_generated_links)
        self.scrape_links_btn.setEnabled(False)
        self.scrape_links_btn.setMinimumHeight(32)
        self.scrape_links_btn.setStyleSheet(BTN_NEUTRAL_OUTLINE)

        self.clear_tickets_btn = QPushButton("✕  CLEAR")
        self.clear_tickets_btn.clicked.connect(self.clear_ticket_input)
        self.clear_tickets_btn.setMinimumHeight(32)
        self.clear_tickets_btn.setStyleSheet(BTN_DANGER_OUTLINE)

        btn_row_h.addWidget(self.generate_links_btn)
        btn_row_h.addWidget(self.download_excel_btn)
        btn_row_h.addWidget(self.download_txt_btn)
        btn_row_h.addWidget(self.scrape_links_btn)
        btn_row_h.addWidget(self.clear_tickets_btn)
        btn_row_h.addStretch()
        body_layout.addWidget(btn_row)


        search_filter_row = QWidget()
        search_filter_row.setStyleSheet("background: transparent;")
        search_filter_h = QHBoxLayout(search_filter_row)
        search_filter_h.setContentsMargins(0, 0, 0, 0)
        search_filter_h.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search tickets or links...")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.07),
                    stop:1 rgba(255, 255, 255, 0.03)
                );
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 8px 16px;
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: 500;
            }
            QLineEdit:focus {
                border: 2px solid rgba(0, 166, 81, 0.5);
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.11),
                    stop:1 rgba(255, 255, 255, 0.07)
                );
            }
        """)
        self.search_input.textChanged.connect(self.filter_link_results)

        self.filter_valid_btn = QPushButton("Valid")
        self.filter_valid_btn.setCheckable(True)
        self.filter_valid_btn.setChecked(True)
        self.filter_valid_btn.clicked.connect(lambda: self.set_filter_mode('valid'))
        self.filter_valid_btn.setMinimumHeight(28)
        self.filter_valid_btn.setStyleSheet(BTN_FILTER_VALID)

        self.filter_invalid_btn = QPushButton("Invalid")
        self.filter_invalid_btn.setCheckable(True)
        self.filter_invalid_btn.clicked.connect(lambda: self.set_filter_mode('invalid'))
        self.filter_invalid_btn.setMinimumHeight(28)
        self.filter_invalid_btn.setStyleSheet(BTN_FILTER_INVALID)

        self.filter_all_btn = QPushButton("All")
        self.filter_all_btn.setCheckable(True)
        self.filter_all_btn.clicked.connect(lambda: self.set_filter_mode('all'))
        self.filter_all_btn.setMinimumHeight(28)
        self.filter_all_btn.setStyleSheet(BTN_FILTER_ALL)

        search_filter_h.addWidget(self.search_input, 1)
        search_filter_h.addWidget(self.filter_valid_btn)
        search_filter_h.addWidget(self.filter_invalid_btn)
        search_filter_h.addWidget(self.filter_all_btn)
        body_layout.addWidget(search_filter_row)


        self.link_results_scroll = QScrollArea()
        self.link_results_scroll.setWidgetResizable(True)
        self.link_results_scroll.setMinimumHeight(120)
        self.link_results_scroll.setMaximumHeight(260)
        self.link_results_scroll.setStyleSheet("""
            QScrollArea {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.04),
                    stop:1 rgba(255, 255, 255, 0.03)
                );
                border: 1px solid rgba(255, 255, 255, 0.11);
                border-radius: 12px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 166, 81, 0.5);
                border-radius: 6px;
            }
        """)

        self.link_results_container = QWidget()
        self.link_results_container.setStyleSheet("background: transparent;")
        self.link_results_layout = QVBoxLayout(self.link_results_container)
        self.link_results_layout.setContentsMargins(8, 8, 8, 8)
        self.link_results_layout.setSpacing(4)
        self.link_results_layout.addStretch()

        self.link_results_scroll.setWidget(self.link_results_container)
        body_layout.addWidget(self.link_results_scroll)


        self.validation_error_panel = QWidget()
        self.validation_error_panel.setVisible(False)
        self.validation_error_panel.setStyleSheet("background: transparent;")
        val_panel_layout = QVBoxLayout(self.validation_error_panel)
        val_panel_layout.setContentsMargins(0, 4, 0, 0)
        val_panel_layout.setSpacing(4)

        val_header_row = QWidget()
        val_header_row.setStyleSheet("background: transparent;")
        val_header_h = QHBoxLayout(val_header_row)
        val_header_h.setContentsMargins(0, 0, 0, 0)
        val_header_h.setSpacing(6)

        val_icon = QLabel("\u26a0")
        val_icon.setStyleSheet("color: #D32F2F; font-size: 10pt; font-weight: 800; background: transparent;")
        val_title_lbl = QLabel("VALIDATION  ERRORS")
        val_title_lbl.setStyleSheet("""
            QLabel {
                color: #D32F2F;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.4px;
                background: transparent;
            }
        """)
        val_header_h.addWidget(val_icon)
        val_header_h.addWidget(val_title_lbl)
        val_header_h.addStretch()
        val_panel_layout.addWidget(val_header_row)

        self.validation_error_text = QTextEdit()
        self.validation_error_text.setReadOnly(True)
        self.validation_error_text.setMinimumHeight(60)
        self.validation_error_text.setMaximumHeight(110)
        self.validation_error_text.setFont(QFont("Consolas", 9))
        self.validation_error_text.setStyleSheet("""
            QTextEdit {
                background: rgba(211, 47, 47, 0.08);
                border: 1px solid rgba(211, 47, 47, 0.30);
                border-radius: 12px;
                padding: 8px 16px;
                color: #8B0000;
                font-weight: 600;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(211, 47, 47, 0.35);
                border-radius: 6px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        val_panel_layout.addWidget(self.validation_error_text)
        body_layout.addWidget(self.validation_error_panel)
        outer.addWidget(body)


        self.generated_links = []
        self.generated_items = []
        self.filter_mode = 'valid'

        return section

    def create_quick_actions_section(self):
        """Create Quick Actions section with main control buttons"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("QUICK  ACTIONS")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(8)


        self.start_button = QPushButton("▶  START")
        self.start_button.clicked.connect(self.start_scraping)
        self.start_button.setMinimumHeight(36)
        self.start_button.setStyleSheet(BTN_SUCCESS)


        self.stop_button = QPushButton("⏸  STOP")
        self.stop_button.clicked.connect(self.stop_scraping)
        self.stop_button.setEnabled(False)
        self.stop_button.setMinimumHeight(36)
        self.stop_button.setStyleSheet(BTN_DANGER_OUTLINE)


        self.config_button = QPushButton("⚙  SETTINGS")
        self.config_button.clicked.connect(self.open_config)
        self.config_button.setMinimumHeight(36)
        self.config_button.setStyleSheet(BTN_PRIMARY_OUTLINE)


        self.clear_log_button = QPushButton("✕  CLEAR LOG")
        self.clear_log_button.clicked.connect(lambda: self.log_area.clear())
        self.clear_log_button.setToolTip("Clear log (Ctrl+L)")
        self.clear_log_button.setMinimumHeight(36)
        self.clear_log_button.setStyleSheet(BTN_NEUTRAL_OUTLINE)

        body_layout.addWidget(self.start_button)
        body_layout.addWidget(self.stop_button)
        body_layout.addWidget(self.config_button)
        body_layout.addWidget(self.clear_log_button)
        outer.addWidget(body)

        return section

    def create_system_status_section(self):
        """Create System Status section with progress bar and status label"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("SYSTEM  STATUS")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)


        self.status_label = QLabel("●  READY")
        self.status_label.setStyleSheet("""
            QLabel {
                background: rgba(255, 255, 255, 0.03);
                padding: 8px 16px;
                border: 1px solid rgba(255, 255, 255, 0.22);
                border-radius: 12px;
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
        """)
        body_layout.addWidget(self.status_label)


        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid rgba(255, 255, 255, 0.28);
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(243, 231, 222, 0.6),
                    stop:1 rgba(243, 231, 222, 0.4)
                );
                border-radius: 12px;
            }
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(33, 150, 243, 0.85),
                    stop:1 rgba(33, 150, 243, 0.65)
                );
                border-radius: 12px;
            }
        """)
        body_layout.addWidget(self.progress_bar)

        outer.addWidget(body)

        return section

    def create_help_section(self):
        """Create Help/Info section"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("HELP  &  INFO")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 10, 14, 12)

        help_text = QLabel("""
• Load Excel file with ticket numbers
• Or paste tickets in Link Generator
• Click START to begin scraping
• Results saved to rechecked.xlsx
        """)
        help_text.setWordWrap(True)
        help_text.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8.5pt;
                font-weight: 500;
                line-height: 1.4;
            }
        """)
        body_layout.addWidget(help_text)
        outer.addWidget(body)

        return section

    def create_recent_activity_section(self):
        """Create Recent Activity section"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("RECENT  ACTIVITY")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 10, 14, 12)

        self.activity_label = QLabel("No recent activity")
        self.activity_label.setWordWrap(True)
        self.activity_label.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8.5pt;
                font-weight: 500;
                line-height: 1.4;
            }
        """)
        body_layout.addWidget(self.activity_label)
        outer.addWidget(body)

        return section

    def create_live_console_section(self):
        """Create Live Console section with log output"""
        section = QGroupBox()
        section.setStyleSheet(f"""
            QGroupBox {{
                background: {GLASS_BG};
                border: {GLASS_BORDER};
                border-radius: {GLASS_RADIUS};
                margin-top: 0px;
                padding: 0px;
            }}
        """)
        apply_soft_shadow(section, blur_radius=20, y_offset=3, alpha=25)

        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)


        header_bar = QWidget()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }
        """)
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(14, 0, 14, 0)
        hb_layout.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet("color: #8B7355; font-size: 7px; background: transparent;")
        title = QLabel("LIVE  CONSOLE")
        title.setStyleSheet("""
            QLabel {
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 800;
                letter-spacing: 1.8px;
                background: transparent;
            }
        """)
        hb_layout.addWidget(dot)
        hb_layout.addWidget(title)
        hb_layout.addStretch()
        outer.addWidget(header_bar)


        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 12)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setFont(QFont("Consolas", 10))
        self.log_area.setStyleSheet("""
            QTextEdit {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(28, 20, 15, 0.06),
                    stop:1 rgba(28, 20, 15, 0.03)
                );
                color: #3A2E26;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
                padding: 8px 12px;
                font-family: "Consolas";
                font-size: 10pt;
            }
            QScrollBar::handle:vertical {
                background: rgba(28, 20, 15, 0.20);
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(28, 20, 15, 0.30);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        body_layout.addWidget(self.log_area)
        outer.addWidget(body)

        return section

    def generate_links(self):
        """Generate links from ticket numbers in the input field"""
        raw_input = self.ticket_input.toPlainText().strip()
        if not raw_input:
            QMessageBox.warning(self, "No Input", "Please enter ticket numbers first!")
            return


        tickets = parse_tickets(raw_input)
        if not tickets:
            QMessageBox.warning(self, "No Tickets", "No valid ticket numbers found in the input!")
            return


        self.generated_items = []
        self.generated_links = []

        for ticket in tickets:
            valid, error_msg = is_valid_ticket(ticket)
            if valid:
                link = make_link(ticket, self.config.BASE_TICKET_URL)
                self.generated_links.append(link)
            else:
                link = ""
            self.generated_items.append({
                'ticket': ticket,
                'link': link,
                'valid': valid,
                'error': error_msg
            })


        total = len(tickets)
        valid_count = len([item for item in self.generated_items if item['valid']])
        invalid_count = total - valid_count

        self.stat_total.setText(str(total))
        self.stat_valid.setText(str(valid_count))
        self.stat_invalid.setText(str(invalid_count))


        if valid_count > 0:
            self.download_excel_btn.setEnabled(True)
            self.download_txt_btn.setEnabled(True)
            self.scrape_links_btn.setEnabled(True)
            self.log(f"✅ Generated {valid_count} valid links from {total} tickets")
            if invalid_count > 0:
                self.log(f"⚠️ Skipped {invalid_count} invalid ticket numbers")

                invalid_items = [item for item in self.generated_items if not item['valid']]
                error_lines = [f"  {item['ticket']}  →  {item['error']}" for item in invalid_items]
                self.validation_error_text.setPlainText(
                    f"{invalid_count} ticket(s) skipped due to validation errors:\n" +
                    "\n".join(error_lines)
                )
                self.validation_error_panel.setVisible(True)
            else:
                self.validation_error_panel.setVisible(False)
        else:
            self.download_excel_btn.setEnabled(False)
            self.download_txt_btn.setEnabled(False)
            self.scrape_links_btn.setEnabled(False)

            error_lines = [f"  {item['ticket']}  →  {item['error']}" for item in self.generated_items]
            self.validation_error_text.setPlainText(
                f"All {total} ticket(s) failed validation:\n" +
                "\n".join(error_lines)
            )
            self.validation_error_panel.setVisible(True)


        self.render_link_results()

    def download_generated_links_excel(self):
        """Download generated links as Excel file"""
        if not self.generated_links:
            QMessageBox.warning(self, "No Links", "No links generated yet!")
            return

        try:

            valid_items = [item for item in self.generated_items if item['valid']]
            tickets = [item['ticket'] for item in valid_items]
            links = [item['link'] for item in valid_items]

            df = pd.DataFrame({
                'Ticket Number': tickets,
                'Link': links
            })


            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"generated_links_{timestamp}.xlsx"
            df.to_excel(filename, index=False)

            self.log(f"✅ Downloaded {len(self.generated_links)} links to {filename}")
            QMessageBox.information(self, "Download Complete",
                                 f"Successfully saved {len(self.generated_links)} links to:\n{filename}")


            os.startfile(os.path.abspath(filename))

        except Exception as e:
            self.log(f"❌ Error downloading links: {str(e)}")
            QMessageBox.critical(self, "Download Error", f"Failed to download links:\n{str(e)}")

    def download_generated_links_txt(self):
        """Download generated links as TXT file"""
        if not self.generated_links:
            QMessageBox.warning(self, "No Links", "No links generated yet!")
            return

        try:
            valid_items = [item for item in self.generated_items if item['valid']]
            lines = [f"{item['ticket']}\t{item['link']}" for item in valid_items]

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"generated_links_{timestamp}.txt"

            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

            self.log(f"✅ Downloaded {len(self.generated_links)} links to {filename}")
            QMessageBox.information(self, "Download Complete",
                                 f"Successfully saved {len(self.generated_links)} links to:\n{filename}")


            os.startfile(os.path.abspath(filename))

        except Exception as e:
            self.log(f"❌ Error downloading links: {str(e)}")
            QMessageBox.critical(self, "Download Error", f"Failed to download links:\n{str(e)}")

    def scrape_generated_links(self):
        """Start scraping using the generated links"""
        if not self.generated_links:
            QMessageBox.warning(self, "No Links", "No links generated yet!")
            return

        try:
            df = pd.DataFrame({'Link': self.generated_links})
            temp_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_generated_links.xlsx")
            df.to_excel(temp_file, index=False)

            self.selected_file_path = temp_file
            self.file_label.setText(f"✔  Generated Links  ({len(self.generated_links)} tickets)")
            self.file_label.setStyleSheet("""
                QLabel {
                    padding: 8px 16px;
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(233, 248, 239, 0.7),
                        stop:1 rgba(233, 248, 239, 0.5)
                    );
                    border: 1px solid rgba(0, 166, 81, 0.5);
                    border-radius: 12px;
                    color: #007A3B;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                    font-weight: 700;
                }
            """)
            self.start_button.setEnabled(True)
            self.log(f"✅ Prepared {len(self.generated_links)} generated links for scraping")

        except Exception as e:
            self.log(f"❌ Error preparing links for scraping: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to prepare links for scraping:\n{str(e)}")

    def clear_ticket_input(self):
        """Clear the ticket input field and reset state"""
        self.ticket_input.clear()
        self.generated_links = []
        self.generated_items = []
        self.stat_total.setText("0")
        self.stat_valid.setText("0")
        self.stat_invalid.setText("0")
        self.download_excel_btn.setEnabled(False)
        self.download_txt_btn.setEnabled(False)
        self.scrape_links_btn.setEnabled(False)
        self.file_name_label.setText("No file selected")
        self.search_input.clear()
        self.validation_error_panel.setVisible(False)
        self.validation_error_text.clear()


        for i in reversed(range(self.link_results_layout.count())):
            if i < self.link_results_layout.count() - 1:
                item = self.link_results_layout.itemAt(i)
                if item.widget():
                    item.widget().deleteLater()

        self.log("🗑️ Cleared ticket input")

    def upload_ticket_file(self):
        """Upload Excel/CSV file with ticket numbers and convert to links"""
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Excel/CSV File",
            "",
            "Excel Files (*.xlsx *.xls);;CSV Files (*.csv);;All Files (*)"
        )

        if file_name:
            try:
                self.log(f"Reading file: {file_name}")

                if file_name.endswith('.csv'):
                    df = pd.read_csv(file_name)
                else:
                    df = pd.read_excel(file_name)


                all_tickets = []
                for col in df.columns:
                    for value in df[col].dropna().astype(str):
                        tickets = parse_tickets(str(value))
                        all_tickets.extend(tickets)

                if not all_tickets:
                    QMessageBox.warning(self, "No Tickets", "No ticket numbers found in the file!")
                    return


                unique_tickets = list(dict.fromkeys(all_tickets))


                converted_links = []
                valid_tickets = []
                invalid_tickets = []

                for ticket in unique_tickets:
                    valid, error_msg = is_valid_ticket(ticket)
                    if valid:
                        link = make_link(ticket, self.config.BASE_TICKET_URL)
                        converted_links.append(link)
                        valid_tickets.append(ticket)
                    else:
                        invalid_tickets.append((ticket, error_msg))

                if not converted_links:

                    error_lines = [f"  {t}  →  {e}" for t, e in invalid_tickets]
                    self.validation_error_text.setPlainText(
                        f"All {len(invalid_tickets)} ticket(s) failed validation:\n" +
                        "\n".join(error_lines)
                    )
                    self.validation_error_panel.setVisible(True)
                    self.log(f"⚠️ All {len(invalid_tickets)} tickets from file failed validation — see Link Generator card for details")
                    return


                if invalid_tickets:
                    error_lines = [f"  {t}  →  {e}" for t, e in invalid_tickets]
                    self.validation_error_text.setPlainText(
                        f"{len(invalid_tickets)} ticket(s) skipped due to validation errors:\n" +
                        "\n".join(error_lines)
                    )
                    self.validation_error_panel.setVisible(True)
                    self.log(f"⚠️ Skipped {len(invalid_tickets)} invalid tickets — see Link Generator card for details")
                else:
                    self.validation_error_panel.setVisible(False)
                    self.validation_error_text.clear()


                timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                output_filename = os.path.join(
                    os.path.dirname(os.path.abspath(file_name)),
                    f"ITMIS_Ticket_Links_{timestamp}.xlsx"
                )

                output_df = pd.DataFrame({
                    'Ticket Number': valid_tickets,
                    'Link': converted_links
                })

                output_df.to_excel(output_filename, index=False)

                self.log(f"✅ Converted {len(converted_links)} tickets to links")
                self.log(f"✅ Saved converted file: {output_filename}")


                msg = QMessageBox(self)
                msg.setWindowTitle("File Converted")
                msg.setText(f"Successfully converted {len(converted_links)} ticket numbers to links!\n\n"
                           f"Converted file saved as: {os.path.basename(output_filename)}")
                if invalid_tickets:
                    msg.setText(f"Successfully converted {len(converted_links)} ticket numbers to links!\n\n"
                               f"Skipped {len(invalid_tickets)} invalid tickets.\n\n"
                               f"Converted file saved as: {os.path.basename(output_filename)}")
                msg.setIcon(QMessageBox.Icon.Information)

                download_btn = msg.addButton("📥 Download Converted File", QMessageBox.ButtonRole.ActionRole)
                scrape_btn = msg.addButton("▶ Start Scraping", QMessageBox.ButtonRole.ActionRole)
                cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)

                msg.exec()

                if msg.clickedButton() == download_btn:
                    os.startfile(output_filename)
                    self.log(f"📥 Downloaded {os.path.basename(output_filename)}")
                elif msg.clickedButton() == scrape_btn:
                    self.selected_file_path = output_filename
                    self.file_label.setText(f"✔  {os.path.basename(output_filename)}  ({len(converted_links)} tickets)")
                    self.file_label.setStyleSheet("""
                        QLabel {
                            padding: 8px 16px;
                            background: qlineargradient(
                                x1:0, y1:0, x2:0, y2:1,
                                stop:0 rgba(233, 248, 239, 0.7),
                                stop:1 rgba(233, 248, 239, 0.5)
                            );
                            border: 1px solid rgba(0, 166, 81, 0.5);
                            border-radius: 12px;
                            color: #007A3B;
                            font-family: "Segoe UI";
                            font-size: 10pt;
                            font-weight: 700;
                        }
                    """)
                    self.start_button.setEnabled(True)
                    self.log(f"▶ Ready to scrape {len(converted_links)} tickets")

            except Exception as e:
                self.log(f"❌ Error processing file: {str(e)}")
                QMessageBox.critical(self, "File Error", f"Failed to process file:\n{str(e)}")

    def load_example_tickets(self):
        """Load example ticket numbers for testing"""
        example_tickets = [
            'LHR.L2SP.2024.08.00074528',
            'LHR.L2SP.2024.01.00012345',
            'LHR.L2SP.2023.11.00087654',
            'MLT.L3SP.2024.07.00056789',
            'INVALID_TICKET',
            'BAD.FORMAT'
        ]
        self.ticket_input.setPlainText('\n'.join(example_tickets))
        self.file_name_label.setText("Example data loaded")
        self.log("🧪 Example tickets loaded")

    def render_link_results(self):
        """Render the generated links in the scrollable results list"""

        for i in reversed(range(self.link_results_layout.count())):
            if i < self.link_results_layout.count() - 1:
                item = self.link_results_layout.itemAt(i)
                if item.widget():
                    item.widget().deleteLater()


        filtered_items = self.get_filtered_items()

        if not filtered_items:
            empty_label = QLabel("No results to display")
            empty_label.setStyleSheet("""
                QLabel {
                    color: #5C4638;
                    font-family: "Segoe UI";
                    font-size: 9pt;
                    font-weight: 500;
                    padding: 20px;
                    text-align: center;
                }
            """)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.link_results_layout.insertWidget(0, empty_label)
            return


        for idx, item in enumerate(filtered_items):
            item_widget = self.create_link_item_widget(item, idx)
            self.link_results_layout.insertWidget(idx, item_widget)

    def create_link_item_widget(self, item, idx):
        """Create a widget for a single link item"""
        widget = QWidget()
        widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 255, 255, 0.04),
                    stop:1 rgba(255, 255, 255, 0.03)
                );
                border: 1px solid rgba(255, 255, 255, 0.11);
                border-radius: 12px;
                padding: 6px;
            }
        """)

        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)


        index_label = QLabel(f"{idx + 1}")
        index_label.setStyleSheet("""
            QLabel {
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(28, 20, 15, 0.08),
                    stop:1 rgba(28, 20, 15, 0.04)
                );
                border-radius: 12px;
                padding: 8px 16px;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 700;
                min-width: 24px;
            }
        """)
        index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(index_label)


        ticket_label = QLabel(item['ticket'])
        ticket_label.setStyleSheet("""
            QLabel {
                color: #1C140F;
                font-family: "Segoe UI";
                font-size: 9pt;
                font-weight: 600;
            }
        """)
        ticket_label.setToolTip(item.get('error', '') if not item['valid'] else '')
        layout.addWidget(ticket_label, 1)


        if item['valid']:
            link_label = QLabel(item['link'])
            link_label.setWordWrap(True)
            link_label.setStyleSheet("""
                QLabel {
                    color: #00A651;
                    font-family: "Segoe UI";
                    font-size: 8pt;
                    font-weight: 500;
                }
            """)
            layout.addWidget(link_label, 2)


            copy_btn = QPushButton("📋")
            copy_btn.setToolTip("Copy link")
            copy_btn.setFixedSize(24, 24)
            copy_btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(255, 255, 255, 0.17),
                        stop:1 rgba(255, 255, 255, 0.06)
                    );
                    border: 1px solid rgba(255, 255, 255, 0.28);
                    border-radius: 12px;
                    font-size: 10pt;
                }
                QPushButton:hover {
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgba(0, 166, 81, 0.25),
                        stop:1 rgba(0, 166, 81, 0.15)
                    );
                    border-color: rgba(0, 166, 81, 0.6);
                }
            """)
            copy_btn.clicked.connect(lambda: self.copy_to_clipboard(item['link']))
            layout.addWidget(copy_btn)



            error_label = QLabel(item.get('error', 'Invalid format'))
            error_label.setStyleSheet("""
                QLabel {
                    color: #D32F2F;
                    font-family: "Segoe UI";
                    font-size: 8pt;
                    font-weight: 500;
                    font-style: italic;
                }
            """)
            error_label.setToolTip(item.get('error', 'Invalid format'))
            layout.addWidget(error_label, 2)


        badge = QLabel("✓" if item['valid'] else "✗")
        _badge_rgb = "0, 166, 81" if item['valid'] else "211, 47, 47"
        _badge_color = "#007A3B" if item['valid'] else "#D32F2F"
        badge.setStyleSheet(f"""
            QLabel {{
                background: rgba({_badge_rgb}, 0.12);
                color: {_badge_color};
                border: 1px solid rgba({_badge_rgb}, 0.35);
                border-radius: 12px;
                padding: 8px 16px;
                font-family: "Segoe UI";
                font-size: 8pt;
                font-weight: 700;
            }}
        """)
        badge.setToolTip(item.get('error', '') if not item['valid'] else 'Valid ticket')
        layout.addWidget(badge)

        return widget

    def get_filtered_items(self):
        """Get items filtered by search query and filter mode"""
        items = self.generated_items


        if self.filter_mode == 'valid':
            items = [item for item in items if item['valid']]
        elif self.filter_mode == 'invalid':
            items = [item for item in items if not item['valid']]


        search_query = self.search_input.text().lower().strip()
        if search_query:
            items = [item for item in items if
                     search_query in item['ticket'].lower() or
                     (item['link'] and search_query in item['link'].lower())]

        return items

    def set_filter_mode(self, mode):
        """Set the filter mode and update UI"""
        self.filter_mode = mode


        self.filter_valid_btn.setChecked(mode == 'valid')
        self.filter_invalid_btn.setChecked(mode == 'invalid')
        self.filter_all_btn.setChecked(mode == 'all')


        self.render_link_results()

    def filter_link_results(self):
        """Filter results based on search input"""
        self.render_link_results()

    def copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        from PyQt6.QtGui import QClipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.log("📋 Copied to clipboard")

    def open_in_browser(self, url):
        """Open URL in default browser"""
        import webbrowser
        webbrowser.open(url)
        self.log(f"🔗 Opened {url}")

    def update_config_preview(self):
        """Update the configuration preview"""
        preview_text = (
            f'<span style="color:#8B7355">TIMING</span>  '
            f'<span style="color:#5C4638">login={self.config.LOGIN_TIMEOUT}s  '
            f'page={self.config.PAGE_LOAD_TIMEOUT}s  '
            f'delay={self.config.DELAY_BETWEEN_TICKETS}s</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">RETRY</span>  '
            f'<span style="color:#5C4638">max={self.config.MAX_RETRIES}  wait={self.config.RETRY_DELAY}s</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">KEYWORDS</span>  '
            f'<span style="color:#5C4638">{len(self.config.KEYWORD_LIST)} configured</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">BROWSER</span>  '
            f'<span style="color:#5C4638">{"headless" if self.config.HEADLESS_MODE else "visible"}</span>'
        )
        self.config_preview.setText(preview_text)

    def select_file(self):
        """File selection — converts ticket-number files to links and populates the Link Generator card."""
        start_dir = os.path.dirname(self.config.LAST_FILE_PATH) if getattr(self.config, "LAST_FILE_PATH", "") else ""
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Excel File",
            start_dir,
            "Excel Files (*.xlsx *.xls);;All Files (*)"
        )

        if not file_name:
            return

        try:
            self.log(f"Validating file: {file_name}")
            df = pd.read_excel(file_name)


            if 'Link' in df.columns:
                raw_values = df['Link'].dropna().astype(str).tolist()
                source_col = 'Link'
            else:
                ticket_col = None
                for col in df.columns:
                    if 'ticket' in str(col).lower() or 'number' in str(col).lower():
                        ticket_col = col
                        break
                if ticket_col:
                    self.log(f"Found '{ticket_col}' column instead of 'Link'. Converting to links...")
                    raw_values = df[ticket_col].dropna().astype(str).tolist()
                    source_col = ticket_col
                else:
                    available_cols = ", ".join(str(c) for c in df.columns[:10])
                    raise ValueError(
                        f"Excel file must have a 'Link' or 'Ticket Number' column.\n"
                        f"Available columns: {available_cols}"
                    )


            all_tokens = []
            for value in raw_values:
                all_tokens.extend(parse_tickets(str(value)))

            if not all_tokens:
                raise ValueError(f"No ticket numbers found in '{source_col}' column")

            unique_tokens = list(dict.fromkeys(all_tokens))
            valid_tickets, invalid_tickets = [], []

            for token in unique_tokens:

                if token.startswith('http'):
                    valid_tickets.append(('__url__', token))
                    continue

                candidate = token.split('/')[-1] if '/' in token else token
                ok, err = is_valid_ticket(candidate)
                if ok:
                    valid_tickets.append((candidate, make_link(candidate, self.config.BASE_TICKET_URL)))
                else:
                    invalid_tickets.append((token, err))


            total = len(unique_tokens)
            valid_count = len(valid_tickets)
            invalid_count = len(invalid_tickets)

            self.stat_total.setText(str(total))
            self.stat_valid.setText(str(valid_count))
            self.stat_invalid.setText(str(invalid_count))


            self.generated_items = []
            self.generated_links = []
            for ticket, link in valid_tickets:
                display_ticket = ticket if ticket != '__url__' else link
                self.generated_items.append({'ticket': display_ticket, 'link': link, 'valid': True, 'error': ''})
                self.generated_links.append(link)
            for ticket, err in invalid_tickets:
                self.generated_items.append({'ticket': ticket, 'link': '', 'valid': False, 'error': err})

            self.render_link_results()


            if invalid_tickets:
                error_lines = [f"  {t}  →  {e}" for t, e in invalid_tickets]
                self.validation_error_text.setPlainText(
                    f"{invalid_count} ticket(s) skipped due to validation errors:\n" +
                    "\n".join(error_lines)
                )
                self.validation_error_panel.setVisible(True)
                self.log(f"⚠️ Skipped {invalid_count} invalid tickets — see Link Generator card for details")
            else:
                self.validation_error_panel.setVisible(False)
                self.validation_error_text.clear()

            if valid_count == 0:
                raise ValueError("No valid ticket numbers or URLs found in the file")


            ticket_list = [t for t, _ in valid_tickets if t != '__url__']
            url_list    = [lnk for _, lnk in valid_tickets]

            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            converted_filename = f"ITMIS_Ticket_Links_{timestamp}.xlsx"

            converted_file = os.path.join(os.path.dirname(os.path.abspath(file_name)), converted_filename)

            out_df = pd.DataFrame({'Ticket Number': ticket_list if ticket_list else url_list, 'Link': url_list})
            out_df.to_excel(converted_file, index=False)
            self.log(f"✅ Converted {valid_count} tickets to links")
            self.log(f"✅ Saved converted file: {converted_file}")


            self.selected_file_path = converted_file
            self.config.LAST_FILE_PATH = converted_file
            self.config.save_settings()

            file_info = f"✔  {os.path.basename(converted_file)}  ({valid_count} tickets)"
            self.file_label.setText(file_info)
            self.file_label.setStyleSheet("""
                QLabel {
                    padding: 8px 16px;
                    background-color: rgba(200, 240, 214, 0.45);
                    border: 1px solid rgba(0, 166, 81, 0.35);
                    border-radius: 6px;
                    color: #007A3B;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                    font-weight: 700;
                }
            """)
            self.file_name_label.setText(os.path.basename(file_name))


            self.download_excel_btn.setEnabled(True)
            self.download_txt_btn.setEnabled(True)
            self.scrape_links_btn.setEnabled(True)

            self.start_button.setEnabled(True)
            self.log(f"File validated successfully: {valid_count} tickets ready")

        except Exception as e:
            error_msg = f"✘  INVALID FILE: {str(e)}"
            self.file_label.setText(error_msg)
            self.file_label.setStyleSheet("""
                QLabel {
                    padding: 8px 16px;
                    background-color: rgba(255, 205, 210, 0.45);
                    border: 1px solid rgba(211, 47, 47, 0.40);
                    border-radius: 6px;
                    color: #D32F2F;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                    font-weight: 700;
                }
            """)
            self.start_button.setEnabled(False)
            self.log(f"File validation failed: {str(e)}")
            QMessageBox.warning(self, "File Validation Error", str(e))

    def start_scraping(self):
        """Start the scraping process with enhanced UI updates"""
        if not self.selected_file_path:
            QMessageBox.warning(self, "No File Selected", "Please select an Excel file first!")
            return


        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.file_button.setEnabled(False)
        self.config_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("●  PROCESSING...")
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 98, 0, 0.10);
                padding: 8px 16px;
                border: 1px solid rgba(255, 98, 0, 0.30);
                border-radius: 6px;
                color: #5C4638;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
        """)


        self.log_area.clear()
        self.stats_label.setText("PROCESSING IN PROGRESS...")


        self.scraper_thread = ScraperThread(self.selected_file_path, self.config)
        self.scraper_thread.progress.connect(self.update_progress)
        self.scraper_thread.log.connect(self.log)
        self.scraper_thread.finished.connect(self.scraping_finished)
        self.scraper_thread.error.connect(self.scraping_error)
        self.scraper_thread.validation_errors.connect(self.show_scraper_validation_errors)
        self.scraper_thread.start()

        self.log("─" * 60)
        self.log("STARTING ITMIS TICKET SCRAPING SESSION")
        self.log("─" * 60)

    def stop_scraping(self):
        """Stop the scraping process"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Stop Scraping",
                "Are you sure you want to stop the scraping process?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.log("■  User requested abort...")
                self.status_label.setText("■  ABORTING...")
                self.status_label.setStyleSheet("""
                    QLabel {
                        background-color: rgba(211, 47, 47, 0.10);
                        padding: 8px 16px;
                        border: 1px solid rgba(211, 47, 47, 0.32);
                        border-radius: 6px;
                        color: #D32F2F;
                        font-family: "Segoe UI";
                        font-size: 10pt;
                        font-weight: 700;
                        letter-spacing: 0.4px;
                    }
                """)
                self.scraper_thread.stop()
                self.scraper_thread.wait(5000)
                self.scraping_finished([])

    def update_progress(self, value):
        """Update progress bar and status with ticket counter + ETA"""
        current = getattr(self.scraper_thread, "current_ticket", 0) if self.scraper_thread else 0
        total = len(getattr(self.scraper_thread, "links", [])) if self.scraper_thread else 0

        if (
            self.scraper_thread
            and getattr(self.scraper_thread, "start_time", None)
            and current > 1
            and total > 0
        ):
            elapsed = time.time() - self.scraper_thread.start_time
            avg_time = elapsed / current
            remaining = max(0, avg_time * (total - current))
            eta = f" | ETA: {int(remaining)}s"
        else:
            eta = ""

        self.progress_bar.setValue(value)
        if total > 0:
            self.status_label.setText(f"●  TICKET {current}/{total}  [{value}%]{eta}")
        else:
            self.status_label.setText(f"●  PROCESSING TICKETS...  [{value}%]{eta}")

    def log(self, message):
        """Enhanced logging with timestamps and formatting"""
        timestamp = datetime.now().strftime("%H:%M:%S")


        if "error" in message.lower() or "failed" in message.lower() or "critical" in message.lower():
            formatted_message = f'<span style="color: #D32F2F; font-weight: bold;">[{timestamp}]  {message}</span>'
        elif "success" in message.lower() or "completed" in message.lower() or "✅" in message:
            formatted_message = f'<span style="color: #00A651; font-weight: bold;">[{timestamp}]  {message}</span>'
        elif "warning" in message.lower() or "⚠" in message:
            formatted_message = f'<span style="color: #9C6F00;">[{timestamp}]  {message}</span>'
        elif message.startswith("===") or message.startswith("---") or message.startswith("─"):
            formatted_message = f'<span style="color: #1565C0; font-weight: bold;">[{timestamp}]  {message}</span>'
        elif "Final record:" in message:
            try:
                json_str = message.split("Final record: ", 1)[1]
                data = json.loads(json_str)
                pretty_json = json.dumps(data, indent=2).replace(" ", "&nbsp;").replace("\\n", "<br>")
                formatted_message = f'<span style="color: #1565C0; font-weight: bold;">[{timestamp}]  Final record:</span><br><span style="color: #B85C00; font-family: Consolas;">{pretty_json}</span>'
            except:
                formatted_message = f'<span style="color: #3A2E26;">[{timestamp}]  {message}</span>'
        else:
            formatted_message = f'<span style="color: #3A2E26;">[{timestamp}]  {message}</span>'

        self.log_area.append(formatted_message)


        cursor = self.log_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_area.setTextCursor(cursor)

    def scraping_finished(self, results):
        """Handle scraping completion"""

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.file_button.setEnabled(True)
        self.config_button.setEnabled(True)
        self.progress_bar.setValue(100)
        self.status_label.setText("●  COMPLETED")
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 166, 81, 0.10);
                padding: 8px 16px;
                border: 1px solid rgba(0, 166, 81, 0.32);
                border-radius: 6px;
                color: #007A3B;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
        """)


        if results:
            self.update_statistics(results)
            self.log("─" * 60)
            self.log("SCRAPING SESSION COMPLETED SUCCESSFULLY")
            self.log("─" * 60)


            self.show_results_dashboard(results)


            reply = QMessageBox.information(
                self,
                "Scraping Complete",
                f"Successfully processed {len(results)} tickets!\n\n"
                f"Results have been saved to 'rechecked.xlsx'",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open
            )

            if reply == QMessageBox.StandardButton.Open:
                self.open_results_file()
        else:
            self.stats_label.setText("NO RESULTS — SESSION STOPPED OR FAILED")
            self.log("Scraping session ended without results")

    def show_results_dashboard(self, results):
        """Show quick summary popup"""
        if not results:
            return

        total = len(results)
        successful = len([r for r in results if r.get("Processing Status") == "Success"])
        with_keywords = len([r for r in results if r.get("Contains Keywords", False)])

        keyword_pct = (with_keywords / total * 100) if total else 0
        success_pct = (successful / total * 100) if total else 0

        msg = f"""
        🎯 Scraping Summary

        Total Tickets: {total}
        Tickets with Keywords: {with_keywords} ({keyword_pct:.1f}%)
        Success Rate: {success_pct:.1f}%

        Files saved:
        • rechecked.xlsx (main results)
        • rechecked_backup_*.xlsx
        • summary_*.json
        """
        QMessageBox.information(self, "Summary Dashboard", msg)

    def open_results_file(self):
        """Open the results Excel file directly"""
        try:
            output_file = os.path.abspath("rechecked.xlsx")
            if os.path.exists(output_file):
                os.startfile(output_file)
                self.log("✅ Opened rechecked.xlsx")
            else:
                QMessageBox.warning(self, "File Not Found", "rechecked.xlsx not found.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open file: {str(e)}")

    def scraping_error(self, error_message):
        """Handle scraping errors"""
        self.status_label.setText("●  FAILED")
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: rgba(211, 47, 47, 0.10);
                padding: 8px 16px;
                border: 1px solid rgba(211, 47, 47, 0.32);
                border-radius: 6px;
                color: #D32F2F;
                font-family: "Segoe UI";
                font-size: 10pt;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
        """)
        self.log(f"CRITICAL ERROR: {error_message}")


        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.file_button.setEnabled(True)
        self.config_button.setEnabled(True)

        QMessageBox.critical(self, "Scraping Error", f"An error occurred during scraping:\n\n{error_message}")

    def show_scraper_validation_errors(self, invalid_entries):
        """Display validation errors from the scraper thread in the Link Generator card panel."""
        if not invalid_entries:
            return
        error_lines = [f"  {ticket}  →  {err}" for ticket, err in invalid_entries]
        self.validation_error_text.setPlainText(
            f"{len(invalid_entries)} ticket(s) skipped due to validation errors:\n" +
            "\n".join(error_lines)
        )
        self.validation_error_panel.setVisible(True)
        self.log(f"⚠️ Skipped {len(invalid_entries)} invalid ticket(s) — see Link Generator card for details")

    def update_statistics(self, results):
        """Update processing statistics with better formatting"""
        if not results:
            return

        total = len(results)
        successful = len([r for r in results if r.get('Processing Status') == 'Success'])
        with_keywords = len([r for r in results if r.get('Contains Keywords', False)])
        failed = total - successful

        success_rate = (successful / total * 100) if total > 0 else 0
        keyword_rate = (with_keywords / successful * 100) if successful > 0 else 0

        stats_text = (
            f'<span style="color:#8B7355">TOTAL</span> <span style="color:#1C140F">{total}</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">SUCCESS</span> <span style="color:#00A651">{successful}</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">FAILED</span> <span style="color:#D32F2F">{failed}</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">RATE</span> <span style="color:#1C140F">{success_rate:.1f}%</span>'
            f'&nbsp;&nbsp;&nbsp;'
            f'<span style="color:#8B7355">KEYWORDS HIT</span> <span style="color:#1C140F">{with_keywords}  [{keyword_rate:.1f}%]</span>'
        )

        self.stats_label.setText(stats_text)
        self.stats_label.setStyleSheet("""
            QLabel {
                padding: 8px 16px;
                background-color: rgba(255, 255, 255, 0.11);
                border: 1px solid rgba(28, 20, 15, 0.14);
                border-radius: 6px;
                color: #1C140F;
                font-family: "Consolas";
                font-size: 10pt;
                font-weight: 600;
            }
        """)

    def open_config(self):
        """Open configuration dialog"""
        if self.config_dialog is None:
            self.config_dialog = ConfigDialog(self.config)


        self.config_dialog.show()
        self.config_dialog.raise_()
        self.config_dialog.activateWindow()


        self.config_dialog.finished.connect(self.update_config_preview)



    def init_live_monitor(self):
        """Wire live monitor signals and session restore."""
        os.makedirs(os.path.dirname(self._live_monitor_session_path), exist_ok=True)
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.persist_live_monitor_session)
        QTimer.singleShot(600, self.offer_restore_live_monitor_session)

    def create_live_monitor_tab(self):
        """Build the Live Monitor tab UI — improved layout."""
        tab = QWidget()
        tab.setStyleSheet("background-color: transparent;")
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(10)

        ctrl_bar = QFrame()
        ctrl_bar.setStyleSheet(f"""
            QFrame {{
                background: {GLASS_BG};
                border: none;
                border-radius: 12px;
            }}
        """)
        apply_soft_shadow(ctrl_bar, blur_radius=18, y_offset=2, alpha=28)
        ctrl_h = QHBoxLayout(ctrl_bar)
        ctrl_h.setContentsMargins(14, 10, 14, 10)
        ctrl_h.setSpacing(10)

        self.lm_start_btn = QPushButton("▶  Start Monitor")
        self.lm_start_btn.setMinimumSize(148, 38)
        self.lm_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lm_start_btn.setStyleSheet(BTN_PRIMARY)
        self.lm_start_btn.clicked.connect(self.start_live_monitor)

        self.lm_stop_btn = QPushButton("■  Stop")
        self.lm_stop_btn.setEnabled(False)
        self.lm_stop_btn.setMinimumSize(100, 38)
        self.lm_stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lm_stop_btn.setStyleSheet(BTN_DANGER_OUTLINE)
        self.lm_stop_btn.clicked.connect(self.stop_live_monitor)

        ctrl_h.addWidget(self.lm_start_btn)
        ctrl_h.addWidget(self.lm_stop_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background-color: rgba(28,20,15,0.10); min-width:1px; max-width:1px;")
        ctrl_h.addWidget(sep)

        self.lm_status_banner = QFrame()
        self.lm_status_banner.setObjectName("lmStatusBanner")
        self.lm_status_banner.setStyleSheet(_LM_STATUS_IDLE)
        apply_soft_shadow(self.lm_status_banner, blur_radius=12, y_offset=1, alpha=18)
        banner_v = QVBoxLayout(self.lm_status_banner)
        banner_v.setContentsMargins(12, 8, 12, 8)
        banner_v.setSpacing(4)

        banner_top = QHBoxLayout()
        banner_top.setSpacing(8)

        self.lm_status_dot = QLabel("●")
        self.lm_status_dot.setStyleSheet(
            "color: #A32D2D; font-size: 11px; background: transparent;"
        )
        self.lm_live_badge = QLabel("IDLE")
        self.lm_live_badge.setStyleSheet(f"""
            QLabel {{
                background-color: {GLASS_BG};
                color: #8B7355;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 7pt;
                font-weight: 800;
                letter-spacing: 1px;
            }}
        """)
        self.lm_status_label = QLabel("Not monitoring")
        self.lm_status_label.setStyleSheet(
            "color: #5C4638; font-weight: 700; font-size: 9pt; background: transparent;"
        )
        banner_top.addWidget(self.lm_status_dot)
        banner_top.addWidget(self.lm_live_badge)
        banner_top.addWidget(self.lm_status_label)
        banner_top.addStretch()

        self.lm_tabs_chip = QLabel("0 tabs open")
        self.lm_tabs_chip.setStyleSheet("""
            QLabel {
                background-color: rgba(28, 20, 15, 0.06);
                color: #8B7355;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 8pt;
                font-weight: 600;
            }
        """)
        banner_top.addWidget(self.lm_tabs_chip)

        banner_v.addLayout(banner_top)

        self.lm_status_subtitle = QLabel("Start monitor to watch open browser tabs")
        self.lm_status_subtitle.setStyleSheet(
            "color: #A89880; font-size: 8pt; font-weight: 500; background: transparent;"
        )
        banner_v.addWidget(self.lm_status_subtitle)
        ctrl_h.addWidget(self.lm_status_banner, 1)

        self.lm_tabs_label = QLabel("")
        self.lm_tabs_label.hide()
        ctrl_h.addStretch()

        self.lm_elapsed_label = QLabel("")
        self.lm_elapsed_label.setStyleSheet(
            "color: #8B7355; font-size: 9pt; font-family: 'Consolas', monospace; background: transparent;"
        )
        ctrl_h.addWidget(self.lm_elapsed_label)

        self.lm_last_captured_label = QLabel("")
        self.lm_last_captured_label.setStyleSheet(
            "color: #8B7355; font-size: 9pt; background: transparent;"
        )
        ctrl_h.addWidget(self.lm_last_captured_label)
        outer.addWidget(ctrl_bar)

        body_h = QHBoxLayout()
        body_h.setSpacing(10)

        feed_frame = QFrame()
        feed_frame.setStyleSheet(f"""
            QFrame {{
                background: {GLASS_BG};
                border: none;
                border-radius: 12px;
            }}
        """)
        apply_soft_shadow(feed_frame, blur_radius=16, y_offset=2, alpha=25)
        feed_v = QVBoxLayout(feed_frame)
        feed_v.setContentsMargins(0, 0, 0, 0)
        feed_v.setSpacing(0)

        feed_hdr = QFrame()
        feed_hdr.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: {GLASS_BORDER_SOFT};
                border-top-left-radius: {GLASS_RADIUS};
                border-top-right-radius: {GLASS_RADIUS};
            }}
        """)
        feed_hdr_h = QHBoxLayout(feed_hdr)
        feed_hdr_h.setContentsMargins(14, 10, 14, 10)
        feed_hdr_h.setSpacing(8)
        feed_title = QLabel("📋  Ticket Feed")
        feed_title.setStyleSheet(
            "color: #3A2E26; font-weight: 800; font-size: 10pt; letter-spacing: 0.4px;"
        )
        self.lm_feed_count = QLabel("0 tickets")
        self.lm_feed_count.setStyleSheet("""
            QLabel {
                background-color: rgba(28,20,15,0.06);
                color: #5C4638;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 8pt;
                font-weight: 700;
            }
        """)
        feed_hdr_h.addWidget(feed_title)
        feed_hdr_h.addStretch()
        feed_hdr_h.addWidget(self.lm_feed_count)
        feed_v.addWidget(feed_hdr)

        self.lm_feed_scroll = QScrollArea()
        self.lm_feed_scroll.setWidgetResizable(True)
        self.lm_feed_scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                background: rgba(28,20,15,0.04);
                width: 8px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.19);
                border-radius: 6px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 0.28); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.lm_feed_container = QWidget()
        self.lm_feed_container.setStyleSheet("background: transparent;")
        self.lm_feed_layout = QVBoxLayout(self.lm_feed_container)
        self.lm_feed_layout.setContentsMargins(10, 10, 10, 10)
        self.lm_feed_layout.setSpacing(8)

        self.lm_empty_state = QFrame()
        self.lm_empty_state.setStyleSheet("background: transparent; border: none;")
        es_v = QVBoxLayout(self.lm_empty_state)
        es_v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_v.setContentsMargins(0, 48, 0, 0)
        es_v.setSpacing(6)
        es_icon = QLabel("📭")
        es_icon.setStyleSheet("font-size: 52px; background: transparent;")
        es_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_primary = QLabel("No tickets captured yet")
        es_primary.setStyleSheet(
            "color: #8B7355; font-size: 11pt; font-weight: 700; background: transparent;"
        )
        es_primary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_secondary = QLabel("Press  ▶ Start Monitor  to begin watching for tickets")
        es_secondary.setStyleSheet(
            "color: #A89880; font-size: 9pt; font-weight: 500; background: transparent;"
        )
        es_secondary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        es_v.addWidget(es_icon)
        es_v.addSpacing(8)
        es_v.addWidget(es_primary)
        es_v.addWidget(es_secondary)
        self.lm_feed_layout.addWidget(self.lm_empty_state)
        self.lm_feed_layout.addStretch()
        self.lm_feed_scroll.setWidget(self.lm_feed_container)
        feed_v.addWidget(self.lm_feed_scroll)
        body_h.addWidget(feed_frame, 7)

        side_frame = QFrame()
        side_frame.setStyleSheet(f"""
            QFrame {{
                background: {GLASS_BG};
                border: none;
                border-radius: 12px;
            }}
        """)
        apply_soft_shadow(side_frame, blur_radius=16, y_offset=2, alpha=25)
        side_v = QVBoxLayout(side_frame)
        side_v.setContentsMargins(0, 0, 0, 0)
        side_v.setSpacing(0)

        summ_hdr = QFrame()
        summ_hdr.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-bottom: {GLASS_BORDER_SOFT};
                border-top-left-radius: {GLASS_RADIUS};
                border-top-right-radius: {GLASS_RADIUS};
            }}
        """)
        summ_hdr_h = QHBoxLayout(summ_hdr)
        summ_hdr_h.setContentsMargins(14, 10, 14, 10)
        summ_title = QLabel("📊  Session Summary")
        summ_title.setStyleSheet(
            "color: #3A2E26; font-weight: 800; font-size: 10pt; letter-spacing: 0.4px;"
        )
        summ_hdr_h.addWidget(summ_title)
        side_v.addWidget(summ_hdr)

        stat_grid_w = QWidget()
        stat_grid_w.setStyleSheet("background: transparent;")
        stat_grid = QGridLayout(stat_grid_w)
        stat_grid.setContentsMargins(12, 12, 12, 8)
        stat_grid.setSpacing(8)

        def _make_stat_cell(val_text, caption_text, icon="", accent_color="#5C4638"):
            cell = QFrame()
            cell.setStyleSheet("""
                QFrame {
                    background: transparent;
                    border: none;
                }
            """)
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(8, 6, 8, 6)
            cv.setSpacing(2)
            top_row = QHBoxLayout()
            top_row.setSpacing(4)
            if icon:
                icon_lbl = QLabel(icon)
                icon_lbl.setStyleSheet("font-size: 11pt; background: transparent;")
                top_row.addWidget(icon_lbl)
            val_lbl = QLabel(val_text)
            val_lbl.setStyleSheet(
                f"color: {accent_color}; font-weight: 800; font-size: 18pt; background: transparent;"
            )
            top_row.addWidget(val_lbl)
            top_row.addStretch()
            cv.addLayout(top_row)
            cap_lbl = QLabel(caption_text.upper())
            cap_lbl.setStyleSheet(
                "color: #8B7355; font-size: 7pt; font-weight: 700; letter-spacing: 0.5px; background: transparent;"
            )
            cv.addWidget(cap_lbl)
            return cell, val_lbl

        cell_total, self.lm_total_label = _make_stat_cell("0", "Tickets captured", "🎫", "#5C4638")
        cell_dupes, self.lm_dupes_label = _make_stat_cell("0", "Duplicates skipped", "↺", "#5C4638")
        cell_tabs, self.lm_stat_tabs_label = _make_stat_cell("0", "Browser tabs", "🗂️", "#5C4638")
        cell_start, self.lm_session_start_label = _make_stat_cell("—", "Session started", "🕐", "#5C4638")
        self.lm_session_start_label.setStyleSheet(
            "color: #1C140F; font-weight: 700; font-size: 11pt; background: transparent;"
        )

        stat_grid.addWidget(cell_total, 0, 0)
        stat_grid.addWidget(cell_dupes, 0, 1)
        stat_grid.addWidget(cell_tabs, 1, 0)
        stat_grid.addWidget(cell_start, 1, 1)
        side_v.addWidget(stat_grid_w)

        log_hdr = QFrame()
        log_hdr.setStyleSheet("""
            QFrame {
                background-color: transparent;
                border: none;
                border-top: 1px solid rgba(28,20,15,0.08);
            }
        """)
        log_hdr_h = QHBoxLayout(log_hdr)
        log_hdr_h.setContentsMargins(14, 8, 14, 4)
        log_hdr_lbl = QLabel("📝  Recent Activity")
        log_hdr_lbl.setStyleSheet(
            "color: #5C4638; font-weight: 700; font-size: 9pt; background: transparent;"
        )
        log_hdr_h.addWidget(log_hdr_lbl)
        side_v.addWidget(log_hdr)

        self.lm_mini_log = QTextEdit()
        self.lm_mini_log.setReadOnly(True)
        self.lm_mini_log.setStyleSheet("""
            QTextEdit {
                background-color: rgba(255, 255, 255, 0.07);
                border: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                color: #3A3025;
                font-family: "Consolas", monospace;
                font-size: 8pt;
                padding: 8px 16px;
            }
            QScrollBar:vertical {
                width: 6px; background: transparent;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,98,0,0.16); border-radius: 6px;
            }
        """)
        side_v.addWidget(self.lm_mini_log, 1)
        body_h.addWidget(side_frame, 3)
        outer.addLayout(body_h, 1)

        bot_bar = QHBoxLayout()
        bot_bar.setSpacing(8)
        _btn_style = """
            QPushButton {
                background-color: transparent;
                color: #5C4638;
                border: 1px solid rgba(28,20,15,0.18);
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 700;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QPushButton:hover {
                border-color: #E84C00;
                color: #E84C00;
                background-color: rgba(232,76,0,0.06);
            }
        """
        self.lm_clear_btn = QPushButton("🗑  Clear")
        self.lm_clear_btn.setMinimumHeight(34)
        self.lm_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lm_clear_btn.setStyleSheet(_btn_style)
        self.lm_clear_btn.clicked.connect(self.clear_live_monitor_all)

        self.lm_export_btn = QPushButton("📥  Export")
        self.lm_export_btn.setMinimumHeight(34)
        self.lm_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lm_export_btn.setStyleSheet(_btn_style)
        self.lm_export_btn.clicked.connect(lambda: self.export_live_monitor_excel(prompt=True))

        self.lm_copy_all_btn = QPushButton("📋  Copy All")
        self.lm_copy_all_btn.setMinimumHeight(34)
        self.lm_copy_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lm_copy_all_btn.setStyleSheet(_btn_style)
        self.lm_copy_all_btn.clicked.connect(self._copy_all_tickets)

        bot_bar.addWidget(self.lm_clear_btn)
        bot_bar.addWidget(self.lm_export_btn)
        bot_bar.addWidget(self.lm_copy_all_btn)
        bot_bar.addStretch()

        self.lm_count_badge = QLabel("0 tickets")
        self.lm_count_badge.setStyleSheet(
            "color: #A89880; font-size: 9pt; font-weight: 600; background: transparent;"
        )
        bot_bar.addWidget(self.lm_count_badge)
        outer.addLayout(bot_bar)
        return tab

    def _lm_orange_btn_style(self):
        return """
            QPushButton {
                background-color: rgba(232, 76, 0, 0.8);
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 10pt;
            }
            QPushButton:hover  { background-color: rgba(204, 62, 0, 0.8); }
            QPushButton:pressed { background-color: rgba(179, 53, 0, 0.85); }
        """

    def _lm_append_status(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        if any(w in message for w in ("Error", "Failed", "error", "failed")):
            colour = "#A32D2D"
            prefix = "✗"
        elif any(w in message for w in ("Captured", "✅", "Exported", "Restored")):
            colour = "#2E7D32"
            prefix = "✓"
        elif any(w in message for w in ("Duplicate", "duplicate", "skipped")):
            colour = "#7B5800"
            prefix = "↺"
        elif any(w in message for w in ("Starting", "Active", "started")):
            colour = "#0D47A1"
            prefix = "▶"
        elif any(w in message for w in ("Stopping", "stopped", "cleared")):
            colour = "#5C4638"
            prefix = "■"
        else:
            colour = "#5C4638"
            prefix = "·"

        html_line = (
            f'<span style="color:#A89880;">[{ts}]</span> '
            f'<span style="color:{colour};">{prefix} {message}</span>'
        )
        self.live_monitor_status_messages.append(html_line)
        self.live_monitor_status_messages = self.live_monitor_status_messages[-100:]
        self.lm_mini_log.setHtml("<br>".join(self.live_monitor_status_messages))
        sb = self.lm_mini_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _lm_set_monitoring_active(self, active):
        if active:
            self.lm_status_banner.setStyleSheet(_LM_STATUS_ACTIVE)
            self.lm_status_dot.setStyleSheet(
                "color: #2E7D32; font-size: 12px; background: transparent;"
            )
            self.lm_live_badge.setText("LIVE")
            self.lm_live_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(46, 125, 50, 0.14);
                    color: #2E7D32;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 7pt;
                    font-weight: 800;
                    letter-spacing: 1px;
                }
            """)
            self.lm_status_label.setText("Patrol active")
            self.lm_status_label.setStyleSheet(
                "color: #1B5E20; font-weight: 800; font-size: 9pt; background: transparent;"
            )
            self.lm_status_subtitle.setText("Watching browser tabs for new tickets…")
            self.lm_status_subtitle.setStyleSheet(
                "color: #388E3C; font-size: 8pt; font-weight: 500; background: transparent;"
            )
            self.lm_tabs_chip.setStyleSheet("""
                QLabel {
                    background-color: rgba(46, 125, 50, 0.12);
                    color: #2E7D32;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 8pt;
                    font-weight: 700;
                }
            """)
            self.lm_start_btn.setEnabled(False)
            self.lm_stop_btn.setEnabled(True)
            self._lm_pulse_bright = False
            if not hasattr(self, "_pulse_timer") or self._pulse_timer is None:
                self._pulse_timer = QTimer()
                self._pulse_timer.timeout.connect(self._pulse_status_dot)
            self._pulse_timer.start(900)
            if not hasattr(self, "_elapsed_timer") or self._elapsed_timer is None:
                self._elapsed_timer = QTimer()
                self._elapsed_timer.timeout.connect(self._lm_tick_elapsed)
            self._elapsed_timer.start(1000)
        else:
            self.lm_status_banner.setStyleSheet(_LM_STATUS_IDLE)
            self.lm_status_dot.setStyleSheet(
                "color: #A32D2D; font-size: 11px; background: transparent;"
            )
            self.lm_live_badge.setText("IDLE")
            self.lm_live_badge.setStyleSheet(f"""
                QLabel {{
                    background-color: {GLASS_BG};
                    color: #8B7355;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 7pt;
                    font-weight: 800;
                    letter-spacing: 1px;
                }}
            """)
            self.lm_status_label.setText("Not monitoring")
            self.lm_status_label.setStyleSheet(
                "color: #5C4638; font-weight: 700; font-size: 9pt; background: transparent;"
            )
            self.lm_status_subtitle.setText("Start monitor to watch open browser tabs")
            self.lm_status_subtitle.setStyleSheet(
                "color: #A89880; font-size: 8pt; font-weight: 500; background: transparent;"
            )
            self.lm_tabs_chip.setText("0 tabs open")
            self.lm_tabs_chip.setStyleSheet("""
                QLabel {
                    background-color: rgba(28, 20, 15, 0.06);
                    color: #8B7355;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 8pt;
                    font-weight: 600;
                }
            """)
            self.lm_start_btn.setEnabled(True)
            self.lm_stop_btn.setEnabled(False)
            self.lm_elapsed_label.setText("")
            if hasattr(self, "_pulse_timer") and self._pulse_timer:
                self._pulse_timer.stop()
            if hasattr(self, "_elapsed_timer") and self._elapsed_timer:
                self._elapsed_timer.stop()

    def _pulse_status_dot(self):
        self._lm_pulse_bright = not getattr(self, "_lm_pulse_bright", False)
        dot_color = "#FF2400" if self._lm_pulse_bright else "#CC0000"
        badge_alpha = "0.20" if self._lm_pulse_bright else "0.12"
        self.lm_status_dot.setStyleSheet(
            f"color: {dot_color}; font-size: 12px; background: transparent;"
        )
        self.lm_live_badge.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(204, 0, 0, {badge_alpha});
                color: #A32D2D;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 7pt;
                font-weight: 800;
                letter-spacing: 1px;
            }}
        """)

    def _lm_tick_elapsed(self):
        if self.live_monitor_session_start:
            delta = datetime.now() - self.live_monitor_session_start
            total_s = int(delta.total_seconds())
            h, rem = divmod(total_s, 3600)
            m, s = divmod(rem, 60)
            if h:
                self.lm_elapsed_label.setText(f"⏱ {h}h {m:02d}m {s:02d}s")
            else:
                self.lm_elapsed_label.setText(f"⏱ {m:02d}m {s:02d}s")

    def _lm_update_summary(self):
        count = len(self.live_monitor_tickets)
        dupes = getattr(self, "_lm_dupe_count", 0)
        self.lm_total_label.setText(str(count))
        if hasattr(self, "lm_dupes_label"):
            self.lm_dupes_label.setText(str(dupes))
        if hasattr(self, "lm_stat_tabs_label"):
            chip_txt = getattr(self, "lm_tabs_chip", None)
            if chip_txt and chip_txt.text():
                parts = chip_txt.text().split()
                self.lm_stat_tabs_label.setText(parts[0] if parts else "0")
            else:
                self.lm_stat_tabs_label.setText("0")
        if self.live_monitor_session_start:
            self.lm_session_start_label.setText(
                self.live_monitor_session_start.strftime("%H:%M:%S")
            )
        else:
            self.lm_session_start_label.setText("—")
        self.lm_count_badge.setText(
            f"{count} ticket{'s' if count != 1 else ''}"
        )
        self.lm_feed_count.setText(
            f"{count} ticket{'s' if count != 1 else ''}"
        )
        self.lm_empty_state.setVisible(count == 0)

    def start_live_monitor(self):
        if self.scraper_thread and self.scraper_thread.isRunning():
            QMessageBox.warning(
                self,
                "Scraper Running",
                "The batch scraper is currently running and shares Chrome resources.\n"
                "Please stop the scraper before starting Live Monitor.",
            )
            return
        if self.live_monitor_thread and self.live_monitor_thread.isRunning():
            return

        self.live_monitor_session_start = datetime.now()
        self._lm_set_monitoring_active(True)
        self._lm_append_status("Starting live monitor...")


        self._debug_log_file = None
        try:
            log_filename = f"debug_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            log_path = os.path.join(os.path.dirname(__file__), log_filename)
            self._debug_log_file = open(log_path, 'w', encoding='utf-8')
            self._debug_log_file.write(f"Debug Log Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._debug_log_file.write("=" * 80 + "\n\n")
            self._debug_log_file.flush()
        except Exception as e:
            self._debug_log_file = None

        self.live_monitor_thread = LiveMonitorThread(
            self.config, session_start=self.live_monitor_session_start
        )
        self.live_monitor_thread.status_update.connect(self.on_live_monitor_status)
        self.live_monitor_thread.ticket_captured.connect(self.on_live_monitor_ticket_captured)
        self.live_monitor_thread.duplicate_detected.connect(self.on_live_monitor_duplicate)
        self.live_monitor_thread.error_occurred.connect(self.on_live_monitor_error)
        self.live_monitor_thread.monitoring_stopped.connect(self.on_live_monitor_stopped)
        self.live_monitor_thread.monitor_stats.connect(self.on_live_monitor_stats)
        restored_preliminary_count = 0
        for t in self.live_monitor_tickets:
            tid = t.get("ticket_id")
            url = t.get("ticket_url")
            if tid:


                self.live_monitor_thread._processed_ids.add(tid)
                self.live_monitor_thread._baseline_ticket_ids.add(tid)
            if url:
                self.live_monitor_thread.processed_urls.add(url)
            if t.get("preliminary"):
                restored_preliminary_count += 1

        self.live_monitor_thread.pending_reextract = []
        if restored_preliminary_count:
            self._lm_append_status(
                f"{restored_preliminary_count} restored preliminary ticket(s) kept as history "
                "and will not be automatically re-fetched in this session."
            )
        self.live_monitor_thread.start()
        self.main_tabs.setCurrentWidget(self.live_monitor_tab)

    def stop_live_monitor(self):
        if self.live_monitor_thread and self.live_monitor_thread.isRunning():
            self._lm_append_status("Stopping monitor...")
            self.live_monitor_thread.stop()
            self.live_monitor_thread.wait(8000)
        self._lm_set_monitoring_active(False)
        if self.live_monitor_tickets:
            self.export_live_monitor_excel(prompt=True)


        if self._debug_log_file:
            try:
                self._debug_log_file.write(f"\nDebug Log Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._debug_log_file.write("=" * 80 + "\n")
                self._debug_log_file.close()
                self._debug_log_file = None
            except Exception:
                pass

    def on_live_monitor_status(self, message):
        self._lm_append_status(message)

        if self._debug_log_file:
            try:
                timestamp = datetime.now().strftime('%H:%M:%S')
                self._debug_log_file.write(f"[{timestamp}] {message}\n")
                self._debug_log_file.flush()
            except Exception:
                pass

    def on_live_monitor_stats(self, tab_count, ticket_count):
        if self.live_monitor_thread and self.live_monitor_thread.isRunning():
            self.lm_status_label.setText("Patrol active")
            self.lm_status_subtitle.setText(
                f"{tab_count} tab{'s' if tab_count != 1 else ''} open"
                f"  ·  {ticket_count} ticket{'s' if ticket_count != 1 else ''} this session"
            )
            chip = f"{tab_count} tab{'s' if tab_count != 1 else ''} open"
            self.lm_tabs_chip.setText(chip)
            self.lm_tabs_label.setText(chip)
            if hasattr(self, "lm_stat_tabs_label"):
                self.lm_stat_tabs_label.setText(str(tab_count))

    def on_live_monitor_ticket_captured(self, record):
        ticket_id = record.get("ticket_id", "")


        existing_index = -1
        for i, t in enumerate(self.live_monitor_tickets):
            if t.get("ticket_id") == ticket_id:
                existing_index = i
                break

        if existing_index >= 0:

            old_record = self.live_monitor_tickets[existing_index]
            if old_record.get("preliminary"):
                self.live_monitor_tickets[existing_index] = record
                self._lm_append_status(f"Updated ticket {ticket_id} with full details")

                self.add_live_monitor_card(record, is_new=False, is_duplicate=False)
                self.lm_last_captured_label.setText(
                    f"Last captured: {datetime.now().strftime('%H:%M:%S')}"
                )
                self._lm_update_summary()
                self.persist_live_monitor_session()
                self._update_ticket_notification(record)
                return


        if ticket_id in self.live_monitor_ticket_ids:
            self.on_live_monitor_duplicate(ticket_id)
            return


        self.live_monitor_tickets.append(record)
        self.live_monitor_ticket_ids.add(ticket_id)
        self.add_live_monitor_card(record, is_new=True, is_duplicate=False)
        self.lm_last_captured_label.setText(
            f"Last captured: {datetime.now().strftime('%H:%M:%S')}"
        )
        self._lm_update_summary()
        self.persist_live_monitor_session()
        self._show_new_ticket_notification(record)

    def on_live_monitor_duplicate(self, ticket_id):
        self._lm_dupe_count += 1
        self._lm_append_status(f"Duplicate skipped: {ticket_id}")
        self._lm_update_summary()
        card = self.live_monitor_card_widgets.get(ticket_id)
        if card:
            self.flash_live_monitor_card(card, duplicate=True)

    def on_live_monitor_error(self, message):
        self._lm_append_status(f"Error: {message}")
        QMessageBox.warning(self, "Live Monitor Error", message)
        self._lm_set_monitoring_active(False)

    def on_live_monitor_stopped(self):
        self._lm_set_monitoring_active(False)
        self._lm_append_status("Monitor stopped.")
        self.persist_live_monitor_session()

    def add_live_monitor_card(self, record, is_new=True, is_duplicate=False):
        ticket_id = record.get("ticket_id", "")




        old_card = self.live_monitor_card_widgets.get(ticket_id)
        if old_card is not None:
            self.lm_feed_layout.removeWidget(old_card)
            old_card.deleteLater()
            del self.live_monitor_card_widgets[ticket_id]

        captured_at = record.get("captured_at", "")
        try:
            cap_dt = datetime.fromisoformat(captured_at)
            ts_display = cap_dt.strftime("%Y-%m-%d  %H:%M:%S")
        except Exception:
            ts_display = captured_at or datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        card = QFrame()
        card.setObjectName("lmTicketCard")
        card.setProperty("ticket_id", ticket_id)
        card.setStyleSheet(_LM_CARD_DEFAULT)
        apply_soft_shadow(card, blur_radius=10, y_offset=2, alpha=14)

        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(6)
        ts_lbl = QLabel(ts_display)
        ts_lbl.setStyleSheet(
            "color: #A89880; font-size: 8pt; font-weight: 600; background: transparent; font-family: 'Consolas', monospace;"
        )
        hdr_row.addWidget(ts_lbl)
        hdr_row.addStretch()

        if is_new and not is_duplicate:
            new_badge = QLabel("NEW")
            new_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(25, 118, 210, 0.14);
                    color: #1565C0;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 7pt;
                    font-weight: 800;
                    letter-spacing: 0.5px;
                }
            """)
            hdr_row.addWidget(new_badge)
        elif is_duplicate:
            dupe_badge = QLabel("DUPLICATE")
            dupe_badge.setStyleSheet("""
                QLabel {
                    background-color: rgba(249, 168, 37, 0.16);
                    color: #8A5A00;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 16px;
                    font-size: 7pt;
                    font-weight: 800;
                    letter-spacing: 0.5px;
                }
            """)
            hdr_row.addWidget(dupe_badge)

        tab_raw = record.get("tab_label") or record.get("tab_title") or ""
        if record.get("tab_index") and not tab_raw:
            tab_raw = f"Tab {record['tab_index']}"
        if tab_raw:
            tab_pill = QLabel(tab_raw)
            tab_pill.setStyleSheet("""
                QLabel {
                    background-color: rgba(21, 101, 192, 0.10);
                    color: #1565C0;
                    border: none;
                    border-radius: 6px;
                    font-size: 8pt;
                    font-weight: 700;
                    padding: 8px 16px;
                }
            """)
            hdr_row.addWidget(tab_pill)
        cv.addLayout(hdr_row)

        id_row = QHBoxLayout()
        id_row.setSpacing(8)
        id_chip = QLabel(ticket_id)
        id_chip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        id_chip.setStyleSheet("""
            QLabel {
                background-color: rgba(232, 76, 0, 0.08);
                color: #C43E00;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-family: "Consolas", monospace;
                font-size: 10pt;
                font-weight: 800;
                letter-spacing: 0.5px;
            }
        """)
        id_row.addWidget(id_chip)
        id_row.addStretch()
        cv.addLayout(id_row)

        station = record.get("station", "")
        if station and station != "Unknown station":
            station_lbl = QLabel(f"📍  {station}")
            station_lbl.setStyleSheet(
                "color: #5C4638; font-size: 9pt; font-weight: 600; background: transparent;"
            )
            cv.addWidget(station_lbl)

        stmt = record.get("formatted_statement", "")
        if stmt:
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setStyleSheet(
                "background-color: rgba(28,20,15,0.08); max-height: 1px; border: none;"
            )
            cv.addWidget(divider)

            stmt_lbl = QLabel(stmt)
            stmt_lbl.setWordWrap(True)
            stmt_lbl.setStyleSheet("""
                QLabel {
                    color: #2A1E16;
                    font-size: 10pt;
                    font-weight: 500;
                    background: rgba(28, 20, 15, 0.03);
                    border-radius: 6px;
                    padding: 8px 16px;
                    line-height: 1.45;
                }
            """)
            cv.addWidget(stmt_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        url = record.get("ticket_url", "")

        open_btn = QPushButton("🔗  Open")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setMinimumHeight(28)
        open_btn.setStyleSheet(self._lm_orange_btn_style())
        open_btn.clicked.connect(lambda _=False, u=url: webbrowser.open(u) if u else None)

        copy_btn = QPushButton("📋  Copy")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setMinimumHeight(28)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(28,20,15,0.06);
                color: #3A2E26;
                border: 1px solid rgba(28,20,15,0.14);
                border-radius: 6px;
                font-size: 10pt;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(232, 76, 0, 0.8);
                color: #ffffff;
                border-color: #E84C00;
            }
        """)
        copy_btn.clicked.connect(
            lambda _=False, b=copy_btn, t=stmt: self._on_copy_clicked(b, t)
        )

        remove_btn = QPushButton("✕")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #A89880;
                border: 1px solid rgba(28,20,15,0.14);
                border-radius: 6px;
                font-weight: 700;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 205, 210, 0.45);
                color: #A32D2D;
                border-color: #F09595;
            }
        """)
        remove_btn.clicked.connect(
            lambda _=False, tid=ticket_id: self.remove_live_monitor_card(tid)
        )

        btn_row.addWidget(open_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        btn_row.addWidget(remove_btn)
        cv.addLayout(btn_row)

        stretch_idx = self.lm_feed_layout.count() - 1
        self.lm_feed_layout.insertWidget(stretch_idx, card)
        self.live_monitor_card_widgets[ticket_id] = card

        if is_new and not is_duplicate:
            self.flash_live_monitor_card(card, duplicate=False)
            QTimer.singleShot(80, self._lm_scroll_feed_to_bottom)

    def _lm_scroll_feed_to_bottom(self):
        try:
            bar = self.lm_feed_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except RuntimeError:

            pass

    def _safe_widget_set_style(self, widget, stylesheet):
        """Set a QWidget stylesheet only if its Qt object is still alive.

        Live-monitor cards are intentionally replaced when a preliminary dashboard
        record is upgraded with full ticket details.  A QTimer.singleShot created
        by the old card can therefore fire after deleteLater() has destroyed the
        underlying C++ object.  PyQt keeps the Python wrapper around long enough
        for that delayed callback to run, which otherwise raises:
            RuntimeError: wrapped C/C++ object ... has been deleted
        """
        try:
            if widget is not None:
                widget.setStyleSheet(stylesheet)
        except RuntimeError:


            pass

    def _safe_widget_set_text(self, widget, text):
        """Set button/label text without crashing if its Qt object was deleted."""
        try:
            if widget is not None:
                widget.setText(text)
        except RuntimeError:
            pass

    def flash_live_monitor_card(self, card, duplicate=False):
        self._safe_widget_set_style(
            card, _LM_CARD_DUPE_FLASH if duplicate else _LM_CARD_NEW_FLASH
        )


        QTimer.singleShot(1600, lambda c=card: self._safe_widget_set_style(c, _LM_CARD_DEFAULT))

    def _on_copy_clicked(self, btn, text):
        QApplication.clipboard().setText(text)
        self._safe_widget_set_text(btn, "✓  Copied")

        QTimer.singleShot(1500, lambda b=btn: self._safe_widget_set_text(b, "📋  Copy"))

    def _copy_all_tickets(self):
        all_text = "\n".join(r.get("formatted_statement", "") for r in self.live_monitor_tickets)
        QApplication.clipboard().setText(all_text)
        btn = self.lm_copy_all_btn
        self._safe_widget_set_text(btn, "✓  All Copied")
        QTimer.singleShot(1600, lambda b=btn: self._safe_widget_set_text(b, "📋  Copy All"))

    def remove_live_monitor_card(self, ticket_id):
        card = self.live_monitor_card_widgets.pop(ticket_id, None)
        if card:
            self.lm_feed_layout.removeWidget(card)
            card.deleteLater()
        self.live_monitor_tickets = [t for t in self.live_monitor_tickets if t.get("ticket_id") != ticket_id]
        self.live_monitor_ticket_ids.discard(ticket_id)
        self._lm_update_summary()
        self.persist_live_monitor_session()

    def clear_live_monitor_all(self):
        if not self.live_monitor_tickets:
            return
        reply = QMessageBox.question(
            self,
            "Clear All",
            "Remove all captured tickets from this session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for tid in list(self.live_monitor_card_widgets.keys()):
            card = self.live_monitor_card_widgets.pop(tid)
            self.lm_feed_layout.removeWidget(card)
            card.deleteLater()
        self.live_monitor_tickets.clear()
        self.live_monitor_ticket_ids.clear()
        self._lm_dupe_count = 0
        self._lm_update_summary()
        self.persist_live_monitor_session()
        self._lm_append_status("Feed cleared.")

    def export_live_monitor_excel(self, prompt=True):
        if not self.live_monitor_tickets:
            if prompt:
                QMessageBox.information(self, "Export", "No tickets to export.")
            return None

        default_name = f"{datetime.now().strftime('%Y-%m-%d')}_live_monitor.xlsx"
        if prompt:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Live Monitor",
                default_name,
                "Excel Files (*.xlsx)",
            )
            if not path:
                return None
        else:
            path = os.path.abspath(default_name)

        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        rows = []
        for i, t in enumerate(self.live_monitor_tickets, start=1):
            cap_raw = t.get("captured_at", "")
            try:
                cap_dt = datetime.fromisoformat(cap_raw)
                date_col = cap_dt.strftime("%Y-%m-%d")
                time_col = t.get("time") or cap_dt.strftime("%H:%M:%S")
            except Exception:
                date_col = ""
                time_col = t.get("time", "")

            rows.append({
                "Sr.": i,
                "Ticket ID": t.get("ticket_id", ""),
                "Formatted Statement": t.get("formatted_statement", ""),
                "Date": date_col,
                "Time": time_col,
                "Ticket URL": t.get("ticket_url", ""),
            })

        try:
            pd.DataFrame(rows).to_excel(path, index=False)
            self._lm_append_status(f"Exported to {os.path.basename(path)}")
            if prompt:
                QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")
            return path
        except Exception as e:
            QMessageBox.warning(self, "Export Failed", str(e))
            return None

    def persist_live_monitor_session(self):
        try:
            os.makedirs(os.path.dirname(self._live_monitor_session_path), exist_ok=True)
            payload = {
                "session_start": (
                    self.live_monitor_session_start.isoformat()
                    if self.live_monitor_session_start
                    else None
                ),
                "tickets": self.live_monitor_tickets,
                "processed_ids": list(self.live_monitor_ticket_ids),
                "processed_urls": [
                    t.get("ticket_url") for t in self.live_monitor_tickets if t.get("ticket_url")
                ],
            }
            with open(self._live_monitor_session_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def offer_restore_live_monitor_session(self):
        if not os.path.exists(self._live_monitor_session_path):
            return
        try:
            with open(self._live_monitor_session_path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return
        tickets = payload.get("tickets") or []
        if not tickets:
            return

        reply = QMessageBox.question(
            self,
            "Restore Live Monitor Session",
            f"Found a previous live monitor session with {len(tickets)} ticket(s).\n"
            "Would you like to restore it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.restore_live_monitor_session(payload)

    def restore_live_monitor_session(self, payload):
        self.live_monitor_tickets = list(payload.get("tickets") or [])
        self.live_monitor_ticket_ids = set(payload.get("processed_ids") or [])
        for t in self.live_monitor_tickets:
            tid = t.get("ticket_id", "")
            if tid:
                self.live_monitor_ticket_ids.add(tid)

        ss = payload.get("session_start")
        if ss:
            try:
                self.live_monitor_session_start = datetime.fromisoformat(ss)
            except Exception:
                self.live_monitor_session_start = None

        for record in self.live_monitor_tickets:
            self.add_live_monitor_card(record, is_new=False, is_duplicate=False)

        self._lm_update_summary()
        self._lm_append_status(f"Restored {len(self.live_monitor_tickets)} ticket(s) from last session.")

    def closeEvent(self, event):
        """Handle application close event"""
        if self.live_monitor_thread and self.live_monitor_thread.isRunning():
            self.live_monitor_thread.stop()
            self.live_monitor_thread.wait(3000)
        self.persist_live_monitor_session()
        if self.scraper_thread and self.scraper_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Exit Application",
                "Scraping is in progress. Are you sure you want to exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.scraper_thread.stop()
                self.scraper_thread.wait(3000)
                if self.notification_tray:
                    self.notification_tray.hide()
                event.accept()
            else:
                event.ignore()
        else:
            if self.notification_tray:
                self.notification_tray.hide()
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)

    app.setApplicationName("ITMIS Ticket Scraper")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("TicketScraper")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(255, 255, 255, 0))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1C140F"))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255, 0))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(255, 255, 255, 0))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255, 230))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1C140F"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1C140F"))
    palette.setColor(QPalette.ColorRole.Button, QColor(255, 255, 255, 30))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1C140F"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(255, 98, 0, 180))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    app.setStyleSheet(GLASS_MESSAGEBOX_QSS)
    app._glass_dialog_blur_filter = GlassDialogBlurFilter(app)
    app.installEventFilter(app._glass_dialog_blur_filter)


    window = MainWindow()
    window.show()

    sys.exit(app.exec())