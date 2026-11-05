"""Formatting quality + latency of the cleanup engine on realistic dictations.

Usage: python tests/bench_quality.py [builtin|cloud|<ollama model>]
Prints each output and checks it for the formatting a writer would expect.
"""
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow import polish  # noqa: E402
from localflow.cleanup import Cleaner  # noqa: E402
from localflow.config import DEFAULTS, Config  # noqa: E402
from localflow.context import FocusContext  # noqa: E402

CASES = [
    ("neutral", "Um so I think we should, uh, ship the new release on Friday, no wait, on Thursday.",
     lambda o: "Thursday" in o and "Friday" not in o),
    ("neutral", "can you check whether the build passed on the staging server",
     lambda o: o.rstrip().endswith("?")),
    ("neutral", "What's the capital of France", lambda o: "?" in o and "Paris" not in o),
    ("neutral", "okay so for the meeting tomorrow we need to cover three things first the budget for next "
                "quarter second the hiring plan and third the office move",
     lambda o: len(re.findall(r"(?m)^\s*(?:\d\.|-)\s", o)) == 3),
    ("neutral", "so yesterday I finally got the new server set up it took a while because the drivers were "
                "outdated and I had to reinstall them twice but it is working now and the backups are running "
                "every night anyway the next thing I want to do is move the database over and then test the "
                "failover before we tell the team",
     lambda o: "\n\n" in o and o.count(".") >= 3),
    ("casual", "hey are you free tonight we could grab dinner at like eight",
     lambda o: "?" in o),
    ("notes", "notes from the call the client wants the launch moved to march they are worried about the "
              "payment integration and they want weekly updates also they asked about a discount for the first year",
     lambda o: o.count("\n- ") + o.startswith("- ") >= 3),
    ("prompt", "okay so I want you to write a python script that goes through my downloads folder and sorts "
               "everything into subfolders by file type like images documents videos and so on and it should "
               "skip files that are still downloading and um log what it moved to a text file and it has to work "
               "on windows",
     lambda o: "##" in o and "download" in o.lower() and "log" in o.lower() and "windows" in o.lower()),
    ("prompt", "I have a react app and the page is really slow when the list has like a thousand items can you "
               "figure out why and suggest a fix I am using redux and the list items have images",
     lambda o: "react" in o.lower() and "redux" in o.lower() and "1,000" in o or "thousand" in o.lower()),
]

backend = sys.argv[1] if len(sys.argv) > 1 else "builtin"
cfg = dict(Config().snapshot()["cleanup"], timeout_seconds=30)
cfg["backend"] = backend if backend in ("builtin", "cloud") else "ollama"
if cfg["backend"] == "ollama":
    cfg["model"] = backend
c = Cleaner(cfg)
c.warm_up().join(180)
print("engine:", c.model_label, "available:", c.available, c.problem or "")
ctx = FocusContext(exe="notepad.exe", title="Untitled")
ok, times = 0, []
for style, raw, check in CASES:
    text = polish.pre_clean(raw, DEFAULTS)
    t0 = time.perf_counter()
    out, _ = c.clean(text, ctx, style, [])
    times.append((time.perf_counter() - t0) * 1000)
    good = out is not None and check(out)
    ok += good
    print(f"\n[{'OK ' if good else 'BAD'}] {style} {times[-1]:.0f} ms\n{out if out is not None else '(rejected) ' + text}")
print(f"\n==> {ok}/{len(CASES)} good, median {statistics.median(times):.0f} ms, max {max(times):.0f} ms")
c.shutdown()
