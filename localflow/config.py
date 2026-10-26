"""Settings stored as JSON in %APPDATA%\\LocalFlow\\config.json.

Unknown keys in the user's file are kept; missing keys are filled from
DEFAULTS so new settings appear automatically after an update.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

# LOCALFLOW_HOME lets tests (and portable installs) use a separate data folder.
DATA_DIR = Path(os.environ.get("LOCALFLOW_HOME") or Path(os.environ.get("APPDATA", Path.home())) / "LocalFlow")
FROZEN = getattr(sys, "frozen", False)          # running from the PyInstaller build
# Read-only files shipped with the app (icons...): next to the package when
# running from source, inside the bundle when frozen.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
CONFIG_PATH = DATA_DIR / "config.json"
LOG_DIR = DATA_DIR / "logs"
HISTORY_PATH = DATA_DIR / "history.db"
SOUNDS_DIR = DATA_DIR / "sounds"

DEFAULTS: dict = {
    # Hold this key to talk; release to transcribe and insert.
    "hotkey_hold": "right ctrl",
    # Press once to start hands-free recording, again to stop.
    # (Ctrl+Alt+Space and Ctrl+Win+Space are often taken by other apps.)
    "hotkey_toggle": "alt+shift+space",
    # Re-insert the last transcript (e.g. after a failed paste).
    "hotkey_paste_last": "alt+shift+z",
    # Taps shorter than this are ignored (so a normal key press never records).
    "min_hold_ms": 300,
    "max_record_seconds": 360,

    "whisper": {
        "model": "large-v3-turbo",
        "device": "auto",          # auto | cuda | cpu
        "compute_type": "auto",    # auto | float16 | int8_float16 | int8
        "cpu_fallback_model": "small",
        "language": "en",          # "auto" to detect per recording
        "beam_size": 5,
    },

    "cleanup": {
        "enabled": True,
        # builtin = bundled llama.cpp engine + GGUF model; ollama = your own Ollama
        "backend": "builtin",
        "builtin_model": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "model": "qwen2.5:3b",          # Ollama model name (backend "ollama")
        "url": "http://127.0.0.1:11434",
        "timeout_seconds": 8,
        "min_words": 3,
        "keep_alive": "30m",
    },

    # Read the text around the cursor (UI Automation) to match casing,
    # spacing and vocabulary. Never leaves the computer.
    "use_context": True,
    "voice_commands": True,        # "new line", "new paragraph"
    "remove_fillers": True,        # um, uh, erm

    # Words Whisper should spell exactly (names, jargon).
    "dictionary": [],
    # Always replace a spoken phrase with a written one: {"spoken": "written"}
    "replacements": {},
    # Say the trigger, get the expansion: {"my address": "221B Baker Street"}
    "snippets": {},

    "restore_clipboard": True,
    "clipboard_restore_delay_ms": 500,
    # Executables that get typed character-by-character instead of pasted.
    "type_instead_apps": [],
    # Terminals where Ctrl+V isn't paste (Git Bash/mintty, PuTTY, X servers).
    "shift_insert_apps": ["mintty.exe", "putty.exe", "kitty.exe", "xming.exe", "vcxsrv.exe", "mobaxterm.exe"],
    # Remote desktops / VMs sync the clipboard late: wait before pasting and
    # restore much later.
    "slow_paste_apps": ["mstsc.exe", "msrdc.exe", "vmconnect.exe", "vmware.exe", "vmware-vmx.exe",
                        "virtualboxvm.exe", "anydesk.exe", "teamviewer.exe", "parsecd.exe",
                        "rustdesk.exe", "vncviewer.exe", "citrix.exe", "wfica32.exe"],
    "chat_apps": [
        "slack.exe", "discord.exe", "whatsapp.exe", "whatsapp.root.exe",
        "telegram.exe", "ms-teams.exe", "teams.exe", "signal.exe",
        "messenger.exe",
    ],
    "chat_title_keywords": ["WhatsApp", "Discord", "Slack", "Messenger", "Telegram", "Teams"],
    "terminal_apps": [
        "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
        "conhost.exe", "openconsole.exe", "wezterm-gui.exe", "alacritty.exe",
        "mintty.exe", "tabby.exe", "warp.exe",
    ],
    "code_apps": [
        "code.exe", "cursor.exe", "windsurf.exe", "pycharm64.exe", "idea64.exe",
        "webstorm64.exe", "rider64.exe", "devenv.exe", "sublime_text.exe",
        "notepad++.exe", "zed.exe",
    ],

    "sounds": True,
    "overlay": True,
    "mini_bar": True,              # always-on bar at the bottom; click to dictate
    "history": True,
    "keep_mic_warm": False,        # avoids clipping the first word on slow mics
    "input_device": None,          # None = system default microphone
    "first_run_done": False,

    # Writing style per kind of app (casual | neutral | formal | technical | verbatim).
    "app_styles": {"chat": "casual", "email": "formal", "document": "formal", "code": "technical",
                   "terminal": "verbatim", "general": "neutral"},
    # Per-app overrides by executable name, e.g. {"slack.exe": "formal"}.
    "app_style_overrides": {},
    # Watch your edits right after a dictation and learn new names/terms.
    "learn_from_corrections": True,
    "learned_words": [],
    # Show the words in the bar while you speak.
    "live_preview": True,
    # Hold to edit the selected text by voice ("make this more formal").
    "hotkey_edit": "right alt",
    # Meeting notes: where notes go (None = Documents\LocalFlow Meetings) and
    # whether the recording is kept after transcription.
    "meetings_folder": None,
    "meetings_keep_audio": False,
    # Check GitHub Releases for new versions (packaged app only).
    "update_check": True,
    "update_repo": "Partharsid/local-flow",
    "update_last_check": 0,
    "update_skip_version": "",
    # Troubleshooting: keep the last recording + its prompt in
    # %APPDATA%\LocalFlow\debug (overwritten each time, never uploaded).
    "debug_save_audio": False,
}


def _merge(defaults: dict, user: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        user = {}
        if self.path.exists():
            try:
                user = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Keep the broken file for the user and start from defaults.
                self.path.replace(self.path.with_suffix(".broken.json"))
        with self._lock:
            self.data = _merge(DEFAULTS, user)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            text = json.dumps(self.data, indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def __getitem__(self, key):
        with self._lock:
            return self.data[key]

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value) -> None:
        with self._lock:
            self.data[key] = value
        self.save()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def replace(self, data: dict) -> None:
        with self._lock:
            self.data = _merge(DEFAULTS, data)
        self.save()
