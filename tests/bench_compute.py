"""Compare Whisper compute types: GPU memory, RAM, speed and accuracy.

Run one type per process (python tests/bench_compute.py int8_float16) so the
memory numbers aren't polluted by a previous model."""
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.cuda_setup import add_nvidia_dll_dirs  # noqa: E402
from localflow.stt import Transcriber  # noqa: E402

SENTENCES = [
    "I deployed the website on Coolify with a Supabase database and a Cloudflare tunnel.",
    "Can you send me the updated report before the meeting tomorrow morning?",
    "Yesterday I went to the market with my friends and we bought vegetables and fruits.",
]


def gpu_used_mb() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def speak(text: str) -> np.ndarray:
    import comtypes.client
    path = Path(tempfile.gettempdir()) / "lf_bench.wav"
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def words(s: str) -> list[str]:
    return [w.strip(".,?!").lower() for w in s.split()]


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


ctype = sys.argv[1]
audios = [speak(s) for s in SENTENCES]
add_nvidia_dll_dirs()
base_gpu = gpu_used_mb()
proc = psutil.Process()
t = Transcriber({"model": "large-v3-turbo", "device": "cuda", "compute_type": ctype, "language": "en", "beam_size": 5})
t.load()
if t.error:
    sys.exit(t.error)
gpu = gpu_used_mb() - base_gpu
times, errs = [], []
for ref, a in zip(SENTENCES, audios):
    t0 = time.perf_counter()
    hyp, _ = t.transcribe(a, "I often mention Coolify, Supabase, Cloudflare.")
    times.append((time.perf_counter() - t0) * 1000)
    errs.append(wer(ref, hyp))
mem = proc.memory_info()
print(f"{ctype:14} GPU +{gpu} MB | RAM {mem.rss // 2**20} MB (private {mem.private // 2**20} MB) | "
      f"avg {np.mean(times):.0f} ms per sentence | WER {np.mean(errs) * 100:.1f}%")
