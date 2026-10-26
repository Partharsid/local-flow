"""Print the default mic's mute state once a second (catches an app re-muting it)."""
import time
import warnings

warnings.simplefilter("ignore")
from pycaw.pycaw import AudioUtilities, EDataFlow, ERole  # noqa: E402

enum = AudioUtilities.GetDeviceEnumerator()
for _ in range(8):
    dev = AudioUtilities.CreateDevice(enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eConsole.value))
    v = dev.EndpointVolume
    print(f"{time.strftime('%H:%M:%S')}  {dev.FriendlyName}: muted={bool(v.GetMute())} level={v.GetMasterVolumeLevelScalar()*100:.0f}%")
    time.sleep(1)
