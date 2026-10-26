"""Measure the built-in engine: prompt vs generation speed (llama-server timings)."""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.cleanup import FEWSHOT, SYSTEM  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402
from localflow.llm_server import LlamaServer  # noqa: E402

srv = LlamaServer()
srv.start(DEFAULTS["cleanup"]["builtin_model"])
print("device:", srv.device)
msgs = [{"role": "system", "content": SYSTEM}]
for a, b in FEWSHOT:
    msgs += [{"role": "user", "content": a}, {"role": "assistant", "content": b}]
for i, text in enumerate(["um so I think we should ship it on friday no wait thursday",
                          "can you send me the report",
                          "let's meet at five no six tomorrow"]):
    r = requests.post(f"{srv.url}/v1/chat/completions", json={
        "messages": msgs + [{"role": "user", "content": text}], "temperature": 0, "max_tokens": 80,
        "cache_prompt": True}, timeout=60).json()
    t = r.get("timings", {})
    print(f"run {i + 1}: prompt {t.get('prompt_n')} tok @ {t.get('prompt_per_second', 0):.0f} tok/s "
          f"({t.get('prompt_ms', 0):.0f} ms), gen {t.get('predicted_n')} tok @ "
          f"{t.get('predicted_per_second', 0):.0f} tok/s ({t.get('predicted_ms', 0):.0f} ms)")
srv.stop()
