"""Learn from corrections: when the user fixes a dictated word, remember it.

After an insert we snapshot the document, then re-read it every couple of
seconds (up to 30 s, or until focus moves away). The inserted text is located
again via the unchanged text around it, and word-level edits inside it are
compared with what we typed. A replacement is learned only when it looks
like a misheard term, not a rewrite: it must sound similar to the original
word ("Qualify" -> "Coolify", "Path" -> "Parth") and must not be an ordinary
English word.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time

from . import context as ctxmod

log = logging.getLogger(__name__)

WATCH_SECONDS = 30
POLL_SECONDS = 2.0
_TOKEN = re.compile(r"[\w.+#-]+", re.UNICODE)


def _words(text: str) -> list[str]:
    return [w.strip(".,") for w in _TOKEN.findall(text) if w.strip(".,")]


def find_learnable(inserted: str, edited: str, known: set[str]) -> list[tuple[str, str]]:
    """(old, new) pairs where a dictated word was corrected into a new term."""
    from .polish import COMMON

    a, b = _words(inserted), _words(edited)
    pairs: list[tuple[str, str]] = []
    sm = difflib.SequenceMatcher(a=[w.lower() for w in a], b=[w.lower() for w in b], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or (i2 - i1) > 2 or (j2 - j1) > 2:
            continue
        old = " ".join(a[i1:i2])
        for w in b[j1:j2]:
            lw = w.lower()
            if (len(w) < 3 or lw in COMMON or w in known or not re.search(r"[A-Za-z]", w)
                    or lw in (x.lower() for x in a)):
                continue
            # Must sound like what was dictated: a fix, not a rewording.
            if difflib.SequenceMatcher(a=old.lower(), b=lw).ratio() < 0.5:
                continue
            pairs.append((old, w))
    return pairs


def _locate(doc: str, inserted: str) -> tuple[int, int] | None:
    core = inserted.strip()
    if not core:
        return None
    i = doc.rfind(core)
    return (i, i + len(core)) if i >= 0 else None


class CorrectionWatcher:
    def __init__(self, on_learn):
        self.on_learn = on_learn       # called with [(old, new), ...]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, inserted: str, hwnd: int, known: set[str]) -> None:
        self.cancel()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(inserted, hwnd, known, self._stop),
                                        daemon=True, name="learn")
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def _run(self, inserted: str, hwnd: int, known: set[str], stop: threading.Event) -> None:
        try:
            time.sleep(0.8)                      # let the paste land
            base = ctxmod.read_document(hwnd)
            span = _locate(base or "", inserted)
            if not span:
                return                           # app doesn't expose its text, or paste failed
            start, end = span
            before_anchor = base[max(0, start - 40):start]
            after_anchor = base[end:end + 40]
            last = base
            deadline = time.monotonic() + WATCH_SECONDS
            while time.monotonic() < deadline and not stop.wait(POLL_SECONDS):
                if ctxmod.foreground_window()[0] != hwnd:
                    break
                doc = ctxmod.read_document(hwnd)
                if doc is None:
                    break
                last = doc
            if last == base:
                return
            # Find the dictated region again using the untouched text around it.
            s = last.rfind(before_anchor) + len(before_anchor) if before_anchor else 0
            if before_anchor and s < len(before_anchor):
                return
            e = last.find(after_anchor, s) if after_anchor else len(last)
            if e < 0 or e - s > len(inserted) * 3 + 40:
                return
            pairs = find_learnable(inserted, last[s:e], known)
            if pairs:
                log.info("Learned %d correction(s)", len(pairs))
                self.on_learn(pairs)
        except Exception as e:  # noqa: BLE001 - learning must never disturb dictation
            log.debug("correction watcher stopped: %s", e)
