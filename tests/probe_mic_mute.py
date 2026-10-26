"""Report mute state and level of every capture (microphone) endpoint."""
from pycaw.pycaw import AudioUtilities, EDataFlow, ERole

enum = AudioUtilities.GetDeviceEnumerator()
default = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eCommunications.value)
print("default mic id:", default.GetId())
for dev in AudioUtilities.GetAllDevices(data_flow=EDataFlow.eCapture.value):
    try:
        vol = dev.EndpointVolume
        print(f"{dev.FriendlyName!s:45} state={dev.state!s:22} muted={bool(vol.GetMute())} "
              f"level={vol.GetMasterVolumeLevelScalar() * 100:.0f}%")
    except Exception as e:  # noqa: BLE001
        print(f"{dev.FriendlyName!s:45} state={dev.state!s:22} (no volume info: {e})")
