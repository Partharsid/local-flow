"""Run real-world dictations through the cleanup engine and show every output.

Usage: python tests/bench_real.py [builtin|cloud] [style ...]
Cases come from long, messy instructions people actually dictate into AI
apps and chat boxes; the point is to read the results, and the guard rails'
verdicts, side by side.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.cleanup import Cleaner, drop_invented_lines, output_is_faithful, prompt_is_faithful  # noqa: E402
from localflow.config import Config  # noqa: E402
from localflow.context import FocusContext  # noqa: E402

CASES = [
    "Yeah, so the folder called, I guess, the folder's name is Lalitha Mobile or LMRS, something like that. Open "
    "that folder and there was a previous Cloud session going on in that folder and analyze what has been done and "
    "what more is pending and everything. Like, get all the context from that Cloud chat to the, a new chat in that.",
    "Then you go to all the pending things like the video recording part and the forms and everything. And you just "
    "complete it all. And check whether the Google sign is working. If it's not working you can go to my website. "
    "You can tell me what to do in Firebase and everything.",
    "There is a folder called RRDowntown and there is also a repo called RRDowntown website or something like that. "
    "Go to it and visit that website first rradowntownrk.in. The client told me that there is an issue actually "
    "about the slots booking. They are booking the slots but they aren't filling. Check that thing, check for the "
    "logs. there is also a folder called server architecture rebuild once analyze it fully and you will know how "
    "to deploy the website using the ARGO CD so that it has no downtime and add it to the mesh network and add the "
    "changes to the cluster. Fix that issue and then deploy it again.",
    "Yeah, is there any alternative way so that we cannot reserve space in RAM and also run the model at the same "
    "speed or something like that.",
    "okay so for tomorrow I need to finish the pitch deck then call the bank about the loan and also buy groceries "
    "like milk eggs and bread and if I have time go to the gym",
    "hey man so I was thinking we could meet on Saturday instead of Friday because Friday I have a class till six "
    "what do you think",
    "So the main thing about the product is that it saves time. People don't have to crack eggs anymore. It's also "
    "hygienic because it's pasteurized. And the third point is that it lasts for two weeks in the fridge. We want "
    "to sell it to restaurants first and then to homes.",
]


def main():
    backend = sys.argv[1] if len(sys.argv) > 1 else "builtin"
    styles = sys.argv[2:] or ["neutral", "prompt"]
    cfg = Config().snapshot()["cleanup"]
    c = Cleaner(dict(cfg, backend=backend))
    c.warm_up().join(180)
    print("engine:", c.model_label, "available:", c.available, c.problem or "")
    ctx = FocusContext(exe="claude.exe", title="Claude")
    for style in styles:
        for raw in CASES:
            t0 = time.perf_counter()
            msgs = c.build_messages(raw, ctx, style, [])
            growth = 2.2 if style == "prompt" else 1.0
            out = c._chat(msgs, min(2048, int(len(raw) / 2.5 * growth) + 96), 60)
            ms = (time.perf_counter() - t0) * 1000
            from localflow.cleanup import _strip_wrapping
            out = _strip_wrapping(out)
            if style == "prompt":
                out = drop_invented_lines(raw, out)
            ok = prompt_is_faithful(raw, out) if style == "prompt" else output_is_faithful(raw, out)
            print(f"\n===== {style} | {ms:.0f} ms | guard {'OK' if ok else 'REJECTED'}\nRAW: {raw[:160]}\n---\n{out}")
    c.shutdown()


if __name__ == "__main__":
    main()
