"""First-run setup: download models and GPU libraries once, with a progress UI.

The installer stays small (~100 MB); the big pieces come from their official
sources, pinned to exact versions and verified by size and SHA-256:

  speech    Whisper (turbo on NVIDIA GPUs, small on CPU)   Hugging Face
  cuda      cuBLAS / cuDNN / NVRTC DLLs (NVIDIA GPUs only)    PyPI (NVIDIA wheels)
  ai        llama.cpp Vulkan build + Qwen3-4B-Instruct GGUF   GitHub / Hugging Face

Downloads run on 8 parallel ranged connections, resume after interruptions
(progress is saved per 8 MB piece) and retry flaky pieces.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .config import DATA_DIR

log = logging.getLogger(__name__)

MODELS = DATA_DIR / "models"
RUNTIME = DATA_DIR / "runtime"
DOWNLOADS = DATA_DIR / "downloads"
PIECE = 8 * 1024 * 1024
HF = "https://huggingface.co"


@dataclass
class Asset:
    url: str
    dest: Path
    size: int
    sha256: str = ""
    extract: str = ""          # "zip" (whole archive) or "wheel-dlls"
    extract_to: Path | None = None

    @property
    def name(self) -> str:
        return self.dest.name


@dataclass
class Component:
    key: str
    title: str
    assets: list[Asset] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(a.size for a in self.assets)


def _hf(repo: str, rev: str, files: list[tuple[str, int, str]], folder: Path) -> list[Asset]:
    return [Asset(f"{HF}/{repo}/resolve/{rev}/{name}", folder / name, size, sha) for name, size, sha in files]


WHISPER = {
    "large-v3-turbo": ("dropbox-dash/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", [
        ("config.json", 2263, ""),
        ("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        ("preprocessor_config.json", 340, ""),
        ("tokenizer.json", 2710337, ""),
        ("vocabulary.json", 1068114, ""),
    ]),
    "small": ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120", [
        ("config.json", 2370, ""),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        ("tokenizer.json", 2203239, ""),
        ("vocabulary.txt", 459861, ""),
    ]),
}

PYPI = "https://files.pythonhosted.org/packages"
CUDA_WHEELS = [
    (f"{PYPI}/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/"
     "nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl",
     553162896, "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"),
    (f"{PYPI}/aa/38/f856579877f7c1c5066e61182e7de7bc27bf35a78c8d1b0fa592e6985bc4/"
     "nvidia_cudnn_cu12-9.27.0.42-py3-none-win_amd64.whl",
     743068852, "06e9b0026f3bad97d2b58666330fabec04fe1672f776661ecb0ce0029c27f142"),
    (f"{PYPI}/52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
     "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
     76408187, "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349"),
]

LLAMA_ZIP = ("https://github.com/ggml-org/llama.cpp/releases/download/b11898/llama-b11898-bin-win-vulkan-x64.zip",
             37051673, "7e51f64f40bbcad8a2c9c73576409e9d72e88d8f92f2a7da776885afc0a50301")
LLM_GGUF = ("Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            f"{HF}/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            2497281120, "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597")


# ---------------------------------------------------------------- hardware --
class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_ulong), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


def gpu_names() -> list[str]:
    names, i = [], 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.DeviceString and dd.DeviceString not in names:
            names.append(dd.DeviceString)
        i += 1
    return names


def nvidia_gpu() -> str | None:
    """Name of an NVIDIA GPU whose driver can run CUDA, else None."""
    if not (Path(r"C:\Windows\System32\nvml.dll").exists() or Path(r"C:\Windows\System32\nvcuda.dll").exists()):
        return None
    return next((n for n in gpu_names() if "nvidia" in n.lower()), None)


# ---------------------------------------------------------------- planning --
def plan(cfg: dict, include_ai: bool = True) -> list[Component]:
    """What still needs downloading for this config (empty list = ready)."""
    from .cuda_setup import cuda_runtime_present
    from .llm_server import LLM_DIR, LlamaServer

    comps: list[Component] = []
    model = cfg["whisper"]["model"]
    if model in WHISPER:
        repo, rev, files = WHISPER[model]
        assets = [a for a in _hf(repo, rev, files, MODELS / model) if not _complete(a)]
        if assets:
            comps.append(Component("speech", f"Speech model (Whisper {model})", assets))
    if cfg["whisper"].get("device") != "cpu" and nvidia_gpu() and not cuda_runtime_present():
        comps.append(Component("cuda", "NVIDIA GPU libraries", [
            Asset(url, DOWNLOADS / url.rsplit("/", 1)[1], size, sha, "wheel-dlls", RUNTIME / "cuda")
            for url, size, sha in CUDA_WHEELS]))
    if include_ai and cfg["cleanup"]["enabled"] and cfg["cleanup"].get("backend", "builtin") == "builtin":
        ai = []
        if not LlamaServer.exe().is_file():
            url, size, sha = LLAMA_ZIP
            ai.append(Asset(url, DOWNLOADS / "llama-vulkan.zip", size, sha, "zip", RUNTIME / "llama"))
        name, url, size, sha = LLM_GGUF
        gguf = Asset(url, LLM_DIR / name, size, sha)
        if not _complete(gguf):
            ai.append(gguf)
        if ai:
            comps.append(Component("ai", "AI cleanup (Qwen3-4B + llama.cpp)", ai))
    return comps


def _complete(a: Asset) -> bool:
    return a.dest.is_file() and a.dest.stat().st_size == a.size


def recommended_config(cfg: dict) -> dict:
    """Pick the speech model for this PC on first run."""
    if nvidia_gpu():
        cfg["whisper"].update(model="large-v3-turbo", device="auto")
    else:
        cfg["whisper"].update(model="small", device="cpu", compute_type="int8")
    return cfg


# ---------------------------------------------------------------- download --
class Cancelled(Exception):
    pass


class Downloader:
    def __init__(self, progress=None, cancel: threading.Event | None = None, connections: int = 8):
        self.progress = progress or (lambda n: None)   # called with bytes just received
        self.cancel = cancel or threading.Event()
        self.connections = connections
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "LocalFlow-setup"

    def fetch(self, a: Asset) -> None:
        if _complete(a):   # final names are only given after the checksum passed
            self.progress(a.size)
            return
        a.dest.parent.mkdir(parents=True, exist_ok=True)
        part = a.dest.with_name(a.dest.name + ".part")
        state = a.dest.with_name(a.dest.name + ".pieces")
        if a.size < 4 * PIECE:
            self._single(a, part)
        else:
            self._parallel(a, part, state)
        if a.sha256 and _sha256(part) != a.sha256:
            part.unlink(missing_ok=True)
            state.unlink(missing_ok=True)
            raise IOError(f"{a.name} failed its checksum; it will be downloaded again on retry")
        part.replace(a.dest)
        state.unlink(missing_ok=True)

    def _single(self, a: Asset, part: Path) -> None:
        for attempt in range(6):
            try:
                with self.session.get(a.url, stream=True, timeout=(15, 60)) as r:
                    r.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(256 * 1024):
                            if self.cancel.is_set():
                                raise Cancelled
                            f.write(chunk)
                            self.progress(len(chunk))
                if part.stat().st_size == a.size:
                    return
            except (requests.RequestException, IOError) as e:
                if isinstance(e, Cancelled):
                    raise
                log.warning("%s attempt %d failed: %s", a.name, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        raise IOError(f"Could not download {a.name}")

    def _parallel(self, a: Asset, part: Path, state: Path) -> None:
        n = (a.size + PIECE - 1) // PIECE
        done: set[int] = set()
        if part.exists() and part.stat().st_size == a.size and state.exists():
            try:
                done = set(json.loads(state.read_text()))
            except (OSError, ValueError):
                done = set()
        else:
            with open(part, "wb") as f:
                f.truncate(a.size)            # preallocate; pieces land at their offsets
        already = sum(min(PIECE, a.size - i * PIECE) for i in done)
        self.progress(already)
        lock = threading.Lock()
        # Resolve redirects once so every piece goes straight to the CDN.
        url = self.session.head(a.url, allow_redirects=True, timeout=30).url

        def piece(i: int) -> None:
            start = i * PIECE
            end = min(a.size, start + PIECE) - 1
            for attempt in range(8):
                if self.cancel.is_set():
                    raise Cancelled
                got = 0
                # Signed CDN links expire: after a few failures go back to the original URL.
                src = url if attempt < 3 else a.url
                try:
                    with self.session.get(src, headers={"Range": f"bytes={start}-{end}"}, stream=True,
                                          timeout=(15, 45)) as r:
                        if r.status_code != 206:
                            raise IOError(f"server ignored range request ({r.status_code})")
                        buf = bytearray()
                        for chunk in r.iter_content(256 * 1024):
                            if self.cancel.is_set():
                                raise Cancelled
                            buf += chunk
                            got += len(chunk)
                            self.progress(len(chunk))
                    if len(buf) != end - start + 1:
                        raise IOError("short read")
                    with open(part, "r+b") as f:
                        f.seek(start)
                        f.write(buf)
                    with lock:
                        done.add(i)
                        state.write_text(json.dumps(sorted(done)))
                    return
                except Cancelled:
                    raise
                except (requests.RequestException, IOError) as e:
                    self.progress(-got)            # that data will be fetched again
                    log.info("%s piece %d attempt %d: %s", a.name, i, attempt + 1, e)
                    time.sleep(min(10, 1.5 * (attempt + 1)))
            raise IOError(f"Could not download {a.name} (piece {i})")

        todo = [i for i in range(n) if i not in done]
        with ThreadPoolExecutor(self.connections) as pool:
            for f in [pool.submit(piece, i) for i in todo]:
                f.result()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def install(a: Asset) -> None:
    """Unpack downloaded archives into place."""
    if a.extract == "zip":
        tmp = a.extract_to.with_name(a.extract_to.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(a.dest) as z:
            z.extractall(tmp)
        shutil.rmtree(a.extract_to, ignore_errors=True)
        tmp.replace(a.extract_to)
        a.dest.unlink(missing_ok=True)
    elif a.extract == "wheel-dlls":
        a.extract_to.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(a.dest) as z:
            for info in z.infolist():
                p = info.filename
                if p.startswith("nvidia/") and "/bin/" in p and p.lower().endswith(".dll"):
                    with z.open(info) as src, open(a.extract_to / Path(p).name, "wb") as dst:
                        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
        a.dest.unlink(missing_ok=True)


def run(components: list[Component], progress, status, cancel: threading.Event) -> None:
    """Download + install everything; progress(bytes), status(component_key, text)."""
    dl = Downloader(progress, cancel)
    for comp in components:
        for a in comp.assets:
            status(comp.key, "Downloading")
            dl.fetch(a)
            if a.extract:
                status(comp.key, "Installing")
                install(a)
        status(comp.key, "Done")
    shutil.rmtree(DOWNLOADS, ignore_errors=True)


# ---------------------------------------------------------------- window ----
def run_setup_window(cfg_obj, auto: bool = False) -> bool:
    """Show the setup window until everything is in place. Returns False if
    the user closed it before finishing. auto=True starts downloading
    immediately (used by `LocalFlow.exe --setup --auto` for unattended tests)."""
    import customtkinter as ctk

    from .branding import ensure_ico
    from .settings_ui import ACCENT, ACCENT_HOVER, BG, CARD, GREEN, LINE, MUTED, RED, TEXT, font

    ctk.set_appearance_mode("dark")
    cfg = cfg_obj.snapshot()
    if not cfg.get("first_run_done"):
        cfg = recommended_config(cfg)
        cfg_obj.replace(cfg)
    gpu = nvidia_gpu()
    result = {"ok": False}
    cancel = threading.Event()

    win = ctk.CTk(fg_color=BG)
    win.title("LocalFlow setup")
    win.geometry("620x560")
    win.resizable(False, False)
    try:
        win.iconbitmap(str(ensure_ico()))
    except Exception:  # noqa: BLE001
        pass

    ctk.CTkLabel(win, text="●  LocalFlow", font=font(22, "bold"), text_color=TEXT).pack(anchor="w", padx=32, pady=(28, 2))
    ctk.CTkLabel(win, text="Private voice dictation that runs entirely on your PC.", font=font(13),
                 text_color=MUTED).pack(anchor="w", padx=32)
    hw = (f"GPU: {gpu} - fast GPU transcription" if gpu
          else "No NVIDIA GPU found - using the efficient CPU speech model")
    ctk.CTkLabel(win, text=hw, font=font(12), text_color=GREEN if gpu else MUTED).pack(anchor="w", padx=32, pady=(8, 0))

    ai_var = ctk.BooleanVar(value=True)
    card = ctk.CTkFrame(win, fg_color=CARD, corner_radius=16, border_width=1, border_color=LINE)
    card.pack(fill="x", padx=28, pady=18)
    rows: dict[str, tuple] = {}
    total_lbl = ctk.CTkLabel(win, text="", font=font(12), text_color=MUTED)
    overall = ctk.CTkProgressBar(win, height=10, corner_radius=5, progress_color=ACCENT, fg_color=LINE)
    btn = ctk.CTkButton(win, text="Download and set up", height=42, corner_radius=12, fg_color=ACCENT,
                        hover_color=ACCENT_HOVER, font=font(14, "bold"))
    state = {"comps": [], "total": 1, "got": 0, "t0": 0.0, "speed": 0.0, "last": (0.0, 0)}

    def gb(n: int) -> str:
        return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"

    def build_rows():
        for w in card.winfo_children():
            w.destroy()
        rows.clear()
        comps = plan(cfg_obj.snapshot(), include_ai=ai_var.get())
        state["comps"] = comps
        state["total"] = max(1, sum(c.size for c in comps))
        if not comps:
            ctk.CTkLabel(card, text="Everything is already installed.", font=font(13), text_color=TEXT).pack(
                padx=20, pady=18)
        for c in comps:
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=20, pady=(14, 4))
            ctk.CTkLabel(r, text=c.title, font=font(13), text_color=TEXT).pack(side="left")
            st = ctk.CTkLabel(r, text=gb(c.size), font=font(12), text_color=MUTED)
            st.pack(side="right")
            rows[c.key] = (st,)
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()
        total_lbl.configure(text=f"Total download: {gb(sum(c.size for c in comps))}" if comps else "")

    ai_box = ctk.CTkCheckBox(win, text="Include AI cleanup (removes filler words, fixes punctuation) - 2.5 GB",
                             variable=ai_var, command=build_rows, font=font(12), text_color=TEXT,
                             fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=LINE)
    ai_box.pack(anchor="w", padx=32, pady=(0, 16))
    build_rows()
    card.pack_configure(pady=(14, 14))
    overall.set(0)
    overall.pack(fill="x", padx=32, pady=(4, 0))
    total_lbl.pack(anchor="w", padx=32, pady=(6, 0))
    btn.pack(fill="x", padx=32, pady=(18, 0), side="bottom")
    ctk.CTkFrame(win, fg_color="transparent", height=24).pack(side="bottom")

    def on_progress(n: int):
        state["got"] += n

    def on_status(key: str, text: str):
        win.after(0, lambda: key in rows and rows[key][0].configure(
            text=text, text_color=GREEN if text == "Done" else TEXT))

    def tick():
        if not state.get("running"):
            return
        got, total = state["got"], state["total"]
        now = time.monotonic()
        lt, lg = state["last"]
        if now - lt >= 1:
            inst = (got - lg) / (now - lt) if lt else 0
            state["speed"] = inst if not state["speed"] else 0.7 * state["speed"] + 0.3 * inst
            state["last"] = (now, got)
        overall.set(min(1.0, got / total))
        sp = state["speed"]
        eta = f" · {int((total - got) / sp // 60)} min {int((total - got) / sp % 60)} s left" if sp > 1e5 else ""
        total_lbl.configure(text=f"{gb(got)} of {gb(total)} · {sp / 1e6:.1f} MB/s{eta}")
        win.after(250, tick)

    def finish(err: Exception | None):
        state["running"] = False
        if err is None:
            overall.set(1)
            total_lbl.configure(text="All set. Starting LocalFlow…", text_color=GREEN)
            cfg_now = cfg_obj.snapshot()
            cfg_now["cleanup"]["enabled"] = bool(ai_var.get()) or not plan(cfg_now, include_ai=True)
            cfg_obj.replace(cfg_now)
            result["ok"] = True
            win.after(900, win.destroy)
        elif isinstance(err, Cancelled):
            win.destroy()
        else:
            log.exception("Setup failed", exc_info=err)
            total_lbl.configure(text=f"Problem: {err}", text_color=RED)
            btn.configure(state="normal", text="Retry (already downloaded parts are kept)")
            ai_box.configure(state="normal")

    def start():
        btn.configure(state="disabled", text="Setting up…")
        ai_box.configure(state="disabled")
        build_rows()
        state.update(running=True, got=0, t0=time.monotonic(), speed=0.0, last=(0.0, 0))
        comps = state["comps"]

        def work():
            try:
                run(comps, on_progress, on_status, cancel)
                win.after(0, lambda: finish(None))
            except Exception as e:  # noqa: BLE001
                win.after(0, lambda e=e: finish(e))
        threading.Thread(target=work, daemon=True, name="setup").start()
        tick()

    def on_close():
        cancel.set()
        win.after(300, win.destroy)

    btn.configure(command=start)
    win.protocol("WM_DELETE_WINDOW", on_close)
    if not state["comps"]:
        result["ok"] = True
        win.after(400, win.destroy)
    elif auto:
        win.after(500, start)
    win.mainloop()
    return result["ok"]
