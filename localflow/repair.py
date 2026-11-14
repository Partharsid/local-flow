"""Find and replace damaged model and engine files.

A file can be damaged after it was downloaded (a crash or power loss while
the disk was busy, a failing drive). Sizes still match, so nothing noticed:
Whisper then failed with "Invalid string length in model.bin" and
llama-server crashed at start (0xC0000005) on every launch.

When a model fails to load, LocalFlow checks its files against the official
SHA-256 checksums and downloads fresh copies of the damaged ones only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path

from . import firstrun
from .firstrun import LLAMA_ZIP, LLM_GGUF, WHISPER, Asset, Component, _hf

log = logging.getLogger(__name__)

MANIFEST = ".manifest.json"          # name -> sha256 of every file in an unpacked runtime folder


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(folder: Path) -> None:
    files = {str(p.relative_to(folder)): sha256(p) for p in folder.rglob("*") if p.is_file() and p.name != MANIFEST}
    (folder / MANIFEST).write_text(json.dumps(files, indent=0))


def runtime_damaged(folder: Path) -> list[str]:
    """Files in an unpacked runtime that differ from its manifest. No manifest
    (installed by an older version): report it as unknown so it's reinstalled."""
    m = folder / MANIFEST
    if not m.is_file():
        return ["(no manifest)"]
    try:
        files = json.loads(m.read_text())
    except (OSError, ValueError):
        return ["(bad manifest)"]
    bad = []
    for name, digest in files.items():
        p = folder / name
        if not p.is_file() or sha256(p) != digest:
            bad.append(name)
    return bad


def find_damaged(cfg: dict, speech: bool = True, ai: bool = True) -> list[Component]:
    """Components whose files on disk don't match their official checksums."""
    comps: list[Component] = []
    model = cfg["whisper"]["model"]
    if speech and model in WHISPER:
        repo, rev, files = WHISPER[model]
        bad = [a for a in _hf(repo, rev, files, firstrun.MODELS / model)
               if a.sha256 and a.dest.is_file() and sha256(a.dest) != a.sha256]
        for a in bad:
            log.warning("Damaged file: %s (checksum mismatch)", a.dest)
        if bad:
            comps.append(Component("speech", f"Speech model (Whisper {model})", bad))
    if ai:
        assets: list[Asset] = []
        name, url, size, digest = LLM_GGUF
        gguf = Asset(url, firstrun.MODELS / "llm" / name, size, digest)
        if gguf.dest.is_file() and sha256(gguf.dest) != digest:
            log.warning("Damaged file: %s (checksum mismatch)", gguf.dest)
            assets.append(gguf)
        llama = firstrun.RUNTIME / "llama"
        if llama.is_dir():
            bad = runtime_damaged(llama)
            if bad:
                log.warning("AI engine files need reinstalling: %s", ", ".join(bad[:5]))
                url, size, digest = LLAMA_ZIP
                assets.append(Asset(url, firstrun.DOWNLOADS / "llama-vulkan.zip", size, digest, "zip", llama))
        if assets:
            comps.append(Component("ai", "AI cleanup (Qwen3-4B + llama.cpp)", assets))
    return comps


def fix(comps: list[Component], progress=None, cancel: threading.Event | None = None) -> None:
    """Download fresh copies (checksum-verified) and put them in place."""
    for comp in comps:
        for a in comp.assets:
            if not a.extract:
                a.dest.unlink(missing_ok=True)       # the downloader skips files that look complete
    firstrun.run(comps, progress or (lambda n: None), lambda key, text: log.info("Repair %s: %s", key, text),
                 cancel or threading.Event())
