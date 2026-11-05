"""Transcribe long dictations in pieces while the user is still speaking.

Whisper's time grows with the length of the recording, so a 2-minute
dictation used to mean several seconds of waiting after the key was released.
While recording, every finished stretch of speech (cut at a natural pause)
is transcribed in the background; at the end only the last few seconds are
left to do.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 480                     # 30 ms


def find_pause(audio: np.ndarray, start: int, end: int, min_pause_s: float = 0.35) -> int | None:
    """Sample index in the middle of the longest pause between start and end."""
    if end - start < FRAME * 20:
        return None
    seg = audio[start:end]
    n = seg.size // FRAME
    rms = np.sqrt(np.mean(seg[:n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    loud = float(np.percentile(rms, 90)) if n else 0.0
    quiet = rms < max(0.003, 0.12 * loud)
    need = int(min_pause_s * RATE / FRAME)
    best, run_start, best_len = None, None, 0
    for i, q in enumerate(np.append(quiet, False)):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            length = i - run_start
            if length >= need and length >= best_len:      # prefer the longest, then the latest
                best, best_len = (run_start + i) // 2, length
            run_start = None
    return None if best is None else start + best * FRAME


class ChunkedTranscriber:
    def __init__(self, transcriber, prompt: str, first_after_s: float = 20.0, min_chunk_s: float = 12.0,
                 force_after_s: float = 45.0):
        self.t = transcriber
        self.prompt = prompt
        self.first_after = int(first_after_s * RATE)
        self.min_chunk = int(min_chunk_s * RATE)
        self.force_after = int(force_after_s * RATE)
        self.done = 0                      # samples already transcribed
        self.texts: list[str] = []
        self.background_ms = 0
        self._lock = threading.Lock()

    def step(self, audio: np.ndarray) -> bool:
        """Transcribe the next finished piece if there is one. True if it did work."""
        with self._lock:
            pending = audio.size - self.done
            if pending < self.first_after:
                return False
            # Leave the last second alone: the speaker may be mid-word.
            cut = find_pause(audio, self.done + self.min_chunk, audio.size - RATE)
            if cut is None:
                if pending < self.force_after:
                    return False
                cut = find_pause(audio, self.done + self.min_chunk, audio.size - RATE, min_pause_s=0.12) \
                    or audio.size - RATE
            text, info = self.t.transcribe(audio[self.done:cut], self.prompt)
            self.texts.append(text)
            self.background_ms += int(info.get("stt_ms") or 0)
            log.info("Pre-transcribed %.1fs of speech while recording (%s ms)", (cut - self.done) / RATE,
                     info.get("stt_ms"))
            self.done = cut
            return True

    def finish(self, audio: np.ndarray) -> tuple[str, dict]:
        """The whole transcript; only the untranscribed tail is decoded now."""
        with self._lock:
            tail = audio[self.done:] if self.done < audio.size else np.zeros(0, np.float32)
            text, info = self.t.transcribe(tail, self.prompt) if tail.size else ("", {})
            parts = [p for p in self.texts + [text] if p]
            info = dict(info, duration=audio.size / RATE, pre_transcribed_s=round(self.done / RATE, 1),
                        background_ms=self.background_ms, segments_kept=len(parts), segments=len(self.texts) + 1)
            info.setdefault("rms", float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0)
            info.setdefault("stt_ms", 0)
            return " ".join(parts).strip(), info
