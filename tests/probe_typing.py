"""Diagnose type_text(): types a known string into a fresh Notepad file with
several delays and reads back what arrived."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow import context as ctxmod  # noqa: E402
from localflow.inserter import type_text  # noqa: E402

SAMPLE = "Status update for the team: quick brown fox."


def read_doc() -> str:
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        tp = auto.GetFocusedControl().GetPattern(auto.PatternId.TextPattern)
        return tp.DocumentRange.GetText(-1)


target = Path(tempfile.gettempdir()) / f"localflow_typeprobe_{int(time.time())}.txt"
target.write_text("", encoding="utf-8")
subprocess.Popen(["notepad.exe", str(target)])
for _ in range(100):
    if target.name.lower() in ctxmod.foreground_window()[2].lower():
        break
    time.sleep(0.1)
else:
    sys.exit("test file not in front; aborting")
time.sleep(0.8)
for delay in (0.004, 0.02, 0.05):
    before = read_doc()
    type_text(SAMPLE + "\n", delay=delay)
    time.sleep(0.5)
    got = read_doc()[len(before):].strip()
    print(f"delay {delay*1000:>4.0f} ms: {'OK ' if got == SAMPLE else 'BAD'} {got!r}")
