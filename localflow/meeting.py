"""Meeting notes: record mic + system audio, transcribe, summarise locally.

Two tracks are recorded separately (your microphone, and what the computer
plays: the other people on Zoom/Meet/Teams). Transcribing them apart labels
each line "You" or "Others" without speaker diarization. When there are no
headphones the mic also hears the speakers; mic lines that repeat a speaker
line at the same moment are dropped as echo.
"""
from __future__ import annotations

import datetime as dt
import difflib
import logging
import shutil
import threading
import time
import wave
import warnings
from pathlib import Path

import numpy as np

from .config import DATA_DIR
from .files import Segment

log = logging.getLogger(__name__)
RATE = 16000
BLOCK = 1600   # 0.1 s


def notes_folder(cfg: dict) -> Path:
    folder = Path(cfg.get("meetings_folder") or Path.home() / "Documents" / "LocalFlow Meetings")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class MeetingRecorder:
    def __init__(self):
        self.started_at = 0.0
        self.folder: Path | None = None
        self.level = 0.0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.errors: list[str] = []

    @property
    def active(self) -> bool:
        return bool(self._threads) and not self._stop.is_set()

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.active else 0.0

    def start(self) -> None:
        import soundcard as sc
        # soundcard warns about tiny capture gaps under load; harmless here.
        warnings.filterwarnings("ignore", module="soundcard")
        self.folder = DATA_DIR / "meetings" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self.errors = []
        mic = sc.default_microphone()
        system = sc.get_microphone(sc.default_speaker().name, include_loopback=True)
        self._threads = [threading.Thread(target=self._record, args=(dev, self.folder / f"{name}.wav", name),
                                          daemon=True, name=f"meeting-{name}")
                         for name, dev in (("mic", mic), ("system", system))]
        self.started_at = time.monotonic()
        for t in self._threads:
            t.start()
        log.info("Meeting recording started (%s + %s)", mic.name, system.name)

    def _record(self, device, path: Path, name: str) -> None:
        try:
            with wave.open(str(path), "wb") as w, device.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK)[:, 0]
                    if name == "mic":
                        rms = float(np.sqrt(np.mean(data * data))) if data.size else 0.0
                        self.level = 0.6 * self.level + 0.4 * min(1.0, rms * 12.0) ** 0.7
                    w.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"{name}: {e}")
            log.exception("Meeting %s track failed", name)

    def stop(self) -> Path | None:
        self._stop.set()
        for t in self._threads:
            t.join(5)
        self._threads = []
        log.info("Meeting recording stopped (%.0f s)", time.monotonic() - self.started_at)
        return self.folder


def _load(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768


def _echo(a: Segment, b: Segment) -> bool:
    overlap = min(a.end, b.end) - max(a.start, b.start)
    if overlap <= 0:
        return False
    return difflib.SequenceMatcher(a=a.text.lower(), b=b.text.lower()).ratio() > 0.55


def _believable(seg, duration: float, previous: str) -> bool:
    """Reject Whisper's hallucinations on quiet or noisy stretches."""
    from .stt import HALLUCINATIONS
    text = seg.text.strip()
    if not text or seg.start >= duration:
        return False                                   # timestamps past the end of the audio
    if seg.no_speech_prob > 0.5 and seg.avg_logprob < -0.7:
        return False
    if seg.compression_ratio > 2.4:                    # "the the the the..."
        return False
    if text.lower() in HALLUCINATIONS or text.lower().rstrip(".!") in HALLUCINATIONS:
        return False
    return text.lower() != previous.lower()            # repeated line = a loop


def transcribe_tracks(transcriber, folder: Path, progress=None, cancel=None, prompt: str = "") -> list[Segment]:
    tracks = [("You", folder / "mic.wav"), ("Others", folder / "system.wav")]
    out: list[Segment] = []
    for i, (speaker, path) in enumerate(tracks):
        if not path.exists():
            continue
        audio = _load(path)
        if audio.size < RATE or float(np.sqrt(np.mean(audio * audio))) < 0.002:
            continue                      # silent track (e.g. nobody else spoke)
        duration = audio.size / RATE
        last = [""]

        def on_segment(seg, _info, speaker=speaker, i=i, duration=duration, last=last):
            if progress:
                progress((i + min(1.0, seg.end / duration)) / len(tracks))
            if not _believable(seg, duration, last[0]):
                return
            last[0] = seg.text.strip()
            out.append(Segment(seg.start, min(seg.end, duration), seg.text.strip(), speaker))
        transcriber.long_form(audio, on_segment, cancel, prompt, carry_context=False)
    others = [s for s in out if s.speaker == "Others"]
    merged = [s for s in out if not (s.speaker == "You" and any(_echo(s, o) for o in others))]
    merged.sort(key=lambda s: s.start)
    return [s for s in merged if s.text]


def _stamp(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


SUMMARY_PROMPT = """You turn meeting transcripts into concise notes. Use only what is in the transcript.
Write Markdown with exactly these sections:
## Summary
(2-4 sentences)
## Key points
- ...
## Decisions
- ... (or "- None recorded")
## Action items
- [ ] Owner: task (or "- None recorded")
Do not invent names, dates or tasks."""


def summarise(cleaner, lines: list[str]) -> str:
    """Chunked summary that fits a small model's context window."""
    if not cleaner.available or not lines:
        return ""
    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) > 6000 and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    partials = []
    for c in chunks:
        partials.append(cleaner._chat([{"role": "system", "content": SUMMARY_PROMPT},
                                       {"role": "user", "content": "TRANSCRIPT:\n" + c}], 700, timeout=180))
    if len(partials) == 1:
        return partials[0]
    combined = "\n\n".join(partials)[:9000]
    return cleaner._chat([{"role": "system", "content": SUMMARY_PROMPT + "\nMerge these partial notes "
                           "from consecutive parts of one meeting into a single set of notes."},
                          {"role": "user", "content": combined}], 900, timeout=240)


def write_notes(cfg: dict, segments: list[Segment], summary: str, started: dt.datetime, duration: float) -> Path:
    folder = notes_folder(cfg)
    path = folder / f"{started:%Y-%m-%d %H%M} Meeting.md"
    lines = [f"# Meeting notes · {started:%d %b %Y, %H:%M}", "",
             f"*Duration {_stamp(duration)} · transcribed locally by LocalFlow*", ""]
    if summary:
        lines += [summary.strip(), ""]
    lines += ["## Transcript", ""]
    lines += [f"**[{_stamp(s.start)}] {s.speaker}:** {s.text}" for s in segments] or ["*No speech was detected.*"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def finish(cfg: dict, transcriber, cleaner, folder: Path, started: dt.datetime, duration: float,
           progress=None, cancel=None, prompt: str = "") -> Path:
    segments = transcribe_tracks(transcriber, folder, progress, cancel, prompt)
    lines = [f"[{_stamp(s.start)}] {s.speaker}: {s.text}" for s in segments]
    try:
        summary = summarise(cleaner, lines)
    except Exception as e:  # noqa: BLE001 - the transcript is still worth saving
        log.warning("Meeting summary failed: %s", e)
        summary = ""
    notes = write_notes(cfg, segments, summary, started, duration)
    if cfg.get("meetings_keep_audio"):
        for name in ("mic.wav", "system.wav"):
            if (folder / name).exists():
                shutil.move(str(folder / name), str(notes.with_name(f"{notes.stem} {name}")))
    shutil.rmtree(folder, ignore_errors=True)
    return notes
