"""Time how long one frame of each pill state takes to render and present."""
import math
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.overlay import GREEN, Overlay  # noqa: E402


class FakeRecorder:
    def levels_snapshot(self):
        t = time.monotonic()
        return [abs(math.sin(i * 0.5 + t)) * 0.8 for i in range(40)]

    def elapsed(self):
        return 3.0


root = tk.Tk()
root.withdraw()
ov = Overlay(root, FakeRecorder())
ov._tick = lambda: None
ov._fullscreen = False

for name, setup in (("idle", lambda: setattr(ov, "state", "idle")),
                    ("recording", lambda: ov.recording("")),
                    ("working", lambda: ov.working("Transcribing")),
                    ("message", lambda: ov.flash("12 words · 0.8s", GREEN, 9))):
    setup()
    mode, tw, th, ta = ov._targets()
    ov._w, ov._h, ov._alpha = tw, th, ta
    for _ in range(5):
        ov._present(mode)
    n = 60
    t0 = time.perf_counter()
    for _ in range(n):
        ov._render(mode)
    r = (time.perf_counter() - t0) / n * 1000
    t0 = time.perf_counter()
    for _ in range(n):
        ov._present(mode)
    p = (time.perf_counter() - t0) / n * 1000
    print(f"{name:10} render {r:5.1f} ms   render+present {p:5.1f} ms   -> max {1000 / p:4.0f} fps")
