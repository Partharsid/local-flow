"""Time one overlay frame (render + present) in each mode."""
import sys, time, tkinter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.overlay import Overlay


class R:
    def levels_snapshot(self): return [0.4] * 40
    def elapsed(self): return 7


root = tkinter.Tk(); root.withdraw()
o = Overlay(root, R())
o.recording(); o.set_preview("hello world this is a longer live transcript line")
for _ in range(60): o._tick()
for mode in ("recording", "working", "idle"):
    o._w, o._h, o._alpha = 260, 50, 1.0
    t = time.perf_counter()
    for _ in range(100): o._present(mode)
    print(mode, f"{(time.perf_counter() - t) * 10:.2f} ms/frame")
