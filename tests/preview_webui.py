"""Serve the web UI against the throwaway stub app from preview_settings.py and print its URL."""
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / "preview_settings.py").read_text(encoding="utf-8")
exec(compile(src.split("win = SettingsWindow(app)")[0], "preview_settings_head", "exec"))   # noqa: S102

app.update_available = None
app.fast_dl = ""
app.cleaner.cloud_note = ""
app.cleaner.build_messages = lambda *a, **k: []
from localflow import webui  # noqa: E402

ui = webui.WebUI(app)
print("URL", ui.server.url(sys.argv[1] if len(sys.argv) > 1 else ""), flush=True)
app.root.mainloop()
