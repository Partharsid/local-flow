"""LocalFlow's window: a small local web page shown in a chromeless Edge window.

The page (localflow/web/) is plain HTML/CSS/JS served from 127.0.0.1 by the
running app, so it can animate smoothly and look like a modern app. It talks
to the app through a tiny JSON API. Nothing leaves the PC: the server only
answers the loopback interface, only with this run's secret token, and only
for requests whose Host header is the loopback address.

Edge's "--app" mode gives a window with no browser chrome (the same WebView2
engine Windows already ships). If Edge is missing, the caller falls back to
the older Tk settings window.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import DATA_DIR
from .hotkeys import parse_combo

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

LANGUAGES = [("English", "en"), ("Auto-detect", "auto"), ("Hindi", "hi"), ("Telugu", "te"), ("Tamil", "ta"),
             ("Kannada", "kn"), ("Malayalam", "ml"), ("Marathi", "mr"), ("Bengali", "bn"), ("Gujarati", "gu"),
             ("Urdu", "ur"), ("Spanish", "es"), ("French", "fr"), ("German", "de"), ("Japanese", "ja"),
             ("Chinese", "zh"), ("Arabic", "ar"), ("Portuguese", "pt"), ("Russian", "ru")]
WHISPER_MODELS = [("large-v3-turbo", "Turbo · best balance (recommended)"),
                  ("large-v3", "Large · most accurate, slower"),
                  ("distil-large-v3", "Distil · fastest large, English only"),
                  ("medium", "Medium"), ("small", "Small · light, CPU friendly"), ("base", "Base · tiny")]
STYLES = [("casual", "Casual"), ("neutral", "Neutral"), ("formal", "Formal"), ("technical", "Technical"),
          ("notes", "Bullet notes"), ("prompt", "AI prompt (structured)"), ("verbatim", "Verbatim (no AI rewrite)")]
APP_KINDS = [("ai", "AI assistants", "Claude, ChatGPT, Gemini, Claude Code"),
             ("chat", "Chat apps", "Slack, WhatsApp, Discord, Teams"),
             ("email", "Email", "Outlook, Gmail, Thunderbird"),
             ("document", "Documents", "Word, Notion, Google Docs, Obsidian"),
             ("code", "Code editors", "VS Code, Cursor, JetBrains"),
             ("terminal", "Terminals", "Windows Terminal, PowerShell, cmd"),
             ("general", "Everything else", "")]


def find_edge() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.is_file():
                return str(p)
    return None


def _pretty_key(spec: str) -> str:
    return "+".join(p.strip().title() for p in spec.split("+")) if spec else "Not set"


class _Server:
    """Loopback HTTP server: static files + JSON routes, behind a secret token."""

    def __init__(self, routes: dict):
        self.routes = routes                     # {("GET"|"POST", "/api/x"): fn(body) -> dict}
        self.token = secrets.token_urlsafe(24)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):          # keep the app log clean
                pass

            def _ok(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                if host not in ("127.0.0.1", "localhost"):
                    return False                 # DNS-rebinding guard
                q = parse_qs(urlparse(self.path).query)
                if q.get("t", [""])[0] == outer.token:
                    return True
                cookie = SimpleCookie(self.headers.get("Cookie") or "")
                return "lf" in cookie and secrets.compare_digest(cookie["lf"].value, outer.token)

            def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj) -> None:
                self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8")

            def do_GET(self):  # noqa: N802
                if not self._ok():
                    return self._send(403, b"forbidden", "text/plain")
                url = urlparse(self.path)
                route = outer.routes.get(("GET", url.path))
                if route:
                    try:
                        return self._json(200, route({k: v[0] for k, v in parse_qs(url.query).items()}))
                    except Exception as e:  # noqa: BLE001
                        log.exception("UI route %s failed", url.path)
                        return self._json(500, {"error": str(e)})
                rel = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
                f = (WEB_DIR / rel).resolve()
                if WEB_DIR.resolve() not in f.parents or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                extra = {"Set-Cookie": f"lf={outer.token}; Path=/; HttpOnly; SameSite=Strict"}
                self._send(200, f.read_bytes(), ctype, extra)

            def do_POST(self):  # noqa: N802
                if not self._ok():
                    return self._send(403, b"forbidden", "text/plain")
                route = outer.routes.get(("POST", urlparse(self.path).path))
                if not route:
                    return self._send(404, b"not found", "text/plain")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    return self._json(200, route(body))
                except Exception as e:  # noqa: BLE001
                    log.exception("UI route %s failed", self.path)
                    return self._json(500, {"error": str(e)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="webui-http").start()

    def url(self, page: str = "") -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}{('#' + page) if page else ''}"

    def stop(self) -> None:
        self.httpd.shutdown()


def launch_window(url: str, size: tuple[int, int] = (1060, 720), tag: str = "ui") -> subprocess.Popen | None:
    edge = find_edge()
    if not edge:
        return None
    profile = DATA_DIR / "webui-profile" / tag
    profile.mkdir(parents=True, exist_ok=True)
    args = [edge, f"--app={url}", f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
            "--disable-features=Translate,msEdgeSidebar,msHub", "--hide-crash-restore-bubble"]
    return subprocess.Popen(args, creationflags=0x08000000)


def find_window(title: str) -> int | None:
    """Handle of the visible Edge app window with exactly this title (Edge's
    launcher process exits at once, so the window itself is the thing to track)."""
    try:
        import win32gui
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title                     and win32gui.GetClassName(h) == "Chrome_WidgetWin_1":
                found.append(h)
        win32gui.EnumWindows(cb, None)
        return found[0] if found else None
    except Exception:  # noqa: BLE001
        return None


def _ui_call(app, fn, timeout: float = 8.0):
    """Run fn on the Tk thread and wait for its result."""
    done, box = threading.Event(), {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e
        done.set()
    app.ui_call(run)
    if not done.wait(timeout):
        raise TimeoutError("the app did not answer in time")
    if "e" in box:
        raise box["e"]
    return box.get("v")


class WebUI:
    """The settings window, driven by the running App."""

    def __init__(self, app):
        self.app = app
        self.proc: subprocess.Popen | None = None
        self._devices: list | None = None
        self.cloud_job = {"state": "idle", "text": ""}
        self.server = _Server({
            ("GET", "/api/state"): self.api_state,
            ("GET", "/api/history"): self.api_history,
            ("GET", "/api/ollama"): self.api_ollama,
            ("POST", "/api/set"): self.api_set,
            ("POST", "/api/hotkey"): self.api_hotkey,
            ("POST", "/api/do"): self.api_do,
            ("POST", "/api/cloud"): self.api_cloud,
        })

    # -------------------------------------------------------------- window
    @staticmethod
    def available() -> bool:
        return find_edge() is not None and WEB_DIR.joinpath("index.html").is_file()

    def open(self, page: str = "") -> bool:
        if self._focus():
            return True
        self.proc = launch_window(self.server.url(page))
        return self.proc is not None

    @staticmethod
    def _focus() -> bool:
        """Bring an already-open LocalFlow window to the front."""
        h = find_window("LocalFlow")
        if not h:
            return False
        try:
            import win32con
            import win32gui
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(h)
        except Exception:  # noqa: BLE001
            pass
        return True

    # -------------------------------------------------------------- state
    def _mic_devices(self) -> list[dict]:
        if self._devices is None:
            out = [{"name": "System default", "id": None}]
            try:
                import sounddevice as sd
                for i, d in enumerate(sd.query_devices()):
                    if (d["max_input_channels"] > 0 and sd.query_hostapis(d["hostapi"])["name"] == "MME"
                            and "sound mapper" not in d["name"].lower()):
                        out.append({"name": d["name"], "id": i})
            except Exception:  # noqa: BLE001
                pass
            self._devices = out
        return self._devices

    def api_state(self, _q=None) -> dict:
        a = self.app
        cfg = a.cfg.snapshot()
        t, c = a.transcriber, a.cleaner
        from . import cloud, trainlog
        from .meeting import notes_folder
        stats = a.history.stats() if a.history else {"count": 0, "audio_seconds": 0, "words": 0}
        provider = cloud.PROVIDERS.get(cfg["cleanup"].get("cloud_provider", ""))
        upd = a.update_available
        return {
            "version": __version__,
            "status": {
                "speech": "error" if t.error else ("ready" if t.ready.is_set() else "loading"),
                "speech_model": (t.model_name or "").split("\\")[-1], "device": (t.device or "").upper(),
                "ai_on": bool(cfg["cleanup"]["enabled"]), "ai_ready": bool(c.available),
                "ai_label": c.model_label if c.available else "", "ai_problem": c.problem or "",
                "download": getattr(a, "fast_dl", ""), "cloud_note": c.cloud_note,
                "mic_muted": bool(getattr(a, "mic_muted", False)), "meeting": bool(a.meeting.active),
                "job": a.job_cancel is not None,
                "update": {"version": upd.version} if upd else None,
            },
            "config": cfg,
            "hotkeys": {k: _pretty_key(cfg.get(k, "")) for k in
                        ("hotkey_hold", "hotkey_toggle", "hotkey_edit", "hotkey_paste_last")},
            "autostart": a.autostart_enabled(),
            "devices": self._mic_devices(),
            "options": {"languages": LANGUAGES, "whisper_models": WHISPER_MODELS, "styles": STYLES,
                        "app_kinds": APP_KINDS},
            "stats": stats,
            "cloud": {"has_key": bool(cloud.load_key()), "provider": provider.label if provider else "",
                      "model": cfg["cleanup"].get("cloud_model", ""), "job": self.cloud_job},
            "training": trainlog.stats(),
            "notes_folder": str(notes_folder(cfg)),
        }

    def api_history(self, q: dict) -> dict:
        h = self.app.history
        if not h:
            return {"enabled": False, "items": []}
        needle = (q.get("q") or "").strip().lower()
        items = [e for e in h.recent(300) if not needle or needle in (e["final"] or "").lower()
                 or needle in (e["app"] or "").lower()]
        return {"enabled": True, "items": [{"id": e["id"], "ts": e["ts"], "app": (e["app"] or "").removesuffix(".exe"),
                                            "text": (e["final"] or "").strip(), "seconds": e["audio_seconds"]}
                                           for e in items[:150]]}

    def api_ollama(self, _q=None) -> dict:
        import requests
        try:
            url = self.app.cfg["cleanup"]["url"].rstrip("/") + "/api/tags"
            return {"models": sorted(m["name"] for m in requests.get(url, timeout=3).json().get("models", []))}
        except Exception:  # noqa: BLE001
            return {"models": []}

    # -------------------------------------------------------------- changes
    @staticmethod
    def _set_path(cfg: dict, path: str, value) -> None:
        *parents, last = path.split(".")
        node = cfg
        for p in parents:
            node = node.setdefault(p, {})
        node[last] = value

    def _apply(self, changes: dict) -> None:
        def run():
            cfg = self.app.cfg.snapshot()
            for path, value in changes.items():
                self._set_path(cfg, path, value)
            self.app.apply_config(cfg)
        _ui_call(self.app, run)

    def api_set(self, body: dict) -> dict:
        self._apply(body.get("changes") or {})
        return self.api_state()

    def api_hotkey(self, body: dict) -> dict:
        key = body.get("key")
        if key not in ("hotkey_hold", "hotkey_toggle", "hotkey_edit", "hotkey_paste_last"):
            return {"error": "unknown shortcut"}
        got, ev = [], threading.Event()
        self.app.hotkeys.capture_next(lambda spec: (got.append(spec), ev.set()), timeout=8.0)
        ev.wait(9.5)
        self.app.hotkeys.cancel_capture()
        spec = got[0] if got else None
        if not spec:
            return {"cancelled": True}
        try:
            parse_combo(spec)
        except ValueError as e:
            return {"error": str(e)}
        others = {self.app.cfg.get(k) for k in ("hotkey_hold", "hotkey_toggle", "hotkey_paste_last", "hotkey_edit")
                  if k != key}
        if spec in others:
            return {"error": "That shortcut is already used"}
        self._apply({key: spec})
        return {"spec": spec, "label": _pretty_key(spec)}

    def api_do(self, body: dict) -> dict:
        a, name, arg = self.app, body.get("name"), body.get("arg")
        if name == "unmute_mic":
            _ui_call(a, a.unmute_mic)
        elif name == "toggle_autostart":
            _ui_call(a, a.toggle_autostart)
        elif name == "check_updates":
            _ui_call(a, lambda: a.check_for_updates(manual=True))
        elif name == "install_update":
            _ui_call(a, a.install_update)
        elif name == "open_folder":
            a.open_folder("logs" if arg == "logs" else "data")
        elif name == "open_notes":
            from .meeting import notes_folder
            os.startfile(str(notes_folder(a.cfg.snapshot())))
        elif name == "start_meeting":
            _ui_call(a, a.start_meeting)
        elif name == "stop_meeting":
            _ui_call(a, a.stop_meeting)
        elif name == "pick_file":
            _ui_call(a, a.pick_and_transcribe, timeout=600)
        elif name == "history_clear" and a.history:
            a.history.clear()
        elif name == "restart_ai":
            _ui_call(a, a.cleaner.warm_up)
        else:
            return {"error": f"unknown action {name}"}
        return {"ok": True}

    # -------------------------------------------------------------- cloud
    def api_cloud(self, body: dict) -> dict:
        from . import cloud
        a = self.app
        if body.get("remove"):
            cloud.delete_key()
            self._apply({"cleanup.cloud_model": "", "cleanup.cloud_provider": ""})
            self.cloud_job = {"state": "idle", "text": ""}
            return {"ok": True}
        if self.cloud_job["state"] == "running":
            return {"ok": True}
        key = (body.get("key") or "").strip() or cloud.load_key()
        if not key:
            self.cloud_job = {"state": "error", "text": "Paste an API key first."}
            return {"ok": False}
        self.cloud_job = {"state": "running", "text": "Testing…"}

        def say(t):
            self.cloud_job = {"state": "running", "text": t}

        def work():
            from .cleanup import output_is_faithful
            from .context import FocusContext
            try:
                prov, best, results = cloud.auto_select(
                    key, lambda raw: a.cleaner.build_messages(raw, FocusContext(), "neutral", []),
                    output_is_faithful, say, free=bool(a.cfg["cleanup"].get("cloud_free_only", True)))
            except Exception as e:  # noqa: BLE001
                self.cloud_job = {"state": "error", "text": f"Couldn't set up cloud cleanup: {str(e)[:160]}"}
                return
            cloud.save_key(key)
            backups = [r.model for r in results if r.passed >= 0 and r.model != best]
            self._apply({"cleanup.cloud_provider": prov, "cleanup.cloud_model": best,
                         "cleanup.cloud_fallbacks": backups, "cleanup.backend": "cloud"})
            self.cloud_job = {"state": "ok", "text": f"Using {best} via {cloud.PROVIDERS[prov].label}."}
        threading.Thread(target=work, daemon=True, name="cloud-bench").start()
        return {"ok": True}


# ====================================================================== setup
def run_setup_web(cfg_obj, auto: bool = False) -> bool | None:
    """First-run setup in the web window. Returns True when everything is in
    place, False if the user closed it first, None if Edge isn't available."""
    from . import firstrun
    if not find_edge() or not WEB_DIR.joinpath("setup.html").is_file():
        return None
    cfg = cfg_obj.snapshot()
    if not cfg.get("first_run_done"):
        cfg = firstrun.recommended_config(cfg)
        cfg_obj.replace(cfg)
    gpu = firstrun.nvidia_gpu()
    st = {"include_ai": True, "state": "idle", "got": 0, "total": 1, "error": "", "status": {}, "comps": [],
          "speed": 0.0, "last": (0.0, 0)}
    cancel = threading.Event()

    def plan():
        comps = firstrun.plan(cfg_obj.snapshot(), include_ai=st["include_ai"])
        st["comps"] = [{"key": c.key, "title": c.title, "size": c.size} for c in comps]
        st["total"] = max(1, sum(c.size for c in comps))
        return comps

    def view(_q=None):
        now = time.monotonic()
        lt, lg = st["last"]
        if st["state"] == "running" and now - lt >= 1:
            inst = (st["got"] - lg) / (now - lt) if lt else 0
            st["speed"] = inst if not st["speed"] else 0.7 * st["speed"] + 0.3 * inst
            st["last"] = (now, st["got"])
        return {"gpu": gpu or "", "include_ai": st["include_ai"], "state": st["state"], "got": st["got"],
                "total": st["total"], "error": st["error"], "speed": st["speed"], "comps": st["comps"],
                "status": st["status"]}

    def toggle(body):
        if st["state"] != "running":
            st["include_ai"] = bool(body.get("include_ai"))
            plan()
        return view()

    def start(_body=None):
        if st["state"] == "running":
            return view()
        comps = plan()
        st.update(state="running", got=0, error="", status={}, speed=0.0, last=(0.0, 0))

        def on_progress(n):
            st["got"] += n

        def on_status(key, text):
            st["status"][key] = text

        def work():
            try:
                firstrun.run(comps, on_progress, on_status, cancel)
                cfg_now = cfg_obj.snapshot()
                cfg_now["cleanup"]["enabled"] = bool(st["include_ai"]) or not firstrun.plan(cfg_now, include_ai=True)
                cfg_obj.replace(cfg_now)
                st["state"] = "done"
            except firstrun.Cancelled:
                st["state"] = "idle"
            except Exception as e:  # noqa: BLE001
                log.exception("Setup failed")
                st.update(state="error", error=str(e)[:200])
        threading.Thread(target=work, daemon=True, name="setup").start()
        return view()

    plan()
    server = _Server({("GET", "/api/setup"): view, ("POST", "/api/setup/toggle"): toggle,
                      ("POST", "/api/setup/start"): start})
    proc = launch_window(server.url() .replace("/?", "/setup.html?"), size=(620, 640), tag="setup")
    if proc is None:
        server.stop()
        return None
    if auto or not st["comps"]:
        start()
    ok, seen, gone_since, t0 = False, False, 0.0, time.monotonic()
    try:
        while True:
            if st["state"] == "done":
                time.sleep(1.6)                       # let the page show "All set"
                ok = True
                break
            if find_window("LocalFlow setup"):
                seen, gone_since = True, 0.0
            elif seen:                                # the window was closed
                gone_since = gone_since or time.monotonic()
                if time.monotonic() - gone_since > 2.5:
                    cancel.set()
                    break
            elif time.monotonic() - t0 > 40:          # it never appeared: use the classic window
                cancel.set()
                return None
            time.sleep(0.3)
    finally:
        h = find_window("LocalFlow setup")
        if h:
            try:
                import win32con
                import win32gui
                win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
            except Exception:  # noqa: BLE001
                pass
        server.stop()
    return ok
