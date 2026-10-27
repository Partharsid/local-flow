"""LocalFlow settings: a dark, sidebar-based window built with CustomTkinter.

Every change is applied immediately (no Save button). Runs on the Tk main
thread; background work (Ollama model list) hands results back via ui_call.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import requests
import sounddevice as sd

from . import __version__
from .branding import ensure_ico
from .hotkeys import parse_combo

# ---------------------------------------------------------------- theme ----
BG = "#0f1014"
SIDEBAR = "#131419"
CARD = "#1a1b22"
CARD_HI = "#20212a"
LINE = "#2a2b35"
TEXT = "#f4f4f6"
MUTED = "#9a9aa6"
ACCENT = "#8b5cf6"
ACCENT_HOVER = "#7c4ef0"
CYAN = "#22d3ee"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#ff4d6d"

LANGUAGES = [("English", "en"), ("Auto-detect", "auto"), ("Hindi", "hi"), ("Telugu", "te"), ("Tamil", "ta"),
             ("Kannada", "kn"), ("Malayalam", "ml"), ("Marathi", "mr"), ("Bengali", "bn"), ("Gujarati", "gu"),
             ("Urdu", "ur"), ("Spanish", "es"), ("French", "fr"), ("German", "de"), ("Japanese", "ja"),
             ("Chinese", "zh"), ("Arabic", "ar"), ("Portuguese", "pt"), ("Russian", "ru")]
MODELS = [("large-v3-turbo", "Turbo · best balance (recommended)"),
          ("large-v3", "Large · most accurate, slower"),
          ("distil-large-v3", "Distil · fastest large, English only"),
          ("medium", "Medium"), ("small", "Small · light, CPU friendly"), ("base", "Base · tiny")]
PAGES = [("home", "Home", "⌂"), ("general", "General", "⚙"), ("models", "Models & AI", "✦"),
         ("tools", "Files & meetings", "◉"), ("dictionary", "Dictionary", "Aa"), ("snippets", "Snippets", "✎"),
         ("history", "History", "⟲"), ("about", "About", "ⓘ")]
STYLE_LABELS = {"casual": "Casual", "neutral": "Neutral", "formal": "Formal", "technical": "Technical",
                "verbatim": "Verbatim (no AI rewrite)"}
APP_KINDS = [("chat", "Chat apps", "Slack, WhatsApp, Discord, Teams"),
             ("email", "Email", "Outlook, Gmail, Thunderbird"),
             ("document", "Documents", "Word, Notion, Google Docs, Obsidian"),
             ("code", "Code editors", "VS Code, Cursor, JetBrains"),
             ("terminal", "Terminals", "Windows Terminal, PowerShell, cmd"),
             ("general", "Everything else", "")]


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def pretty_key(spec: str) -> str:
    return "+".join(p.strip().title() for p in spec.split("+")) if spec else "Not set"


class SettingsWindow:
    def __init__(self, app):
        ctk.set_appearance_mode("dark")
        self.app = app
        self._capture_token = None
        self._toast_job = None
        self.win = ctk.CTkToplevel(app.root, fg_color=BG)
        self.win.title("LocalFlow")
        self.win.geometry("980x660")
        self.win.minsize(860, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        ico = str(ensure_ico())
        self.win.after(250, lambda: self.win.iconbitmap(ico))  # CTkToplevel resets the icon at start

        self.win.grid_columnconfigure(1, weight=1)
        self.win.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self.win, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.header = ctk.CTkLabel(self.content, text="", font=font(24, "bold"), text_color=TEXT, anchor="w")
        self.header.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 6))
        self.toast = ctk.CTkLabel(self.content, text="", font=font(12), text_color=GREEN, anchor="e")
        self.toast.grid(row=0, column=0, sticky="e", padx=32, pady=(30, 6))

        self.pages: dict[str, ctk.CTkScrollableFrame] = {}
        self._builders = {"home": self._page_home, "general": self._page_general, "models": self._page_models,
                          "dictionary": self._page_dictionary, "snippets": self._page_snippets,
                          "history": self._page_history, "about": self._page_about, "tools": self._page_tools}
        self.show("home")
        self.win.after(100, self.win.lift)
        self.win.after(120, self.win.focus_force)

    # ------------------------------------------------------------ layout
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self.win, fg_color=SIDEBAR, corner_radius=0, width=220)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        logo = ctk.CTkFrame(bar, fg_color="transparent")
        logo.pack(fill="x", padx=20, pady=(24, 26))
        dot = ctk.CTkLabel(logo, text="●", font=font(20), text_color=ACCENT, width=20)
        dot.pack(side="left")
        ctk.CTkLabel(logo, text=" LocalFlow", font=font(19, "bold"), text_color=TEXT).pack(side="left")
        self.nav: dict[str, ctk.CTkButton] = {}
        for key, label, icon in PAGES:
            b = ctk.CTkButton(bar, text=f"  {icon}    {label}", anchor="w", height=40, corner_radius=10,
                              font=font(14), fg_color="transparent", hover_color=CARD_HI, text_color=MUTED,
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=12, pady=2)
            self.nav[key] = b
        self.status = ctk.CTkLabel(bar, text="", font=font(12), text_color=MUTED, anchor="w", justify="left")
        self.status.pack(side="bottom", fill="x", padx=22, pady=20)
        self._refresh_status()

    def show(self, key: str) -> None:
        for k, b in self.nav.items():
            b.configure(fg_color=CARD_HI if k == key else "transparent", text_color=TEXT if k == key else MUTED)
        for p in self.pages.values():
            p.grid_forget()
        # History and Home are rebuilt each visit so they show fresh data.
        if key in ("home", "history") and key in self.pages:
            self.pages.pop(key).destroy()
        if key not in self.pages:
            page = ctk.CTkScrollableFrame(self.content, fg_color=BG, corner_radius=0,
                                          scrollbar_button_color=LINE, scrollbar_button_hover_color=MUTED)
            page.grid_columnconfigure(0, weight=1)
            self._builders[key](page)
            self.pages[key] = page
        self.pages[key].grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 18))
        self.header.configure(text=dict((k, lbl) for k, lbl, _ in PAGES)[key])

    def _card(self, parent, title: str | None = None, subtitle: str | None = None) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, border_width=1, border_color=LINE)
        card.pack(fill="x", padx=14, pady=8)
        if title:
            ctk.CTkLabel(card, text=title, font=font(15, "bold"), text_color=TEXT, anchor="w").pack(
                fill="x", padx=20, pady=(16, 0 if subtitle else 8))
        if subtitle:
            ctk.CTkLabel(card, text=subtitle, font=font(12), text_color=MUTED, anchor="w", justify="left",
                         wraplength=620).pack(fill="x", padx=20, pady=(2, 8))
        return card

    def _row(self, card, label: str, hint: str | None = None) -> ctk.CTkFrame:
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=7)
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(left, text=label, font=font(13), text_color=TEXT, anchor="w").pack(fill="x")
        if hint:
            ctk.CTkLabel(left, text=hint, font=font(11), text_color=MUTED, anchor="w", justify="left",
                         wraplength=460).pack(fill="x")
        return row

    def _switch(self, card, label: str, hint: str | None, path: str, after=None) -> None:
        row = self._row(card, label, hint)
        var = tk.BooleanVar(value=self._get(path))

        def changed():
            self._commit(lambda c: self._set(c, path, var.get()))
            if after:
                after(var.get())
        ctk.CTkSwitch(row, text="", variable=var, command=changed, progress_color=ACCENT,
                      button_color=TEXT, button_hover_color="#ffffff", fg_color=LINE, width=46).pack(side="right")

    def _menu(self, row, values: list[str], current: str, command, width=260) -> ctk.CTkOptionMenu:
        m = ctk.CTkOptionMenu(row, values=values, command=command, width=width, height=34, corner_radius=10,
                              fg_color=CARD_HI, button_color=CARD_HI, button_hover_color=LINE,
                              dropdown_fg_color=CARD_HI, dropdown_hover_color=LINE, font=font(13),
                              dropdown_font=font(13), text_color=TEXT, dynamic_resizing=False)
        m.set(current)
        m.pack(side="right")
        return m

    # ------------------------------------------------------------ config helpers
    def _get(self, path: str):
        node = self.app.cfg.snapshot()
        for p in path.split("."):
            node = node[p]
        return node

    @staticmethod
    def _set(cfg: dict, path: str, value) -> None:
        *parents, last = path.split(".")
        node = cfg
        for p in parents:
            node = node[p]
        node[last] = value

    def _commit(self, mutate, message: str = "Saved") -> None:
        cfg = self.app.cfg.snapshot()
        mutate(cfg)
        self.app.apply_config(cfg)
        self._show_toast(message)
        self._refresh_status()

    def _show_toast(self, text: str, color: str = GREEN) -> None:
        self.toast.configure(text=f"✓ {text}" if color == GREEN else text, text_color=color)
        if self._toast_job:
            self.win.after_cancel(self._toast_job)
        self._toast_job = self.win.after(1800, lambda: self.toast.configure(text=""))

    def _refresh_status(self) -> None:
        t = self.app.transcriber
        if t.error:
            lines = [("●", RED, "Speech model failed")]
        elif not t.ready.is_set():
            lines = [("●", AMBER, "Loading speech model…")]
        else:
            lines = [("●", GREEN, f"Running on {(t.device or '').upper()}")]
        cleanup = self.app.cfg["cleanup"]["enabled"]
        lines.append(("●", GREEN if cleanup and self.app.cleaner.available else MUTED,
                      "AI cleanup on" if cleanup and self.app.cleaner.available else "AI cleanup off"))
        if getattr(self.app, "mic_muted", False):
            lines.append(("●", AMBER, "Microphone muted"))
        self.status.configure(text="\n".join(f"{dot}  {txt}" for dot, _c, txt in lines),
                              text_color=lines[0][1] if lines[0][1] != GREEN else MUTED)

    # ------------------------------------------------------------ pages
    def _page_home(self, page) -> None:
        stats = self.app.history.stats() if self.app.history else {"count": 0, "audio_seconds": 0, "words": 0}
        words = int(stats["words"] or 0)
        speak_min = (stats["audio_seconds"] or 0) / 60
        saved_min = max(0.0, words / 40 - speak_min)          # vs typing at 40 wpm
        grid = ctk.CTkFrame(page, fg_color="transparent")
        grid.pack(fill="x", padx=8, pady=(4, 6))
        tiles = [(f"{words:,}", "words dictated", ACCENT), (f"{saved_min:,.0f} min", "saved vs typing", CYAN),
                 (f"{stats['count']:,}", "dictations", GREEN),
                 (f"{(words / speak_min) if speak_min else 0:,.0f}", "words per minute", AMBER)]
        for i, (value, label, color) in enumerate(tiles):
            grid.grid_columnconfigure(i, weight=1, uniform="tiles")
            t = ctk.CTkFrame(grid, fg_color=CARD, corner_radius=16, border_width=1, border_color=LINE)
            t.grid(row=0, column=i, sticky="ew", padx=6, pady=4)
            ctk.CTkLabel(t, text=value, font=font(26, "bold"), text_color=color, anchor="w").pack(
                fill="x", padx=18, pady=(16, 0))
            ctk.CTkLabel(t, text=label, font=font(12), text_color=MUTED, anchor="w").pack(fill="x", padx=18,
                                                                                         pady=(0, 16))

        hk = pretty_key(self.app.cfg["hotkey_hold"])
        card = self._card(page, "How to dictate", f"Hold {hk}, speak, release. Or press "
                                                   f"{pretty_key(self.app.cfg['hotkey_toggle'])} for hands-free, "
                                                   f"or click the bar at the bottom of your screen. Esc cancels.")
        if getattr(self.app, "mic_muted", False):
            row = self._row(card, "Your microphone is muted in Windows", "Nothing can be heard until it's unmuted.")
            ctk.CTkButton(row, text="Unmute microphone", fg_color=AMBER, hover_color="#f59e0b",
                          text_color="#111", font=font(13, "bold"), corner_radius=10,
                          command=lambda: (self.app.unmute_mic(), self.show("home"))).pack(side="right")

        t = self.app.transcriber
        card = self._card(page, "Engine")
        row = self._row(card, "Speech recognition", "Whisper runs on this PC; audio never leaves it.")
        state = "Failed - see logs" if t.error else (f"{(t.model_name or '').split(chr(92))[-1]} · "
                                                      f"{(t.device or '').upper()}" if t.ready.is_set() else "Loading…")
        ctk.CTkLabel(row, text=state, font=font(13), text_color=MUTED).pack(side="right")
        row = self._row(card, "AI cleanup", "Fixes fillers, self-corrections and punctuation, locally.")
        cl = self.app.cleaner
        ctk.CTkLabel(row, text=(cl.model_label if cl.available else (cl.problem or "Off")),
                     font=font(13), text_color=MUTED).pack(side="right")

        card = self._card(page, "Recent")
        recent = self.app.history.recent(5) if self.app.history else []
        if not recent:
            ctk.CTkLabel(card, text="Nothing yet. Your dictations will appear here.", font=font(13),
                         text_color=MUTED, anchor="w").pack(fill="x", padx=20, pady=(0, 16))
        for e in recent:
            self._history_item(card, e)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    def _page_general(self, page) -> None:
        card = self._card(page, "Shortcuts", "Click a shortcut, then press the keys you want.")
        for key, label, hint in (("hotkey_hold", "Hold to talk", "Hold, speak, release."),
                                 ("hotkey_toggle", "Hands-free", "Press to start, press again to stop."),
                                 ("hotkey_edit", "Edit by voice",
                                  "Select text, hold, and say what to change. Nothing selected: it writes for you."),
                                 ("hotkey_paste_last", "Paste last transcript", "Re-insert your last dictation.")):
            row = self._row(card, label, hint)
            btn = ctk.CTkButton(row, text=pretty_key(self.app.cfg[key]), width=180, height=34, corner_radius=10,
                                fg_color=CARD_HI, hover_color=LINE, border_width=1, border_color=LINE,
                                text_color=TEXT, font=font(13, "bold"))
            btn.configure(command=lambda k=key, b=btn: self._record_hotkey(k, b))
            btn.pack(side="right")

        card = self._card(page, "Input")
        row = self._row(card, "Microphone")
        devices = [("System default", None)]
        try:
            for i, d in enumerate(sd.query_devices()):
                if (d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME"
                        and "sound mapper" not in d["name"].lower()):
                    devices.append((d["name"], i))
        except Exception:  # noqa: BLE001
            pass
        names = dict(devices)
        cur = next((n for n, i in devices if i == self.app.cfg.get("input_device")), "System default")
        self._menu(row, list(names), cur, lambda v: self._commit(lambda c: c.__setitem__("input_device", names[v])))
        row = self._row(card, "Language", "Choose your language for the best accuracy.")
        langs = dict(LANGUAGES)
        cur = next((n for n, c in LANGUAGES if c == self.app.cfg["whisper"]["language"]), "English")
        self._menu(row, list(langs), cur, lambda v: self._commit(lambda c: self._set(c, "whisper.language", langs[v])))

        card = self._card(page, "Behaviour")
        self._switch(card, "Show the dictation bar", "A small bar at the bottom of the screen. Click it to dictate.",
                     "mini_bar")
        self._switch(card, "Sounds", "Soft start and stop sounds.", "sounds")
        self._switch(card, "Restore clipboard after pasting", "Your copied content comes back automatically.",
                     "restore_clipboard")
        self._switch(card, "Live preview", "Show your words in the bar while you speak.", "live_preview")
        self._switch(card, "Learn from my corrections",
                     "When you fix a misheard name right after dictating, it's added to your Dictionary.",
                     "learn_from_corrections")
        self._switch(card, "Keep microphone ready", "Never clips your first word, but keeps the mic in use.",
                     "keep_mic_warm")
        row = self._row(card, "Start with Windows")
        var = tk.BooleanVar(value=self.app.autostart_enabled())
        ctk.CTkSwitch(row, text="", variable=var, progress_color=ACCENT, fg_color=LINE, button_color=TEXT,
                      command=lambda: (self.app.toggle_autostart(), self._show_toast("Saved"))).pack(side="right")

    def _page_models(self, page) -> None:
        card = self._card(page, "Speech recognition", "Runs on your GPU. Changing the model downloads it once.")
        row = self._row(card, "Whisper model")
        labels = {m: f"{d}" for m, d in MODELS}
        cur_model = self.app.cfg["whisper"]["model"]
        cur = labels.get(cur_model, cur_model)
        inverse = {v: k for k, v in labels.items()}
        self._menu(row, list(labels.values()), cur,
                   lambda v: self._commit(lambda c: self._set(c, "whisper.model", inverse[v]),
                                          "Switching model…"), width=320)

        card = self._card(page, "AI cleanup", "A small local language model polishes each dictation on your PC.")
        self._switch(card, "Enable AI cleanup", "Removes fillers, applies self-corrections, fixes punctuation.",
                     "cleanup.enabled")
        engines = {"Built-in · Qwen3-4B (private, no setup)": "builtin", "Ollama · your own models": "ollama"}
        row = self._row(card, "Engine", "The built-in engine runs on your GPU with no extra software.")
        cur_engine = next(k for k, v in engines.items() if v == self.app.cfg["cleanup"].get("backend", "builtin"))
        self._menu(row, list(engines), cur_engine,
                   lambda v: (self._commit(lambda c: self._set(c, "cleanup.backend", engines[v]), "Switching engine…"),
                              self.show("models")), width=320)
        if self.app.cfg["cleanup"].get("backend", "builtin") == "ollama":
            row = self._row(card, "Ollama model", "Any Ollama chat model. 3B models are fast; 7B+ are smarter.")
            self.llm_menu = self._menu(row, [self.app.cfg["cleanup"]["model"]], self.app.cfg["cleanup"]["model"],
                                       lambda v: self._commit(lambda c: self._set(c, "cleanup.model", v)))
            threading.Thread(target=self._load_ollama_models, daemon=True).start()

        card = self._card(page, "Writing style per app", "How the AI polishes text in each kind of app.")
        labels = list(STYLE_LABELS.values())
        by_label = {v: k for k, v in STYLE_LABELS.items()}
        for kind, title, hint in APP_KINDS:
            row = self._row(card, title, hint or None)
            cur = STYLE_LABELS.get((self.app.cfg.get("app_styles") or {}).get(kind, "neutral"), "Neutral")
            self._menu(row, labels, cur, lambda v, kind=kind: self._commit(
                lambda c: c.setdefault("app_styles", {}).__setitem__(kind, by_label[v])), width=220)
        self._pairs_card(page, "app_style_overrides", "Per-app overrides",
                         "Give one app its own style: casual, neutral, formal, technical or verbatim.",
                         "App (e.g. slack.exe)", "Style (e.g. formal)")

        card = self._card(page, "Smart formatting")
        self._switch(card, "Read text around the cursor",
                     "Matches capitals, spacing and names to what you're writing. Stays on this PC.", "use_context")
        self._switch(card, "Voice commands", 'Say "new line" or "new paragraph".', "voice_commands")
        self._switch(card, "Remove filler words", "um, uh, erm…", "remove_fillers")

    def _load_ollama_models(self) -> None:
        try:
            tags = requests.get(self.app.cfg["cleanup"]["url"].rstrip("/") + "/api/tags", timeout=3).json()
            names = sorted(m["name"] for m in tags.get("models", []))
        except requests.RequestException:
            return
        if names:
            self.app.ui_call(lambda: self.llm_menu.winfo_exists() and self.llm_menu.configure(values=names))

    def _page_dictionary(self, page) -> None:
        card = self._card(page, "Your words",
                          "Names and terms to spell exactly. Near-misses of names are corrected automatically.")
        add = ctk.CTkFrame(card, fg_color="transparent")
        add.pack(fill="x", padx=20, pady=(0, 6))
        entry = ctk.CTkEntry(add, placeholder_text="Add a word, e.g. Partharsid", height=36, corner_radius=10,
                             fg_color=CARD_HI, border_color=LINE, font=font(13))
        entry.pack(side="left", fill="x", expand=True)
        chips = ctk.CTkFrame(card, fg_color="transparent")
        chips.pack(fill="x", padx=16, pady=(4, 16))

        def render():
            for w in chips.winfo_children():
                w.destroy()
            words = self.app.cfg.get("dictionary") or []
            if not words:
                ctk.CTkLabel(chips, text="No words yet.", font=font(12), text_color=MUTED).pack(anchor="w", padx=4)
            # Flow layout: chips sized to their text, wrapping to new rows.
            f = font(12)
            max_w = max(400, card.winfo_width() - 60) if card.winfo_width() > 1 else 640
            row, used = None, max_w
            for w in words:
                label = f"{w}   ✕"
                cw = int(f.measure(label)) + 30
                if used + cw > max_w:
                    row = ctk.CTkFrame(chips, fg_color="transparent")
                    row.pack(fill="x", pady=3)
                    used = 0
                ctk.CTkButton(row, text=label, width=cw, height=30, corner_radius=15, fg_color=CARD_HI,
                              hover_color="#3a2230", border_width=1, border_color=LINE, text_color=TEXT,
                              font=f, command=lambda w=w: remove(w)).pack(side="left", padx=4)
                used += cw + 8

        def add_word(_e=None):
            w = entry.get().strip()
            if w and w not in (self.app.cfg.get("dictionary") or []):
                self._commit(lambda c: c.__setitem__("dictionary", (c.get("dictionary") or []) + [w]), f"Added {w}")
                render()
            entry.delete(0, "end")

        def remove(w):
            self._commit(lambda c: c.__setitem__("dictionary", [x for x in c.get("dictionary") or [] if x != w]),
                         f"Removed {w}")
            render()

        entry.bind("<Return>", add_word)
        ctk.CTkButton(add, text="Add", width=80, height=36, corner_radius=10, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(13, "bold"), command=add_word).pack(side="left", padx=(8, 0))
        render()

        self._pairs_card(page, "replacements", "Replacements",
                         "Always rewrite a phrase Whisper keeps mishearing.", "Heard (e.g. qualify)",
                         "Write (e.g. Coolify)")

    def _page_snippets(self, page) -> None:
        self._pairs_card(page, "snippets", "Snippets",
                         "Say the trigger to insert the text. Use \\n for a new line.",
                         "Say (e.g. my email)", "Insert (e.g. me@example.com)")

    def _pairs_card(self, page, key: str, title: str, subtitle: str, left_ph: str, right_ph: str) -> None:
        card = self._card(page, title, subtitle)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=20, pady=(0, 6))

        def save_all():
            pairs = {}
            for a, b in rows:
                k, v = a.get().strip(), b.get().strip().replace("\\n", "\n")
                if k:
                    pairs[k] = v
            self._commit(lambda c: c.__setitem__(key, pairs))

        rows: list = []

        def add_row(k="", v=""):
            r = ctk.CTkFrame(body, fg_color="transparent")
            r.pack(fill="x", pady=3)
            a = ctk.CTkEntry(r, placeholder_text=left_ph, height=34, corner_radius=10, fg_color=CARD_HI,
                             border_color=LINE, font=font(13), width=220)
            a.pack(side="left")
            ctk.CTkLabel(r, text="→", font=font(14), text_color=MUTED, width=30).pack(side="left")
            b = ctk.CTkEntry(r, placeholder_text=right_ph, height=34, corner_radius=10, fg_color=CARD_HI,
                             border_color=LINE, font=font(13))
            b.pack(side="left", fill="x", expand=True)
            if k:
                a.insert(0, k)
            if v:
                b.insert(0, v.replace("\n", "\\n"))
            pair = (a, b)

            def delete():
                rows.remove(pair)
                r.destroy()
                save_all()
            ctk.CTkButton(r, text="✕", width=34, height=34, corner_radius=10, fg_color=CARD_HI, hover_color="#3a2230",
                          text_color=MUTED, command=delete).pack(side="left", padx=(8, 0))
            for e in pair:
                e.bind("<FocusOut>", lambda _e: save_all())
                e.bind("<Return>", lambda _e: save_all())
            rows.append(pair)

        for k, v in (self.app.cfg.get(key) or {}).items():
            add_row(k, v)
        ctk.CTkButton(card, text="+  Add", width=100, height=34, corner_radius=10, fg_color=CARD_HI,
                      hover_color=LINE, text_color=TEXT, font=font(13), command=add_row).pack(
            anchor="w", padx=20, pady=(4, 16))

    def _page_history(self, page) -> None:
        if not self.app.history:
            self._card(page, "History is off", "Turn it on to keep your dictations on this PC.")
            return
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(4, 6))
        search = ctk.CTkEntry(top, placeholder_text="Search your dictations…", height=38, corner_radius=12,
                              fg_color=CARD, border_color=LINE, font=font(13))
        search.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(top, text="Clear all", width=100, height=38, corner_radius=12, fg_color=CARD,
                      hover_color="#3a2230", text_color=RED, font=font(13),
                      command=lambda: self._clear_history()).pack(side="left", padx=(8, 0))
        listing = ctk.CTkFrame(page, fg_color="transparent")
        listing.pack(fill="both", expand=True, padx=8)
        entries = self.app.history.recent(200)

        def render(_e=None):
            for w in listing.winfo_children():
                w.destroy()
            q = search.get().strip().lower()
            shown = [e for e in entries if not q or q in (e["final"] or "").lower() or q in (e["app"] or "").lower()]
            if not shown:
                ctk.CTkLabel(listing, text="No dictations found.", font=font(13), text_color=MUTED).pack(pady=30)
            for e in shown[:100]:
                card = ctk.CTkFrame(listing, fg_color=CARD, corner_radius=14, border_width=1, border_color=LINE)
                card.pack(fill="x", padx=6, pady=5)
                self._history_item(card, e)
        search.bind("<KeyRelease>", render)
        render()

    def _history_item(self, parent, e: dict) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=8)
        when = dt.datetime.fromtimestamp(e["ts"]).strftime("%d %b · %H:%M")
        app_name = (e["app"] or "").removesuffix(".exe")
        ctk.CTkLabel(row, text=f"{when}   {app_name}", font=font(11), text_color=MUTED, anchor="w").pack(fill="x")
        body = ctk.CTkFrame(row, fg_color="transparent")
        body.pack(fill="x")
        text = (e["final"] or "").strip()
        ctk.CTkLabel(body, text=text, font=font(13), text_color=TEXT, anchor="w", justify="left",
                     wraplength=560).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(body, text="Copy", width=64, height=28, corner_radius=8, fg_color=CARD_HI, hover_color=LINE,
                      text_color=TEXT, font=font(12), command=lambda t=text: self._copy(t)).pack(side="right")

    def _copy(self, text: str) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self._show_toast("Copied")

    def _clear_history(self) -> None:
        if messagebox.askyesno("Clear history", "Delete all saved dictations from this PC?", parent=self.win):
            self.app.history.clear()
            self.show("history")
            self._show_toast("History cleared")

    def _page_tools(self, page) -> None:
        card = self._card(page, "Transcribe a file",
                          "Audio or video becomes a .txt transcript and .srt subtitles next to the file. "
                          "Dictation pauses while it runs.")
        row = self._row(card, "Choose a recording", "mp3, m4a, wav, mp4, mkv, webm…")
        busy = self.app.job_cancel is not None
        ctk.CTkButton(row, text="Busy…" if busy else "Choose file", width=140, height=32, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, font=font(13, "bold"),
                      state="disabled" if busy else "normal",
                      command=self.app.pick_and_transcribe).pack(side="right")

        card = self._card(page, "Meeting notes",
                          "Records your microphone and your computer's audio (the other people on a call), "
                          "then writes a transcript, summary and action items. Everything stays on this PC. "
                          "Let others know you're taking notes.")
        row = self._row(card, "Recording" if self.app.meeting.active else "Start a meeting",
                        "A red indicator stays on screen while recording.")
        if self.app.meeting.active:
            ctk.CTkButton(row, text="Stop and make notes", width=180, height=32, corner_radius=10, fg_color=RED,
                          hover_color="#e23d5c", font=font(13, "bold"),
                          command=lambda: (self.app.stop_meeting(), self.show("tools"))).pack(side="right")
        else:
            ctk.CTkButton(row, text="Start recording", width=160, height=32, corner_radius=10, fg_color=ACCENT,
                          hover_color=ACCENT_HOVER, font=font(13, "bold"),
                          command=lambda: (self.app.start_meeting(), self.show("tools"))).pack(side="right")
        self._switch(card, "Keep the audio recording", "Otherwise only the notes are kept.", "meetings_keep_audio")
        from .meeting import notes_folder
        row = self._row(card, "Notes folder", str(notes_folder(self.app.cfg.snapshot())))
        ctk.CTkButton(row, text="Open", width=90, height=32, corner_radius=10, fg_color=CARD_HI, hover_color=LINE,
                      text_color=TEXT, command=lambda: os.startfile(str(notes_folder(self.app.cfg.snapshot())))).pack(
            side="right")

        learned = list(reversed(self.app.cfg.get("learned_words") or []))[:12]
        card = self._card(page, "Learned from your corrections",
                          "These were added to your Dictionary automatically. Remove any in Dictionary.")
        if not learned:
            ctk.CTkLabel(card, text="Nothing learned yet.", font=font(12), text_color=MUTED, anchor="w").pack(
                fill="x", padx=20, pady=(0, 14))
        for item in learned:
            ctk.CTkLabel(card, text=f"“{item.get('from', '')}”  →  {item.get('to', '')}", font=font(13),
                         text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=2)
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    def _page_about(self, page) -> None:
        card = self._card(page, f"LocalFlow {__version__}", "Private voice dictation that runs entirely on your PC.")
        for line in ("Your voice is processed in memory and discarded after transcription.",
                     "Text around your cursor is read locally, never from password fields.",
                     "Dictations stay out of Windows clipboard history and cloud sync.",
                     "History is stored only on this PC, and you can clear it any time."):
            ctk.CTkLabel(card, text=f"✓  {line}", font=font(13), text_color=TEXT, anchor="w").pack(
                fill="x", padx=20, pady=3)
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()
        card = self._card(page, "Updates", "New versions come from github.com/Partharsid/local-flow releases.")
        self._switch(card, "Check for updates automatically", "Once a day; you choose when to install.",
                     "update_check")
        row = self._row(card, "Check now")
        ctk.CTkButton(row, text="Check for updates", width=160, height=32, corner_radius=10, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, font=font(13, "bold"),
                      command=lambda: (self.app.check_for_updates(manual=True),
                                       self._show_toast("Checking… you'll get a notification"))).pack(side="right")
        card = self._card(page, "Files")
        for label, which in (("Open logs folder", "logs"), ("Open data folder", "data")):
            row = self._row(card, label)
            ctk.CTkButton(row, text="Open", width=90, height=32, corner_radius=10, fg_color=CARD_HI,
                          hover_color=LINE, text_color=TEXT, command=lambda w=which: self.app.open_folder(w)).pack(
                side="right")

    # ------------------------------------------------------------ hotkeys
    def _record_hotkey(self, key: str, btn: ctk.CTkButton) -> None:
        previous = self.app.cfg[key]
        btn.configure(text="Press keys…", border_color=ACCENT, text_color=ACCENT)
        # Raw input doesn't swallow keys, so keep focus off the button: a
        # pressed Space or Enter would otherwise click it again.
        self.win.focus_set()
        token = object()
        self._capture_token = token

        def finish(spec: str | None):
            if self._capture_token is not token or not btn.winfo_exists():
                return
            self._capture_token = None
            btn.configure(border_color=LINE, text_color=TEXT)
            if not spec:
                btn.configure(text=pretty_key(previous))
                return
            others = {self.app.cfg.get(k) for k in ("hotkey_hold", "hotkey_toggle", "hotkey_paste_last", "hotkey_edit")
                      if k != key}
            try:
                parse_combo(spec)
            except ValueError as e:
                btn.configure(text=pretty_key(previous))
                self._show_toast(str(e), RED)
                return
            if spec in others:
                btn.configure(text=pretty_key(previous))
                self._show_toast("That shortcut is already used", RED)
                return
            btn.configure(text=pretty_key(spec))
            self._commit(lambda c: c.__setitem__(key, spec), f"Shortcut set to {pretty_key(spec)}")

        # The callback arrives on the keyboard-hook thread: hand over to Tk.
        self.app.hotkeys.capture_next(lambda spec: self.app.ui_call(finish, spec), timeout=8.0)
        self.win.after(8300, lambda: (self.app.hotkeys.cancel_capture(), finish(None))
                       if self._capture_token is token else None)

    def close(self) -> None:
        # Never leave a hotkey capture running: it would swallow all typing.
        self._capture_token = None
        self.app.hotkeys.cancel_capture()
        self.app.settings_window = None
        self.win.destroy()
