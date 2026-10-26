"""Exercise voice-edit prompts against the built-in engine."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.cleanup import Cleaner  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402
from localflow.context import FocusContext  # noqa: E402

c = Cleaner(dict(DEFAULTS["cleanup"]))
c.warm_up().join(180)
ctx = FocusContext(exe="outlook.exe", title="Message")
cases = [
    ("hey can u send me the files by tmrw, need them for the client thing", "make this more formal"),
    ("The meeting is moved to Thursday at 4 pm.", "translate to Hindi"),
    ("we need more testing, better docs and a new logo before launch", "turn this into bullet points"),
    ("", "write a short polite email declining the meeting on Friday"),
]
for sel, instr in cases:
    t0 = time.perf_counter()
    out = c.rewrite(sel, instr, ctx, "formal")
    print(f"--- {instr!r} ({int((time.perf_counter() - t0) * 1000)} ms)\n{out}\n")
c.shutdown()
