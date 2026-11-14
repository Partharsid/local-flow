"""The fine-tuned dictation model path, expert prompts and the training log."""
import json

import pytest

from localflow import cleanup, trainlog, tuned
from localflow.context import FocusContext


def test_prompt_format_is_compact():
    p = tuned.build_prompt("prompt", "Claude.exe", ["EGGEEZ", "LMRS"], "  fix the login  ")
    assert p.startswith("<|im_start|>system\nmode: prompt\napp: claude.exe\nvocab: EGGEEZ, LMRS<|im_end|>")
    assert p.endswith("<|im_start|>user\nfix the login<|im_end|>\n<|im_start|>assistant\n")
    assert "vocab" not in tuned.build_prompt("neutral", "", [], "hi")
    assert "mode: neutral" in tuned.build_prompt("weird", "", [], "hi")


def test_expert_footer_only_on_real_requests():
    structured = "Fix the login.\n\nContext:\n- It fails on Safari."
    assert tuned.expert(structured).endswith(tuned.EXPERT_FOOTER)
    assert tuned.expert("Thanks, go ahead.") == "Thanks, go ahead."


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@pytest.fixture
def fast_cleaner(monkeypatch):
    c = cleanup.Cleaner({"backend": "builtin", "dictation_model": "localflow-x.gguf", "fast_model": True})
    c.available = True
    monkeypatch.setattr(cleanup.LlamaServer, "installed", staticmethod(lambda f: f == "localflow-x.gguf"))
    monkeypatch.setattr(c, "_fast_ready", lambda: True)
    sent = {}

    def post(url, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        return FakeResp({"content": sent.get("reply", "")})
    monkeypatch.setattr(c._session, "post", post)
    return c, sent


def test_dictation_uses_fast_model(fast_cleaner):
    c, sent = fast_cleaner
    sent["reply"] = "I was thinking we could meet at 6 tomorrow."
    out, _ = c.clean("um so I was thinking we could meet at like 5 no wait 6 tomorrow", FocusContext(exe="notepad.exe"),
                     "neutral", [])
    assert out == "I was thinking we could meet at 6 tomorrow."
    assert sent["url"].endswith("/completion")
    assert sent["json"]["stop"] == [tuned.END]
    assert "mode: neutral" in sent["json"]["prompt"]
    assert c.model_label == "localflow-x"


def test_fast_output_still_guarded(fast_cleaner):
    c, sent = fast_cleaner
    sent["reply"] = "The capital of France is Paris, a city known for the Eiffel Tower and great food."
    out, _ = c.clean("what's the capital of france", FocusContext(), "neutral", [])
    assert out is None                      # answered instead of transcribing: rejected


def test_expert_prompt_added_after_guard(fast_cleaner):
    c, sent = fast_cleaner
    c.cfg["prompt_expert"] = True
    raw = ("okay so the login keeps failing it started after I updated the auth library yesterday and it only "
           "happens on safari can you find out why")
    sent["reply"] = ("Can you find out why login keeps failing?\n\nContext:\n- It started after I updated the auth "
                     "library yesterday.\n- It only happens on Safari.")
    out, _ = c.clean(raw, FocusContext(exe="claude.exe"), "prompt", [])
    assert out.endswith(tuned.EXPERT_FOOTER)


def test_general_model_when_no_fast_model(monkeypatch):
    c = cleanup.Cleaner({"backend": "builtin", "dictation_model": "missing.gguf"})
    monkeypatch.setattr(cleanup.LlamaServer, "installed", staticmethod(lambda f: False))
    assert not c.fast_active


def test_trainlog_keeps_corrections(tmp_path, monkeypatch):
    monkeypatch.setattr(trainlog, "PATH", tmp_path / "d.jsonl")
    rid = trainlog.add("neutral", "slack.exe", [], "send it to part", "Send it to Part.")
    trainlog.add("neutral", "slack.exe", [], "hello there", "Hello there.")
    trainlog.correct(rid, "Send it to Part.", "Send it to Parth.")
    trainlog.correct(rid, "Send it to Part.", "Something completely different and much much longer than before.")
    rows = {r["input"]: r for r in trainlog.load()}
    assert rows["send it to part"]["output"] == "Send it to Parth."
    assert rows["send it to part"]["corrected"] is True
    assert rows["hello there"]["corrected"] is False
    assert trainlog.stats() == {"dictations": 2, "corrections": 1}
    lines = (tmp_path / "d.jsonl").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(l) for l in lines) and len(lines) == 3


def test_fix_detection():
    assert trainlog.looks_like_fix("Meet at 5.", "Meet at 6.")
    assert not trainlog.looks_like_fix("Meet at 5.", "Meet at 5.")
    assert not trainlog.looks_like_fix("Meet at 5.", "")
    assert not trainlog.looks_like_fix("Hi.", "Hi. And here is a whole new paragraph I typed myself afterwards.")


def test_dictation_component_download(tmp_path, monkeypatch):
    from localflow import firstrun, llm_server
    monkeypatch.setattr(llm_server, "LLM_DIR", tmp_path)
    cfg = {"cleanup": {"enabled": True, "backend": "builtin", "fast_model": True,
                       "dictation_model": firstrun.DICTATION_GGUF[0]}}
    comp = firstrun.dictation_component(cfg)
    assert comp and comp.assets[0].dest == tmp_path / firstrun.DICTATION_GGUF[0]
    assert comp.assets[0].url.startswith("https://github.com/Partharsid/local-flow/releases/download/models-1/")
    cfg["cleanup"]["fast_model"] = False
    assert firstrun.dictation_component(cfg) is None


def test_fresh_install_setup_includes_fast_model(tmp_path, monkeypatch):
    from localflow import config, firstrun, llm_server
    monkeypatch.setattr(llm_server, "LLM_DIR", tmp_path)
    monkeypatch.setattr(llm_server, "LLAMA_DIR", tmp_path / "llama")
    cfg = json.loads(json.dumps(config.DEFAULTS))
    comps = firstrun.plan(cfg)
    ai = next(c for c in comps if c.key == "ai")
    names = [a.dest.name for a in ai.assets]
    assert firstrun.DICTATION_GGUF[0] in names and names.index(firstrun.DICTATION_GGUF[0]) < names.index(firstrun.LLM_GGUF[0])
