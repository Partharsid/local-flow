# PyInstaller build for LocalFlow (one-folder, windowed).
#   .venv\Scripts\pyinstaller packaging\LocalFlow.spec --noconfirm
# NVIDIA cuBLAS/cuDNN (~1.3 GB) are deliberately NOT bundled: first-run
# setup downloads them only on PCs with an NVIDIA GPU.
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "customtkinter", "pystray", "uiautomation", "pycaw"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("localflow") + ["win32timezone", "comtypes.stream"]
datas += [(str(ROOT / "assets" / "localflow.ico"), "assets"), (str(ROOT / "assets" / "localflow.png"), "assets")]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "torch", "tensorflow", "matplotlib", "IPython", "notebook", "pandas", "scipy",
              "nvidia"],
    noarchive=False,
)

# Keep NVIDIA's CUDA libraries out even if dependency analysis finds them.
CUDA = re.compile(r"(cublas|cudnn|nvrtc|cudart|nvblas)[^/\\]*\.dll$", re.I)
a.binaries = [b for b in a.binaries if not CUDA.search(b[0]) or b[0].lower().startswith("ctranslate2")]
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("nvidia/")]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalFlow",
    icon=str(ROOT / "assets" / "localflow.ico"),
    console=False,
    disable_windowed_traceback=False,
    version=str(ROOT / "packaging" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalFlow", upx=False)
