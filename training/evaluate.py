"""Score a cleanup model on the held-out sets (accuracy and speed).

    python training/evaluate.py --gguf runs/q17/model-Q5_K_M.gguf
    python training/evaluate.py --baseline          # the current Qwen3-4B with the long prompt

Run with LocalFlow's own venv (it imports localflow). Prints a table and
writes every output to runs/eval-<name>.jsonl so they can be read.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import socket
import subprocess
import statistics
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import gold  # noqa: E402
from localflow.tuned import END, build_prompt  # noqa: E402

LLAMA = Path(os.environ["APPDATA"]) / "LocalFlow" / "runtime" / "llama"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(model: Path):
    from localflow.llm_server import pick_device
    port = free_port()
    args = [str(LLAMA / "llama-server.exe"), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-ngl", "99", "-c", "4096", "-np", "1", "--no-webui", "--log-disable"]
    dev = pick_device(LLAMA / "llama-server.exe")
    if dev:
        args += ["--device", dev, "-sm", "none"]
    p = subprocess.Popen(args, cwd=str(LLAMA), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(240):
        try:
            if requests.get(url + "/health", timeout=1).json().get("status") == "ok":
                return p, url
        except (requests.RequestException, ValueError):
            pass
        if p.poll() is not None:
            raise RuntimeError(f"llama-server exited {p.returncode}")
        time.sleep(0.5)
    raise TimeoutError


def norm(s: str) -> str:
    return "\n".join(" ".join(l.split()) for l in s.strip().splitlines())


def shape(s: str) -> tuple:
    """Layout only: which lines are bullets, numbers, headings or blank."""
    out = []
    for l in s.strip().splitlines():
        l = l.strip()
        out.append("-" if l.startswith("- ") else "1" if l[:2].rstrip(".").isdigit() and ". " in l[:4]
                   else "" if not l else "h" if l.endswith(":") else "t")
    return tuple(out)


def cases(limit: int, user_too: bool = False):
    rows = [dict(mode=m, app=a, vocab=v, input=i, output=o, src="gold") for m, a, v, i, o in gold.E]
    synth = [json.loads(l) for l in open(HERE / "data" / "synth_eval.jsonl", encoding="utf-8")]
    rows += [dict(r, src="synth") for r in synth[:limit]]
    user = HERE / "data" / "user_eval.jsonl"
    if user_too and user.exists():
        rows += [dict(json.loads(l), src="user") for l in open(user, encoding="utf-8")]
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--synth", type=int, default=300)
    ap.add_argument("--name")
    ap.add_argument("--user", action="store_true", help="also test on your held-back corrections")
    a = ap.parse_args()
    if a.baseline:
        from localflow.cleanup import Cleaner, _strip_wrapping
        from localflow.context import FocusContext
        model = LLAMA.parent.parent / "models" / "llm" / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        cl = Cleaner({"backend": "builtin"})
    else:
        model = Path(a.gguf).resolve()
    name = a.name or ("baseline-4b" if a.baseline else model.stem)
    proc, url = start(model)
    s = requests.Session()
    res = []
    try:
        rows = cases(a.synth, a.user)
        for r in rows[:2]:     # warm up
            s.post(url + "/completion", json={"prompt": "hi", "n_predict": 1})
        for r in rows:
            n = min(1024, int(len(r["input"]) / 2.5 * (2.2 if r["mode"] == "prompt" else 1.2)) + 96)
            t0 = time.perf_counter()
            if a.baseline:
                msgs = cl.build_messages(r["input"], FocusContext(exe=r["app"], title=""), r["mode"], r["vocab"])
                j = s.post(url + "/v1/chat/completions", json={"messages": msgs, "max_tokens": n, "temperature": 0,
                                                              "cache_prompt": True}).json()
                out = _strip_wrapping(j["choices"][0]["message"]["content"] or "")
                ntok = j.get("usage", {}).get("completion_tokens", 0)
            else:
                j = s.post(url + "/completion", json={"prompt": build_prompt(r["mode"], r["app"], r["vocab"], r["input"]),
                                                      "n_predict": n, "temperature": 0, "stop": [END],
                                                      "cache_prompt": True}).json()
                out = j["content"].strip()
                ntok = j.get("tokens_predicted", 0)
            ms = (time.perf_counter() - t0) * 1000
            sim = difflib.SequenceMatcher(None, norm(out), norm(r["output"])).ratio()
            res.append(dict(r, got=out, ms=ms, tokens=ntok, sim=sim, exact=norm(out) == norm(r["output"]),
                            layout=shape(out) == shape(r["output"])))
    finally:
        proc.kill()
    out_path = HERE / "runs" / f"eval-{name}.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in res:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def line(label, rs):
        if not rs:
            return
        print(f"{label:<12} n={len(rs):<4} sim {statistics.mean(r['sim'] for r in rs):.3f}  "
              f"exact {sum(r['exact'] for r in rs) / len(rs):.0%}  layout {sum(r['layout'] for r in rs) / len(rs):.0%}  "
              f"ms {statistics.mean(r['ms'] for r in rs):.0f} (p90 {sorted(r['ms'] for r in rs)[int(len(rs) * .9) - 1]:.0f})")
    print(f"== {name}")
    line("ALL", res)
    line("gold", [r for r in res if r["src"] == "gold"])
    line("synth", [r for r in res if r["src"] == "synth"])
    for m in ("neutral", "casual", "formal", "technical", "notes", "prompt"):
        line(m, [r for r in res if r["mode"] == m])
    toks = sum(r["tokens"] for r in res)
    print(f"speed: {toks / (sum(r['ms'] for r in res) / 1000):.0f} tokens/s overall")


if __name__ == "__main__":
    main()
