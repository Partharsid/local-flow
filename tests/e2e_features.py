"""End-to-end checks for 1.1 features with the real models (no microphone needed).

1. File transcription: SAPI speaks into a WAV, transcribe_file writes .txt/.srt.
2. Live preview: Transcriber.quick on a partial recording.
3. Meeting notes: records mic + system audio while SAPI plays through the
   speakers (the "other person"), then transcribes and summarises.
"""
import datetime as dt
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localflow import meeting  # noqa: E402
from localflow.cleanup import Cleaner  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402
from localflow.files import transcribe_file  # noqa: E402
from localflow.stt import Transcriber  # noqa: E402

TEXT = ("Welcome to the weekly sync. The deployment moved to Thursday. "
        "Priya will update the documentation, and Rahul will fix the login bug before Friday.")


def sapi_to_wav(text: str, path: Path) -> None:
    import comtypes.client
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()


def speak_aloud(text: str) -> None:
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    comtypes.client.CreateObject("SAPI.SpVoice").Speak(text)


results = {}
tmp = Path(tempfile.mkdtemp(prefix="lf_feat_"))
cfg = dict(DEFAULTS)
stt = Transcriber(cfg["whisper"])
stt.load()
assert not stt.error, stt.error

# 1) file transcription
wav = tmp / "weekly sync.wav"
sapi_to_wav(TEXT, wav)
t0 = time.perf_counter()
txt, srt, segs = transcribe_file(stt, wav, progress=lambda f: None)
results["file"] = {"ms": int((time.perf_counter() - t0) * 1000), "txt": txt.read_text(encoding="utf-8").strip(),
                   "srt_head": srt.read_text(encoding="utf-8").splitlines()[:3]}
ok_file = "thursday" in results["file"]["txt"].lower() and "-->" in results["file"]["srt_head"][1]

# 2) live preview on the first ~4 s
with wave.open(str(wav)) as w:
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
t0 = time.perf_counter()
preview = stt.quick(audio[: 16000 * 4])
results["preview"] = {"ms": int((time.perf_counter() - t0) * 1000), "text": preview}
ok_preview = "weekly" in preview.lower() or "welcome" in preview.lower()

# 3) meeting notes
cleaner = Cleaner(dict(cfg["cleanup"]))
warm = cleaner.warm_up()
rec = meeting.MeetingRecorder()
rec.start()
started = dt.datetime.now()
time.sleep(0.8)
player = threading.Thread(target=speak_aloud, args=(TEXT,))
player.start()
player.join()
time.sleep(1.0)
duration = rec.elapsed()
folder = rec.stop()
warm.join(180)
t0 = time.perf_counter()
notes = meeting.finish(dict(cfg, meetings_folder=str(tmp / "notes")), stt, cleaner, folder, started, duration)
results["meeting"] = {"ms": int((time.perf_counter() - t0) * 1000), "errors": rec.errors,
                      "notes": notes.read_text(encoding="utf-8")}
cleaner.shutdown()
transcript = results["meeting"]["notes"].split("## Transcript", 1)[-1].lower()
ok_meeting = ("others:** " in transcript and "thursday" in transcript and "## summary" in results["meeting"]["notes"].lower()
              and "restaurant" not in transcript and transcript.count("thank you") <= 1)

print(f"[file]    ok={ok_file}  {results['file']['ms']} ms\n  txt: {results['file']['txt']}\n"
      f"  srt: {results['file']['srt_head']}")
print(f"[preview] ok={ok_preview}  {results['preview']['ms']} ms  {results['preview']['text']!r}")
print(f"[meeting] ok={ok_meeting}  {results['meeting']['ms']} ms  errors={results['meeting']['errors']}\n"
      + results["meeting"]["notes"])
print("RESULT:", "PASS" if ok_file and ok_preview and ok_meeting else "CHECK OUTPUT")
