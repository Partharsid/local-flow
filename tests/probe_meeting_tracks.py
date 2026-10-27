"""Record a short meeting while SAPI speaks; inspect the two WAV tracks."""
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow import meeting  # noqa: E402


def speak():
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    comtypes.client.CreateObject("SAPI.SpVoice").Speak("The deployment moved to Thursday. Priya will update the docs.")


rec = meeting.MeetingRecorder()
rec.start()
time.sleep(0.8)
t = threading.Thread(target=speak)
t.start()
t.join()
time.sleep(1.0)
wall = rec.elapsed()
folder = rec.stop()
print(f"wall {wall:.1f}s, errors {rec.errors}")
for name in ("mic", "system"):
    with wave.open(str(folder / f"{name}.wav")) as w:
        n, rate = w.getnframes(), w.getframerate()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768
    print(f"{name:6}: {n / rate:.1f}s at {rate} Hz, rms {float(np.sqrt(np.mean(a ** 2))):.4f}, "
          f"peak {float(np.abs(a).max()):.3f}")
print("folder:", folder)
