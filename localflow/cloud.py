"""Optional cloud AI cleanup: OpenRouter, Google Gemini, Groq, Cerebras or OpenAI.

Paste one API key; the provider is recognised from the key itself. LocalFlow
then lists the models that key can use, keeps a shortlist of fast, small,
instruction-following models, and benchmarks them on real dictation cases.
The most accurate model wins, and the fastest breaks a tie. Re-run it any
time from Settings > Models & AI.

The key is stored in Windows Credential Manager, never in config.json. Only
the transcript and the app's name are sent, never the text on your screen.
"""
from __future__ import annotations

import logging
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

CRED_TARGET = "LocalFlow:cloud-api-key"


@dataclass
class Provider:
    name: str
    label: str
    base: str
    # Ordered preferences: regexes over model ids. Earlier = more suitable
    # (fast, cheap, good at following editing rules).
    prefer: list[str]
    headers: dict = field(default_factory=dict)


PROVIDERS = {
    "openrouter": Provider(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
        [r"^google/gemini-[\d.]+-flash-lite$", r"^anthropic/claude-haiku-[\d.]+$", r"^openai/gpt-[\d.]+-luna$",
         r"^google/gemini-[\d.]+-flash$", r"^qwen/qwen[\d.]+-flash$", r"^deepseek/deepseek-v[\d.]+-flash$",
         r"^z-ai/glm-[\d.]+-flash$", r"^openai/gpt-[\d.]+-mini$", r"^mistralai/mistral-small",
         r"^meta-llama/llama-4-scout"],
        {"HTTP-Referer": "https://github.com/Partharsid/local-flow", "X-Title": "LocalFlow"}),
    "gemini": Provider(
        "gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
        [r"^gemini-[\d.]+-flash-lite(-preview[\w-]*)?$", r"^gemini-[\d.]+-flash(-preview[\w-]*)?$",
         r"^gemini-flash-lite-latest$", r"^gemini-flash-latest$"]),
    "groq": Provider(
        "groq", "Groq", "https://api.groq.com/openai/v1",
        [r"llama-4-scout", r"llama-3\.3-70b-versatile", r"qwen", r"gpt-oss-20b", r"llama-3\.1-8b-instant"]),
    "cerebras": Provider(
        "cerebras", "Cerebras", "https://api.cerebras.ai/v1",
        [r"llama-4-scout", r"qwen-3", r"llama-3\.3-70b", r"gpt-oss", r"llama3\.1-8b"]),
    "openai": Provider(
        "openai", "OpenAI", "https://api.openai.com/v1",
        [r"^gpt-[\d.]+-luna$", r"^gpt-[\d.]+-nano$", r"^gpt-[\d.]+-mini$"]),
}
# Never useful for text cleanup (or not chat models at all).
_EXCLUDE = re.compile(r"(?i)(image|vision|-vl|omni|audio|tts|transcri|embed|live|realtime|search|:batch|:free|"
                      r"guard|legal|code|-exp|thinking|reasoning|banana|whisper|moderation|-pro\b)")


def detect_provider(key: str) -> str | None:
    key = key.strip()
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("csk-"):
        return "cerebras"
    if key.startswith("sk-"):
        return "openai"
    return None


# -- key storage ---------------------------------------------------------------
def save_key(key: str) -> None:
    import win32cred
    win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": CRED_TARGET, "UserName": "LocalFlow",
                         "CredentialBlob": key.strip(), "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)


def load_key() -> str:
    try:
        import win32cred
        blob = win32cred.CredRead(CRED_TARGET, win32cred.CRED_TYPE_GENERIC)["CredentialBlob"]
        return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
    except Exception:  # noqa: BLE001 - not set
        return ""


def delete_key() -> None:
    try:
        import win32cred
        win32cred.CredDelete(CRED_TARGET, win32cred.CRED_TYPE_GENERIC)
    except Exception:  # noqa: BLE001
        pass


