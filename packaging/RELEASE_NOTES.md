**LocalFlow: private voice dictation that runs entirely on your PC.**

Hold **Right Ctrl**, speak, and let go. Your words appear, cleaned up, in whatever app you're typing in. Nothing leaves your computer.

### New in 1.2.1
- **Stable at sign-in:** when the speech model or AI engine fails to load right after Windows starts (while other startup apps are busy with the GPU), LocalFlow keeps retrying in the background until both work, instead of staying broken until a restart.
- **Free cloud AI with OpenRouter:** paste a free OpenRouter key in *Settings → Models & AI → Cloud AI*. LocalFlow tests the free models (such as Gemma) on real dictations and picks the best and fastest, with the next best as automatic backups. Free use is 50 requests a day, or 1,000 once you've bought $10 of OpenRouter credits.
- **Never stuck without AI:** if the cloud is offline or out of free requests, the built-in engine takes over until the cloud is back.
- **The API key box is always visible** in Settings. Before, choosing "Cloud" didn't show it.
- **Better structure:** points, reasons and benefits become bullet lists, and multi-step instructions become numbered steps. Prompt mode now organises only what you said (task, context, steps) and no longer invents requirements.
- **Fixed:** in Claude and other chat apps, dictation into an empty box started with a space and a lowercase letter.

### New in 1.2.0
- **Fixed: LocalFlow lost its AI when it started with Windows.** Windows runs sign-in apps in a restricted job, and inside it the GPU speech model and the AI engine failed to load. LocalFlow quietly fell back to a small CPU model with no cleanup. It now leaves that job at startup, and retries the GPU and AI engine if they fail.
- **Writes like a writer:** question marks on every question, paragraphs for long dictation, and numbered or bulleted lists when you list things.
- **AI prompt mode:** in Claude, ChatGPT, Gemini or Claude Code (in a terminal), what you say becomes a clear, structured prompt (task, context, requirements, constraints) that keeps every detail you said and adds nothing you didn't. Short replies stay plain messages.
- **Say a mode first:** "Prompt mode, …", "Bullet points, …", "Email mode, …", "Casual mode, …", "Plain mode, …" or "Verbatim, …".
- **Bullet notes** style for any app.
- **Much faster long dictation:** speech is transcribed in pieces while you talk. A 100-second dictation now appears about 0.5 s after you stop (was 2.7 s), and it's more accurate too.
- **Faster speech recognition:** about 2.5× faster decoding with the same accuracy.
- **Optional cloud AI cleanup:** paste an OpenRouter, Google Gemini, Groq, Cerebras or OpenAI key. LocalFlow tests the fast models your key can use and picks the most accurate and quickest one. It needs no GPU or memory on your PC. The key is kept in Windows Credential Manager. Only the transcript and app name are sent, never your audio or screen text.
- Lighter on laptops: the live preview only runs on an NVIDIA GPU, and the AI engine's first dictation is fast (its prompt cache is filled at startup).

### New in 1.1.1
- **Lighter when idle:** the AI engine unloads after 10 minutes without use, freeing about 2.5 GB of RAM and 2.6 GB of GPU memory. It loads again in the background as soon as you start talking. Change or turn this off with `cleanup.idle_unload_minutes` in the config (0 keeps it loaded).
- **No longer blocks Windows shutdown or restart:** LocalFlow now exits cleanly when you sign out or shut down.
- The stuck-key safety net now covers edit-by-voice (Right Alt) too.

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
