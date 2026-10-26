"""Check which candidate global shortcuts are free on this PC (RegisterHotKey)."""
import ctypes

from localflow.hotkeys import _hotkey_args, parse_combo

user32 = ctypes.windll.user32
for spec in ("ctrl+alt+space", "ctrl+shift+space", "alt+shift+space", "ctrl+win+space", "alt+shift+d",
             "ctrl+alt+d", "ctrl+shift+d", "alt+shift+z", "f9", "ctrl+alt+h"):
    mods, vk = _hotkey_args(parse_combo(spec))
    ok = user32.RegisterHotKey(None, 99, mods, vk)
    if ok:
        user32.UnregisterHotKey(None, 99)
    print(f"{spec:18} {'free' if ok else 'TAKEN by another app'}")
