"""Make LocalFlow's dictation model better from your own corrections.

    training\\.venv\\Scripts\\python.exe training\\retrain.py            # full run (~1-2 h on an RTX 4060)
    training\\.venv\\Scripts\\python.exe training\\retrain.py --check    # just show how much data there is

1. Reads the dictations LocalFlow saved (%APPDATA%\\LocalFlow\\training)
   and keeps the ones you corrected; a few are held back for testing.
2. Makes a fresh synthetic dataset and trains a new model on everything.
3. Converts it to GGUF and quantizes it.
4. Tests the new and the installed model on the same held-out examples, and
   installs the new one only if it scores higher.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP = Path(os.environ["APPDATA"]) / "LocalFlow"
LLM_DIR = APP / "models" / "llm"
QUANT = APP / "runtime" / "llama" / "llama-quantize.exe"
PY = sys.executable
APP_PY = ROOT / ".venv" / "Scripts" / "python.exe"
MODEL_NAME = "localflow-dictation-1.7b-Q5_K_M.gguf"


def corrections() -> tuple[list[dict], list[dict]]:
    sys.path.insert(0, str(ROOT))
    from localflow import trainlog
    rows = [r for r in trainlog.load() if r["corrected"]]
    random.Random(7).shuffle(rows)
    k = max(1, len(rows) // 7) if len(rows) >= 14 else 0
    return rows[k:], rows[:k]


def run(*args, **kw):
    print(">", " ".join(map(str, args)), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


def score(name: str) -> float:
    line = (HERE / "runs" / f"score-{name}.txt").read_text()
    return float(re.search(r"ALL\s+n=\d+\s+sim ([\d.]+)", line).group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--base", default=str(HERE / "models" / "Qwen3-1.7B"))
    ap.add_argument("--epochs", default="2")
    a = ap.parse_args()
    train, test = corrections()
    print(f"{len(train)} corrected dictations for training, {len(test)} held back for testing")
    if a.check:
        return
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    with open(data / "user_corrections.jsonl", "w", encoding="utf-8") as f:
        for r in train:
            f.write(json.dumps({k: r[k] for k in ("mode", "app", "vocab", "input", "output")}, ensure_ascii=False) + "\n")
    with open(data / "user_eval.jsonl", "w", encoding="utf-8") as f:
        for r in test:
            f.write(json.dumps({k: r[k] for k in ("mode", "app", "vocab", "input", "output")}, ensure_ascii=False) + "\n")
    seed = str(random.randint(2, 10_000))
    run(PY, HERE / "gen_data.py", "--n", "16000", "--seed", seed, "--out", data / "synth_train.jsonl",
        "--exclude", data / "synth_eval.jsonl")
    out = HERE / "runs" / "retrain"
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    run(PY, HERE / "train.py", "--base", a.base, "--out", out, "--epochs", a.epochs, "--tokens", "4096", "--accum", "4",
        env=env)
    run(PY, HERE / "convert_gguf.py", out / "merged", out / "f16.gguf")
    run(QUANT, out / "f16.gguf", out / MODEL_NAME, "Q5_K_M")
    (out / "f16.gguf").unlink()
    shutil.rmtree(out / "merged")
    for name, path in (("new", out / MODEL_NAME), ("installed", LLM_DIR / MODEL_NAME)):
        if not path.exists():
            continue
        r = subprocess.run([str(APP_PY), str(HERE / "evaluate.py"), "--gguf", str(path), "--name", name,
                            "--user"], capture_output=True, text=True, check=True)
        (HERE / "runs" / f"score-{name}.txt").write_text(r.stdout)
        print(r.stdout)
    new = score("new")
    old = score("installed") if (LLM_DIR / MODEL_NAME).exists() else 0.0
    if new >= old:
        if (LLM_DIR / MODEL_NAME).exists():
            shutil.copy2(LLM_DIR / MODEL_NAME, LLM_DIR / (MODEL_NAME + ".previous"))
        shutil.copy2(out / MODEL_NAME, LLM_DIR / MODEL_NAME)
        print(f"Installed the new model ({new:.3f} vs {old:.3f}). Restart LocalFlow to use it.")
    else:
        print(f"Kept the installed model: the new one scored lower ({new:.3f} vs {old:.3f}).")


if __name__ == "__main__":
    main()
