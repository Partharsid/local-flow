# LocalFlow for Android

Private voice dictation on your phone: a floating bubble over any keyboard, speech recognition and text cleanup that run
entirely on the device (whisper.cpp + llama.cpp), and a Roman-script **Hinglish** mode for mixed Hindi and English.

## Build

1. Fetch the engines (pinned): `git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp android/third_party/whisper.cpp`
   and `git clone --depth 1 --branch b11898 https://github.com/ggml-org/llama.cpp android/third_party/llama.cpp`
2. JDK 17, Android SDK 35 + NDK 27.1 + CMake 3.22 (Android Studio installs all of them).
3. `gradle :app:installDebug` (Gradle 8.10) with the phone connected. arm64-v8a only.

## How it works

- **Bubble**: an accessibility service shows a small translucent bubble above the keyboard when a text field is focused.
  Tap or hold it, speak, and the text is typed at the cursor. The service only reads the focused field.
- **Device tiers** (`DeviceProfile`): Lite (<4.8 GB RAM) base speech model; Balanced small; Full (>= 6.8 GB) small + the
  fine-tuned cleanup model. "Best accuracy" swaps in large-v3-turbo.
- **Speed**: Q8_0 speech models (int8 matrix kernels), encoder window trimmed to the clip length, silence trimmed before
  decoding, big-core pinning, models loaded on demand and unloaded after 5 idle minutes.
- **Hinglish**: a fine-tuned speech model (Oriserve Whisper-Hindi2Hinglish-Swift, Apache-2.0, converted to ggml Q8_0 with
  `tools/quantize`) writes Hindi in English letters directly. On 11 Hindi/Hinglish clips it measured ~35% word errors vs
  ~66% for the general model with a Roman prompt, and ran ~10x faster (`tools/eval_hinglish.py`). Without it installed, Hinglish falls
  back to the general model plus a Roman-script prompt, and `text/Hinglish.kt` transliterates any Devanagari that remains.
- **Latency**: finished sentences are transcribed while you are still speaking (`Recorder.takeSegment`); only the last bit is left at stop.
  Longer English dictation is typed at once, then replaced by the AI-polished version (prompt-lookup speculative decoding, ~2x faster).

Debug builds include `BenchReceiver` to time the engine on a WAV: `adb shell am broadcast -n app.localflow/.BenchReceiver
-a app.localflow.BENCH --es path /data/local/tmp/x.wav --es mode HINGLISH`.
