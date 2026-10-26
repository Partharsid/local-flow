"""End-to-end check without a microphone.

1. Windows SAPI speaks a sentence into a 16 kHz WAV.
2. Notepad opens with some existing text.
3. The real pipeline runs: UIA context -> Whisper -> cleanup -> fit -> paste.
4. Notepad's text is read back through UI Automation and printed.

Run:  .venv\\Scripts\\python.exe tests\\e2e_notepad.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from localflow import context as ctxmod  # noqa: E402
from localflow import polish  # noqa: E402
from localflow.cleanup import Cleaner  # noqa: E402
from localflow.config import DEFAULTS  # noqa: E402
from localflow.inserter import KEYEVENTF_KEYUP, Inserter, _key, _send, type_text  # noqa: E402
from localflow.stt import Transcriber  # noqa: E402

SPEECH = "um so I think we should, uh, ship the new release on Friday, no wait, on Thursday"
BEFORE = "Status update for the team: "


def speak_to_wav(text: str, path: Path) -> np.ndarray:
    import comtypes.client
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream.Format = fmt
    stream.Open(str(path), 3)  # SSFMCreateForWrite
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Rate = 0
    voice.Speak(text)
    stream.Close()
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return (data.astype(np.float32) / 32768.0)


def notepad_text(title_fragment: str) -> str:
    """Read the document of *our* Notepad window, wherever focus is."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        focused = auto.GetFocusedControl()
        print(f"    (focus is on {focused.ControlTypeName} {focused.ClassName!r} in "
              f"{ctxmod.foreground_window()[2]!r})")
        win = auto.WindowControl(searchDepth=1, SubName=title_fragment)
        doc = win.DocumentControl(searchDepth=8)
        tp = doc.GetPattern(auto.PatternId.TextPattern)
        return tp.DocumentRange.GetText(-1) if tp else ""


def main() -> int:
    cfg = dict(DEFAULTS)
    wav = Path(tempfile.gettempdir()) / "localflow_e2e.wav"
    audio = speak_to_wav(SPEECH, wav)
    print(f"[1] synthesized {audio.size / 16000:.1f}s of speech")

    stt = Transcriber(cfg["whisper"])
    t0 = time.perf_counter()
    stt.load()
    if stt.error:
        print("STT load failed:", stt.error)
        return 1
    print(f"[2] whisper {stt.model_name} loaded on {stt.device} in {time.perf_counter() - t0:.1f}s")

    cleaner = Cleaner(cfg["cleanup"])
    cleaner.warm_up()

    ctxmod.prewarm()  # the app does this at startup

    # Use our own new file: Windows 11 Notepad restores old tabs, and typing
    # into whatever tab has focus could modify the user's documents.
    target = Path(tempfile.gettempdir()) / f"localflow_e2e_{int(time.time())}.txt"
    target.write_text("", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(target)])
    if not wait_for_title(target.name, 10):
        print("Notepad did not bring our test file to the front; aborting without typing.")
        return 1
    time.sleep(1.2)  # the title appears before the editor accepts input
    type_text(BEFORE)
    time.sleep(0.5)
    ctx = ctxmod.capture(True, timeout=2.0)
    print(f"[3] context: exe={ctx.exe} source={ctx.source} caret={ctx.caret_known} before={ctx.before[-40:]!r}")

    t0 = time.perf_counter()
    raw, info = stt.transcribe(audio, "")
    print(f"[4] raw ({info.get('stt_ms')} ms): {raw!r}")
    text = polish.pre_clean(raw, cfg)
    for _ in range(40):  # give the LLM warm-up a moment
        if cleaner.available:
            break
        time.sleep(0.25)
    cleaned, llm_ms = cleaner.clean(text, ctx, polish.style_for(ctx, cfg), [])
    print(f"[5] cleaned ({llm_ms} ms, available={cleaner.available}): {cleaned!r}")
    final = polish.fit_to_context(cleaned or text, ctx, cfg)
    print(f"[6] final: {final!r}  (pipeline {int((time.perf_counter() - t0) * 1000)} ms)")

    if not wait_for_title(target.name, 2):
        print("Focus left the test file; not inserting.")
        return 1
    Inserter(cfg).insert(final, ctx.exe)
    time.sleep(0.8)
    result = notepad_text(target.stem)
    print(f"[7] notepad now contains: {result!r}")
    ok = result.startswith(BEFORE.rstrip()) and "thursday" in result.lower() and "friday" not in result.lower()
    print("RESULT:", "PASS" if ok else "CHECK OUTPUT")
    # Save and close only our own tab (Ctrl+S, Ctrl+W), never other tabs.
    if wait_for_title(target.name, 1):
        for combo in ((0x11, 0x53), (0x11, 0x57)):
            _send([_key(combo[0]), _key(combo[1]), _key(combo[1], 0, KEYEVENTF_KEYUP), _key(combo[0], 0, KEYEVENTF_KEYUP)])
            time.sleep(0.6)
    return 0 if ok else 2


def wait_for_title(fragment: str, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fragment.lower() in ctxmod.foreground_window()[2].lower():
            return True
        time.sleep(0.1)
    return False


if __name__ == "__main__":
    sys.exit(main())
