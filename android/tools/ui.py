"""Tiny adb UI helper for device tests: python tools/ui.py tap "text" | tapfield | shot name | dump"""
import os
import re
import subprocess
import sys

ADB = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
ENV = dict(os.environ, MSYS_NO_PATHCONV="1")


def adb(*a):
    return subprocess.run([ADB, *a], capture_output=True, text=True, env=ENV).stdout


def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/u.xml")
    adb("pull", "/sdcard/u.xml", "u.xml")
    return open("u.xml", encoding="utf-8").read()


def find(needle):
    for n in re.findall(r"<node [^>]*>", dump()):
        t = re.search(r' text="([^"]*)"', n)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
        hit = (needle == "EditText" and "EditText" in n) or (t and needle in t.group(1))
        if hit and b:
            x1, y1, x2, y2 = map(int, b.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd in ("tap", "tapfield"):
        xy = find("EditText" if cmd == "tapfield" else sys.argv[2])
        print("tap", xy)
        if xy:
            adb("shell", "input", "tap", str(xy[0]), str(xy[1]))
    elif cmd == "shot":
        adb("shell", "screencap", "-p", "/sdcard/s.png")
        adb("pull", "/sdcard/s.png", sys.argv[2] + ".png")
    elif cmd == "dump":
        for n in re.findall(r"<node [^>]*>", dump()):
            t = re.search(r' text="([^"]*)"', n)
            b = re.search(r'bounds="([^"]*)"', n)
            if t and t.group(1):
                print(t.group(1)[:60], b.group(1))
