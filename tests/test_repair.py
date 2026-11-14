"""Damaged model files (right size, wrong bytes) are found and replaced."""
import hashlib

from localflow import firstrun, repair
from localflow.config import DEFAULTS


def _setup(tmp_path, monkeypatch, good: bytes):
    monkeypatch.setattr(firstrun, "MODELS", tmp_path / "models")
    monkeypatch.setattr(firstrun, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(firstrun, "DOWNLOADS", tmp_path / "downloads")
    sha = hashlib.sha256(good).hexdigest()
    monkeypatch.setitem(repair.WHISPER, "large-v3-turbo", ("r/m", "rev", [("model.bin", len(good), sha),
                                                                           ("config.json", 2, "")]))
    monkeypatch.setattr(repair, "LLM_GGUF", ("q.gguf", "https://x/q.gguf", len(good), sha))
    d = tmp_path / "models" / "large-v3-turbo"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    (tmp_path / "models" / "llm").mkdir()
    return d


def test_intact_files_pass(tmp_path, monkeypatch):
    good = b"weights" * 1000
    d = _setup(tmp_path, monkeypatch, good)
    (d / "model.bin").write_bytes(good)
    (tmp_path / "models" / "llm" / "q.gguf").write_bytes(good)
    llama = tmp_path / "runtime" / "llama"
    llama.mkdir(parents=True)
    (llama / "mtmd.dll").write_bytes(b"dll")
    repair.write_manifest(llama)
    assert repair.find_damaged(DEFAULTS) == []


def test_same_size_wrong_bytes_is_repaired(tmp_path, monkeypatch):
    good = b"weights" * 1000
    d = _setup(tmp_path, monkeypatch, good)
    bad = bytearray(good)
    bad[5000] ^= 0xFF                                   # one flipped byte, same size
    (d / "model.bin").write_bytes(bad)
    (tmp_path / "models" / "llm" / "q.gguf").write_bytes(good)
    llama = tmp_path / "runtime" / "llama"
    llama.mkdir(parents=True)
    (llama / "mtmd.dll").write_bytes(b"dll")
    repair.write_manifest(llama)
    (llama / "mtmd.dll").write_bytes(b"dlX")            # damaged after install

    comps = repair.find_damaged(DEFAULTS)
    assert [c.key for c in comps] == ["speech", "ai"]
    assert [a.name for a in comps[0].assets] == ["model.bin"]
    assert [a.extract for a in comps[1].assets] == ["zip"]   # the engine is reinstalled from its archive

    ran = []
    monkeypatch.setattr(firstrun, "run", lambda c, p, s, cancel: ran.append([a.name for x in c for a in x.assets]))
    repair.fix(comps)
    assert not (d / "model.bin").exists()                # removed so the downloader fetches it again
    assert ran == [["model.bin", "llama-vulkan.zip"]]


def test_engine_without_manifest_is_reinstalled(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, b"x")
    (tmp_path / "runtime" / "llama").mkdir(parents=True)
    comps = repair.find_damaged(DEFAULTS, speech=False, ai=True)
    assert comps and comps[0].assets[0].extract == "zip"
