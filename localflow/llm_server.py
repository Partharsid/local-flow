"""Built-in AI cleanup engine: a private llama.cpp server (Vulkan build).

LocalFlow starts `llama-server.exe` on a free loopback port with the bundled
GGUF model. The process is placed in a Job Object with KILL_ON_JOB_CLOSE, so
it can never outlive LocalFlow (even after a crash) and hold GPU memory.
"""
from __future__ import annotations

import logging
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

from .config import DATA_DIR

log = logging.getLogger(__name__)

LLAMA_DIR = DATA_DIR / "runtime" / "llama"
LLM_DIR = DATA_DIR / "models" / "llm"
CREATE_NO_WINDOW = 0x08000000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_INTEGRATED = ("uhd", "iris", "intel(r) graphics", "radeon(tm) graphics", "vega", "microsoft basic")


def pick_device(exe: Path) -> str | None:
    """Choose one GPU for llama.cpp. Left alone it splits the model across
    every Vulkan device, and on laptops the slow integrated GPU (which also
    reports *more* shared memory than the real card) made cleanup ~15x
    slower. Prefer dedicated GPUs, then the most memory."""
    try:
        out = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW).stdout + ""
    except (OSError, subprocess.SubprocessError):
        return None
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if ":" not in line or not line.lower().startswith("vulkan"):
            continue
        name, rest = line.split(":", 1)
        mem = re.search(r"\((\d+) MiB", rest)
        integrated = any(k in rest.lower() for k in _INTEGRATED)
        devices.append((not integrated, int(mem.group(1)) if mem else 0, name.strip(), rest.strip()))
    if not devices:
        return None
    best = max(devices)
    log.info("AI engine GPU: %s %s", best[2], best[3])
    return best[2]


def _kill_on_close_job():
    import win32job
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


class LlamaServer:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.model_path: Path | None = None
        self._job = None
        self._lock = threading.Lock()
        self.device: str | None = None   # chosen once per run

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @staticmethod
    def exe() -> Path:
        return LLAMA_DIR / "llama-server.exe"

    @staticmethod
    def installed(model_file: str) -> bool:
        return LlamaServer.exe().is_file() and (LLM_DIR / model_file).is_file()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, model_file: str, timeout: float = 120) -> None:
        """Start (or keep) the server for model_file; blocks until healthy."""
        with self._lock:
            model = LLM_DIR / model_file
            if self.running() and self.model_path == model:
                return
            self.stop()
            if not self.installed(model_file):
                raise FileNotFoundError(f"Built-in AI engine or model missing ({model_file})")
            self.port = _free_port()
            args = [str(self.exe()), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
                    "-ngl", "99",            # offload every layer to the GPU when there is one
                    "-c", "4096", "-np", "1", "--no-webui", "--log-disable"]
            if self.device is None:
                self.device = pick_device(self.exe()) or ""
            if self.device:
                args += ["--device", self.device, "-sm", "none"]
            self.proc = subprocess.Popen(args, cwd=str(LLAMA_DIR), creationflags=CREATE_NO_WINDOW,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                import win32api
                import win32con
                import win32job
                self._job = _kill_on_close_job()
                handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                              False, self.proc.pid)
                win32job.AssignProcessToJobObject(self._job, handle)
                win32api.CloseHandle(handle)
            except Exception as e:  # noqa: BLE001 - still works, just not crash-proof
                log.warning("Could not tie llama-server to LocalFlow's lifetime: %s", e)
            self.model_path = model
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"llama-server exited with code {self.proc.returncode}")
                try:
                    if requests.get(f"{self.url}/health", timeout=1).json().get("status") == "ok":
                        log.info("Built-in AI engine ready on port %d (%s)", self.port, model_file)
                        return
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.25)
            self.stop()
            raise TimeoutError("Built-in AI engine did not start in time")

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
        self._job = None
