"""App icon: a violet-to-cyan rounded tile with a white microphone."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import BUNDLE_DIR, DATA_DIR

ASSETS = BUNDLE_DIR / "assets"
# The bundle may be read-only (Program Files), so a generated icon goes to the data folder.
ICO_PATH = ASSETS / "localflow.ico" if (ASSETS / "localflow.ico").exists() else DATA_DIR / "localflow.ico"
VIOLET, CYAN = (139, 92, 246), (34, 211, 238)


def app_icon(size: int = 256, state: str = "idle") -> Image.Image:
    """state tints the tile for the tray: idle | recording | working | error | loading."""
    ss = 4
    n = size * ss
    tile = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # Diagonal gradient.
    a, b = {"recording": ((255, 77, 109), (255, 140, 90)),
            "error": ((251, 191, 36), (245, 158, 11)),
            "loading": ((113, 113, 122), (82, 82, 91))}.get(state, (VIOLET, CYAN))
    t = (np.arange(n)[None, :] + np.arange(n)[:, None]) / (2 * n)          # diagonal 0..1
    rgb = np.stack([a[i] + (b[i] - a[i]) * t for i in range(3)], axis=-1)
    grad = Image.fromarray(np.dstack([rgb, np.full((n, n), 255)]).astype(np.uint8), "RGBA")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=int(n * 0.24), fill=255)
    tile.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(tile)
    w = n / 64
    white = (255, 255, 255, 255)
    d.rounded_rectangle((25 * w, 12 * w, 39 * w, 38 * w), radius=7 * w, fill=white)      # capsule
    d.arc((18 * w, 22 * w, 46 * w, 46 * w), 0, 180, fill=white, width=int(3.6 * w))      # cradle
    d.line((32 * w, 46 * w, 32 * w, 53 * w), fill=white, width=int(3.6 * w))
    d.line((25 * w, 53.5 * w, 39 * w, 53.5 * w), fill=white, width=int(3.6 * w))
    if state == "working":
        d.ellipse((46 * w, 4 * w, 60 * w, 18 * w), fill=white)
    return tile.resize((size, size), Image.LANCZOS)


def ensure_ico() -> Path:
    if not ICO_PATH.exists():
        ICO_PATH.parent.mkdir(parents=True, exist_ok=True)
        app_icon(256).save(ICO_PATH, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ICO_PATH
