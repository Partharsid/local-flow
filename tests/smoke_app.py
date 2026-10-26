"""Run the real app and drive it: hands-free start/stop cycles, hover-free,
cancel, paste-last. Reproduces crashes without anyone touching the keyboard.
Uses a throwaway data folder (LOCALFLOW_HOME) but the real models via a
config copied from the user's install.
"""
import faulthandler
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="lf_smoke_"))
real = Path(os.environ["APPDATA"]) / "LocalFlow"
cfg = json.loads((real / "config.json").read_text(encoding="utf-8"))
cfg.update(sounds=False, first_run_done=True, history=False)
# Reuse the downloaded Whisper model in place (a full path works as a model name).
cfg["whisper"]["model"] = str(real / "models" / "large-v3-turbo")
(home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
os.environ["LOCALFLOW_HOME"] = str(home)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
faulthandler.enable(all_threads=True)
from localflow.app import App, setup_logging  # noqa: E402
from localflow.hotkeys import HotkeyEvent  # noqa: E402

setup_logging()
app = App()


def drive():
    app.transcriber.ready.wait(120)
    print("model ready; driving", flush=True)
    for i in range(6):
        app.events.put(HotkeyEvent.TOGGLE)       # start hands-free
        time.sleep(1.5)
        app.events.put(HotkeyEvent.TOGGLE if i % 3 else HotkeyEvent.CANCEL)
        time.sleep(2.5)
        print(f"cycle {i + 1} ok", flush=True)
    app.overlay.flash("Smoke test done", "#34d399", 1)
    time.sleep(1.5)
    print("SMOKE PASS", flush=True)
    app.quit()


threading.Thread(target=drive, daemon=True).start()
app.run()
shutil.rmtree(home, ignore_errors=True)
