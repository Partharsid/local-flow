"""Local speech-to-text with faster-whisper (CTranslate2)."""
from __future__ import annotations

import logging
import re
import threading
import time

import numpy as np

from .config import DATA_DIR
from .cuda_setup import add_nvidia_dll_dirs

log = logging.getLogger(__name__)

MODELS_DIR = DATA_DIR / "models"
# Exact model.bin sizes, so a half-finished download is never loaded.
LOCAL_MODEL_SIZES = {"large-v3-turbo": 1_617_884_929}  # dropbox-dash/faster-whisper-large-v3-turbo@0a363e9

# Whisper invents these on silence/noise. Only dropped when they are the
# entire transcript of a quiet recording.
HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "thank you for watching", "thank you for watching.", "you", "bye", "bye.",
    "subtitles by the amara.org community", ".", "...", "okay.", "so",
    "please subscribe", "thank you so much for watching",
}


def _avg_logprob(segs) -> float:
    """Duration-weighted confidence of a decode (higher is better, 0 = certain)."""
    if not segs:
        return -10.0
    total = sum(max(s.end - s.start, 0.01) for s in segs)
    return sum(s.avg_logprob * max(s.end - s.start, 0.01) for s in segs) / total


class Transcriber:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = None
        self.device = None
        self.model_name = None
        self.ready = threading.Event()
        self.error: str | None = None
        self._lock = threading.Lock()

    # -- loading -----------------------------------------------------------
    def unload(self) -> None:
        """Drop the current model (waits for an in-flight transcription)."""
        self.ready.clear()
        with self._lock:
            self.model = None

    def load(self) -> None:
        """Load the model; safe to call from a background thread. Always ends
        with `ready` set, and `error` set on failure, so callers never hang."""
        self.ready.clear()
        self.error = None
        try:
            self._load()
        except Exception as e:  # noqa: BLE001 - e.g. import or DLL failures
            self.error = f"Could not load speech engine: {e}"
            log.exception("Speech engine failed to load")
            self.ready.set()

    @staticmethod
    def resolve(name: str) -> str:
        """Prefer a local copy in %APPDATA%\\LocalFlow\\models\\<name> (works
        offline and avoids Hugging Face redirects); otherwise let
        faster-whisper download it by name."""
        local = MODELS_DIR / name
        model_bin = local / "model.bin"
        if model_bin.is_file() and (local / "tokenizer.json").is_file():
            expected = LOCAL_MODEL_SIZES.get(name)
            if expected is None or model_bin.stat().st_size == expected:
                return str(local)
            log.warning("Local %s looks incomplete (%d bytes); ignoring it", name, model_bin.stat().st_size)
        return name

    def _load(self) -> None:
        add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        name = self.resolve(self.cfg.get("model", "large-v3-turbo"))
        want = self.cfg.get("device", "auto")
        compute = self.cfg.get("compute_type", "auto")
        attempts: list[tuple[str, str, str]] = []
        if want in ("auto", "cuda"):
            # int8 weights: about half the VRAM and RAM of float16, same accuracy
            # in our benchmark (tests/bench_compute.py), and faster.
            attempts.append((name, "cuda", "int8_float16" if compute == "auto" else compute))
            attempts.append((name, "cuda", "float16"))
        if want in ("auto", "cpu"):
            cpu_name = name if want == "cpu" else self.cfg.get("cpu_fallback_model", "small")
            attempts.append((cpu_name, "cpu", "int8" if compute in ("auto", "float16", "int8_float16") else compute))

        last_err = None
        for model_name, device, ctype in attempts:
            try:
                t0 = time.perf_counter()
                model = WhisperModel(model_name, device=device, compute_type=ctype)
                # Warm-up: the first CUDA call allocates kernels; do it now, not
                # on the user's first dictation. Also proves cuDNN actually loads.
                list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en",
                                      beam_size=1, vad_filter=False)[0])
                with self._lock:
                    self.model, self.device, self.model_name = model, device, model_name
                log.info("Whisper %s ready on %s/%s in %.1fs", model_name, device, ctype,
                         time.perf_counter() - t0)
                self.ready.set()
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("Whisper %s on %s/%s failed: %s", model_name, device, ctype, e)
        self.error = f"Could not load speech model: {last_err}"
        log.error(self.error)
        self.ready.set()

    # -- inference ---------------------------------------------------------
    def _decode(self, model, audio: np.ndarray, lang: str, prompt: str):
        segments, tinfo = model.transcribe(
            audio,
            language=None if lang in ("auto", "", None) else lang,
            beam_size=int(self.cfg.get("beam_size", 5)),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 400},
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            without_timestamps=True,
            temperature=[0.0, 0.2, 0.4],
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        return list(segments), tinfo

    def transcribe(self, audio: np.ndarray, prompt: str = "") -> tuple[str, dict]:
        info: dict = {"duration": audio.size / 16000}
        model = self.model
        if model is None:
            raise RuntimeError(self.error or "Speech model is not loaded yet")
        if audio.size < 16000 * 0.25:
            return "", info
        rms = float(np.sqrt(np.mean(audio * audio)))
        info["rms"] = rms
        if rms < 0.0015:  # effectively silence
            return "", info

        # Normalise quiet microphones so VAD and Whisper see a healthy level.
        peak = float(np.max(np.abs(audio)))
        if 0 < peak < 0.3:
            audio = audio * min(0.3 / peak, 8.0)

        lang = self.cfg.get("language", "en")
        t0 = time.perf_counter()
        with self._lock:
            model = self.model or model  # a reload may have swapped it in meanwhile
            segs, tinfo = self._decode(model, audio, lang, prompt)
            if prompt and _avg_logprob(segs) < -0.8:
                # A prompt can derail Whisper. If it was unsure, try again
                # without one and keep whichever decode is more confident.
                segs2, tinfo2 = self._decode(model, audio, lang, "")
                info["prompt_retry"] = True
                if _avg_logprob(segs2) > _avg_logprob(segs):
                    segs, tinfo = segs2, tinfo2
                    info["prompt_dropped"] = True
        info["stt_ms"] = int((time.perf_counter() - t0) * 1000)
        info["avg_logprob"] = round(_avg_logprob(segs), 2)
        info["language"] = getattr(tinfo, "language", lang)

        kept = [s.text for s in segs if s.no_speech_prob < 0.85 or s.avg_logprob > -0.6]
        info["segments"], info["segments_kept"] = len(segs), len(kept)
        text = re.sub(r"\s+", " ", " ".join(kept)).strip()
        core = text.strip(" .,")
        if prompt and core and ("glossary:" in text.lower()
                                or (len(core) > 30 and core in prompt and rms < 0.01)):
            # Whisper sometimes regurgitates its prompt on near-silence. A
            # short real word that happens to be in the glossary is kept.
            log.info("Dropped transcript that only echoed the prompt")
            return "", info
        if text.lower().strip() in HALLUCINATIONS and (rms < 0.01 or info["duration"] < 1.2):
            log.info("Dropped likely hallucination: %r", text)
            return "", info
        return text, info
