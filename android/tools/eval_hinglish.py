"""Score an on-device Hinglish eval run against the expected transcripts.

    python tools/eval_hinglish.py <label> <whisper-model-file> <lang> <noprompt:true|false>

Pushes nothing: the WAVs must already be in /data/local/tmp/eval (see tools/ for how they were made).
Prints each clip's output and the word error rate (lower is better) and speed (RTF = seconds of
compute per second of audio).
"""
import json
import os
import re
import subprocess
import sys
import time

ADB = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
ENV = dict(os.environ, MSYS_NO_PATHCONV="1")
EXPECTED = r"C:\Users\parth\lf-models\hf\expected.json"


def adb(*a):
    return subprocess.run([ADB, *a], capture_output=True, text=True, encoding="utf-8", env=ENV).stdout


def norm(s: str) -> list[str]:
    s = s.lower().replace("-", " ")
    return re.findall(r"[a-z0-9']+", s)


def wer(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)], len(ref)


def main(label, model, lang, noprompt, fullctx="false", beam="1", mode="HINGLISH", threads="0"):
    adb("shell", "am", "start", "-n", "app.localflow/.MainActivity")
    time.sleep(2)
    adb("logcat", "-c")
    adb("shell", "am", "broadcast", "-n", "app.localflow/.BenchReceiver", "-a", "app.localflow.EVAL",
        "--es", "dir", "/data/local/tmp/eval", "--es", "whisper", model, "--es", "lang", lang,
        "--ez", "noprompt", noprompt, "--ez", "fullctx", fullctx, "--ei", "beam", beam, "--es", "mode", mode, "--ei", "threads", threads)
    out = ""
    for _ in range(300):
        time.sleep(2)
        out = adb("logcat", "-d", "-s", "LF-EVAL")
        if "TOTAL" in out:
            break
    exp = json.load(open(EXPECTED, encoding="utf-8"))
    by_stem = {}
    for k, v in exp.items():
        by_stem.setdefault(os.path.splitext(os.path.basename(k))[0][:40], []).append(v)
    errs = words = 0
    print(f"=== {label}")
    seen = {}
    for line in out.splitlines():
        m = re.search(r"LF-EVAL\s*:\s*(\S+) \| (\d+)ms \| (.*)$", line)
        if not m:
            t = re.search(r"TOTAL (.*)", line)
            if t:
                print("   ", t.group(1))
            continue
        stem, ms, text = m.groups()
        cands = by_stem.get(stem)
        if not cands:
            for k, v in by_stem.items():
                if k.startswith(stem) or stem.startswith(k):
                    cands = v
        if cands:
            i = seen.get(stem, 0)
            ref = cands[min(i, len(cands) - 1)]
            seen[stem] = i + 1
            e, n = wer(norm(ref), norm(text))
            errs += e
            words += n
            print(f"  {e}/{n}  {text[:70]}   <= {ref[:50]}")
    print(f"  WER {errs}/{words} = {100 * errs / max(1, words):.0f}%")


if __name__ == "__main__":
    main(*sys.argv[1:])
