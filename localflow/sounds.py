"""Short, soft start/stop cues generated once as WAV files."""
from __future__ import annotations

import io
import logging
import wave
import winsound
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
RATE = 44100


def _tone(freqs: list[float], ms: int, volume: float = 0.18) -> bytes:
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    sig = np.zeros(n)
    seg = n // len(freqs)
    for i, f in enumerate(freqs):
        part = slice(i * seg, n if i == len(freqs) - 1 else (i + 1) * seg)
        sig[part] = np.sin(2 * np.pi * f * t[part])
    env = np.minimum(1, np.minimum(t / 0.008, (t[-1] - t + 1e-9) / 0.04))  # soft attack/release
    pcm = (sig * env * volume * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Sounds:
    def __init__(self, folder: Path, enabled: bool = True):
        self.enabled = enabled
        self.files = {}
        folder.mkdir(parents=True, exist_ok=True)
        specs = {"start": ([660, 880], 90), "stop": ([880, 660], 90), "error": ([300, 220], 160)}
        for name, (freqs, ms) in specs.items():
            p = folder / f"{name}.wav"
            if not p.exists():
                p.write_bytes(_tone(freqs, ms))
            self.files[name] = str(p)

    def play(self, name: str) -> None:
        if not self.enabled or name not in self.files:
            return
        try:
            winsound.PlaySound(self.files[name], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError as e:
            log.debug("sound failed: %s", e)
