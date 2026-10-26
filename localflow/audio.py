"""Microphone capture into memory at 16 kHz mono float32 (Whisper's format).

Audio never touches the disk. If the device refuses 16 kHz we record at its
native rate and resample.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

TARGET_RATE = 16000
PREROLL_SECONDS = 0.4


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio
    n_out = int(round(audio.size * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class Recorder:
    """Start/stop recorder with a live level meter for the overlay."""

    def __init__(self, device=None, keep_warm: bool = False):
        self.device = device
        self.keep_warm = keep_warm
        self._stream = None
        self._rate = TARGET_RATE
        self._chunks: list = []
        self._preroll: deque = deque()
        self._preroll_len = 0
        self._recording = False
        self._lock = threading.Lock()
        self.level = 0.0                     # smoothed 0..1 for the UI
        self.levels: deque = deque(maxlen=48)
        self.started_at = 0.0
        if keep_warm:
            self._open()

    # -- stream management -------------------------------------------------
    def _open(self) -> None:
        if self._stream is not None:
            return
        for rate in (TARGET_RATE, None):
            try:
                kwargs = dict(channels=1, dtype="float32", callback=self._callback,
                              device=self.device, blocksize=0)
                if rate:
                    kwargs["samplerate"] = rate
                stream = sd.InputStream(**kwargs)
                stream.start()
                self._stream = stream
                self._rate = int(stream.samplerate)
                log.info("Mic opened at %d Hz (device=%s)", self._rate, self.device)
                return
            except Exception as e:  # noqa: BLE001 - PortAudio raises many types
                log.warning("Opening mic at %s Hz failed: %s", rate, e)
        raise RuntimeError("Could not open the microphone. Check Windows privacy settings "
                           "(Settings > Privacy & security > Microphone).")

    def _close(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        mono = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        lvl = min(1.0, rms * 12.0) ** 0.7
        self.level = 0.6 * self.level + 0.4 * lvl
        with self._lock:
            if self._recording:
                self._chunks.append(mono)
                self.levels.append(self.level)
            elif self.keep_warm:
                self._preroll.append(mono)
                self._preroll_len += mono.size
                while self._preroll and self._preroll_len - self._preroll[0].size > PREROLL_SECONDS * self._rate:
                    self._preroll_len -= self._preroll.popleft().size

    # -- public API --------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            self._chunks = list(self._preroll) if self.keep_warm else []
            self._preroll.clear()
            self._preroll_len = 0
            self.levels.clear()
            self._recording = True
        self.started_at = time.monotonic()
        try:
            self._open()
        except Exception:
            with self._lock:
                self._recording = False
            raise

    def stop(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
        if not self.keep_warm:
            self._close()
        self.level = 0.0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks).astype(np.float32)
        return _resample(audio, self._rate, TARGET_RATE)

    def levels_snapshot(self) -> list:
        """Copy of recent levels; the audio thread appends concurrently."""
        with self._lock:
            return list(self.levels)

    @property
    def recording(self) -> bool:
        return self._recording

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self._recording else 0.0

    def set_keep_warm(self, keep_warm: bool) -> None:
        self.keep_warm = keep_warm
        if keep_warm:
            self._open()
        elif not self._recording:
            self._close()

    def close(self) -> None:
        self._recording = False
        self._close()
