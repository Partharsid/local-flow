"""Check soundcard capture: real sample rate and whether loopback hears playback."""
import threading
import time
import warnings

import numpy as np
import soundcard as sc

warnings.filterwarnings("ignore")


def speak():
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    v = comtypes.client.CreateObject("SAPI.SpVoice")
    print("SAPI output device:", v.AudioOutput.GetDescription())
    v.Speak("Testing the meeting recorder. The deployment moved to Thursday.")


print("default speaker:", sc.default_speaker().name, "| default mic:", sc.default_microphone().name)
for name, dev in (("system", sc.get_microphone(sc.default_speaker().name, include_loopback=True)),
                  ("mic", sc.default_microphone())):
    with dev.recorder(samplerate=16000, channels=1, blocksize=1600) as rec:
        t = threading.Thread(target=speak) if name == "system" else None
        if t:
            t.start()
        got, t0 = [], time.perf_counter()
        while time.perf_counter() - t0 < 4.0:
            got.append(rec.record(numframes=1600)[:, 0])
        data = np.concatenate(got)
        wall = time.perf_counter() - t0
        if t:
            t.join()
    print(f"{name:6}: {data.size} samples in {wall:.2f}s wall -> {data.size / wall:.0f} samples/s, "
          f"rms {float(np.sqrt(np.mean(data ** 2))):.4f}, peak {float(np.abs(data).max()):.3f}")
