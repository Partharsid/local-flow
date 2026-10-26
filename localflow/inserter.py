"""Put text into whatever app has focus.

Default: clipboard + Ctrl+V, the most compatible method on Windows (works in
browsers, Electron apps, Office, terminals). The user's clipboard is saved in
full and restored afterwards, unless they copied something new meanwhile.
Our temporary clipboard entry is flagged so it stays out of Win+V history and
cloud clipboard sync.

Fallback: SendInput with KEYEVENTF_UNICODE, character by character.
"""
from __future__ import annotations

import ctypes
import logging
import struct
import threading
import time
from ctypes import wintypes

import win32clipboard as wcb
import win32con

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)

# --- SendInput plumbing -------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "lwin": 0x5B, "rwin": 0x5C, "v": 0x56,
      "insert": 0x2D, "lctrl": 0xA2, "rctrl": 0xA3, "lalt": 0xA4, "ralt": 0xA5,
      "lshift": 0xA0, "rshift": 0xA1, "return": 0x0D}
ULONG_PTR = ctypes.c_size_t
INJECT_TAG = 0x4C464C57  # "LFLW": lets our own keyboard hook ignore these events


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, INJECT_TAG)))


def _send(inputs: list[INPUT]) -> int:
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


MODIFIERS = ["lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift", "lwin", "rwin"]


