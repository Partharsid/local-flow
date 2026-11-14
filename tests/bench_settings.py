"""Time building and switching every settings page (what you feel as lag)."""
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / "preview_settings.py").read_text(encoding="utf-8")
exec(compile(src.split("win = SettingsWindow(app)")[0], "preview_settings_head", "exec"))   # noqa: S102

t = time.perf_counter()
win = SettingsWindow(app)
app.root.update()
win._advanced(True)
print(f"open window (home): {(time.perf_counter() - t) * 1000:.0f} ms")
end = time.time() + 6
while time.time() < end:          # let the idle prebuild finish
    app.root.update()
    time.sleep(0.01)
for k, _l, _i in PAGES:
    for visit in ("first", "again"):
        t = time.perf_counter()
        win._go(k)
        app.root.update()
        print(f"{k:11s} {visit:5s} {(time.perf_counter() - t) * 1000:6.0f} ms")
app.root.destroy()
