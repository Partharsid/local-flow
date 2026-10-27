**LocalFlow: private voice dictation that runs entirely on your PC.**

Hold **Right Ctrl**, speak, and let go. Your words appear, cleaned up, in whatever app you're typing in. Nothing leaves your computer.

### New in 1.1
- **Edit by voice:** select text, hold **Right Alt**, and say "make this formal", "translate to Hindi" or "turn this into bullet points". With nothing selected, it writes new text at your cursor.
- **Meeting notes:** records your mic and your computer's audio (the other people on a call), labels lines **You** and **Others**, and writes a summary, key points, decisions and action items as Markdown in *Documents\LocalFlow Meetings*.
- **Transcribe files:** audio or video becomes `.txt` plus `.srt` subtitles (tray menu or *Settings → Files & meetings*).
- **Live preview:** your words appear in the bar while you speak.
- **Learns from your corrections:** fix a misheard name right after dictating and it's added to your Dictionary.
- **Writing style per app:** casual in chat, formal in email, technical in code editors and verbatim in terminals, with your own per-app overrides.
- **Auto-update:** checks this page daily and installs new versions in one click.

### Install
1. Download **LocalFlow-Setup-x.y.z.exe** below and run it. No admin rights are needed.
2. On first launch LocalFlow downloads its models once, then works fully offline:
   - Whisper large-v3-turbo (1.6 GB)
   - NVIDIA GPU libraries, if you have an NVIDIA GPU (1.4 GB)
   - Qwen3-4B for AI features (2.5 GB, optional)

Upgrading from 1.0 keeps your settings and models: just run the new installer.

### Requirements
- Windows 10 or 11, 64-bit
- **Recommended:** an NVIDIA GPU with 6 GB or more of VRAM. Without one, LocalFlow uses a smaller CPU model.
- About 6 GB of disk space for the models

Windows SmartScreen may warn about an unrecognised app because the installer isn't code-signed. Choose **More info → Run anyway**.
