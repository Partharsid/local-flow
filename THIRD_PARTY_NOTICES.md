# Third-party components

LocalFlow's own code is MIT licensed. It builds on the projects below, which keep their own licences.

## Downloaded at first run (not shipped in the installer)

| Component | Source | Licence |
|---|---|---|
| Whisper large-v3-turbo, CTranslate2 format | huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo (OpenAI Whisper weights) | MIT |
| Whisper small, CTranslate2 format (CPU-only PCs) | huggingface.co/Systran/faster-whisper-small | MIT |
| Qwen3-4B-Instruct-2507, GGUF Q4_K_M | huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF (Qwen team) | Apache-2.0 |
| llama.cpp `llama-server` (Vulkan build b11898) | github.com/ggml-org/llama.cpp | MIT |
| NVIDIA cuBLAS, cuDNN, NVRTC (NVIDIA GPUs only) | PyPI `nvidia-*-cu12` wheels | NVIDIA Software License Agreement |

## Bundled in the app

| Component | Licence |
|---|---|
| faster-whisper, CTranslate2 | MIT |
| Silero VAD (in faster-whisper) | MIT |
| ONNX Runtime | MIT |
| PyAV / FFmpeg libraries | BSD-3-Clause / LGPL-2.1 |
| CustomTkinter, comtypes, pycaw, sounddevice (PortAudio) | MIT |
| pystray | LGPL-3.0 (source: github.com/moses-palmer/pystray) |
| uiautomation, requests | Apache-2.0 |
| pywin32 | PSF |
| NumPy, psutil | BSD-3-Clause |
| Pillow | MIT-CMU |
| Python | PSF |

Thank you to all of these projects.

## LocalFlow for Android: speech models

- **Whisper-Hindi2Hinglish-Swift** by Oriserve (https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Swift), Apache-2.0,
  a fine-tune of openai/whisper-base (MIT). Converted to ggml and quantised to Q8_0; hosted as `localflow-hinglish-q8_0.bin`
  in the `models-2` release.
- **whisper.cpp** (MIT) and **llama.cpp** (MIT) by ggml-org, built into the Android app.
- **Whisper** models by OpenAI (MIT), in ggml form from https://huggingface.co/ggerganov/whisper.cpp.


## Names, brands and places list (Android `names.bin`)
Built by `android/tools/build_names.py` from: GeoNames place names (CC BY 4.0, https://www.geonames.org), Wikidata labels (CC0), the `english-words` list (public domain) and FrequencyWords Hindi (CC BY-SA 4.0), plus a hand-picked list in `android/tools/names_curated.txt`. The Hindi/English word lists are only used to avoid rewriting ordinary words.
