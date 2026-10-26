"""Read what's around the cursor in the focused app via Windows UI Automation.

Gives us the text before/after the caret and the current selection so the
inserted text can match casing, spacing and punctuation, plus vocabulary
(names, identifiers) visible near the cursor. Everything stays local.

UIA calls can block on a hung app, so capture() runs in a worker thread with
a hard timeout and always returns *something*.
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading
from dataclasses import dataclass, field

import psutil

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32

BEFORE_CHARS = 1500
AFTER_CHARS = 400

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
            "vivaldi.exe", "arc.exe", "zen.exe", "librewolf.exe"}


@dataclass
class FocusContext:
    exe: str = ""
    title: str = ""
    hwnd: int = 0
    control_type: str = ""
    before: str = ""
    selected: str = ""
    after: str = ""
    caret_known: bool = False     # True when before/after are exact
    is_password: bool = False
    source: str = "none"          # textpattern | value | none
    words: list[str] = field(default_factory=list)

    @property
    def is_browser(self) -> bool:
        return self.exe in BROWSERS


def _pid_of(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_of(pid: int) -> str:
    try:
        return psutil.Process(pid).name().lower()
    except (psutil.Error, ValueError):
        return ""


_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _uwp_child_exe(hwnd: int, host_pid: int) -> str:
    """UWP apps (WhatsApp, Teams...) run inside ApplicationFrameHost; the real
    process owns a child window of the frame."""
    found: list[str] = []

    def cb(child, _lparam):
        pid = _pid_of(child)
        if pid and pid != host_pid:
            found.append(_exe_of(pid))
            return False
        return True
    user32.EnumChildWindows(hwnd, _ENUM_PROC(cb), 0)
    return found[0] if found else ""


def foreground_window() -> tuple[int, str, str]:
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = _pid_of(hwnd)
    exe = _exe_of(pid)
    if exe == "applicationframehost.exe":
        exe = _uwp_child_exe(hwnd, pid) or exe
    return hwnd, exe, buf.value


def is_elevated_window(hwnd: int) -> bool:
    """True if the window belongs to an elevated (admin) process while we are
    not elevated: Windows then blocks our synthetic keystrokes (UIPI)."""
    import win32api
    import win32con
    try:
        if _token_elevated(win32api.GetCurrentProcess()):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, _pid_of(hwnd))
    except Exception:  # noqa: BLE001 - access denied usually means elevated
        return True
    try:
        return _token_elevated(h)
    except Exception:  # noqa: BLE001
        return False
    finally:
        win32api.CloseHandle(h)


def _token_elevated(proc_handle) -> bool:
    import win32security
    tok = win32security.OpenProcessToken(proc_handle, win32security.TOKEN_QUERY)
    try:
        return bool(win32security.GetTokenInformation(tok, win32security.TokenElevation))
    finally:
        tok.Close()


def prewarm() -> None:
    """Import UI Automation and build comtypes' COM wrappers ahead of the
    first dictation (first run takes seconds and would hit the timeout)."""
    try:
        ctx = FocusContext()
        _capture_uia(ctx)
    except Exception as e:  # noqa: BLE001
        log.debug("UIA prewarm failed: %s", e)


_IDENT = re.compile(r"\b(?:[A-Z][a-z]+[A-Z]\w*|[a-z]+_[a-z0-9_]+|[a-z]+[A-Z]\w*|[A-Z]{2,}[a-z]*\w*)\b")
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b")
_STOP = {"The", "This", "That", "There", "These", "Those", "What", "When", "Where", "Which",
         "Who", "Why", "How", "And", "But", "For", "You", "Your", "Our", "Its", "His", "Her",
         "They", "Then", "Thanks", "Thank", "Hello", "Hi", "Hey", "Dear", "Yes", "Okay"}


_UI_WORDS = {"Copy", "Paste", "Cut", "Edit", "View", "File", "Help", "Fork", "Pin", "Read", "Reorder",
              "Show", "Hide", "Stop", "Start", "Chat", "Add", "List", "Remote", "Control", "Context",
              "Connectors", "Outputs", "Background", "Environment", "Desktop", "Settings", "Search",
              "Share", "Delete", "Save", "Open", "Close", "Cancel", "Submit", "Send", "Reply", "Retry",
              "Untitled", "Notepad", "Home", "Back", "Next", "More", "Menu", "Tools", "Window", "Format"}


def extract_vocabulary(*texts: str, limit: int = 40) -> list[str]:
    """Names, product words and code identifiers worth spelling exactly.

    Ordinary words and UI labels ("Copy", "Edit") are skipped: they add
    nothing and only distract the models."""
    from .polish import COMMON  # local import: polish imports this module

    seen: dict[str, None] = {}
    for text in texts:
        if not text:
            continue
        for m in _IDENT.finditer(text):
            seen.setdefault(m.group(0), None)
        for m in _PROPER.finditer(text):
            w = m.group(0)
            if w not in _STOP and w not in _UI_WORDS and w.lower() not in COMMON:
                seen.setdefault(w, None)
    return list(seen)[:limit]


_uia_ready = False


def _capture_uia(ctx: FocusContext) -> None:
    import uiautomation as auto  # imported lazily: it is slow and COM-bound

    global _uia_ready
    if not _uia_ready:
        from .config import LOG_DIR
        try:  # keep its debug log out of the working directory
            auto.Logger.SetLogFile(str(LOG_DIR / "uiautomation.log"))
        except Exception:  # noqa: BLE001
            pass
        _uia_ready = True

    with auto.UIAutomationInitializerInThread(debug=False):
        el = auto.GetFocusedControl()
        if el is None:
            return
        ctx.control_type = el.ControlTypeName or ""
        try:
            ctx.is_password = bool(el.IsPassword)
        except Exception:  # noqa: BLE001
            ctx.is_password = False
        if ctx.is_password:
            ctx.source = "password"
            return

        # Preferred: TextPattern gives the exact caret/selection position.
        try:
            tp = el.GetPattern(auto.PatternId.TextPattern)
        except Exception:  # noqa: BLE001
            tp = None
        if tp is not None:
            try:
                sels = tp.GetSelection()
                if sels:
                    sel = sels[0]
                    start, end = auto.TextPatternRangeEndpoint.Start, auto.TextPatternRangeEndpoint.End
                    # waitTime=0: uiautomation otherwise sleeps 0.5 s after
                    # every range move, which alone blew the capture timeout.
                    ctx.selected = sel.GetText(4000) or ""
                    before = sel.Clone()
                    before.MoveEndpointByRange(end, sel, start, waitTime=0)
                    before.MoveEndpointByUnit(start, auto.TextUnit.Character, -BEFORE_CHARS, waitTime=0)
                    ctx.before = before.GetText(-1) or ""
                    after = sel.Clone()
                    after.MoveEndpointByRange(start, sel, end, waitTime=0)
                    after.MoveEndpointByUnit(end, auto.TextUnit.Character, AFTER_CHARS, waitTime=0)
                    ctx.after = after.GetText(-1) or ""
                    ctx.caret_known = True
                    ctx.source = "textpattern"
                    return
            except Exception as e:  # noqa: BLE001
                log.debug("TextPattern read failed: %s", e)

        # Fallback: ValuePattern gives the whole value but no caret; assume end.
        try:
            vp = el.GetPattern(auto.PatternId.ValuePattern)
            if vp is not None:
                value = vp.Value or ""
                ctx.before = value[-BEFORE_CHARS:]
                ctx.source = "value"
        except Exception as e:  # noqa: BLE001
            log.debug("ValuePattern read failed: %s", e)


def _title_subject(title: str) -> str:
    """'Q3 plan - Google Docs - Google Chrome' -> 'Q3 plan' (drop app names)."""
    return title.split(" - ")[0].split(" — ")[0].strip()


def capture(use_uia: bool = True, timeout: float = 1.5) -> FocusContext:
    """Default timeout is generous because capture runs while the user is
    still speaking; it only delays anything if it outlasts the recording."""
    hwnd, exe, title = foreground_window()
    ctx = FocusContext(exe=exe, title=title, hwnd=hwnd)
    if use_uia:
        t = threading.Thread(target=_safe_capture, args=(ctx,), daemon=True, name="uia-capture")
        t.start()
        t.join(timeout)
        if t.is_alive():
            log.info("UI Automation timed out for %s; continuing without context", exe)
            # The thread keeps running; it only mutates a context we no longer read.
            ctx = FocusContext(exe=exe, title=title, hwnd=hwnd, source="timeout")
    # Never let \r\n vs \n differences confuse the formatter.
    ctx.before = ctx.before.replace("\r\n", "\n").replace("\r", "\n")
    ctx.after = ctx.after.replace("\r\n", "\n").replace("\r", "\n")
    ctx.selected = ctx.selected.replace("\r\n", "\n")
    ctx.words = extract_vocabulary(ctx.before[-800:], ctx.after[:300], ctx.selected[:500], _title_subject(title))
    return ctx


def _safe_capture(ctx: FocusContext) -> None:
    try:
        _capture_uia(ctx)
    except Exception as e:  # noqa: BLE001
        log.debug("UI Automation capture failed for %s: %s", ctx.exe, e)
