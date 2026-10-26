"""Make NVIDIA's cuBLAS/cuDNN DLLs visible to CTranslate2.

They come from one of two places:
  * the `nvidia-*-cu12` pip wheels (site-packages/nvidia/<lib>/bin) when
    running from source, or
  * %APPDATA%\\LocalFlow\\runtime\\cuda, filled by the first-run setup in the
    packaged app (the DLLs are ~1 GB, too big to ship in the installer).

Windows searches neither by default. Must run before `faster_whisper` /
`ctranslate2` are imported.
"""
from __future__ import annotations

import os
import site
import sys
from pathlib import Path

from .config import DATA_DIR

CUDA_RUNTIME_DIR = DATA_DIR / "runtime" / "cuda"
_done = False


def _candidate_dirs() -> list[Path]:
    dirs = [CUDA_RUNTIME_DIR]
    roots = []
    try:
        roots += [Path(p) for p in site.getsitepackages()]
    except AttributeError:  # frozen apps have no site module data
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")
    for root in roots:
        dirs += sorted((root / "nvidia").glob("*/bin"))
    return [d for d in dirs if d.is_dir()]


def add_nvidia_dll_dirs() -> list[str]:
    global _done
    if _done or sys.platform != "win32":
        return []
    _done = True
    added: list[str] = []
    for bin_dir in _candidate_dirs():
        d = str(bin_dir.resolve())
        if d in added:
            continue
        os.add_dll_directory(d)
        # CTranslate2 loads cuDNN with plain LoadLibrary, which uses PATH.
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        added.append(d)
    return added


def cuda_runtime_present() -> bool:
    """True if cuBLAS and cuDNN can be found (pip wheels or downloaded)."""
    names = {p.name.lower() for d in _candidate_dirs() for p in d.glob("*.dll")}
    return any(n.startswith("cublas64_") for n in names) and any(n.startswith("cudnn64_") for n in names)
