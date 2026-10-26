"""Global hotkeys without slowing down the user's typing.

A WH_KEYBOARD_LL hook makes *every* keystroke on the system wait for our
(Python) callback, so any GIL contention showed up as typing lag in all apps.
Instead:

  * Raw Input (RIDEV_INPUTSINK) delivers a copy of every key event to a
    hidden message-only window. Windows never waits for us, so we can't lag
    anyone. Used for hold-to-talk, Esc and stuck-key tracking.
  * RegisterHotKey handles the combos that must be *swallowed* (hands-free
    toggle, paste-last); the OS consumes those keys natively.

Synthetic input (our own Ctrl+V) arrives with hDevice == NULL and is ignored.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes

from .inserter import KEYEVENTF_KEYUP, _key, _send

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_INPUT, WM_HOTKEY, WM_QUIT, WM_APP = 0x00FF, 0x0312, 0x0012, 0x8000
WM_REREGISTER = WM_APP + 1
RID_INPUT, RIM_TYPEKEYBOARD = 0x10000003, 1
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK, RI_KEY_E0 = 0x1, 0x2
HWND_MESSAGE = wintypes.HWND(-3)
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
HOTKEY_TOGGLE, HOTKEY_PASTE = 1, 2


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# Generic modifier names map to both left and right virtual keys.
GROUPS = {
    "ctrl": {0xA2, 0xA3}, "control": {0xA2, 0xA3},
    "alt": {0xA4, 0xA5}, "shift": {0xA0, 0xA1},
    "win": {0x5B, 0x5C}, "windows": {0x5B, 0x5C},
}
NAMES = {
    "left ctrl": 0xA2, "right ctrl": 0xA3, "left alt": 0xA4, "right alt": 0xA5, "alt gr": 0xA5,
    "left shift": 0xA0, "right shift": 0xA1, "left win": 0x5B, "right win": 0x5C,
    "left windows": 0x5B, "right windows": 0x5C,
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "caps lock": 0x14, "capslock": 0x14, "scroll lock": 0x91, "pause": 0x13, "insert": 0x2D,
    "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "menu": 0x5D, "apps": 0x5D, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC,
    ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}
for _i in range(1, 25):
    NAMES[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    NAMES[_c] = ord(_c.upper())

MODIFIER_VKS = set().union(*GROUPS.values())
_MOD_FLAG = {0xA2: MOD_CONTROL, 0xA3: MOD_CONTROL, 0xA4: MOD_ALT, 0xA5: MOD_ALT,
             0xA0: MOD_SHIFT, 0xA1: MOD_SHIFT, 0x5B: MOD_WIN, 0x5C: MOD_WIN}
ESC = 0x1B
MASK_VK = 0xE8  # unassigned VK: tapping it stops Win/Alt from opening menus


def parse_combo(spec: str) -> list[set[int]]:
    """'ctrl+alt+space' -> [{ctrls}, {alts}, {space}]; each part is any-of."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    out = []
    for p in parts:
        if p in GROUPS:
            out.append(set(GROUPS[p]))
        elif p in NAMES:
            out.append({NAMES[p]})
        else:
            raise ValueError(f"Unknown key name: {p!r}")
    return out


def _combo_down(combo: list[set[int]], down: set[int]) -> bool:
    return all(part & down for part in combo)


_VK_NAME = {v: k for k, v in NAMES.items() if len(k) > 1 or k.isalnum()}
for _n in ("left ctrl", "right ctrl", "left alt", "right alt", "left shift", "right shift",
           "left win", "right win", "space", "esc", "caps lock", "scroll lock", "page up", "page down"):
    _VK_NAME[NAMES[_n]] = _n
_GENERIC = [("ctrl", GROUPS["ctrl"]), ("alt", GROUPS["alt"]), ("shift", GROUPS["shift"]), ("win", GROUPS["win"])]


def combo_name(vks: list[int]) -> str:
    """Readable spec for captured keys: a lone key keeps its side
    ('right ctrl'); in combos modifiers are generic ('ctrl+alt+space')."""
    uniq = list(dict.fromkeys(vks))
    if len(uniq) == 1:
        return _VK_NAME.get(uniq[0], f"vk{uniq[0]}")
    mods = [name for name, group in _GENERIC if group & set(uniq)]
    rest = [_VK_NAME.get(v, f"vk{v}") for v in uniq if v not in MODIFIER_VKS]
    return "+".join(mods + rest)


def _hotkey_args(combo: list[set[int]]) -> tuple[int, int] | None:
    """RegisterHotKey (modifiers, vk) for a combo whose last part is a normal key."""
    *mods, last = combo
    vk = next(iter(last))
    if vk in MODIFIER_VKS or len(last) != 1:
        return None  # modifier-only combos can't be registered
    flags = MOD_NOREPEAT
    for part in mods:
        flags |= _MOD_FLAG.get(next(iter(part)), 0)
    return flags, vk


