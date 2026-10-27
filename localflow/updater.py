"""Check GitHub Releases for a newer LocalFlow and install it in one click.

Only the packaged app updates itself (running from source = use git). The
installer download is verified against the SHA-256 digest GitHub publishes
for every release asset before it is run.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

import requests

from . import __version__
from .config import DATA_DIR

log = logging.getLogger(__name__)


@dataclass
class Update:
    version: str
    url: str
    size: int
    sha256: str
    notes_url: str


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _parse(remote) > _parse(local)


def check(repo: str, skip: str = "") -> Update | None:
    """Latest release if it is newer than this build (None otherwise, or if
    GitHub can't be reached / the repository isn't public)."""
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=10,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "LocalFlow-updater"})
        if r.status_code != 200:
            log.info("Update check: GitHub answered %s", r.status_code)
            return None
        rel = r.json()
    except (requests.RequestException, ValueError) as e:
        log.info("Update check failed: %s", e)
        return None
    version = (rel.get("tag_name") or "").lstrip("v")
    if not version or not is_newer(version) or version == skip:
        return None
    for a in rel.get("assets", []):
        name = a.get("name", "")
        digest = a.get("digest") or ""
        if name.startswith("LocalFlow-Setup-") and name.endswith(".exe") and digest.startswith("sha256:"):
            return Update(version, a["browser_download_url"], int(a["size"]), digest.split(":", 1)[1],
                          rel.get("html_url", ""))
    log.info("Release %s has no verifiable installer asset", version)
    return None


def download_and_run(update: Update, progress=None) -> None:
    """Download + verify the installer, then start it. The caller must quit
    right away: the installer waits for LocalFlow to close (AppMutex)."""
    from .firstrun import Asset, Downloader

    dest = DATA_DIR / "downloads" / f"LocalFlow-Setup-{update.version}.exe"
    Downloader(progress or (lambda n: None)).fetch(Asset(update.url, dest, update.size, update.sha256))
    # Start the installer a few seconds later, after LocalFlow has exited:
    # it refuses to run while LocalFlow holds its mutex, and the app's files
    # are locked while it runs. /relaunch=1 starts LocalFlow again afterwards.
    cmd = (f'ping -n 4 127.0.0.1 >nul & start "" "{dest}" /SILENT /SUPPRESSMSGBOXES /NORESTART '
           f'/CLOSEAPPLICATIONS /relaunch=1')
    subprocess.Popen(["cmd.exe", "/c", cmd], close_fds=True,
                     creationflags=0x08000000 | 0x00000008)   # CREATE_NO_WINDOW | DETACHED_PROCESS
    log.info("Scheduled installer for %s", update.version)


def cleanup_old_downloads() -> None:
    for p in (DATA_DIR / "downloads").glob("LocalFlow-Setup-*.exe"):
        try:
            p.unlink()
        except OSError:
            pass
