"""Whisper speed/accuracy for beam sizes on synthetic speech (SAPI voice).

Usage: python tests/bench_stt.py
"""
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.config import Config  # noqa: E402
from localflow.stt import Transcriber  # noqa: E402

TEXT = ("So yesterday I finally got the new server set up. It took a while because the drivers were outdated "
        "and I had to reinstall them twice, but it is working now and the backups are running every night. "
        "The next thing I want to do is move the database over, and then test the failover before we tell "
        "the team. Can you check whether the staging build passed? Also remind Priya about the invoice.")


def speak(text: str) -> np.ndarray:
    import tempfile
    import wave

    import comtypes.client
    path = Path(tempfile.gettempdir()) / "lf_bench_stt.wav"
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18          # 16 kHz 16-bit mono
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def wer(ref: str, hyp: str) -> float:
    r, h = re.findall(r"[a-z']+", ref.lower()), re.findall(r"[a-z']+", hyp.lower())
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[-1] / max(1, len(r))


audio = speak(TEXT)
print(f"audio {audio.size / 16000:.1f}s")
cfg = Config().snapshot()["whisper"]
t = Transcriber(cfg)
t.load()
print("model", t.model_name, t.device)
for beam in (5, 2, 1):
    t.cfg = dict(cfg, beam_size=beam)
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        text, info = t.transcribe(audio)
        times.append((time.perf_counter() - t0) * 1000)
    print(f"beam {beam}: {min(times):.0f}-{max(times):.0f} ms, WER {wer(TEXT, text):.3f}")