def _normalise_vk(rk: RAWKEYBOARD) -> int:
    """Raw input reports generic Ctrl/Alt/Shift; split them into left/right."""
    vk, e0 = rk.VKey, bool(rk.Flags & RI_KEY_E0)
    if vk == 0x11:
        return 0xA3 if e0 else 0xA2
    if vk == 0x12:
        return 0xA5 if e0 else 0xA4
    if vk == 0x10:
        return 0xA1 if rk.MakeCode == 0x36 else 0xA0
    return vk


class HotkeyEvent:
    START_HOLD = "start_hold"
    START_EDIT = "start_edit"
    STOP = "stop"
    CANCEL = "cancel"
    TOGGLE = "toggle"
    PASTE_LAST = "paste_last"


class HotkeyManager:
    def __init__(self, events: "queue.Queue[str]", is_injecting=lambda: False):
        self.events = events
        self.is_injecting = is_injecting
        self.hold: list[set[int]] = []
        self.toggle: list[set[int]] = []
        self.paste_last: list[set[int]] = []
        self.recording = False       # set by the app
        self.cancel_window = 0.6     # other keys within this window cancel a hold
        self.registration_errors: list[str] = []
        self._down: set[int] = set()
        self.edit: list[set[int]] = []
        self._hold_active = False
        self._active_keys: set[int] = set()
        self._hold_started = 0.0
        self.last_hold_ms = 0
        self._capture_cb = None
        self._captured: list[int] = []
        self._capture_deadline = 0.0
        self._lock = threading.Lock()
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._wndproc = WNDPROC(self._proc)   # keep a reference

    # -- configuration -------------------------------------------------------
    def configure(self, hold: str, toggle: str, paste_last: str, edit: str = "") -> None:
        with self._lock:
            self.hold = parse_combo(hold) if hold else []
            self.toggle = parse_combo(toggle) if toggle else []
            self.paste_last = parse_combo(paste_last) if paste_last else []
            self.edit = parse_combo(edit) if edit else []
            self._hold_active = False
        if self._hwnd:
            # RegisterHotKey must run on the window's thread.
            user32.PostMessageW(self._hwnd, WM_REREGISTER, 0, 0)

    def _register_hotkeys(self) -> None:
        self.registration_errors = []
        self._raw_fallback: set[int] = set()
        for hid, combo, label in ((HOTKEY_TOGGLE, self.toggle, "hands-free"),
                                  (HOTKEY_PASTE, self.paste_last, "paste-last")):
            user32.UnregisterHotKey(self._hwnd, hid)
            if not combo:
                continue
            args = _hotkey_args(combo)
            if args is None:
                continue  # modifier-only: detected via raw input instead (not swallowed)
            if not user32.RegisterHotKey(self._hwnd, hid, args[0], args[1]):
                # Still detect it through raw input (the other app gets it too).
                self._raw_fallback.add(hid)
                msg = f"The {label} shortcut is already used by another app"
                self.registration_errors.append(msg)
                log.warning("%s (error %s)", msg, ctypes.get_last_error())

    # -- capture (settings "Record" button) -----------------------------------
    def capture_next(self, callback, timeout: float = 8.0) -> None:
        self._captured = []
        self._capture_deadline = time.monotonic() + timeout
        self._capture_cb = callback

    def cancel_capture(self) -> None:
        self._capture_cb = None
        self._captured = []

    # -- message handling (hotkey thread) -------------------------------------
    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_INPUT:
                self._on_raw(lparam)
            elif msg == WM_HOTKEY:
                if self._capture_cb is None:
                    if wparam == HOTKEY_TOGGLE:
                        self._hold_active = False
                        self.events.put(HotkeyEvent.TOGGLE)
                    elif wparam == HOTKEY_PASTE:
                        self.events.put(HotkeyEvent.PASTE_LAST)
                return 0
            elif msg == WM_REREGISTER:
                self._register_hotkeys()
                return 0
        except Exception:  # noqa: BLE001 - never let an exception cross into Win32
            log.exception("hotkey handler error")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_raw(self, lparam) -> None:
        raw = RAWINPUT()
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        if user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(raw), ctypes.byref(size),
                                  ctypes.sizeof(RAWINPUTHEADER)) in (0, 0xFFFFFFFF):
            return
        if raw.header.dwType != RIM_TYPEKEYBOARD or not raw.header.hDevice:
            return  # not a keyboard, or synthetic input (SendInput) - e.g. our own paste
        if self.is_injecting():
            return
        rk = raw.keyboard
        if rk.VKey in (0, 0xFF):
            return
        self._handle(_normalise_vk(rk), not (rk.Flags & RI_KEY_BREAK))

    def _handle(self, vk: int, is_down: bool) -> None:
        repeat = is_down and vk in self._down
        if is_down:
            self._down.add(vk)
        else:
            self._down.discard(vk)

        cb = self._capture_cb
        if cb is not None and time.monotonic() > self._capture_deadline and not self._captured:
            self._capture_cb = cb = None
        if cb is not None:
            if is_down and not repeat:
                self._captured.append(vk)
            elif not is_down and self._captured and not (set(self._captured) & self._down):
                self._capture_cb = None
                try:
                    cb(combo_name(self._captured))
                except Exception:  # noqa: BLE001
                    log.exception("capture callback failed")
            return
        if repeat:
            return

        with self._lock:
            toggle, paste_last = self.toggle, self.paste_last
            holds = [(c, ev) for c, ev in ((self.hold, HotkeyEvent.START_HOLD), (self.edit, HotkeyEvent.START_EDIT))
                     if c]
        # While a hold is active, only its own keys count as "hold keys".
        hold_keys = self._active_keys if self._hold_active else set()

        if is_down:
            if vk == ESC and self.recording:
                self._hold_active = False
                self.events.put(HotkeyEvent.CANCEL)
                return
            # Modifier-only combos (and ones another app owns) come via raw input.
            fallback = getattr(self, "_raw_fallback", set())
            for hid, combo, ev in ((HOTKEY_TOGGLE, toggle, HotkeyEvent.TOGGLE),
                                   (HOTKEY_PASTE, paste_last, HotkeyEvent.PASTE_LAST)):
                if (combo and (_hotkey_args(combo) is None or hid in fallback)
                        and vk in combo[-1] and _combo_down(combo, self._down)):
                    self._hold_active = False
                    self.events.put(ev)
                    return
            if not self._hold_active and not self.recording:
                for combo, ev in holds:
                    keys = set().union(*combo)
                    if vk not in keys:
                        continue
                    extra = {k for k in self._down if k not in keys}
                    if extra:
                        # Keys whose key-up we never saw (UAC, lock screen) must not block us.
                        stale = {k for k in extra if not (user32.GetAsyncKeyState(k) & 0x8000)}
                        self._down -= stale
                        extra -= stale
                    if _combo_down(combo, self._down) and not extra:
                        self.last_hold_ms = 0
                        self._hold_active = True
                        self._active_keys = keys
                        self._hold_started = time.monotonic()
                        if keys & (GROUPS["win"] | GROUPS["alt"]):
                            self._mask()
                        self.events.put(ev)
                        return
                return
            if self._hold_active and vk not in hold_keys:
                # Another key while holding: it was a shortcut (e.g. Ctrl+C).
                if time.monotonic() - self._hold_started < self.cancel_window:
                    self._hold_active = False
                    self.events.put(HotkeyEvent.CANCEL)
            return

        if self._hold_active and vk in hold_keys:
            self._hold_active = False
            self.last_hold_ms = self.held_ms()
            self.events.put(HotkeyEvent.STOP)

    def _mask(self) -> None:
        _send([_key(MASK_VK), _key(MASK_VK, 0, KEYEVENTF_KEYUP)])

    # -- queries used by the app ------------------------------------------------
    def cancel_hold(self) -> None:
        self._hold_active = False

    def hold_physically_down(self) -> bool:
        """Ask Windows directly: the key-up may have been lost (Win+L, UAC)."""
        keys = self._active_keys
        return bool(keys) and any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys)

    def held_ms(self) -> int:
        return int((time.monotonic() - self._hold_started) * 1000)

    # -- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        ready = threading.Event()
        error: list[str] = []

        def run():
            self._thread_id = kernel32.GetCurrentThreadId()
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName="LocalFlowKeys")
            user32.RegisterClassW(ctypes.byref(wc))
            self._hwnd = user32.CreateWindowExW(0, "LocalFlowKeys", None, 0, 0, 0, 0, 0,
                                                HWND_MESSAGE, None, hinst, None)
            rid = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, self._hwnd)   # generic desktop / keyboard
            if not self._hwnd or not user32.RegisterRawInputDevices(ctypes.byref(rid), 1,
                                                                     ctypes.sizeof(RAWINPUTDEVICE)):
                error.append(f"raw input registration failed ({ctypes.get_last_error()})")
            else:
                self._register_hotkeys()
            ready.set()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            for hid in (HOTKEY_TOGGLE, HOTKEY_PASTE):
                user32.UnregisterHotKey(self._hwnd, hid)

        self._thread = threading.Thread(target=run, daemon=True, name="hotkeys")
        self._thread.start()
        ready.wait(3)
        if error:
            raise RuntimeError("Could not set up global hotkeys: " + error[0])

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
