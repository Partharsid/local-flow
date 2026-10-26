"""Re-transcribe the last saved recording under different settings to see
which stage loses words. Needs "debug_save_audio": true in config.json.

Run: .venv\\Scripts\\python.exe tests\\replay_debug.py
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow.config import DATA_DIR, DEFAULTS  # noqa: E402
from localflow.cuda_setup import add_nvidia_dll_dirs  # noqa: E402
from localflow.stt import Transcriber  # noqa: E402

d = DATA_DIR / "debug"
with wave.open(str(d / "last.wav")) as w:
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
meta = json.loads((d / "last.json").read_text(encoding="utf-8"))
rms = float(np.sqrt(np.mean(audio ** 2)))
print(f"audio {audio.size / 16000:.1f}s  rms {rms:.4f}  peak {np.abs(audio).max():.3f}  app {meta['exe']}")
print(f"prompt ({len(meta['prompt'])} chars): {meta['prompt'][:300]!r}\n")

# How much of the recording does the VAD consider speech?
from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: E402
for thr in (0.5, 0.35, 0.2):
    ts = get_speech_timestamps(audio, VadOptions(threshold=thr, min_silence_duration_ms=700, speech_pad_ms=300))
    print(f"VAD threshold {thr}: {sum(t['end'] - t['start'] for t in ts) / 16000:.1f}s of speech in {len(ts)} chunks")
print()

add_nvidia_dll_dirs()
t = Transcriber(DEFAULTS["whisper"])
t.load()
m = t.model


def run(label, **kw):
    segs, _ = m.transcribe(audio, language="en", beam_size=5, condition_on_previous_text=False,
                           without_timestamps=True, **kw)
    segs = list(segs)
    text = " ".join(s.text.strip() for s in segs)
    detail = ", ".join(f"nsp={s.no_speech_prob:.2f}/lp={s.avg_logprob:.2f}" for s in segs)
    print(f"{label:34} -> {text!r}\n{'':37}[{detail}]")


app_text, app_info = t.transcribe(audio, meta["prompt"])
print("App pipeline (old prompt):         ->", repr(app_text), {k: app_info.get(k) for k in ("avg_logprob", "prompt_retry", "prompt_dropped")})
run("VAD + prompt (raw segments)", vad_filter=True, initial_prompt=meta["prompt"] or None,
    vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300})
run("VAD, no prompt", vad_filter=True, vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300})
run("no VAD, prompt", vad_filter=False, initial_prompt=meta["prompt"] or None)
run("no VAD, no prompt", vad_filter=False)
