"""Which program is on the other end of a connection to 127.0.0.1 (for the phone page).

Every account on a computer shares 127.0.0.1, so "it came from this computer" isn't enough: the phone
page also asks the operating system who owns the other end of the connection.

- "tailscale": Tailscale's own service (Windows: the Tailscale service or a tailscaled.exe it started;
  Linux: a root-owned socket, which is how tailscaled runs). Only these may pass on a Tailscale login.
- "me": a program running as the same user as the phone page.
- "other": anyone else (another Windows account, a service account). The phone page refuses them.
- "unknown": the system doesn't say (macOS, or an error). The phone page then trusts 127.0.0.1 as before.

Nothing here needs administrator rights: Windows lists every connection with its process number, and a
program may read its own user's processes and the Tailscale service's process number.
"""

from __future__ import annotations

import os
import socket
import struct
import sys

LOCALHOST = "127.0.0.1"
TAILSCALE_SERVICE = "Tailscale"


def kind_of_client(client_port: int, server_port: int) -> str:
    """Who owns the client end of the connection 127.0.0.1:client_port -> 127.0.0.1:server_port."""
    try:
        if os.name == "nt":
            return _windows_kind(_windows_owner(client_port, server_port))
        if sys.platform.startswith("linux"):
            return _linux_kind(_linux_uid(client_port, server_port))
    except (OSError, ValueError, AttributeError):
        return "other" if os.name == "nt" or sys.platform.startswith("linux") else "unknown"
    return "unknown"


def kind_of_listener(port: int) -> str:
    """Who is listening on 127.0.0.1:port ("me", "other", "tailscale", or "unknown")."""
    return kind_of_client(port, 0)


# ---- Windows -----------------------------------------------------------------------------------------
def _windows_owner(local_port: int, remote_port: int) -> int | None:
    """Process number owning the IPv4 socket 127.0.0.1:local_port connected to 127.0.0.1:remote_port.

    remote_port 0 means the socket listening on local_port."""
    import ctypes
    from ctypes import wintypes

    iphlp = ctypes.WinDLL("iphlpapi")
    get_table = iphlp.GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG,
                          ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    tcp_table_owner_pid_all = 5
    for _ in range(5):                         # the table can grow between the two calls
        buf = ctypes.create_string_buffer(size.value or 1)
        result = get_table(buf, ctypes.byref(size), False, socket.AF_INET, tcp_table_owner_pid_all, 0)
        if result == 0:
            break
        if result != 122:                      # ERROR_INSUFFICIENT_BUFFER: try again with the new size
            raise OSError(f"GetExtendedTcpTable failed ({result})")
    else:
        raise OSError("GetExtendedTcpTable kept growing")
    raw = buf.raw
    (count,) = struct.unpack_from("<I", raw, 0)
    loopback = struct.unpack("<I", socket.inet_aton(LOCALHOST))[0]
    for i in range(count):
        state, laddr, lport, raddr, rport, pid = struct.unpack_from("<6I", raw, 4 + i * 24)
        if laddr != loopback or socket.ntohs(lport & 0xFFFF) != local_port:
            continue
        if remote_port == 0 and state == 2:    # MIB_TCP_STATE_LISTEN
            return pid
        if remote_port and raddr == loopback and socket.ntohs(rport & 0xFFFF) == remote_port:
            return pid
    return None


def _windows_kind(pid: int | None) -> str:
    if not pid:
        return "other"
    if _is_tailscale(pid):
        return "tailscale"
    mine = _windows_user(os.getpid())
    return "me" if mine and _windows_user(pid) == mine else "other"


def _is_tailscale(pid: int) -> bool:
    service = _service_pid(TAILSCALE_SERVICE)
    if not service:
        return False
    if pid == service:
        return True
    name, parent = _process_name_and_parent(pid)
    return parent == service and name.lower() == "tailscaled.exe"


def _service_pid(name: str) -> int:
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenSCManagerW.restype = wintypes.HANDLE
    adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenServiceW.restype = wintypes.HANDLE
    adv.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    adv.QueryServiceStatusEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    adv.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    manager = adv.OpenSCManagerW(None, None, 0x0001)               # SC_MANAGER_CONNECT
    if not manager:
        return 0
    try:
        service = adv.OpenServiceW(manager, name, 0x0004)          # SERVICE_QUERY_STATUS
        if not service:
            return 0
        try:
            buf = ctypes.create_string_buffer(64)
            needed = wintypes.DWORD(0)
            if not adv.QueryServiceStatusEx(service, 0, buf, len(buf), ctypes.byref(needed)):
                return 0
            return struct.unpack_from("<9I", buf.raw, 0)[7]        # SERVICE_STATUS_PROCESS.dwProcessId
        finally:
            adv.CloseServiceHandle(service)
    finally:
        adv.CloseServiceHandle(manager)


def _process_name_and_parent(pid: int) -> tuple[str, int]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)             # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return "", 0
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return entry.szExeFile, entry.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return "", 0


def _windows_user(pid: int) -> str | None:
    """The account (as a SID string) a process runs as, or None when Windows won't say (someone else's)."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [wintypes.HLOCAL]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    process = k32.OpenProcess(0x1000, False, pid)                  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        token = wintypes.HANDLE()
        if not adv.OpenProcessToken(process, 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
            return None
        try:
            buf = ctypes.create_string_buffer(256)
            needed = wintypes.DWORD(0)
            if not adv.GetTokenInformation(token, 1, buf, len(buf), ctypes.byref(needed)):  # TokenUser
                return None
            sid = ctypes.c_void_p.from_buffer(buf).value             # TOKEN_USER.User.Sid
            text = wintypes.LPWSTR()
            if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                return None
            try:
                return text.value
            finally:
                k32.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
        finally:
            k32.CloseHandle(token)
    finally:
        k32.CloseHandle(process)


# ---- Linux -------------------------------------------------------------------------------------------
def _linux_uid(local_port: int, remote_port: int) -> int | None:
    """The user id owning 127.0.0.1:local_port (connected to remote_port, or listening when it is 0)."""
    loopback = "0100007F" if sys.byteorder == "little" else "7F000001"
    with open("/proc/net/tcp", encoding="ascii") as f:
        next(f)
        for line in f:
            parts = line.split()
            local, remote, state, uid = parts[1], parts[2], parts[3], parts[7]
            if local != f"{loopback}:{local_port:04X}":
                continue
            if remote_port == 0 and state == "0A":                   # TCP_LISTEN
                return int(uid)
            if remote_port and remote == f"{loopback}:{remote_port:04X}":
                return int(uid)
    return None


def _linux_kind(uid: int | None) -> str:
    if uid is None:
        return "other"
    if uid == os.getuid():
        return "me"
    return "tailscale" if uid == 0 else "other"
