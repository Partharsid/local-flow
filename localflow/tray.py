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
            pystray.Menu.SEPARATOR,
            item("Unmute microphone", lambda: a.unmute_mic(), visible=lambda _i: a.mic_muted),
            item("Settings…", lambda: a.ui_call(a.open_settings), default=True),
            item("Copy last transcript", lambda: a.paste_last(from_tray=True)),
            pystray.Menu.SEPARATOR,
            # Setting changes run on the UI thread, same as the settings window.
            item("AI cleanup", lambda: a.ui_call(a.toggle_setting, "cleanup.enabled"),
                 checked=lambda _i: a.cfg["cleanup"]["enabled"]),
            item("Read text around cursor", lambda: a.ui_call(a.toggle_setting, "use_context"),
                 checked=lambda _i: a.cfg["use_context"]),
            item("Sounds", lambda: a.ui_call(a.toggle_setting, "sounds"), checked=lambda _i: a.cfg["sounds"]),
            item("Start with Windows", lambda: a.ui_call(a.toggle_autostart),
                 checked=lambda _i: a.autostart_enabled()),
            pystray.Menu.SEPARATOR,
            item("Open logs folder", lambda: a.open_folder("logs")),
            item("Quit LocalFlow", lambda: a.quit()),
        )

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        try:
            self.icon.icon = self._images.get(state, self._images["idle"])
            if tooltip:
                self.icon.title = tooltip[:120]
        except Exception as e:  # noqa: BLE001
            log.debug("tray update failed: %s", e)

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
