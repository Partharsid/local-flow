"""List input devices and record 2 s from each to see which one hears sound."""
import numpy as np
import sounddevice as sd

d_in = sd.default.device[0]
print("default input:", d_in, sd.query_devices(d_in)["name"])
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] < 1:
        continue
    api = sd.query_hostapis(d["hostapi"])["name"]
    if api not in ("MME", "Windows WASAPI"):
        continue
    try:
        sr = 16000 if api == "MME" else int(d["default_samplerate"])
        a = sd.rec(int(2 * sr), samplerate=sr, channels=1, dtype="float32", device=i)
        sd.wait()
        rms, peak = float(np.sqrt(np.mean(a * a))), float(np.abs(a).max())
        print(f"[{i:>2}] {api:15} {d['name'][:45]:45} rms={rms:.5f} peak={peak:.4f}")
    except Exception as e:  # noqa: BLE001
        print(f"[{i:>2}] {api:15} {d['name'][:45]:45} ERROR {str(e)[:60]}")
