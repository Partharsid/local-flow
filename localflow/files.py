"""Transcribe audio/video files to .txt and .srt, locally."""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

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


def transcribe_file(transcriber, src: Path, progress=None, cancel: threading.Event | None = None,
                    prompt: str = "") -> tuple[Path, Path, list[Segment]]:
    """Write <name>.txt and <name>.srt; progress(fraction) as it goes."""
    segments: list[Segment] = []

    def on_segment(seg, info):
        segments.append(Segment(seg.start, seg.end, seg.text.strip()))
        if progress and getattr(info, "duration", 0):
            progress(min(1.0, seg.end / info.duration))

    transcriber.long_form(src, on_segment, cancel, prompt)
    if cancel is not None and cancel.is_set():
        raise InterruptedError("cancelled")
    txt, srt = output_paths(src)
    txt.write_text(to_paragraphs(segments), encoding="utf-8")
    srt.write_text(to_srt(segments), encoding="utf-8")
    log.info("Transcribed %s: %d segments", src.name, len(segments))
    return txt, srt, segments
