"""The floating pill: a tiny translucent bar that grows only while dictating.

Rendered with Pillow at 2x and shown through a native layered window
(UpdateLayeredWindow) for anti-aliased edges and real per-pixel transparency.
Motion is time based (the same speed at any frame rate), frames are drawn only
into the small pill rectangle, and nothing is drawn while the bar is at rest.

Focus safety: the window has WS_EX_NOACTIVATE and answers WM_MOUSEACTIVATE
with MA_NOACTIVATE, so clicking it never takes focus from the app the text is
going into. While it is only displaying (not clickable) it is also
WS_EX_TRANSPARENT so clicks pass straight through.

Threading: everything here runs on the Tk main thread (the window's messages
are dispatched by Tk's event loop). Other threads only call the small public
methods, which just set fields.
"""
from __future__ import annotations

import ctypes
import math
import time
from ctypes import wintypes
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- palette ---
# Minimal and translucent: a dark glass capsule with soft white content. The
# only colours are a red record dot and green / amber status dots.
BG = (16, 16, 20, 168)
BORDER = (255, 255, 255, 34)
FG = (246, 246, 248, 255)
MUTED = (205, 205, 213, 200)
SOFT = (255, 255, 255, 235)
RED = (255, 84, 104)
GREEN_RGB = (74, 222, 128)
AMBER_RGB = (251, 191, 36)
# Exported for callers that pass a flash colour (kept as hex for compatibility).
GREEN = "#34d399"
AMBER = "#fbbf24"

IDLE_W, IDLE_H = 36, 5        # resting bar: a faint hairline capsule
IDLE_FILL = (18, 18, 24, 150)
IDLE_BORDER = (255, 255, 255, 85)
SS = 2                        # supersampling factor
CANVAS_W, CANVAS_H = 320, 60
MARGIN = 4                    # transparent border so edges never clip
BOTTOM_GAP = 10               # distance from the screen / taskbar edge
FRAME_MS = 16                 # frame interval while something moves
BAR_COUNT = 18


