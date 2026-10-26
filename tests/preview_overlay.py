"""Render every pill state to one PNG for design review (no window shown)."""
import math
import sys
import tkinter as tk
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.overlay import AMBER, GREEN, Overlay  # noqa: E402


class FakeRecorder:
    def levels_snapshot(self):
        return [abs(math.sin(i * 0.55)) * (0.35 + 0.6 * abs(math.sin(i * 0.17))) for i in range(40)]

    def elapsed(self):
        return 7.4


root = tk.Tk()
root.withdraw()
ov = Overlay(root, FakeRecorder())
ov._tick = lambda: None   # no animation loop for a static preview
ov._fullscreen = False


def shot(mode, setup):
    setup()
    _m, tw, th, _a = ov._targets() if mode != "hover" else ("hover", 0, 0, 1)
    if mode == "hover":
        ov._hover = True
        _m, tw, th, _a = ov._targets()
    ov._w, ov._h, ov._alpha = tw, th, 1.0
    for _ in range(12):           # let the waveform bars settle
        ov._render(mode)
    return ov._render(mode)


frames = [
    shot("idle", lambda: setattr(ov, "state", "idle")),
    shot("hover", lambda: setattr(ov, "state", "idle")),
    shot("recording", lambda: ov.recording("")),
    shot("recording", lambda: ov.recording("hands-free")),
    shot("working", lambda: ov.working("Transcribing")),
    shot("message", lambda: ov.flash("12 words · 0.8s", GREEN, 5)),
    shot("message", lambda: ov.flash("Mic is muted · tray icon → Unmute microphone", AMBER, 5)),
]
bg = (243, 244, 246)
sheet = Image.new("RGBA", (frames[0].width, frames[0].height * len(frames)), bg + (255,))
for i, f in enumerate(frames):
    sheet.alpha_composite(f, (0, i * f.height))
out = Path(__file__).resolve().parent.parent / "assets" / "overlay_preview.png"
out.parent.mkdir(exist_ok=True)
sheet.convert("RGB").save(out)
print(out)
