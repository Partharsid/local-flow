"""List top-level windows owned by the running LocalFlow process."""
import ctypes
from ctypes import wintypes

import psutil

user32 = ctypes.windll.user32
pids = {p.pid for p in psutil.process_iter(["name", "cmdline"])
        if p.info["name"] == "pythonw.exe" and "localflow" in " ".join(p.info["cmdline"] or [])}
print("LocalFlow pids:", pids)
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def cb(hwnd, _):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in pids:
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        print(f"  {cls.value:28} visible={bool(user32.IsWindowVisible(hwnd))} "
              f"rect=({r.left},{r.top})-({r.right},{r.bottom})")
    return True


user32.EnumWindows(EnumProc(cb), 0)
