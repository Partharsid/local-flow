"""Transcribe audio/video files to .txt and .srt, locally."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MEDIA_TYPES = [("Audio and video", "*.mp3 *.m4a *.wav *.flac *.ogg *.opus *.aac *.wma *.mp4 *.mkv *.mov "
                "*.webm *.avi *.wmv"), ("All files", "*.*")]


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    blocks = []
    for i, s in enumerate(segments, 1):
        text = f"{s.speaker}: {s.text}" if s.speaker else s.text
        blocks.append(f"{i}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{text.strip()}\n")
    return "\n".join(blocks)


def to_paragraphs(segments: list[Segment], gap: float = 2.0) -> str:
    """Join segments into paragraphs, breaking at pauses longer than `gap`."""
    paras, cur, last_end = [], [], None
    for s in segments:
        if cur and last_end is not None and s.start - last_end > gap:
            paras.append(" ".join(cur))
            cur = []
        cur.append(s.text.strip())
        last_end = s.end
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras) + "\n"


def output_paths(src: Path) -> tuple[Path, Path]:
    """Next to the source file, or Documents\\LocalFlow Transcripts if that
    folder isn't writable."""
    folder = src.parent
    if not os.access(folder, os.W_OK):
        folder = Path.home() / "Documents" / "LocalFlow Transcripts"
        folder.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    return folder / f"{stem}.txt", folder / f"{stem}.srt"


def decode_media(src: Path) -> np.ndarray:
    """Decode any audio/video file to 16 kHz mono float32 with PyAV.

    Done here instead of in faster-whisper: its decoder passes an argument
    (metadata_errors) that current PyAV releases no longer accept."""
    import av
    chunks: list[np.ndarray] = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(str(src)) as container:
        if not container.streams.audio:
            raise ValueError("this file has no audio track")
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame) or []:
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None) or []:     # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe_file(transcriber, src: Path, progress=None, cancel: threading.Event | None = None,
                    prompt: str = "") -> tuple[Path, Path, list[Segment]]:
    """Write <name>.txt and <name>.srt; progress(fraction) as it goes."""
    segments: list[Segment] = []
    audio = decode_media(src)
    duration = max(audio.size / 16000, 0.01)

    def on_segment(seg, _info):
        segments.append(Segment(seg.start, seg.end, seg.text.strip()))
        if progress:
            progress(min(1.0, seg.end / duration))

    transcriber.long_form(audio, on_segment, cancel, prompt)
    if cancel is not None and cancel.is_set():
        raise InterruptedError("cancelled")
    txt, srt = output_paths(src)
    txt.write_text(to_paragraphs(segments), encoding="utf-8")
    srt.write_text(to_srt(segments), encoding="utf-8")
    log.info("Transcribed %s: %d segments", src.name, len(segments))
    return txt, srt, segments
