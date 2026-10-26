"""The floating pill: an always-on mini bar that expands while dictating.

Rendered with Pillow at 2x and shown through a native layered window
(UpdateLayeredWindow) for anti-aliased edges, real per-pixel transparency and
a soft shadow, none of which Tk's canvas can do.

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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- palette ---
BG = (22, 23, 29, 242)
BORDER = (255, 255, 255, 26)
HIGHLIGHT = (255, 255, 255, 18)
FG = (244, 244, 246, 255)
MUTED = (161, 161, 170, 255)
ACCENT_A = (139, 92, 246)       # violet
ACCENT_B = (34, 211, 238)       # cyan
RED = (255, 77, 109)
GREEN_RGB = (52, 211, 153)
AMBER_RGB = (251, 191, 36)
# Exported for callers that pass a flash colour (kept as hex for compatibility).
GREEN = "#34d399"
AMBER = "#fbbf24"

IDLE_W, IDLE_H = 64, 14    # resting bar: small, but visible on dark and light apps
IDLE_BORDER = (255, 255, 255, 70)
SS = 2                     # supersampling factor
CANVAS_W, CANVAS_H = 560, 104
SHADOW = 14

# ---------------------------------------------------------------- win32 -----
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


@lru_cache(maxsize=48)
def _shadow(w: int, h: int, cw: int, ch: int, left: int, top: int, scale: float) -> Image.Image:
    """Blurred rounded-rect shadow at 1x; cached per pill size."""
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = int(4 * scale)
    d.rounded_rectangle((left, top + off, left + w, top + h + off), radius=h // 2, fill=(0, 0, 0, 110))
    return img.filter(ImageFilter.GaussianBlur(int(9 * scale)))


def _ease(cur: float, target: float, speed: float) -> float:
    d = target - cur
    return target if abs(d) < 0.5 else cur + d * speed


class Overlay:
    def __init__(self, root, recorder, enabled: bool = True):
        self.root = root
        self.recorder = recorder
        self.enabled = enabled
        self.mini_bar = True
        self.hotkey_label = "Right Ctrl"
        self.on_click = None            # set by the app: called on a click
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
        self._w, self._h, self._alpha = 46.0, 8.0, 0.0
        self._hover = False
        self._clickable = None
        self._fullscreen = False
        self._bars = [0.0] * 28
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
        y = work.bottom - self.ch + int(2 * self.s)   # pill rests ~12 px above the screen/taskbar edge
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
            return "hidden", 46, 8, 0.0
        if state == "recording":
            return ("recording", 400, 70, 1.0) if self.preview else ("recording", 268, 46, 1.0)
        if state == "working":
            return "working", 46 + _text_w(self.message + "...", 12.5, True) + 22, 46, 1.0
        if state == "message":
            return "message", min(CANVAS_W - 40, 46 + _text_w(self.message, 12.5, True) + 22), 46, 1.0
        if self.meeting_since:              # always visible while a meeting is being recorded
            return "meeting", 172, 32, 1.0
        if not self.mini_bar or self._fullscreen:
            return "hidden", 46, 8, 0.0
        if self._hover:
            text = f"Click or hold {self.hotkey_label} to dictate"
            return "hover", 38 + _text_w(text, 12) + 18, 36, 1.0
        return "idle", IDLE_W, IDLE_H, 0.92

    def _tick(self) -> None:
        delay = 250
        try:
            now = time.monotonic()
            if now >= self._next_monitor_check:
                self._place()
                self._next_monitor_check = now + 1.0
            mode, tw, th, ta = self._targets()
            self._set_clickable(mode in ("idle", "hover") or (mode == "recording" and self.handsfree))
            self._w = _ease(self._w, tw, 0.28)
            self._h = _ease(self._h, th, 0.28)
            self._alpha = ta if abs(self._alpha - ta) < 0.02 else self._alpha + (ta - self._alpha) * 0.3
            animating = (abs(self._w - tw) > 0.5 or abs(self._h - th) > 0.5 or abs(self._alpha - ta) > 0.02
                         or mode in ("recording", "working"))
            if mode == "meeting":           # the clock only needs a repaint each second
                key_extra = int(now - self.meeting_since)
            else:
                key_extra = 0
            key = (mode, int(self._w), int(self._h), round(self._alpha, 2), self.message, self.tone, self._pos,
                   self.preview, key_extra)
            if animating or key != self._last_frame_key:
                self._present(mode)
                self._last_frame_key = key
            # ~40 fps while something moves; otherwise a slow heartbeat (state
            # changes arrive via kick(), so idle polling can be lazy).
            delay = 25 if animating else (250 if mode == "meeting" else 500)
            if mode == "message":   # wake exactly when the message should go
                delay = max(25, min(delay, int((self._until - now) * 1000) + 5))
        finally:
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
        img = self._render(mode)
        arr = np.asarray(img, dtype=np.uint16)
        a = arr[..., 3:4]
        a = (a * self._alpha).astype(np.uint16)
        rgb = arr[..., :3] * a // 255                      # premultiply
        bgra = np.concatenate([rgb[..., ::-1], a], axis=2).astype(np.uint8)
        ctypes.memmove(self._bits, bgra.tobytes(), bgra.nbytes)
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
        s, k = self.s, self.s * SS
        w, h = int(self._w * s), int(max(self._h, 4) * s)
        left = (self.cw - w) // 2
        top = self.ch - h - int(SHADOW * s)
        # Layer 1: cached soft shadow at 1x.
        base = _shadow(w, h, self.cw, self.ch, left, top, s).copy()
        # Layer 2: the pill and its content at 2x, downsampled for anti-aliasing.
        pw, ph = w * SS, h * SS
        pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(pill)
        outline = IDLE_BORDER if mode == "idle" else BORDER
        radius = min(ph // 2, int(24 * k))   # capsule when short, rounded card when tall
        d.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=radius, fill=BG, outline=outline, width=max(1, int(k)))
        if ph > 24 * k:  # faint glassy rim along the top edge
            inset = ph * 0.75
            d.line((inset, int(1.2 * k), pw - inset, int(1.2 * k)), fill=HIGHLIGHT, width=max(1, int(k)))
        cy = ph / 2
        if mode == "recording":
            two_rows = self.preview and ph > 56 * k
            self._draw_recording(pill, d, pw, 23 * k if two_rows else cy, k)
            if two_rows:
                self._draw_preview(d, pw, ph - 18 * k, k)
        elif mode == "working":
            self._draw_working(d, pw, cy, k)
        elif mode == "message":
            self._draw_message(d, pw, cy, k)
        elif mode == "hover":
            self._draw_hover(d, pw, cy, k)
        elif mode == "meeting":
            r = 4.5 * k
            d.ellipse((16 * k - r, cy - r, 16 * k + r, cy + r), fill=RED + (255,))
            secs = int(time.monotonic() - self.meeting_since)
            d.text((28 * k, cy), f"Meeting notes  {secs // 60}:{secs % 60:02d}", font=_font(11.5 * k, True),
                   fill=FG, anchor="lm")
        elif mode == "idle" and ph >= 10 * k:
            # "Sleeping waveform": five small gradient dots say "ready to dictate".
            n, gap, r = 5, 7 * k, 1.6 * k
            x0 = pw / 2 - gap * (n - 1) / 2
            for i in range(n):
                col = _mix(ACCENT_A, ACCENT_B, i / (n - 1)) + (255,)
                rr = r * (1.25 if i == 2 else 1.0)
                d.ellipse((x0 + i * gap - rr, cy - rr, x0 + i * gap + rr, cy + rr), fill=col)
        pill = pill.resize((w, h), Image.LANCZOS)
        base.alpha_composite(pill, (left, top))
        return base

    def _draw_recording(self, pill, d, pw, cy, k) -> None:
        t = time.monotonic() - self._pulse_t0
        pulse = 0.5 + 0.5 * math.sin(t * 4.2)
        # Glowing red dot.
        dx = 22 * k
        glow = Image.new("RGBA", pill.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gr = (8 + 4 * pulse) * k
        gd.ellipse((dx - gr, cy - gr, dx + gr, cy + gr), fill=RED + (int(90 + 60 * pulse),))
        pill.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5 * k)))
        r = 5 * k
        d.ellipse((dx - r, cy - r, dx + r, cy + r), fill=RED + (255,))
        # Waveform: bars ease towards the live microphone level.
        levels = self.recorder.levels_snapshot()[-len(self._bars):]
        levels = [0.0] * (len(self._bars) - len(levels)) + levels
        for i, lv in enumerate(levels):
            self._bars[i] += (lv - self._bars[i]) * 0.45
        right_pad = (88 if self.handsfree else 58) * k  # room for timer (+ stop button)
        x0, x1 = 40 * k, pw - right_pad
        n = len(self._bars)
        step = (x1 - x0) / n
        bw = max(2 * k, step * 0.52)
        maxh = (46 - 18) * k
        for i, lv in enumerate(self._bars):
            bh = max(3 * k, min(1.0, lv * 1.15) * maxh)
            x = x0 + i * step
            col = _mix(ACCENT_A, ACCENT_B, i / max(1, n - 1)) + (255 if lv > 0.03 else 110,)
            d.rounded_rectangle((x, cy - bh / 2, x + bw, cy + bh / 2), radius=bw / 2, fill=col)
        secs = int(self.recorder.elapsed())
        label = f"✎ {self.badge}" if self.badge else f"{secs // 60}:{secs % 60:02d}"
        f = _font(11.5 * k, True)
        tx = pw - (22 * k if not self.handsfree else 46 * k)
        d.text((tx, cy), label, font=f, fill=ACCENT_B + (255,) if self.badge else MUTED, anchor="rm")
        if self.handsfree:
            # Stop button (click the pill to finish).
            bx = pw - 24 * k
            br = 11 * k
            d.ellipse((bx - br, cy - br, bx + br, cy + br), fill=(255, 255, 255, 34))
            sq = 4 * k
            d.rounded_rectangle((bx - sq, cy - sq, bx + sq, cy + sq), radius=1.5 * k, fill=FG)

    def _draw_preview(self, d, pw, cy, k) -> None:
        """Newest words of the live transcript, trimmed from the left."""
        f = _font(11.5 * k)
        room = pw - 36 * k
        words = self.preview.split()
        text = " ".join(words)
        while words and f.getlength(text) > room:
            words.pop(0)
            text = "…" + " ".join(words)
        d.text((18 * k, cy), text, font=f, fill=(214, 214, 222, 255), anchor="lm")

    def _draw_working(self, d, pw, cy, k) -> None:
        t = time.monotonic()
        cx, r = 26 * k, 8 * k
        start = (t * 360 * 1.3) % 360
        span = 90 + 60 * math.sin(t * 3)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 30), width=int(2.4 * k))
        steps = 10
        seg = span / steps
        for i in range(steps):  # gradient arc built from short segments
            a0 = (start + seg * i) % 360
            col = _mix(ACCENT_A, ACCENT_B, i / steps) + (255,)
            d.arc((cx - r, cy - r, cx + r, cy + r), a0, a0 + seg + 1.5, fill=col, width=max(2, int(2.4 * k)))
        dots = "." * (1 + int(t * 2.5) % 3)
        d.text((46 * k, cy), self.message + dots, font=_font(12.5 * k, True), fill=FG, anchor="lm")

    def _draw_message(self, d, pw, cy, k) -> None:
        cx, r = 26 * k, 10 * k
        col = {"ok": GREEN_RGB, "warn": AMBER_RGB}.get(self.tone, ACCENT_A)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (255,))
        if self.tone == "ok":
            d.line((cx - 4.5 * k, cy + 0.2 * k, cx - 1.2 * k, cy + 3.6 * k, cx + 5 * k, cy - 3.8 * k),
                   fill=(18, 18, 22, 255), width=int(2.2 * k), joint="curve")
        else:
            d.text((cx, cy), "!" if self.tone == "warn" else "i", font=_font(13 * k, True),
                   fill=(18, 18, 22, 255), anchor="mm")
        d.text((46 * k, cy), self.message, font=_font(12.5 * k, True), fill=FG, anchor="lm")

    def _draw_hover(self, d, pw, cy, k) -> None:
        cx, r = 20 * k, 9 * k
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT_A + (255,))
        # tiny microphone glyph
        d.rounded_rectangle((cx - 2.6 * k, cy - 5 * k, cx + 2.6 * k, cy + 1.6 * k), radius=2.6 * k, fill=FG)
        d.arc((cx - 4.5 * k, cy - 3.5 * k, cx + 4.5 * k, cy + 4 * k), 20, 160, fill=FG, width=int(1.3 * k))
        d.line((cx, cy + 4 * k, cx, cy + 5.6 * k), fill=FG, width=int(1.3 * k))
        text = f"Click or hold {self.hotkey_label} to dictate"
        d.text((38 * k, cy), text, font=_font(12 * k), fill=(228, 228, 234, 255), anchor="lm")
