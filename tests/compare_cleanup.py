"""Run the user's real raw transcripts through cleanup models side by side.

Usage: python tests/compare_cleanup.py qwen2.5:3b qwen2.5:1.5b
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow import polish  # noqa: E402
from localflow.cleanup import Cleaner  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402
from localflow.context import FocusContext  # noqa: E402

# Raw Whisper output from the user's live test session (history.db).
CASES = [
    ("Um so I think we should, uh, ship the new release on Friday, no wait, on Thursday.",
     "ship the new release on thursday"),
    ("Let's meet at five p.m. No wait at six p.m. tomorrow.", "six p.m. tomorrow|6 p.m. tomorrow"),
    ("Send the file to John, scratch that, send it to Priya instead.", "priya"),
    ("I need 3 things, first milk, second bread and third eggs.", "1."),
    ("Can you send me the updated report before the meeting?", "?"),
    ("Yesterday I went to the market with my friends. We bought some vegetables and fruits and then we came "
     "back home and cooked dinner together.", "dinner together"),
    ("hey can you uh check the deployment logs and like tell me if the build passed", "build passed"),
    ("What's the capital of France", "capital of france"),
]

ctx = FocusContext(exe="notepad.exe", title="Untitled")
for model in sys.argv[1:]:
    # "builtin" = bundled llama.cpp engine; anything else = an Ollama model name
    backend = "builtin" if model == "builtin" else "ollama"
    c = Cleaner(dict(DEFAULTS["cleanup"], backend=backend, model=model, timeout_seconds=30))
    c.warm_up().join(180)
    ok, times = 0, []
    print(f"\n=== {model} ===")
    for raw, must in CASES:
        text = polish.pre_clean(raw, DEFAULTS)
        t0 = time.perf_counter()
        out, _ms = c.clean(text, ctx, "neutral", [])
        times.append((time.perf_counter() - t0) * 1000)
        final = out or text
        low = final.lower()
        # "a|b" = any alternative must appear; also reject leftovers of the correction.
        good = (out is not None and any(m in low for m in must.lower().split("|"))
                and "no wait" not in low and "john" not in low and "paris" not in low)
        ok += good
        print(f"{'OK ' if good else 'BAD'} {final!r}")
    print(f"--> {ok}/{len(CASES)} good, avg {sum(times) / len(times):.0f} ms")
    c.shutdown()
