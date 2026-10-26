"""Explain *why* a recording was silent: muted endpoint, privacy block, or
no microphone at all. Used to replace a vague "No speech detected"."""
from __future__ import annotations

import logging
import warnings
import winreg

log = logging.getLogger(__name__)

_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _consent(root, path: str) -> str | None:
    try:
        with winreg.OpenKey(root, path) as k:
            return winreg.QueryValueEx(k, "Value")[0]
    except OSError:
        return None


def _default_volume():
    from pycaw.pycaw import AudioUtilities, EDataFlow, ERole
    enum = AudioUtilities.GetDeviceEnumerator()
    dev = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eConsole.value)
    return AudioUtilities.CreateDevice(dev).EndpointVolume


def is_muted() -> bool:
    import comtypes
    comtypes.CoInitialize()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return bool(_default_volume().GetMute())
    except Exception:  # noqa: BLE001
        return False
    finally:
        comtypes.CoUninitialize()


def unmute() -> bool:
    """Unmute the default microphone. Only called from an explicit user
    action (tray menu). Returns True if it is now unmuted."""
    import comtypes
    comtypes.CoInitialize()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vol = _default_volume()
            vol.SetMute(0, None)
            if vol.GetMasterVolumeLevelScalar() < 0.05:
                vol.SetMasterVolumeLevelScalar(0.7, None)
            return not vol.GetMute()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not unmute microphone: %s", e)
        return False
    finally:
        comtypes.CoUninitialize()


def diagnose() -> str | None:
    """Return a short user-facing problem description, or None if the
    microphone looks usable (the user may simply not have spoken)."""
    if _consent(winreg.HKEY_LOCAL_MACHINE, _CONSENT) == "Deny" or _consent(winreg.HKEY_CURRENT_USER, _CONSENT) == "Deny":
        return "Mic blocked: Settings > Privacy > Microphone"
    if _consent(winreg.HKEY_CURRENT_USER, _CONSENT + r"\NonPackaged") == "Deny":
        return "Mic blocked for desktop apps (Privacy settings)"
    import comtypes
    comtypes.CoInitialize()  # called from worker threads, which have no COM yet
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pycaw warns about unreadable properties
            from pycaw.pycaw import AudioUtilities, EDataFlow, ERole
            enum = AudioUtilities.GetDeviceEnumerator()
            dev = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eConsole.value)
            device = AudioUtilities.CreateDevice(dev)
            vol = device.EndpointVolume
            if vol.GetMute():
                return "Mic is muted · tray icon → Unmute microphone"
            if vol.GetMasterVolumeLevelScalar() < 0.05:
                return "Mic volume is 0% in Sound settings"
    except Exception as e:  # noqa: BLE001 - no default mic, COM errors
        log.info("Could not inspect microphone state: %s", e)
        return "No working microphone found"
    finally:
        comtypes.CoUninitialize()
    return None
