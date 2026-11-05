"""Leave the Job Object Windows puts login (Startup folder) apps in.

At sign-in, Windows 11 starts Startup-folder apps inside a resource-limited
job. Everything LocalFlow starts inherits it: Whisper on CUDA then failed with
"Invalid string length in model.bin" and llama-server crashed (0xC0000005),
so every autostart silently fell back to Whisper small on the CPU with no AI
cleanup, while a manual start worked fine. LocalFlow now relaunches itself
outside that job before doing anything else.
"""
from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
# Flags that only describe how the job behaves, not limits on us.
_HARMLESS = 0x0800 | 0x1000 | 0x2000 | 0x0400   # BREAKAWAY_OK, SILENT_BREAKAWAY_OK, KILL_ON_JOB_CLOSE, DIE_ON_UNHANDLED
ESCAPED_FLAG = "--escaped-job"


def job_info() -> dict | None:
    """Limits of the job this process is in, or None when not in one."""
    try:
        import win32api
        import win32job
        if not win32job.IsProcessInJob(win32api.GetCurrentProcess(), None):
            return None
        ext = win32job.QueryInformationJobObject(None, win32job.JobObjectExtendedLimitInformation)
        flags = ext["BasicLimitInformation"]["LimitFlags"]
        info = {"flags": flags, "process_memory": ext["ProcessMemoryLimit"], "job_memory": ext["JobMemoryLimit"],
                "working_set_max": ext["BasicLimitInformation"]["MaximumWorkingSetSize"]}
        try:
            ui = win32job.QueryInformationJobObject(None, win32job.JobObjectBasicUIRestrictions)
            info["ui"] = ui.get("UIRestrictionsClass", 0) if isinstance(ui, dict) else ui
        except Exception:  # noqa: BLE001
            pass
        return info
    except Exception as e:  # noqa: BLE001
        log.debug("job query failed: %s", e)
        return None


def restrictive(info: dict | None) -> bool:
    if not info:
        return False
    return bool(info["flags"] & ~_HARMLESS) or bool(info.get("ui")) or info["process_memory"] or info["job_memory"]


def _command(extra: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    return [exe, "-m", "localflow", *extra]


def relaunch_outside_job(argv: list[str]) -> bool:
    """Start a copy of LocalFlow that is not in our job. True if it started."""
    args = [a for a in argv if a != "--autostart"] + [ESCAPED_FLAG]
    cmd = _command(args)
    try:
        subprocess.Popen(cmd, creationflags=CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)
        log.info("Relaunched outside the startup job (breakaway)")
        return True
    except OSError as e:
        log.info("Job breakaway not allowed (%s); trying WMI", e)
    try:
        # WMI creates the process from its own service host, outside our job.
        import win32com.client
        cls = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2").Get("Win32_Process")
        params = cls.Methods_("Create").InParameters.SpawnInstance_()
        params.CommandLine = subprocess.list2cmdline(cmd)
        code = cls.ExecMethod_("Create", params).ReturnValue
        if code == 0:
            log.info("Relaunched outside the startup job (WMI)")
            return True
        log.warning("WMI relaunch failed with code %s", code)
    except Exception as e:  # noqa: BLE001
        log.warning("WMI relaunch failed: %s", e)
    return False


def maybe_escape(argv: list[str]) -> bool:
    """Called first thing in main(). True means: a fresh copy was started,
    so this process should exit now."""
    info = job_info()
    if info:
        log.info("Running inside a job: %s", {k: (hex(v) if k == "flags" else v) for k, v in info.items()})
    if ESCAPED_FLAG in argv or not info:
        return False
    if "--autostart" in argv or restrictive(info):
        return relaunch_outside_job(argv[1:])
    return False
