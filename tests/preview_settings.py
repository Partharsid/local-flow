"""Open the settings window against a throwaway config + sample history and
screenshot every page into assets/settings_<page>.png for design review."""
import ctypes
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ctypes.windll.shcore.SetProcessDpiAwareness(1)

from localflow.config import Config  # noqa: E402
from localflow.history import History  # noqa: E402
from localflow.settings_ui import PAGES, SettingsWindow  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="lf_preview_"))


class Stub:
    pass


app = Stub()
app.root = tk.Tk()
app.root.withdraw()
app.cfg = Config(tmp / "config.json")
app.cfg.set("dictionary", ["Parth", "Partharsid", "Coolify", "Supabase", "Cloudflare"])
app.cfg.set("replacements", {"qualify": "Coolify"})
app.cfg.set("snippets", {"my email": "me@example.com", "sign off": "Best,\nParth"})
app.history = History(tmp / "history.db")
now = time.time()
for i, (appname, text) in enumerate([
        ("slack.exe", "Sounds good, I'll push the fix tonight"),
        ("chrome.exe", "Can you send me the updated report before the meeting?"),
        ("code.exe", "Refactor the hotkey manager so capture can never swallow typing"),
        ("notepad.exe", "Hi Rahul\nThanks for your help.\nRegards, Parth."),
        ("outlook.exe", "I deployed the website on Coolify with a Supabase database and a Cloudflare tunnel.")]):
    app.history.add(ts=now - i * 900, app=appname, title="", raw=text, final=text,
                    audio_seconds=4 + i, total_ms=900, stt_ms=600, llm_ms=250, method="paste")
app.transcriber = Stub()
app.transcriber.error = None
app.transcriber.ready = threading.Event()
app.transcriber.ready.set()
app.transcriber.model_name = r"C:\models\large-v3-turbo"
app.transcriber.device = "cuda"
app.cleaner = Stub()
app.cleaner.available = True
app.cleaner.problem = None
app.cleaner.model_label = "Qwen3-4B-Instruct-2507-Q4_K_M"
app.mic_muted = False
app.hotkeys = Stub()
app.hotkeys.capture_next = lambda cb, timeout=8.0: None
app.hotkeys.cancel_capture = lambda: None
app.ui_call = lambda fn, *a: app.root.after(0, lambda: fn(*a))
app.apply_config = lambda cfg: app.cfg.replace(cfg)
app.autostart_enabled = lambda: False
app.toggle_autostart = lambda: None
app.open_folder = lambda w: None
app.unmute_mic = lambda: None
app.settings_window = None
app.job_cancel = None
app.meeting = Stub()
app.meeting.active = False
app.pick_and_transcribe = lambda: None
app.start_meeting = lambda: None
app.stop_meeting = lambda: None
app.check_for_updates = lambda manual=False: None
app.cfg.set("learned_words", [{"from": "Qualify", "to": "Coolify"}, {"from": "Path", "to": "Parth"}])

win = SettingsWindow(app)
out_dir = Path(__file__).resolve().parent.parent / "assets"
keys = [k for k, _l, _i in PAGES]


def grab_window(w):
    """PrintWindow renders the window itself, so this works even when the
    display is off or locked (a screen grab would be black)."""
    import win32gui
    import win32ui
    from PIL import Image
    hwnd = ctypes.windll.user32.GetAncestor(w.winfo_id(), 2)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    src = win32ui.CreateDCFromHandle(win32gui.GetWindowDC(hwnd))
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, width, height)
    mem.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)


def capture(i=0):
    if i:
        grab_window(win.win).save(out_dir / f"settings_{keys[i - 1]}.png")
    if i < len(keys):
        win.show(keys[i])
        win.win.lift()
        app.root.after(900, lambda: capture(i + 1))
    else:
        print("saved", len(keys), "pages to", out_dir)
        app.root.after(100, app.root.destroy)


app.root.after(1500, capture)
app.root.mainloop()
