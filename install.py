"""Create LocalFlow shortcuts (Start Menu, optionally Desktop).

    .venv\\Scripts\\python.exe install.py            # Start Menu
    .venv\\Scripts\\python.exe install.py --desktop  # also a Desktop shortcut
    .venv\\Scripts\\python.exe install.py --remove   # remove both
"""
import os
import sys
from pathlib import Path

from localflow.app import create_shortcut

start_menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/LocalFlow.lnk"
desktop = Path(os.environ["USERPROFILE"]) / "Desktop/LocalFlow.lnk"

if "--remove" in sys.argv:
    for p in (start_menu, desktop):
        p.unlink(missing_ok=True)
    print("Removed LocalFlow shortcuts.")
else:
    create_shortcut(start_menu)
    print(f"Start Menu shortcut: {start_menu}")
    if "--desktop" in sys.argv:
        create_shortcut(desktop)
        print(f"Desktop shortcut: {desktop}")
