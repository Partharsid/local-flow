"""System-tray icon and menu (pystray runs it on its own thread)."""
from __future__ import annotations

import logging

import pystray

from .branding import app_icon

log = logging.getLogger(__name__)

STATES = ("idle", "recording", "working", "error", "loading")


class Tray:
    def __init__(self, app):
        self.app = app
        self._images = {k: app_icon(64, k) for k in STATES}
        self.icon = pystray.Icon("LocalFlow", self._images["loading"], "LocalFlow - loading", self._menu())

    def _menu(self) -> pystray.Menu:
        a = self.app
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _i: a.status_text(), None, enabled=False),
            item(lambda _i: f"Install update ({a.update_available.version})" if a.update_available else "",
                 lambda: a.install_update(), visible=lambda _i: a.update_available is not None),
            pystray.Menu.SEPARATOR,
            item("Unmute microphone", lambda: a.unmute_mic(), visible=lambda _i: a.mic_muted),
            item("Open LocalFlow", lambda: a.ui_call(a.open_settings), default=True),
            item("Copy last transcript", lambda: a.paste_last(from_tray=True)),
            item("Cancel transcription", lambda: a.cancel_job(), visible=lambda _i: a.job_cancel is not None),
            item("Stop meeting notes", lambda: a.ui_call(a.stop_meeting), visible=lambda _i: a.meeting.active),
            pystray.Menu.SEPARATOR,
            item("Quit LocalFlow", lambda: a.quit()),
        )

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        try:
            self.icon.icon = self._images.get(state, self._images["idle"])
            if tooltip:
                self.icon.title = tooltip[:120]
        except Exception as e:  # noqa: BLE001
            log.debug("tray update failed: %s", e)

    def set_tooltip(self, tooltip: str) -> None:
        try:
            self.icon.title = tooltip[:120]
        except Exception as e:  # noqa: BLE001
            log.debug("tray tooltip failed: %s", e)

    def notify(self, text: str, title: str = "LocalFlow") -> None:
        try:
            self.icon.notify(text, title)
        except Exception as e:  # noqa: BLE001
            log.debug("tray notify failed: %s", e)

    def refresh(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