# -- API -------------------------------------------------------------------------
class CloudClient:
    def __init__(self, provider: str, key: str, model: str = ""):
        self.p = PROVIDERS[provider]
        self.key = key
        self.model = model
        self.session = requests.Session()
        self._no_reasoning_param: set[str] = set()   # models that reject our "don't think" hint

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", **self.p.headers}

    def list_models(self) -> list[dict]:
        r = self.session.get(f"{self.p.base}/models", headers=self._headers(), timeout=15)
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            mid = str(m.get("id", "")).removeprefix("models/")
            out.append({"id": mid, "created": m.get("created") or 0})
        return out

    def _payload(self, model: str, messages, max_tokens: int, temperature: float, hint: bool) -> dict:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if self.p.name == "openrouter":
            body["provider"] = {"sort": "latency"}       # route to the fastest host of this model
            if hint:
                body["reasoning"] = {"enabled": False}
        elif self.p.name == "gemini" and hint:
            body["reasoning_effort"] = "none"
        elif self.p.name == "openai" and hint:
            body["reasoning_effort"] = "minimal"
        return body

    def chat(self, messages: list[dict], max_tokens: int, timeout: float, temperature: float = 0.0,
             model: str | None = None) -> str:
        model = model or self.model
        if not model:
            raise RuntimeError("No cloud model chosen yet")
        hint = model not in self._no_reasoning_param
        r = self.session.post(f"{self.p.base}/chat/completions", headers=self._headers(), timeout=timeout,
                              json=self._payload(model, messages, max_tokens, temperature, hint))
        if r.status_code == 400 and hint:
            # Some models must think, or don't know the parameter: ask plainly.
            self._no_reasoning_param.add(model)
            r = self.session.post(f"{self.p.base}/chat/completions", headers=self._headers(), timeout=timeout,
                                  json=self._payload(model, messages, max_tokens, temperature, False))
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        text = msg.get("content") or ""
        if isinstance(text, list):                      # content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        return re.sub(r"(?s)^\s*<think>.*?</think>\s*", "", text).strip()


def shortlist(provider: str, models: list[dict], limit: int = 6) -> list[str]:
    """The newest model of each preferred family, in preference order."""
    p = PROVIDERS[provider]
    picked: list[str] = []
    for pattern in p.prefer:
        fam = [m for m in models if re.search(pattern, m["id"]) and not _EXCLUDE.search(m["id"])]
        if not fam:
            continue

        def version(m):
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", m["id"].split("/")[-1])[:2]] or [0.0]
            return ("preview" not in m["id"], m["created"], nums)   # stable beats preview
        best = max(fam, key=version)["id"]
        if best not in picked:
            picked.append(best)
        if len(picked) >= limit:
            break
    return picked


# Cases the cleanup must get right; each check sees the model's output.
BENCH_CASES = [
    ("Um so I think we should, uh, ship the new release on Friday, no wait, on Thursday.",
     lambda o: "thursday" in o.lower() and "friday" not in o.lower() and "wait" not in o.lower()),
    ("what's the capital of France",
     lambda o: "?" in o and "paris" not in o.lower()),
    ("I need three things first milk second bread and third eggs",
     lambda o: bool(re.search(r"(?m)^\s*(1\.|-|\*)\s*milk", o, re.I))),
]


@dataclass
class BenchResult:
    model: str
    passed: int
    latency_ms: float
    error: str = ""


def benchmark(client: CloudClient, models: list[str], build_messages, judge) -> list[BenchResult]:
    """Score each model on BENCH_CASES. build_messages(raw) -> messages;
    judge(raw, out) -> True when the guard rails accept the output."""
    def run(model: str) -> BenchResult:
        passed, times = 0, []
        try:
            for raw, check in BENCH_CASES:
                t0 = time.perf_counter()
                out = client.chat(build_messages(raw), 200, timeout=20, model=model)
                times.append((time.perf_counter() - t0) * 1000)
                passed += bool(out) and judge(raw, out) and check(out)
            # Second timing pass on one case: the first request often includes a cold start.
            t0 = time.perf_counter()
            client.chat(build_messages(BENCH_CASES[0][0]), 200, timeout=20, model=model)
            times.append((time.perf_counter() - t0) * 1000)
            return BenchResult(model, passed, statistics.median(times[1:]))
        except Exception as e:  # noqa: BLE001
            return BenchResult(model, -1, 1e9, str(e)[:120])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, models))
    results.sort(key=lambda r: (-r.passed, r.latency_ms))
    for r in results:
        log.info("Cloud benchmark %s: %s/%d passed, %.0f ms %s", r.model, r.passed, len(BENCH_CASES),
                 r.latency_ms, r.error)
    return results


def auto_select(key: str, build_messages, judge, progress=None) -> tuple[str, str, list[BenchResult]]:
    """Detect the provider, shortlist and benchmark its models.
    Returns (provider, best model, all results). Raises with a readable message."""
    provider = detect_provider(key)
    if not provider:
        raise ValueError("Unrecognised key. Use an OpenRouter (sk-or-…), Gemini (AIza…), Groq (gsk_…), "
                         "Cerebras (csk-…) or OpenAI (sk-…) key.")
    client = CloudClient(provider, key)
    if progress:
        progress(f"Checking your {PROVIDERS[provider].label} key…")
    try:
        models = client.list_models()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 0
        raise ValueError("The key was rejected" if code in (401, 403) else f"{PROVIDERS[provider].label} "
                         f"answered {code}") from e
    candidates = shortlist(provider, models)
    if not candidates:
        raise ValueError("No suitable fast model is available for this key")
    if progress:
        progress(f"Testing {len(candidates)} models for speed and accuracy…")
    results = benchmark(client, candidates, build_messages, judge)
    best = next((r for r in results if r.passed >= 0), None)
    if best is None:
        raise ValueError(f"Every model failed: {results[0].error}")
    return provider, best.model, results
