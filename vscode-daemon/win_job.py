"""Windows Job Objects: stop an application's whole process tree (issue #396).

On Windows the application check starts the app through ``cmd.exe`` or
PowerShell. ``taskkill /T`` follows the tree only while the wrapper lives; a
wrapper that already exited leaves its children orphaned and untraceable, and
the server keeps its port. A Job Object holds every process started inside it,
whatever happens to the parent: the process is created suspended, put in a job
created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, then resumed, so nothing
it starts can escape before it is contained. Stopping terminates the job.

Driven through ``ctypes`` so the daemon keeps ``nats-py`` as its only
dependency. Every function is a no-op that returns ``None`` or ``False``
outside Windows, or when a call fails; the caller then falls back to
``taskkill /T``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800

AVAILABLE = os.name == "nt"


def creationflags() -> int:
    """The flags a process must be started with to be contained (Windows only)."""
    return CREATE_SUSPENDED if AVAILABLE else 0


def _api() -> Any:
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BASIC_LIMITS(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMITS),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    return ctypes, kernel32, ntdll, EXTENDED_LIMITS


def _resume(ntdll: Any, kernel32: Any, process: subprocess.Popen) -> bool:
    handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, process.pid)
    if not handle:
        return False
    try:
        return ntdll.NtResumeProcess(handle) == 0
    finally:
        kernel32.CloseHandle(handle)


def contain(process: subprocess.Popen) -> Optional[int]:
    """Put *process*, started with ``creationflags()``, in a kill-on-close job and resume it.

    Returns the job's handle, or ``None`` when it could not be contained; the
    process is resumed either way, so it never stays suspended."""
    if not AVAILABLE:
        return None
    try:
        ctypes, kernel32, ntdll, EXTENDED_LIMITS = _api()
    except (OSError, AttributeError) as exc:
        logger.warning("Job Objects unavailable: %s", exc)
        return None
    job = None
    try:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = EXTENDED_LIMITS()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, process.pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not kernel32.AssignProcessToJobObject(job, handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        finally:
            kernel32.CloseHandle(handle)
    except OSError as exc:
        logger.warning("Could not contain process %s in a job: %s", process.pid, exc)
        if job:
            kernel32.CloseHandle(job)
        job = None
    if not _resume(ntdll, kernel32, process):
        logger.warning("Could not resume process %s", process.pid)
    return job


def terminate(job: Optional[int]) -> bool:
    """Kill every process in *job* and close it; ``True`` when the job was terminated."""
    if not AVAILABLE or not job:
        return False
    try:
        _, kernel32, _, _ = _api()
    except (OSError, AttributeError):
        return False
    try:
        return bool(kernel32.TerminateJobObject(job, 1))
    finally:
        # Closing the last handle kills anything left (KILL_ON_JOB_CLOSE).
        kernel32.CloseHandle(job)