def release_modifiers(timeout: float = 0.8) -> None:
    """Wait for the user to let go of modifiers; force key-ups if they stick.

    A still-held Alt or Win would turn our Ctrl+V into a different shortcut.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        held = [m for m in MODIFIERS if _is_down(VK[m])]
        if not held:
            return
        time.sleep(0.02)
    held = [m for m in MODIFIERS if _is_down(VK[m])]
    if held:
        log.info("Forcing key-up for stuck modifiers: %s", held)
        _send([_key(VK[m], 0, KEYEVENTF_KEYUP) for m in held])
        time.sleep(0.03)


def send_paste(shift_insert: bool = False) -> None:
    if shift_insert:
        seq = [_key(VK["shift"]), _key(VK["insert"]), _key(VK["insert"], 0, KEYEVENTF_KEYUP),
               _key(VK["shift"], 0, KEYEVENTF_KEYUP)]
    else:
        seq = [_key(VK["ctrl"]), _key(VK["v"]), _key(VK["v"], 0, KEYEVENTF_KEYUP),
               _key(VK["ctrl"], 0, KEYEVENTF_KEYUP)]
    _send(seq)


def type_text(text: str, delay: float = 0.008) -> None:
    """Type via Unicode key events, one character per SendInput call.

    Sending long batches made apps (Windows 11 Notepad) drop characters, so
    each character goes on its own with a tiny pause."""
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            _send([_key(VK["return"]), _key(VK["return"], 0, KEYEVENTF_KEYUP)])
        else:
            data = ch.encode("utf-16-le")
            seq: list[INPUT] = []
            for i in range(0, len(data), 2):  # surrogate pairs go as two units
                unit = int.from_bytes(data[i:i + 2], "little")
                seq += [_key(0, unit, KEYEVENTF_UNICODE), _key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
            _send(seq)
        time.sleep(delay)


# --- Clipboard ----------------------------------------------------------------
_SKIP_FORMATS = {
    win32con.CF_BITMAP, win32con.CF_METAFILEPICT, win32con.CF_PALETTE, win32con.CF_ENHMETAFILE,
    win32con.CF_OWNERDISPLAY, win32con.CF_DSPBITMAP, win32con.CF_DSPMETAFILEPICT,
    win32con.CF_DSPENHMETAFILE,
}
# Synthesised by Windows from CF_UNICODETEXT; restoring them causes duplicates.
_SYNTH_TEXT = {win32con.CF_TEXT, win32con.CF_OEMTEXT}


def _open_clipboard(retries: int = 20) -> bool:
    for _ in range(retries):
        try:
            wcb.OpenClipboard(0)
            return True
        except Exception:  # noqa: BLE001 - another app holds it
            time.sleep(0.025)
    return False


def _drop_files_bytes(paths: tuple[str, ...]) -> bytes:
    # DROPFILES header: pFiles, pt.x, pt.y, fNC, fWide; then UTF-16 paths.
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    body = "".join(p + "\0" for p in paths) + "\0"
    return header + body.encode("utf-16-le")


def save_clipboard() -> list[tuple[int, object]]:
    saved: list[tuple[int, object]] = []
    if not _open_clipboard():
        return saved
    try:
        fmt = 0
        formats = []
        while True:
            fmt = wcb.EnumClipboardFormats(fmt)
            if not fmt:
                break
            formats.append(fmt)
        has_unicode = win32con.CF_UNICODETEXT in formats
        for f in formats:
            if f in _SKIP_FORMATS or (has_unicode and f in _SYNTH_TEXT):
                continue
            try:
                data = wcb.GetClipboardData(f)
            except Exception:  # noqa: BLE001 - delayed-render or handle formats
                continue
            if f == win32con.CF_HDROP and isinstance(data, tuple):
                data = _drop_files_bytes(data)
            if isinstance(data, (bytes, str)):
                saved.append((f, data))
    finally:
        wcb.CloseClipboard()
    return saved


def _register(name: str) -> int:
    return wcb.RegisterClipboardFormat(name)


def set_clipboard_text(text: str, exclude_from_history: bool = True) -> int:
    """Set text; returns the clipboard sequence number after our write.

    exclude_from_history=False for Remote Desktop/VM viewers: their clipboard
    redirection works as a clipboard monitor and may skip "excluded" content."""
    if not _open_clipboard():
        raise RuntimeError("Clipboard is busy")
    try:
        wcb.EmptyClipboard()
        wcb.SetClipboardData(win32con.CF_UNICODETEXT, text)
        if exclude_from_history:
            # Keep dictations out of clipboard history and cloud sync.
            wcb.SetClipboardData(_register("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
        wcb.SetClipboardData(_register("CanIncludeInClipboardHistory"), struct.pack("<I", 0))
        wcb.SetClipboardData(_register("CanUploadToCloudClipboard"), struct.pack("<I", 0))
    finally:
        wcb.CloseClipboard()
    return user32.GetClipboardSequenceNumber()


def restore_clipboard(saved: list[tuple[int, object]], expected_seq: int) -> None:
    if user32.GetClipboardSequenceNumber() != expected_seq:
        log.info("Clipboard changed since paste; not restoring")
        return
    if not _open_clipboard():
        return
    try:
        wcb.EmptyClipboard()
        for fmt, data in saved:
            try:
                wcb.SetClipboardData(fmt, data)
            except Exception as e:  # noqa: BLE001
                log.debug("Could not restore clipboard format %s: %s", fmt, e)
    finally:
        wcb.CloseClipboard()


class Inserter:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.injecting = threading.Event()
        self._restore_lock = threading.Lock()
        # (saved_user_clipboard, our_sequence_number, token) awaiting restore.
        self._pending: tuple[list, int, object] | None = None

    def insert(self, text: str, exe: str = "") -> str:
        """Insert text into the focused window. Returns the method used."""
        if not text:
            return "none"
        self.injecting.set()
        try:
            release_modifiers()
            type_apps = {a.lower() for a in self.cfg.get("type_instead_apps", [])}
            if exe.lower() in type_apps:
                type_text(text)
                return "type"
            exe_l = exe.lower()
            slow = exe_l in {a.lower() for a in self.cfg.get("slow_paste_apps", [])}
            shift_insert = exe_l in {a.lower() for a in self.cfg.get("shift_insert_apps", [])}
            restore = self.cfg.get("restore_clipboard", True)
            token = object()
            with self._restore_lock:
                saved: list = []
                if restore:
                    pending = self._pending
                    if pending and user32.GetClipboardSequenceNumber() == pending[1]:
                        # The clipboard still holds our previous dictation: the
                        # user's real clipboard is the one saved back then.
                        saved = pending[0]
                    else:
                        saved = save_clipboard()
                seq = set_clipboard_text(text, exclude_from_history=not slow)
                self._pending = (saved, seq, token) if restore else None
                # Remote clipboards need time to sync before the paste lands.
                time.sleep(0.35 if slow else 0.03)
                send_paste(shift_insert=shift_insert)
            if restore:
                delay = int(self.cfg.get("clipboard_restore_delay_ms", 500)) / 1000
                if slow:
                    delay = max(delay, 2.0)

                def later():
                    time.sleep(delay)
                    with self._restore_lock:
                        if self._pending is None or self._pending[2] is not token:
                            return  # a newer dictation owns the restore now
                        self._pending = None
                        restore_clipboard(saved, seq)
                threading.Thread(target=later, daemon=True, name="clipboard-restore").start()
            return "paste"
        finally:
            # Let the injected key events drain through the hook before clearing.
            threading.Timer(0.15, self.injecting.clear).start()
