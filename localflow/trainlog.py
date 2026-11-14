"""Training examples from real use, kept only on this PC.

Every AI-cleaned dictation is saved as (mode, app, dictionary, what Whisper
heard, what LocalFlow typed). If you then fix the typed text within ~30 s,
your fixed version is saved too; those fixes are the most valuable training
data there is. training/retrain.py turns this file into a better model.

File: %APPDATA%\\LocalFlow\\training\\dictations.jsonl (one JSON per line).
"""
from __future__ import annotations

import difflib
import json
import logging
import threading
import time
import uuid

from .config import DATA_DIR

log = logging.getLogger(__name__)

PATH = DATA_DIR / "training" / "dictations.jsonl"
MAX_BYTES = 50 * 1024 * 1024
_lock = threading.Lock()


def _write(rec: dict) -> None:
    with _lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        if PATH.exists() and PATH.stat().st_size > MAX_BYTES:
            return
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add(mode: str, app: str, vocab: list[str], heard: str, typed: str) -> str:
    """Save one dictation; returns its id (for a later correction)."""
    rid = uuid.uuid4().hex[:12]
    try:
        _write({"id": rid, "ts": round(time.time()), "mode": mode, "app": app, "vocab": list(vocab)[:40],
                "input": heard, "output": typed})
    except OSError as e:
        log.debug("training log not written: %s", e)
    return rid


def looks_like_fix(typed: str, edited: str) -> bool:
    """An edit of the dictated text, not new writing or a deletion."""
    a, b = typed.strip(), edited.strip()
    if not a or not b or a == b:
        return False
    if not 0.5 <= len(b) / len(a) <= 1.6:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def correct(rid: str, typed: str, edited: str) -> None:
    if not rid or not looks_like_fix(typed, edited):
        return
    try:
        _write({"id": rid, "ts": round(time.time()), "corrected": edited.strip()})
        log.info("Saved your correction as a training example")
    except OSError as e:
        log.debug("correction not written: %s", e)


def load() -> list[dict]:
    """Dictations with any correction applied: [{..., "output", "corrected": bool}]."""
    if not PATH.exists():
        return []
    rows: dict[str, dict] = {}
    with open(PATH, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "corrected" in r:
                if r["id"] in rows:
                    rows[r["id"]]["output"] = r["corrected"]
                    rows[r["id"]]["corrected"] = True
            elif "input" in r:
                rows[r["id"]] = dict(r, corrected=False)
    return list(rows.values())


def stats() -> dict:
    rows = load()
    return {"dictations": len(rows), "corrections": sum(r["corrected"] for r in rows)}


def clear() -> None:
    with _lock:
        PATH.unlink(missing_ok=True)