# ---------------------------------------------------------------- win32 -----
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
GWL_EXSTYLE = -20
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
HWND_TOPMOST = -1
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WM_MOUSEMOVE, WM_LBUTTONUP, WM_MOUSELEAVE, WM_MOUSEACTIVATE, WM_SETCURSOR = 0x200, 0x202, 0x2A3, 0x21, 0x20
MA_NOACTIVATE = 3
WM_WAKE = 0x8000 + 7       # WM_APP + 7: "state changed, draw now"
WM_TIMER = 0x0113
FRAME_TIMER = 1
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
TME_LEAVE = 0x2
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
IDC_HAND = 32649


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("hwndTrack", wintypes.HWND),
                ("dwHoverTime", wintypes.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.SetCursor.argtypes = [wintypes.HANDLE]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.restype = ctypes.c_size_t
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _fullscreen_app_running() -> bool:
    """Windows' own answer (the API that decides when to hold notifications):
    true for full-screen games, videos and presentations, false for ordinary
    maximized windows."""
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    # 2 = busy (full-screen app), 3 = Direct3D full screen, 4 = presentation mode
    return state.value in (2, 3, 4)


def _monitor_of_foreground() -> tuple[wintypes.RECT, wintypes.RECT, bool]:
    """(work area, monitor rect, a full-screen app is running) for the monitor
    showing the app the user is working in."""
    fg = user32.GetForegroundWindow()
    mon = user32.MonitorFromWindow(fg, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    return mi.rcWork, mi.rcMonitor, _fullscreen_app_running()


# ---------------------------------------------------------------- drawing ---
def _font(size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _font_cached(round(size * 2) / 2, bold)


@lru_cache(maxsize=32)
def _font_cached(size: float, bold: bool) -> ImageFont.FreeTypeFont:
    for name in (("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)) + ("arial.ttf",):
        try:
            return ImageFont.truetype(name, int(size))
        except OSError:
            continue
    return ImageFont.load_default()


def _text_w(text: str, size: float, bold: bool = False) -> float:
    """Width at 1x, measured at the 2x size actually drawn (no rounding drift)."""
    return _font(size * SS, bold).getlength(text) / SS


def _approach(cur: float, target: float, dt: float, rate: float) -> float:
    """Frame-rate independent ease: the same motion at 30, 60 or 144 fps."""
    d = target - cur
    if abs(d) < 0.12:
        return target
    return cur + d * (1.0 - math.exp(-rate * dt))


def _clean(text: str) -> str:
    """Segoe UI has no check/pencil glyphs; the status dot says it instead."""
    return text.replace("✓", "").replace("✎", "").strip()


class Overlay:
    def __init__(self, root, recorder, enabled: bool = True):
        self.root = root
        self.recorder = recorder
        self.enabled = enabled
        self.mini_bar = True
        self.hotkey_label = "Right Ctrl"
        self.on_click = None            # set by the app: called on a click
        self.on_session_end = None      # set by the app: Windows is shutting down / signing out
        dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
        self.s = max(1.0, dpi / 96)
        self.cw, self.ch = int(CANVAS_W * self.s), int(CANVAS_H * self.s)

        # shared state (any thread writes, Tk thread reads)
        self.state = "idle"             # idle | recording | working | message | hidden
        self.handsfree = False
        self.preview = ""               # live transcript while recording
        self.badge = ""
        self.meeting_since = 0.0        # monotonic start time while meeting notes record
        self.message = ""
        self.tone = "info"              # info | ok | warn
        self._until = 0.0
        self._pulse_t0 = time.monotonic()

        # animation state (Tk thread only)
        self._w, self._h, self._alpha = float(IDLE_W), float(IDLE_H), 0.0
        self._last_t = time.monotonic()
        self._hires = False
        self._hover = False
        self._clickable = None
        self._fullscreen = False
        self._bars = [0.0] * BAR_COUNT
        self._last_frame_key = None
        self._pos = (0, 0)
        self._next_monitor_check = 0.0
        self._timer_ms = 0

        self._create_window()
        self._tick()

    # ------------------------------------------------------------ public API
    # Callable from any thread: they set fields, then kick() wakes the Tk
    # thread through the window's message queue so the change shows at once.
    def recording(self, hint: str = "", badge: str = "") -> None:
        self.handsfree = bool(hint)
        self.badge = badge             # e.g. "Edit" for voice-edit mode
        self._pulse_t0 = time.monotonic()
        self.preview = ""
        self.state = "recording"
        self.kick()

    def set_preview(self, text: str) -> None:
        self.preview = text
        self.kick()

    def working(self, text: str = "Transcribing") -> None:
        self.message = text
        self.state = "working"
        self.kick()

    def flash(self, text: str, color: str = "", seconds: float = 1.6) -> None:
        self.message = text
        self.tone = "ok" if color == GREEN else ("warn" if color == AMBER else "info")
        self._until = time.monotonic() + seconds
        self.state = "message"
        self.kick()

    def hide(self) -> None:
        self.state = "idle"
        self.kick()

    def kick(self) -> None:
        if getattr(self, "hwnd", None):
            user32.PostMessageW(self.hwnd, WM_WAKE, 0, 0)

    def _wake(self) -> None:
        """Run the next frame now instead of waiting for the idle timer.

        Runs inside our window procedure. It must never call into Tkinter:
        this message is dispatched while Tk's event loop has released the
        GIL, and re-entering Tkinter there aborts the process ("Fatal Python
        error: PyEval_RestoreThread"). Frames are paced by a Win32 timer."""
        self._tick()

    # ------------------------------------------------------------ window
    def _create_window(self) -> None:
        self._wndproc = WNDPROC(self._proc)   # keep a reference
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "LocalFlowPill"
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT
        self.hwnd = user32.CreateWindowExW(ex, "LocalFlowPill", "LocalFlow", WS_POPUP, 0, 0, self.cw, self.ch,
                                           None, None, hinst, None)
        self._clickable = False
        screen = user32.GetDC(None)
        self._memdc = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=self.cw, biHeight=-self.ch,
                               biPlanes=1, biBitCount=32, biCompression=0)
        self._bits = ctypes.c_void_p()
        self._dib = gdi32.CreateDIBSection(self._memdc, ctypes.byref(bmi), 0, ctypes.byref(self._bits), None, 0)
        gdi32.SelectObject(self._memdc, self._dib)
        self._canvas = np.ctypeslib.as_array((ctypes.c_ubyte * (self.cw * self.ch * 4)).from_address(self._bits.value)).reshape(self.ch, self.cw, 4)
        self._shown = False

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE                     # never steal focus
            if msg == WM_SETCURSOR and self._clickable:
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND)))
                return 1
            if msg in (WM_WAKE, WM_TIMER):
                self._wake()
                return 0
            if msg == WM_QUERYENDSESSION:
                return 1                                 # never block shutdown / sign-out
            if msg == WM_ENDSESSION:
                if wparam and self.on_session_end:
                    self.on_session_end()
                return 0
            if msg == WM_MOUSEMOVE:
                if not self._hover:
                    self._hover = True
                    tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                    user32.TrackMouseEvent(ctypes.byref(tme))
                    self._wake()
                return 0
            if msg == WM_MOUSELEAVE:
                self._hover = False
                self._wake()
                return 0
            if msg == WM_LBUTTONUP:
                if self.on_click:
                    self.on_click(self.state)
                return 0
        except Exception:  # noqa: BLE001 - never let an exception cross into Win32
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _set_clickable(self, clickable: bool) -> None:
        if clickable == self._clickable:
            return
        style = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        style = (style & ~WS_EX_TRANSPARENT) if clickable else (style | WS_EX_TRANSPARENT)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, style)
        self._clickable = clickable
        if not clickable:
            self._hover = False

    def _place(self) -> None:
        work, _mon, full = _monitor_of_foreground()
        self._fullscreen = full
        x = work.left + (work.right - work.left - self.cw) // 2
        y = work.bottom - self.ch - int((BOTTOM_GAP - MARGIN) * self.s)
        if (x, y) != self._pos:
            self._pos = (x, y)
            self._last_frame_key = None   # force a repaint at the new spot

    # ------------------------------------------------------------ frame loop
    def _targets(self) -> tuple[str, float, float, float]:
        """(mode, width, height, opacity) the pill is animating towards."""
        state = self.state
        if state == "message" and time.monotonic() > self._until:
            state = self.state = "idle"
        if not self.enabled:
            return "hidden", IDLE_W, IDLE_H, 0.0
        if state == "recording":
            if self.preview:
                return "recording", 260, 50, 1.0
            return "recording", (164 if self.handsfree else 134), 28, 1.0
        if state == "working":
            return "working", 38 + _text_w(self.message + "...", 11.5, True) + 14, 28, 1.0
        if state == "message":
            return "message", min(CANVAS_W - 2 * MARGIN, 34 + _text_w(_clean(self.message), 11.5, True) + 14), 28, 1.0
        if self.meeting_since:              # always visible while a meeting is being recorded
            return "meeting", 124, 24, 1.0
        if not self.mini_bar or self._fullscreen:
            return "hidden", IDLE_W, IDLE_H, 0.0
        if self._hover:
            return "hover", 30 + _text_w(self._hover_text(), 11) + 14, 26, 1.0
        return "idle", IDLE_W, IDLE_H, 1.0

    def _hover_text(self) -> str:
        return f"Hold {self.hotkey_label} to dictate"

    def _hires_timer(self, on: bool) -> None:
        """1 ms system timer resolution only while moving, so frames are even."""
        if on != self._hires:
            self._hires = on
            try:
                (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
            except OSError:
                pass

    def _tick(self) -> None:
        delay = 250
        animating = False
        try:
            now = time.monotonic()
            dt = min(0.05, max(0.001, now - self._last_t))
            self._last_t = now
            if now >= self._next_monitor_check:
                self._place()
                self._next_monitor_check = now + 1.0
            mode, tw, th, ta = self._targets()
            self._set_clickable(mode in ("idle", "hover") or (mode == "recording" and self.handsfree))
            self._w = _approach(self._w, tw, dt, 20)
            self._h = _approach(self._h, th, dt, 20)
            self._alpha = _approach(self._alpha, ta, dt, 14)
            animating = (abs(self._w - tw) > 0.12 or abs(self._h - th) > 0.12 or abs(self._alpha - ta) > 0.01
                         or mode in ("recording", "working"))
            if mode == "recording":         # bars follow the microphone level
                levels = self.recorder.levels_snapshot()[-BAR_COUNT:]
                levels = [0.0] * (BAR_COUNT - len(levels)) + list(levels)
                k = 1.0 - math.exp(-dt * 26)
                for i, lv in enumerate(levels):
                    self._bars[i] += (lv - self._bars[i]) * k
            key_extra = int(now - self.meeting_since) if mode == "meeting" else 0
            key = (mode, round(self._w, 1), round(self._h, 1), round(self._alpha, 2), self.message, self.tone,
                   self._pos, self.preview, key_extra)
            if animating or key != self._last_frame_key:
                self._present(mode)
                self._last_frame_key = key
            # ~60 fps while something moves; otherwise a slow heartbeat (state
            # changes arrive via kick(), so idle polling can be lazy).
            delay = FRAME_MS if animating else (250 if mode == "meeting" else 500)
            if mode == "message":   # wake exactly when the message should go
                delay = max(FRAME_MS, min(delay, int((self._until - now) * 1000) + 5))
        finally:
            self._hires_timer(animating)
            if delay != self._timer_ms:  # SetTimer with the same id just reschedules
                user32.SetTimer(self.hwnd, FRAME_TIMER, delay, None)
                self._timer_ms = delay

    # ------------------------------------------------------------ rendering
    def _present(self, mode: str) -> None:
        if self._alpha <= 0.01:
            if self._shown:
                user32.ShowWindow(self.hwnd, SW_HIDE)
                self._shown = False
            return
        pill = self._pill(mode)
        w, h = pill.size
        arr = np.asarray(pill, dtype=np.uint16)
        a = (arr[..., 3:4] * int(self._alpha * 256) >> 8)
        rgb = arr[..., :3] * a // 255                      # premultiply
        left = (self.cw - w) // 2
        top = self.ch - h - int(MARGIN * self.s)
        self._canvas[:] = 0
        region = self._canvas[top:top + h, left:left + w]
        region[..., 0] = rgb[..., 2]                       # BGRA
        region[..., 1] = rgb[..., 1]
        region[..., 2] = rgb[..., 0]
        region[..., 3] = a[..., 0]
        screen = user32.GetDC(None)
        pt_dst = wintypes.POINT(*self._pos)
        size = wintypes.SIZE(self.cw, self.ch)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pt_dst), ctypes.byref(size), self._memdc,
                                   ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)
        user32.ReleaseDC(None, screen)
        if not self._shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            self._shown = True

    def _render(self, mode: str) -> Image.Image:
        """The whole canvas with the pill on it (used by the design preview)."""
        pill = self._pill(mode)
        base = Image.new("RGBA", (self.cw, self.ch), (0, 0, 0, 0))
        base.alpha_composite(pill, ((self.cw - pill.width) // 2, self.ch - pill.height - int(MARGIN * self.s)))
        return base

    def _pill(self, mode: str) -> Image.Image:
        """Just the pill, at the display's pixel size."""
        s, k = self.s, self.s * SS
        w, h = max(4, int(self._w * s)), max(3, int(self._h * s))
        pw, ph = w * SS, h * SS
        pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        idle = mode == "idle"
        ImageDraw.Draw(pill).rounded_rectangle(
            (0, 0, pw - 1, ph - 1), radius=ph // 2 if ph < 40 * k else int(16 * k),
            fill=IDLE_FILL if idle else BG, outline=IDLE_BORDER if idle else BORDER, width=max(1, int(k)))
        # Content goes on its own layer: ImageDraw replaces pixels on RGBA
        # images instead of blending, which would punch holes in the glass.
        content = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(content)
        cy = ph / 2
        if mode == "recording":
            two_rows = bool(self.preview) and ph > 40 * k
            self._draw_recording(d, pw, 14 * k if two_rows else cy, k)
            if two_rows:
                self._draw_preview(d, pw, ph - 15 * k, k)
        elif mode == "working":
            self._draw_working(d, cy, k)
        elif mode == "message":
            self._draw_message(d, cy, k)
        elif mode == "hover":
            self._draw_hover(d, cy, k)
        elif mode == "meeting":
            r = 3.2 * k
            d.ellipse((13 * k - r, cy - r, 13 * k + r, cy + r), fill=RED + (255,))
            secs = int(time.monotonic() - self.meeting_since)
            d.text((23 * k, cy), f"Meeting  {secs // 60}:{secs % 60:02d}", font=_font(10.5 * k, True),
                   fill=FG, anchor="lm")
        pill.alpha_composite(content)
        return pill.reduce(SS)

    def _draw_recording(self, d, pw, cy, k) -> None:
        t = time.monotonic() - self._pulse_t0
        pulse = 0.5 + 0.5 * math.sin(t * 4.0)
        dx, r = 13 * k, (3.2 + 0.5 * pulse) * k
        d.ellipse((dx - r, cy - r, dx + r, cy + r), fill=RED + (int(215 + 40 * pulse),))
        right_pad = (54 if self.handsfree else 36) * k          # timer (+ stop button)
        x0, x1 = 25 * k, min(pw - right_pad, 25 * k + BAR_COUNT * 5.4 * k)
        step = (x1 - x0) / BAR_COUNT
        bw = max(1.6 * k, step * 0.5)
        maxh = 17 * k
        for i, lv in enumerate(self._bars):
            bh = max(2.4 * k, min(1.0, lv * 1.2) * maxh)
            x = x0 + i * step
            col = SOFT if lv > 0.05 else (255, 255, 255, 80)
            d.rounded_rectangle((x, cy - bh / 2, x + bw, cy + bh / 2), radius=bw / 2, fill=col)
        secs = int(self.recorder.elapsed())
        label = _clean(self.badge) if self.badge else f"{secs // 60}:{secs % 60:02d}"
        tx = pw - (11 * k if not self.handsfree else 32 * k)
        d.text((tx, cy), label, font=_font(10 * k), fill=MUTED, anchor="rm")
        if self.handsfree:                      # stop button: click the pill to finish
            bx, br = pw - 17 * k, 7 * k
            d.ellipse((bx - br, cy - br, bx + br, cy + br), fill=(255, 255, 255, 40))
            sq = 2.6 * k
            d.rounded_rectangle((bx - sq, cy - sq, bx + sq, cy + sq), radius=1 * k, fill=FG)

    def _draw_preview(self, d, pw, cy, k) -> None:
        """Newest words of the live transcript, trimmed from the left."""
        f = _font(10.5 * k)
        room = pw - 26 * k
        words = self.preview.split()
        text = " ".join(words)
        while words and f.getlength(text) > room:
            words.pop(0)
            text = "…" + " ".join(words)
        d.text((13 * k, cy), text, font=f, fill=(225, 225, 232, 235), anchor="lm")

    def _draw_working(self, d, cy, k) -> None:
        t = time.monotonic()
        cx, r = 17 * k, 5.5 * k
        start = (t * 360 * 1.2) % 360
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 38), width=max(1, int(1.6 * k)))
        d.arc((cx - r, cy - r, cx + r, cy + r), start, start + 100, fill=SOFT, width=max(2, int(1.8 * k)))
        dots = "." * (1 + int(t * 2.5) % 3)
        d.text((31 * k, cy), self.message + dots, font=_font(11.5 * k, True), fill=FG, anchor="lm")

    def _draw_message(self, d, cy, k) -> None:
        col = {"ok": GREEN_RGB, "warn": AMBER_RGB}.get(self.tone, (210, 210, 220))
        r = 3.4 * k
        d.ellipse((15 * k - r, cy - r, 15 * k + r, cy + r), fill=col + (255,))
        d.text((27 * k, cy), _clean(self.message), font=_font(11.5 * k, True), fill=FG, anchor="lm")

    def _draw_hover(self, d, cy, k) -> None:
        r = 3 * k
        d.ellipse((13 * k - r, cy - r, 13 * k + r, cy + r), fill=SOFT)
        d.text((24 * k, cy), self._hover_text(), font=_font(11 * k), fill=(230, 230, 236, 255), anchor="lm")
