"""LocalFlow application: wires hotkeys, recorder, STT, cleanup and insertion.

Threads:
  main            Tk (overlay + settings window)
  keyboard-hook   low-level hook -> self.events
  controller      reacts to hotkey events, starts/stops recording
  pipeline        transcribes/cleans/inserts one dictation at a time
  pystray         tray icon
"""
from __future__ import annotations

import ctypes
import datetime
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from . import APP_NAME, __version__
from . import context as ctxmod
from . import meeting as meeting_mod
from . import mic_status, polish
from .audio import Recorder
from .cleanup import Cleaner
from .config import CONFIG_PATH, DATA_DIR, FROZEN, HISTORY_PATH, LOG_DIR, SOUNDS_DIR, Config
from .history import History
from .hotkeys import HotkeyEvent, HotkeyManager
from .inserter import Inserter, set_clipboard_text
from .learning import CorrectionWatcher
from .overlay import AMBER, GREEN, Overlay
from .sounds import Sounds
from .stt import Transcriber
from .tray import Tray

log = logging.getLogger("localflow")


_crash_log = None


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Native crashes (ctypes callbacks, Tk) kill the process before logging
    # runs; faulthandler still writes every thread's stack to this file.
    import faulthandler
    global _crash_log
    _crash_log = open(LOG_DIR / "crash.log", "a", encoding="utf-8")  # noqa: SIM115 - must stay open
    faulthandler.enable(_crash_log, all_threads=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "localflow.log", maxBytes=1_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None and sys.stderr.isatty():
        root.addHandler(logging.StreamHandler())
    for noisy in ("urllib3", "faster_whisper", "comtypes", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def single_instance() -> object | None:
    import win32api
    import win32event
    import winerror
    mutex = win32event.CreateMutex(None, False, "Local\\LocalFlowSingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return mutex


def startup_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/LocalFlow.lnk"


class App:
    def __init__(self):
        self.cfg = Config()
        self.events: queue.Queue[str] = queue.Queue()
        self.state = "idle"            # idle | recording
        self.mode = "hold"
        self.busy = 0                  # dictations being processed
        self._busy_lock = threading.Lock()
        self.last_text: str | None = None
        self._mic_warned = False
        self.mic_muted = False
        self.job_cancel: threading.Event | None = None   # running file transcription
        self.settings_window = None
        self._ctx_future: Future | None = None
        self._ui_calls: queue.Queue = queue.Queue()
        self._pipeline = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
        self._helpers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="helper")
        self._quit = threading.Event()

        self.sounds = Sounds(SOUNDS_DIR, self.cfg["sounds"])
        self.history = History(HISTORY_PATH) if self.cfg["history"] else None
        self.recorder = Recorder(self.cfg.get("input_device"), self.cfg["keep_mic_warm"])
        self.transcriber = Transcriber(self.cfg["whisper"])
        self.cleaner = Cleaner(self.cfg["cleanup"])
        self.inserter = Inserter(self.cfg.data)
        self.hotkeys = HotkeyManager(self.events, is_injecting=self.inserter.injecting.is_set)
        self.learner = CorrectionWatcher(self._on_learned)
        self.meeting = meeting_mod.MeetingRecorder()
        self._meeting_started = datetime.datetime.now()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.overlay = Overlay(self.root, self.recorder, self.cfg["overlay"])
        self.overlay.on_click = self._on_pill_click
        self._sync_overlay_settings()
        self.tray = Tray(self)

    def _sync_overlay_settings(self) -> None:
        self.overlay.enabled = self.cfg["overlay"]
        self.overlay.mini_bar = self.cfg.get("mini_bar", True)
        self.overlay.hotkey_label = hotkey_label(self.cfg["hotkey_hold"] or self.cfg["hotkey_toggle"])

    def _on_pill_click(self, state: str) -> None:
        """Clicking the mini bar starts hands-free dictation; clicking the
        hands-free pill stops it. (Tk thread: just queue the event.)"""
        if state in ("idle", "hidden") or (state == "recording" and self.mode == "toggle"):
            self.events.put(HotkeyEvent.TOGGLE)

    # ------------------------------------------------------------------ setup
    def run(self) -> None:
        log.info("%s %s starting; config at %s", APP_NAME, __version__, CONFIG_PATH)
        self._configure_hotkeys()
        self.hotkeys.start()
        self.tray.run()
        threading.Thread(target=self._load_models, daemon=True, name="model-loader").start()
        threading.Thread(target=self._controller, daemon=True, name="controller").start()
        self.root.after(50, self._drain_ui_calls)
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _configure_hotkeys(self) -> None:
        self.hotkeys.configure(self.cfg["hotkey_hold"], self.cfg["hotkey_toggle"], self.cfg["hotkey_paste_last"],
                               self.cfg.get("hotkey_edit", ""))

    def _load_models(self) -> None:
        self.tray.set_state("loading", "LocalFlow - loading speech model…")
        warm = self.cleaner.warm_up() if self.cfg["cleanup"]["enabled"] else None
        if self.cfg["use_context"]:
            self._helpers.submit(ctxmod.prewarm)
        self.transcriber.load()
        if self.transcriber.error:
            self.tray.set_state("error", "LocalFlow - speech model failed to load")
            self.tray.notify(self.transcriber.error[:200])
            return
        self.tray.set_state("idle", f"LocalFlow - ready ({self.transcriber.model_name} on {self.transcriber.device})")
        if warm is not None:
            warm.join(timeout=90)
        self.mic_muted = mic_status.is_muted()
        self.tray.refresh()
        for problem in self.hotkeys.registration_errors:
            self.tray.notify(problem + ". Pick another one in Settings > General.")
        if self.mic_muted:
            self._mic_warned = True
            self.tray.notify("Your microphone is muted in Windows. Right-click this icon > Unmute microphone.")
        elif self.cleaner.problem and self.cfg["cleanup"]["enabled"]:
            self.tray.notify(self.cleaner.problem)
        elif not self.cfg.get("first_run_done"):
            self.tray.notify(f"Ready. Hold {self.cfg['hotkey_hold'].title()} and speak, or press "
                             f"{self.cfg['hotkey_toggle'].title()} for hands-free. Right-click this icon for settings.")
            self.cfg.set("first_run_done", True)

    def status_text(self) -> str:
        if not self.transcriber.ready.is_set():
            return "Loading speech model…"
        if self.transcriber.error:
            return "Speech model failed - see logs"
        llm = "AI cleanup on" if (self.cfg["cleanup"]["enabled"] and self.cleaner.available) else "AI cleanup off"
        return f"Ready · {self.transcriber.model_name} on {self.transcriber.device.upper()} · {llm}"

    # ------------------------------------------------------------- controller
    def _controller(self) -> None:
        while not self._quit.is_set():
            try:
                ev = self.events.get(timeout=0.25)
            except queue.Empty:
                ev = None
            try:
                if ev is None:
                    if self.state == "recording" and self.recorder.elapsed() > self.cfg["max_record_seconds"]:
                        log.info("Max recording length reached; stopping")
                        self._finish_recording()
                    elif (self.state == "recording" and self.mode == "hold" and self.recorder.elapsed() > 1.0
                          and not self.hotkeys.hold_physically_down()):
                        # The key-up never reached our hook (e.g. Win+L while
                        # holding): finish instead of recording for minutes.
                        log.info("Hold key is no longer down but no key-up arrived; finishing")
                        self.hotkeys.cancel_hold()
                        self._finish_recording()
                    continue
                self._on_event(ev)
            except Exception:  # noqa: BLE001
                log.exception("controller error on %s", ev)
                self._reset_after_error("Something went wrong - see logs")

    def _on_event(self, ev: str) -> None:
        if ev == HotkeyEvent.START_HOLD and self.state == "idle":
            self._start_recording("hold")
        elif ev == HotkeyEvent.START_EDIT and self.state == "idle":
            self._start_recording("edit")
        elif ev == HotkeyEvent.TOGGLE:
            if self.state == "idle":
                self._start_recording("toggle")
            elif self.state == "recording":
                self._finish_recording()
        elif ev == HotkeyEvent.STOP and self.state == "recording" and self.mode in ("hold", "edit"):
            if self.hotkeys.last_hold_ms < int(self.cfg["min_hold_ms"]):
                self._discard_recording(quiet=True)
            else:
                self._finish_recording()
        elif ev == HotkeyEvent.CANCEL and self.state == "recording":
            self._discard_recording(quiet=self.mode in ("hold", "edit") and self.hotkeys.held_ms() < 700)
        elif ev == HotkeyEvent.PASTE_LAST:
            self.paste_last()

    def _start_recording(self, mode: str) -> None:
        self.mode = mode
        self.learner.cancel()   # wrap up watching the previous dictation
        use_uia = bool(self.cfg["use_context"])
        self._ctx_future = self._helpers.submit(ctxmod.capture, use_uia)
        # Feedback first: opening the mic can take 100-300 ms.
        self.overlay.recording("Esc cancels" if mode == "toggle" else "", badge="Edit" if mode == "edit" else "")
        self.sounds.play("start")
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            log.error("Mic start failed: %s", e)
            self.overlay.flash("Microphone unavailable", AMBER, 3)
            self.sounds.play("error")
            return
        self.state = "recording"
        self.hotkeys.recording = True
        self.tray.set_state("recording")
        if self.cfg.get("live_preview", True) and mode != "edit":
            threading.Thread(target=self._preview_loop, args=(self.recorder.started_at,), daemon=True,
                             name="live-preview").start()

    def _preview_loop(self, session: float) -> None:
        """Show a rough transcript in the bar while the user is speaking."""
        prompt = self._whisper_prompt(list(self.cfg.get("dictionary") or []))
        while self.state == "recording" and self.recorder.started_at == session:
            time.sleep(0.9)
            if self.state != "recording" or self.recorder.elapsed() < 1.2 or not self.transcriber.ready.is_set():
                continue
            try:
                text = self.transcriber.quick(self.recorder.snapshot(20.0), prompt)
            except Exception as e:  # noqa: BLE001 - preview is best-effort
                log.debug("preview failed: %s", e)
                continue
            if text and self.state == "recording" and self.recorder.started_at == session:
                self.overlay.set_preview(text)

    def _stop_capture(self):
        audio = self.recorder.stop()
        self.state = "idle"
        self.hotkeys.recording = False
        return audio

    def _idle_tray(self) -> None:
        """Back to idle, unless an earlier dictation is still being processed."""
        if self.state != "recording":
            self.tray.set_state("working" if self.busy > 0 else "idle")

    def _discard_recording(self, quiet: bool) -> None:
        self._stop_capture()
        self._idle_tray()
        if quiet:
            self.overlay.hide()
        else:
            self.overlay.flash("Cancelled", MUTED_TEXT, 0.9)

    def _finish_recording(self) -> None:
        audio = self._stop_capture()
        self.sounds.play("stop")
        ctx_future = self._ctx_future
        with self._busy_lock:
            self.busy += 1
        self.overlay.working("Transcribing" if self.transcriber.ready.is_set() else "Loading model")
        self.tray.set_state("working")
        if self.mode == "edit":
            self._pipeline.submit(self._process_edit, audio, ctx_future)
        else:
            self._pipeline.submit(self._process, audio, ctx_future)

    def _reset_after_error(self, msg: str) -> None:
        if self.state == "recording":
            self._stop_capture()
        self.overlay.flash(msg, AMBER, 3)
        self.sounds.play("error")
        self._idle_tray()

    # --------------------------------------------------------------- pipeline
    def _process(self, audio, ctx_future: Future | None) -> None:
        t_start = time.perf_counter()
        try:
            self._process_inner(audio, ctx_future, t_start)
        except Exception as e:  # noqa: BLE001
            log.exception("Dictation failed")
            self.overlay.flash(f"Error: {str(e)[:40]}", AMBER, 3)
            self.sounds.play("error")
        finally:
            with self._busy_lock:
                self.busy -= 1
            self._idle_tray()

    def _process_inner(self, audio, ctx_future: Future | None, t_start: float) -> None:
        cfg = self.cfg.snapshot()
        if not self.transcriber.ready.wait(timeout=180) or self.transcriber.model is None:
            raise RuntimeError(self.transcriber.error or "speech model not loaded")

        try:
            # capture() gives up on its own after 1.5 s, so this rarely waits.
            ctx = ctx_future.result(timeout=1.7) if ctx_future else ctxmod.FocusContext()
        except Exception:  # noqa: BLE001
            hwnd, exe, title = ctxmod.foreground_window()
            ctx = ctxmod.FocusContext(exe=exe, title=title, hwnd=hwnd)

        # The user's own words go to Whisper; words scraped from the screen
        # only go to the LLM. Unrelated screen/UI text in Whisper's prompt
        # wrecked transcription (6 s of speech came back as "Yeah?").
        user_vocab = list(dict.fromkeys(list(cfg.get("dictionary") or [])
                                        + list((cfg.get("replacements") or {}).values())))
        vocab = list(dict.fromkeys(user_vocab + ctx.words))
        style = polish.style_for(ctx, cfg)
        prompt = self._whisper_prompt(user_vocab)

        if self.state != "recording":
            self.overlay.working("Transcribing")
        if cfg.get("debug_save_audio"):
            self._save_debug(audio, prompt, ctx)
        raw, info = self.transcriber.transcribe(audio, prompt)
        log.info("STT: %.1fs audio, rms %.4f, peak %.3f -> %d words, %d/%d segments kept",
                 info.get("duration", 0), info.get("rms", 0.0), float(abs(audio).max()) if audio.size else 0,
                 len(raw.split()), info.get("segments_kept", 0), info.get("segments", 0))
        if not raw:
            peak = float(abs(audio).max()) if audio.size else 0.0
            log.info("No speech detected (%.1fs audio, rms %.5f, peak %.5f)",
                     info.get("duration", 0), info.get("rms", 0.0), peak)
            # Digital silence means the OS gave us nothing: say why.
            problem = mic_status.diagnose() if peak < 1e-4 else None
            if problem:
                log.warning("Silent recording: %s", problem)
                self.mic_muted = mic_status.is_muted()
                self.tray.refresh()
                self.overlay.flash(problem, AMBER, 4)
                self.sounds.play("error")
                if not self._mic_warned:
                    self._mic_warned = True
                    self.tray.notify(problem)
            elif self.state != "recording":
                self.overlay.flash("No speech detected", MUTED_TEXT, 1.2)
            return
        self._mic_warned = False

        text = polish.pre_clean(raw, cfg)
        llm_ms = 0
        if (cfg["cleanup"]["enabled"] and style != "verbatim"
                and len(text.split()) >= int(cfg["cleanup"].get("min_words", 3)) and not ctx.is_password):
            if self.state != "recording":
                self.overlay.working("Polishing")
            cleaned, llm_ms = self._clean_preserving_lines(text, ctx, style, vocab, cfg)
            if cleaned:
                text = polish.apply_replacements(cleaned, cfg.get("replacements") or {})
        text = polish.fix_dictionary_spellings(text, list(cfg.get("dictionary") or []))
        text = polish.apply_snippets(text, cfg.get("snippets") or {})

        # The user may have switched windows while we were working.
        hwnd, exe, title = ctxmod.foreground_window()
        if hwnd != ctx.hwnd:
            log.info("Focus moved from %s to %s; re-reading context", ctx.exe, exe)
            ctx = ctxmod.capture(bool(cfg["use_context"]), timeout=0.8)  # user is waiting now
        final = polish.fit_to_context(text, ctx, cfg)
        self.last_text = final

        if ctxmod.is_elevated_window(ctx.hwnd):
            # Windows blocks keystrokes into admin windows from normal apps;
            # leave the text on the clipboard so the user can paste it.
            set_clipboard_text(final)
            log.info("Target %s is elevated; left text on clipboard", ctx.exe)
            self.overlay.flash("Admin window: press Ctrl+V to paste", AMBER, 3)
            return

        method = self.inserter.insert(final, ctx.exe)
        total_ms = int((time.perf_counter() - t_start) * 1000)
        log.info("Inserted %d chars into %s via %s | audio %.1fs, stt %sms, llm %sms, total %sms, ctx=%s",
                 len(final), ctx.exe, method, info.get("duration", 0), info.get("stt_ms"), llm_ms, total_ms,
                 ctx.source)
        if cfg.get("learn_from_corrections", True) and not ctx.is_password and method != "none":
            self.learner.watch(final, ctx.hwnd, set(cfg.get("dictionary") or []))
        if self.history and not ctx.is_password:
            self.history.add(app=ctx.exe, title=ctx.title[:200], raw=raw, final=final,
                             audio_seconds=round(info.get("duration", 0), 2), total_ms=total_ms,
                             stt_ms=info.get("stt_ms"), llm_ms=llm_ms, method=method)
        if self.state != "recording":
            words = len(final.split())
            self.overlay.flash(f"✓  {words} word{'s' if words != 1 else ''} · {total_ms / 1000:.1f}s", GREEN, 1.1)

    def _process_edit(self, audio, ctx_future: Future | None) -> None:
        """Voice edit: rewrite the selection (or write new text) per instruction."""
        t_start = time.perf_counter()
        try:
            cfg = self.cfg.snapshot()
            if not self.transcriber.ready.wait(timeout=180) or self.transcriber.model is None:
                raise RuntimeError(self.transcriber.error or "speech model not loaded")
            try:
                ctx = ctx_future.result(timeout=1.7) if ctx_future else ctxmod.FocusContext()
            except Exception:  # noqa: BLE001
                hwnd, exe, title = ctxmod.foreground_window()
                ctx = ctxmod.FocusContext(exe=exe, title=title, hwnd=hwnd)
            instruction, info = self.transcriber.transcribe(audio, "")
            if not instruction:
                self.overlay.flash("No instruction heard", MUTED_TEXT, 1.5)
                return
            if ctx.is_password:
                self.overlay.flash("Can't edit a password field", AMBER, 2)
                return
            if not self.cleaner.available:
                self.overlay.flash("Voice edit needs AI cleanup turned on", AMBER, 3)
                return
            selected = ctx.selected.strip() and ctx.selected
            if not selected:
                selected = self.inserter.copy_selection()
            self.overlay.working("Rewriting" if selected.strip() else "Writing")
            result = self.cleaner.rewrite(selected, instruction, ctx, polish.style_for(ctx, cfg))
            if not result.strip():
                self.overlay.flash("The AI returned nothing", AMBER, 2)
                return
            if not selected.strip():
                result = polish.fit_to_context(result, ctx, cfg)   # writing at the cursor
            if ctxmod.is_elevated_window(ctx.hwnd):
                set_clipboard_text(result)
                self.overlay.flash("Admin window: press Ctrl+V to paste", AMBER, 3)
                return
            method = self.inserter.insert(result, ctx.exe)
            self.last_text = result
            total_ms = int((time.perf_counter() - t_start) * 1000)
            log.info("Voice edit (%s) in %s: %d -> %d chars, %d ms", "rewrite" if selected.strip() else "write",
                     ctx.exe, len(selected), len(result), total_ms)
            if self.history:
                self.history.add(app=ctx.exe, title=ctx.title[:200], raw=f"[edit] {instruction}", final=result,
                                 audio_seconds=round(info.get("duration", 0), 2), total_ms=total_ms,
                                 stt_ms=info.get("stt_ms"), llm_ms=0, method=method)
            self.overlay.flash("✓  Edited" if selected.strip() else "✓  Written", GREEN, 1.2)
        except Exception as e:  # noqa: BLE001
            log.exception("Voice edit failed")
            self.overlay.flash(f"Edit failed: {str(e)[:36]}", AMBER, 3)
            self.sounds.play("error")
        finally:
            with self._busy_lock:
                self.busy -= 1
            self._idle_tray()

    def _clean_preserving_lines(self, text: str, ctx, style: str, vocab: list[str], cfg: dict):
        """LLM cleanup that keeps spoken line breaks ("new line").

        Given multi-line input, the model merged the lines into one sentence,
        so each line is cleaned on its own and the breaks are put back."""
        if "\n" not in text:
            return self.cleaner.clean(text, ctx, style, vocab)
        min_words = int(cfg["cleanup"].get("min_words", 3))
        out_lines, total_ms, changed = [], 0, False
        for line in text.split("\n"):
            if len(line.split()) >= min_words:
                cleaned, ms = self.cleaner.clean(line, ctx, style, vocab)
                total_ms += ms
                if cleaned and "\n" not in cleaned:
                    out_lines.append(cleaned)
                    changed = True
                    continue
            out_lines.append(line)
        return ("\n".join(out_lines) if changed else None), total_ms

    @staticmethod
    def _save_debug(audio, prompt: str, ctx) -> None:
        import json
        import wave
        d = DATA_DIR / "debug"
        d.mkdir(parents=True, exist_ok=True)
        pcm = (audio.clip(-1, 1) * 32767).astype("<i2").tobytes()
        with wave.open(str(d / "last.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        (d / "last.json").write_text(json.dumps({"prompt": prompt, "exe": ctx.exe, "source": ctx.source,
                                                 "before_tail": ctx.before[-200:]}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    @staticmethod
    def _whisper_prompt(user_vocab: list[str]) -> str:
        """A short, natural sentence naming the user's own terms. Whisper reads
        its prompt as preceding speech, so it must stay small and relevant."""
        if not user_vocab:
            return ""
        return ("I often mention " + ", ".join(user_vocab[:25]) + ".")[:400]

    # ----------------------------------------------------------------- actions
    def _on_learned(self, pairs: list[tuple[str, str]]) -> None:
        """Learner thread: hand over to the UI thread, which owns config changes."""
        self.ui_call(self._apply_learned, pairs)

    def _apply_learned(self, pairs: list[tuple[str, str]]) -> None:
        cfg = self.cfg.snapshot()
        words = [new for _old, new in pairs if new not in (cfg.get("dictionary") or [])]
        if not words:
            return
        cfg["dictionary"] = list(cfg.get("dictionary") or []) + words
        learned = list(cfg.get("learned_words") or []) + [{"from": o, "to": n, "ts": time.time()} for o, n in pairs]
        cfg["learned_words"] = learned[-300:]
        self.apply_config(cfg)
        log.info("Added %d learned word(s) to the dictionary", len(words))
        self.overlay.flash("Learned " + ", ".join(f"“{w}”" for w in words[:3]), GREEN, 2.2)

    # -- file transcription --------------------------------------------------
    def pick_and_transcribe(self) -> None:
        """Tk thread: choose a media file, then transcribe it in the background."""
        from tkinter import filedialog

        from .files import MEDIA_TYPES
        if self.job_cancel is not None:
            self.overlay.flash("A file is already being transcribed", AMBER, 2)
            return
        self.root.attributes("-topmost", True)   # keep the dialog in front
        try:
            path = filedialog.askopenfilename(parent=self.root, title="Transcribe a file", filetypes=MEDIA_TYPES)
        finally:
            self.root.attributes("-topmost", False)
        if path:
            self.transcribe_path(Path(path))

    def transcribe_path(self, path: Path) -> None:
        from .files import transcribe_file
        cancel = threading.Event()
        self.job_cancel = cancel
        self.tray.refresh()

        def work():
            try:
                if not self.transcriber.ready.wait(180) or self.transcriber.model is None:
                    raise RuntimeError("speech model not loaded")
                self.overlay.working("Transcribing file")
                prompt = self._whisper_prompt(list(self.cfg.get("dictionary") or []))
                txt, _srt, segs = transcribe_file(
                    self.transcriber, path, lambda f: self.overlay.working(f"Transcribing file {int(f * 100)}%"),
                    cancel, prompt)
                self.overlay.flash(f"Transcript saved · {len(segs)} segments", GREEN, 3)
                subprocess.Popen(["explorer", "/select,", str(txt)])
            except InterruptedError:
                self.overlay.flash("File transcription cancelled", MUTED_TEXT, 1.5)
            except Exception as e:  # noqa: BLE001
                log.exception("File transcription failed")
                self.overlay.flash(f"Couldn't transcribe: {str(e)[:40]}", AMBER, 4)
            finally:
                self.job_cancel = None
                self.tray.refresh()
        threading.Thread(target=work, daemon=True, name="file-transcribe").start()

    # -- meeting notes ------------------------------------------------------------
    def start_meeting(self) -> None:
        if self.meeting.active:
            return
        try:
            self.meeting.start()
        except Exception as e:  # noqa: BLE001
            log.exception("Could not start meeting recording")
            self.overlay.flash(f"Can't record meeting: {str(e)[:36]}", AMBER, 4)
            return
        self._meeting_started = datetime.datetime.now()
        self.overlay.meeting_since = time.monotonic()
        self.overlay.kick()
        self.tray.refresh()
        self.tray.notify("Recording meeting notes (your mic + computer audio). "
                         "Stop from the tray menu when you're done.")

    def stop_meeting(self) -> None:
        if not self.meeting.active:
            return
        duration = self.meeting.elapsed()
        folder = self.meeting.stop()
        self.overlay.meeting_since = 0.0
        self.tray.refresh()
        started = self._meeting_started
        cancel = threading.Event()
        self.job_cancel = cancel

        def work():
            try:
                if not self.transcriber.ready.wait(180) or self.transcriber.model is None:
                    raise RuntimeError("speech model not loaded")
                self.overlay.working("Transcribing meeting")
                prompt = self._whisper_prompt(list(self.cfg.get("dictionary") or []))
                notes = meeting_mod.finish(
                    self.cfg.snapshot(), self.transcriber, self.cleaner, folder, started, duration,
                    lambda f: self.overlay.working(f"Transcribing meeting {int(f * 100)}%"), cancel, prompt)
                self.overlay.flash("Meeting notes saved", GREEN, 3)
                os.startfile(str(notes))           # opens in the default Markdown/text app
            except Exception as e:  # noqa: BLE001
                log.exception("Meeting notes failed")
                self.overlay.flash(f"Meeting notes failed: {str(e)[:30]}", AMBER, 4)
            finally:
                self.job_cancel = None
                self.tray.refresh()
        threading.Thread(target=work, daemon=True, name="meeting-notes").start()

    def cancel_job(self) -> None:
        if self.job_cancel is not None:
            self.job_cancel.set()

    def unmute_mic(self) -> None:
        """Tray action: the user asked us to unmute their microphone."""
        ok = mic_status.unmute()
        self.mic_muted = not ok
        self.tray.refresh()
        self.tray.notify("Microphone unmuted. Hold " + self.cfg["hotkey_hold"].title() + " and speak."
                         if ok else "Could not unmute the microphone. Use Sound settings > Input.")

    def paste_last(self, from_tray: bool = False) -> None:
        text = self.last_text or (self.history.last_final() if self.history else None)
        if not text:
            self.overlay.flash("Nothing to paste yet", MUTED_TEXT, 1.2)
            return
        if from_tray:
            # The tray menu itself has focus, so pasting would go nowhere.
            set_clipboard_text(text)
            self.tray.notify("Last transcript copied to the clipboard.")
            return
        hwnd, exe, _ = ctxmod.foreground_window()
        self._helpers.submit(self.inserter.insert, text, exe)

    def ui_call(self, fn, *args) -> None:
        """Run fn on the Tk thread (pystray callbacks arrive on its thread)."""
        self._ui_calls.put((fn, args))

    def _drain_ui_calls(self) -> None:
        try:
            while True:
                fn, args = self._ui_calls.get_nowait()
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001
                    log.exception("UI call failed")
        except queue.Empty:
            pass
        if self._quit.is_set():
            self.root.quit()
            return
        self.root.after(50, self._drain_ui_calls)

    def open_settings(self) -> None:
        from .settings_ui import SettingsWindow
        if self.settings_window is not None:
            self.settings_window.win.lift()
            self.settings_window.win.focus_force()
            return
        self.settings_window = SettingsWindow(self)

    def apply_config(self, new: dict) -> None:
        old = self.cfg.snapshot()
        self.cfg.replace(new)
        cfg = self.cfg.snapshot()
        self.inserter.cfg = self.cfg.data
        self.sounds.enabled = cfg["sounds"]
        self._sync_overlay_settings()
        self.cleaner.cfg = cfg["cleanup"]
        engine_keys = ("enabled", "backend", "model", "builtin_model")
        if cfg["cleanup"]["enabled"] and any(old["cleanup"].get(k) != cfg["cleanup"].get(k) for k in engine_keys):
            self.cleaner.warm_up()
        elif not cfg["cleanup"]["enabled"]:
            self.cleaner.shutdown()          # free the GPU memory
            self.cleaner.available = False
        if cfg["history"] and not self.history:
            self.history = History(HISTORY_PATH)
        elif not cfg["history"]:
            self.history = None
        if cfg["input_device"] != old["input_device"]:
            self.recorder.close()
            self.recorder.device = cfg["input_device"]
        if cfg["keep_mic_warm"] != old["keep_mic_warm"]:
            self.recorder.set_keep_warm(cfg["keep_mic_warm"])
        self._configure_hotkeys()
        w_old, w_new = old["whisper"], cfg["whisper"]
        self.transcriber.cfg = w_new
        if (w_old["model"], w_old["device"], w_old["compute_type"]) != (w_new["model"], w_new["device"], w_new["compute_type"]):
            log.info("Speech model changed to %s; reloading", w_new["model"])
            self.transcriber.unload()
            threading.Thread(target=self._load_models, daemon=True, name="model-loader").start()
        self.tray.refresh()

    def toggle_setting(self, dotted: str) -> None:
        cfg = self.cfg.snapshot()
        node = cfg
        *path, last = dotted.split(".")
        for p in path:
            node = node[p]
        node[last] = not node[last]
        self.apply_config(cfg)

    def autostart_enabled(self) -> bool:
        return startup_shortcut().exists()

    def toggle_autostart(self) -> None:
        lnk = startup_shortcut()
        if lnk.exists():
            lnk.unlink()
        else:
            create_shortcut(lnk)
        self.tray.refresh()

    def open_folder(self, which: str) -> None:
        path = LOG_DIR if which == "logs" else DATA_DIR
        subprocess.Popen(["explorer", str(path)])

    def quit(self) -> None:
        self._quit.set()

    def _shutdown(self) -> None:
        log.info("Shutting down")
        self._quit.set()
        self.hotkeys.stop()
        if self.meeting.active:
            self.meeting.stop()          # recording files stay in the data folder
        self.cleaner.shutdown()
        self.recorder.close()
        self.tray.stop()
        self._pipeline.shutdown(wait=False, cancel_futures=True)
        self._helpers.shutdown(wait=False, cancel_futures=True)


MUTED_TEXT = "#c9cad1"


def hotkey_label(spec: str) -> str:
    """'right ctrl' -> 'Right Ctrl', 'ctrl+alt+space' -> 'Ctrl+Alt+Space'."""
    return "+".join(p.strip().title() for p in (spec or "").split("+")) or "the hotkey"


def create_shortcut(lnk: Path) -> None:
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(lnk))
    if FROZEN:  # packaged app: LocalFlow.exe itself
        sc.TargetPath = sys.executable
        sc.WorkingDirectory = str(Path(sys.executable).parent)
    else:       # running from source
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        sc.TargetPath = str(pythonw if pythonw.exists() else sys.executable)
        sc.Arguments = "-m localflow"
        sc.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
    from .branding import ensure_ico
    sc.IconLocation = str(ensure_ico())
    sc.Description = "LocalFlow - private offline dictation"
    sc.Save()


def main() -> int:
    setup_logging()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    if "--self-test" in sys.argv:   # headless check; fine alongside a running LocalFlow
        from .selftest import run_self_test
        return run_self_test(sys.argv[sys.argv.index("--self-test") + 1:])
    if "--setup" in sys.argv:       # (re)run first-run setup only, e.g. --setup --auto
        from . import firstrun
        cfg = Config()
        if not cfg.get("first_run_done"):
            cfg.replace(firstrun.recommended_config(cfg.snapshot()))
        ok = not firstrun.plan(cfg.snapshot()) or firstrun.run_setup_window(cfg, auto="--auto" in sys.argv)
        return 0 if ok else 1
    mutex = single_instance()
    if mutex is None:
        ctypes.windll.user32.MessageBoxW(None, "LocalFlow is already running (see the tray icon).", APP_NAME, 0x40)
        return 0
    try:
        from . import firstrun
        cfg = Config()
        if not cfg.get("first_run_done"):
            cfg.replace(firstrun.recommended_config(cfg.snapshot()))
        if firstrun.plan(cfg.snapshot()) and not firstrun.run_setup_window(cfg):
            return 0                       # user closed setup before it finished
        App().run()
    except Exception:  # noqa: BLE001
        log.exception("Fatal error")
        ctypes.windll.user32.MessageBoxW(None, f"LocalFlow crashed. Details are in\n{LOG_DIR}", APP_NAME, 0x10)
        return 1
    return 0
