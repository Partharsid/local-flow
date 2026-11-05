"""Prompt/notes modes, AI-app detection, cloud model selection, streaming STT."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from localflow import cloud, polish
from localflow.cleanup import Cleaner, drop_invented_lines, output_is_faithful, prompt_is_faithful
from localflow.config import DEFAULTS, _merge, _migrate
from localflow.context import FocusContext
from localflow.streaming import ChunkedTranscriber, find_pause


# --- modes -------------------------------------------------------------------
@pytest.mark.parametrize("text,style,rest", [
    ("Prompt mode, build me a todo app", "prompt", "Build me a todo app"),
    ("Okay, as a prompt. Fix the login bug", "prompt", "Fix the login bug"),
    ("Bullet points: milk, bread and eggs", "notes", "Milk, bread and eggs"),
    ("Email mode. hi John, the invoice is attached", "formal", "Hi John, the invoice is attached"),
    ("Verbatim, ls -la", "verbatim", "ls -la"),
    ("I like bullet points a lot", None, "I like bullet points a lot"),
    ("prompt mode", None, "prompt mode"),                 # nothing after it: keep as text
])
def test_spoken_mode(text, style, rest):
    assert polish.spoken_mode(text) == (style, rest)


@pytest.mark.parametrize("exe,title,kind", [
    ("claude.exe", "Claude", "ai"),
    ("chrome.exe", "ChatGPT - Google Chrome", "ai"),
    ("windowsterminal.exe", "✳ Fix the login bug", "ai"),       # Claude Code sets this title
    ("windowsterminal.exe", "claude", "ai"),
    ("windowsterminal.exe", "PowerShell", "terminal"),
    ("windowsterminal.exe", "claudette-server logs", "terminal"),
    ("slack.exe", "general", "chat"),
])
def test_ai_app_detection(exe, title, kind):
    assert polish.app_kind(FocusContext(exe=exe, title=title), DEFAULTS) == kind


def test_ai_apps_default_to_prompt_mode():
    assert polish.style_for(FocusContext(exe="claude.exe", title="Claude"), DEFAULTS) == "prompt"


RAW = ("I have a react app and the page is really slow when the list has like a thousand items can you figure out "
       "why and suggest a fix I am using redux and the list items have images")


def test_prompt_guard_accepts_structured_rewrite():
    out = ("Find out why my React app is slow when a list has 1,000 items, and suggest a fix.\n\n## Context\n"
           "- I'm using Redux.\n- The list items have images.")
    assert prompt_is_faithful(RAW, out)


def test_prompt_guard_rejects_an_answer():
    answer = ("Use virtualization with react-window so only visible rows render, memoize row components with "
              "React.memo, lazy-load thumbnails via IntersectionObserver, and normalize selectors with reselect "
              "to prevent unnecessary re-renders across the component tree.")
    assert not prompt_is_faithful(RAW, answer)


def test_invented_constraints_are_dropped():
    out = ("Find out why the React app is slow with 1,000 items and suggest a fix.\n\n## Context\n- It uses Redux.\n"
           "\n## Constraints\n- Do not suggest removing Redux or refactoring the entire app.\n"
           "- Avoid server-side rendering unless necessary.")
    cleaned = drop_invented_lines(RAW, out)
    assert "Constraints" not in cleaned and "Redux" in cleaned


def test_spoken_negation_is_kept():
    raw = "write a script that renames my photos and don't touch the raw files"
    out = "Write a script that renames my photos.\n\n## Constraints\n- Don't touch the raw files."
    assert "raw files" in drop_invented_lines(raw, out)


def test_paragraph_and_bullets_pass_dictation_guard():
    raw = "for the trip we should pack sunscreen the chargers our passports and some snacks"
    assert output_is_faithful(raw, "For the trip, we should pack:\n- Sunscreen\n- The chargers\n- Our passports\n"
                                   "- Some snacks")


def test_config_migration_updates_old_defaults():
    user = {"whisper": {"beam_size": 5}, "app_styles": {"chat": "formal"}}
    data = _migrate(_merge(DEFAULTS, user), user)
    assert data["whisper"]["beam_size"] == 2 and data["app_styles"]["ai"] == "prompt"
    assert data["app_styles"]["chat"] == "formal"
    user2 = dict(user, config_version=2, app_styles={"ai": "neutral"})
    assert _migrate(_merge(DEFAULTS, user2), user2)["app_styles"]["ai"] == "neutral"


# --- streaming STT -------------------------------------------------------------
def test_find_pause_and_chunking():
    rng = np.random.default_rng(0)
    a = (rng.standard_normal(16000 * 60) * 0.1).astype(np.float32)
    a[16000 * 15:16000 * 15 + 8000] *= 0.001          # 0.5 s pause at 15 s
    assert abs(find_pause(a, 16000 * 12, 16000 * 29) / 16000 - 15.25) < 0.1

    class FakeT:
        def transcribe(self, x, prompt=""):
            return f"<{x.size / 16000:.1f}>", {"stt_ms": 1}
    c = ChunkedTranscriber(FakeT(), "")
    assert not c.step(a[:16000 * 10])                   # too short to bother
    assert c.step(a[:16000 * 31]) and abs(c.done / 16000 - 15.25) < 0.1
    text, info = c.finish(a)
    assert text == "<15.2> <44.8>" and info["duration"] == 60.0


# --- cloud -----------------------------------------------------------------------
@pytest.mark.parametrize("key,provider", [("sk-or-v1-abc", "openrouter"), ("AIzaSyX", "gemini"),
                                          ("gsk_123", "groq"), ("csk-1", "cerebras"), ("sk-proj-1", "openai"),
                                          ("hello", None)])
def test_detect_provider(key, provider):
    assert cloud.detect_provider(key) == provider


def test_shortlist_prefers_newest_fast_models():
    models = [{"id": i, "created": c} for i, c in [
        ("google/gemini-3.1-flash-lite", 1), ("google/gemini-3.5-flash-lite", 3),
        ("google/gemini-3.5-flash-lite:batch", 3), ("anthropic/claude-haiku-5.5", 2),
        ("google/gemini-3.6-flash", 4), ("google/gemini-3.1-flash-image", 5), ("openai/gpt-5.6-sol", 6)]]
    assert cloud.shortlist("openrouter", models) == ["google/gemini-3.5-flash-lite", "anthropic/claude-haiku-5.5",
                                                     "google/gemini-3.6-flash"]


class _FakeAPI(BaseHTTPRequestHandler):
    """OpenAI-compatible: 'smart' cleans well but slowly, 'fast' is fast but answers questions."""
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"data": [{"id": "gemini-9.0-flash-lite"}, {"id": "gemini-9.0-flash"},
                             {"id": "gemini-9.0-flash-image"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeAPI.seen.append(req)
        if req.get("reasoning_effort") and req["model"] == "gemini-9.0-flash":
            return self._send({"error": "unknown parameter"}, 400)   # must be retried without it
        raw = req["messages"][-1]["content"]
        if req["model"] == "gemini-9.0-flash":
            time.sleep(0.05)
            out = {"what's the capital of France": "What's the capital of France?",
                   "I need three things first milk second bread and third eggs":
                       "I need three things:\n1. Milk\n2. Bread\n3. Eggs"}.get(raw, "I think we should ship the new "
                                                                                 "release on Thursday.")
        else:
            out = "Paris is the capital of France." if "France" in raw else raw
        self._send({"choices": [{"message": {"content": out}}]})


@pytest.fixture()
def fake_api(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setitem(cloud.PROVIDERS, "gemini", cloud.Provider(
        "gemini", "Google Gemini", f"http://127.0.0.1:{srv.server_port}", cloud.PROVIDERS["gemini"].prefer))
    yield srv
    srv.shutdown()


def test_auto_select_picks_the_accurate_model(fake_api):
    c = Cleaner(dict(DEFAULTS["cleanup"], backend="cloud"))
    provider, best, results = cloud.auto_select(
        "AIzaFAKE", lambda raw: c.build_messages(raw, FocusContext(), "neutral", []), output_is_faithful)
    assert provider == "gemini" and best == "gemini-9.0-flash"
    assert {r.model for r in results} == {"gemini-9.0-flash-lite", "gemini-9.0-flash"}   # image model skipped


def test_cloud_cleanup_end_to_end(fake_api, monkeypatch):
    monkeypatch.setattr(cloud, "load_key", lambda: "AIzaFAKE")
    c = Cleaner(dict(DEFAULTS["cleanup"], backend="cloud", cloud_provider="gemini", cloud_model="gemini-9.0-flash"))
    c.warm_up().join(10)
    assert c.available
    _FakeAPI.seen.clear()
    ctx = FocusContext(exe="notepad.exe", title="secret.txt", before="my bank PIN is 1234")
    out, _ms = c.clean("what's the capital of France", ctx, "neutral", [])
    assert out == "What's the capital of France?"
    sent = json.dumps(_FakeAPI.seen)
    assert "1234" not in sent and "secret.txt" not in sent      # screen text never goes to the cloud
