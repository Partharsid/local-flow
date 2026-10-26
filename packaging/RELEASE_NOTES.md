**LocalFlow: private voice dictation that runs entirely on your PC.**

Hold **Right Ctrl**, speak, and let go. Your words appear, cleaned up, in whatever app you're typing in. Nothing leaves your computer.

### Install
1. Download **LocalFlow-Setup-x.y.z.exe** below and run it. No admin rights are needed.
2. On first launch LocalFlow downloads its models once, then works fully offline:
   - Whisper large-v3-turbo (1.6 GB)
   - NVIDIA GPU libraries, if you have an NVIDIA GPU (1.4 GB)
   - Qwen3-4B for AI cleanup (2.5 GB, optional)

The portable `.zip` is the same app without an installer.

### Requirements
- Windows 10 or 11, 64-bit
- **Recommended:** an NVIDIA GPU with 6 GB or more of VRAM (about 1 s from speech to text). Without an NVIDIA GPU, LocalFlow uses a smaller CPU model.
- About 6 GB of disk space for the models

Windows SmartScreen may warn about an unrecognised app because the installer isn't code-signed. Choose **More info → Run anyway**.
