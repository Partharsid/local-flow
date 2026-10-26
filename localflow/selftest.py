"""`LocalFlow.exe --self-test [result.json]`: headless check of the packaged app.

Windows' built-in voice speaks a sentence, Whisper transcribes it and the AI
engine cleans it up. The result is written as JSON (the windowed exe has no
console). Exit code 0 means the speech and cleanup pipeline works on this PC.
"""
from __future__ import annotations

import json
import tempfile
import time
import traceback
import wave
from pathlib import Path

import numpy as np

SENTENCE = "um so I think we should, uh, ship the new release on Friday, no wait, on Thursday"


def _speak() -> np.ndarray:
    import comtypes.client
    path = Path(tempfile.gettempdir()) / "localflow_selftest.wav"
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18  # 16 kHz 16-bit mono
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(SENTENCE)
    stream.Close()
    with wave.open(str(path)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    path.unlink(missing_ok=True)
    return audio


def run_self_test(args: list[str]) -> int:
    from . import polish
    from .cleanup import Cleaner
    from .config import Config
    from .context import FocusContext
    from .stt import Transcriber

    out_path = Path(args[0]) if args else None
    result: dict = {"ok": False}
    cleaner = None
    try:
        cfg = Config().snapshot()
        t0 = time.perf_counter()
        stt = Transcriber(cfg["whisper"])
        stt.load()
        if stt.error:
            raise RuntimeError(stt.error)
        result["whisper"] = {"model": str(stt.model_name).split("\\")[-1], "device": stt.device,
                             "load_s": round(time.perf_counter() - t0, 1)}
        cleaner = Cleaner(cfg["cleanup"])
        warm = cleaner.warm_up() if cfg["cleanup"]["enabled"] else None
        audio = _speak()
        raw, info = stt.transcribe(audio, "")
        result.update(raw=raw, stt_ms=info.get("stt_ms"))
        text = polish.pre_clean(raw, cfg)
        if warm is not None:
            warm.join(180)
            cleaned, llm_ms = cleaner.clean(text, FocusContext(exe="selftest.exe"), "general", [])
            result.update(cleaned=cleaned, llm_ms=llm_ms, ai=cleaner.model_label if cleaner.available else
                          (cleaner.problem or "unavailable"))
            text = cleaned or text
        result["final"] = text
        low = text.lower()
        result["ok"] = "thursday" in low and "ship" in low
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()
    finally:
        if cleaner is not None:
            cleaner.shutdown()
    payload = json.dumps(result, indent=2)
    if out_path:
        out_path.write_text(payload, encoding="utf-8")
    else:
        try:
            print(payload)
        except Exception:  # noqa: BLE001 - windowed build: no console
            pass
    return 0 if result["ok"] else 1
