"""Read only the active Weixin account's local media key, without persisting it."""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import psutil


class StickerKeyUnavailable(RuntimeError):
    pass


# Weixin 4.1.9.23: pin both accessors before following their account layout.
# Unknown builds fail closed rather than reading arbitrary process memory.
ACCESSORS = (
    (0x20960, 92, '2a105623b0d0231ce8cea33821c305e5584f59e1993effa0e1307425a476c77d'),
    (0x3033E0, 602, '26d826670815a3e6c9ba10878f402aa7cb9a80d296b17f7c987e8b77b79da8f4'),
)
LOGIN_MANAGER_RVA = 0xA52F438
KEY_MASK = struct.pack('<QQQQ', 0xD28F8959E7EE6112, 0x96276D7D96A203EB,
                       0x7AF7D61426DE5172, 0x94B4237B9E14B271)


def account_key(read, base: int, account: str) -> bytes:
    """Bounded reads; also recheck identity and key after reading the account."""
    def exact(address, size):
        if not 0x10000 <= address < 0x800000000000 - size or not 0 < size <= 4096:
            raise StickerKeyUnavailable()
        value = read(address, size)
        if len(value) != size:
            raise StickerKeyUnavailable()
        return value

    def pointer(address):
        return struct.unpack('<Q', exact(address, 8))[0]

    def string(address, lengths=None):
        header = exact(address, 32)
        length, capacity = struct.unpack('<QQ', header[16:])
        if not 0 < length <= 128 or not length <= capacity <= 4096:
            raise StickerKeyUnavailable()
        if lengths is not None and length not in lengths:
            raise StickerKeyUnavailable()
        if capacity < 16:
            return header[:length]
        return exact(struct.unpack('<Q', header[:8])[0], length)

    for rva, size, digest in ACCESSORS:
        if hashlib.sha256(exact(base + rva, size)).hexdigest() != digest:
            raise StickerKeyUnavailable()
    manager = pointer(base + LOGIN_MANAGER_RVA)
    user = pointer(manager + 0x68)
    expected = account.encode('utf-8')
    if string(user + 0x48) != expected:
        raise StickerKeyUnavailable()
    masked = string(user + 0x298, {16, 24, 32})
    if (pointer(base + LOGIN_MANAGER_RVA) != manager
            or pointer(manager + 0x68) != user
            or string(user + 0x48) != expected
            or string(user + 0x298, {16, 24, 32}) != masked):
        raise StickerKeyUnavailable()
    return bytes(value ^ KEY_MASK[index % 32] for index, value in enumerate(masked))


def bridge_process(bridge_url):
    url = urlsplit(bridge_url)
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or not url.port:
        raise StickerKeyUnavailable()
    owners = {connection.pid for connection in psutil.net_connections(kind='tcp')
              if connection.status == psutil.CONN_LISTEN and connection.pid
              and connection.laddr.port == url.port
              and connection.laddr.ip in {'127.0.0.1', '::1'}}
    if len(owners) != 1:
        raise StickerKeyUnavailable()
    process = psutil.Process(owners.pop())
    if not re.fullmatch(r'(?:weixin\d*|weixi\d+)\.exe', process.name().casefold()):
        raise StickerKeyUnavailable()
    return process.pid, process.create_time()


@contextmanager
def process_memory(pid):
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    signatures = (
        (kernel.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        (kernel.ReadProcessMemory, [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
        (psapi.EnumProcessModules, [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (psapi.GetModuleFileNameExW, [wintypes.HANDLE, ctypes.c_void_p,
                                    wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
    )
    for function, args, result in signatures:
        function.argtypes, function.restype = args, result
    handle = kernel.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ only.
    if not handle:
        raise StickerKeyUnavailable()
    try:
        modules = (ctypes.c_void_p * 2048)()
        needed = wintypes.DWORD()
        if (not psapi.EnumProcessModules(handle, modules, ctypes.sizeof(modules), ctypes.byref(needed))
                or needed.value > ctypes.sizeof(modules)):
            raise StickerKeyUnavailable()
        matches = []
        for module in modules[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            name = ctypes.create_unicode_buffer(32768)
            if (psapi.GetModuleFileNameExW(handle, module, name, len(name))
                    and re.fullmatch(r'(?:weixin\d*|weixi\d+)\.dll', Path(name.value).name.casefold())):
                matches.append(module)
        if len(matches) != 1:
            raise StickerKeyUnavailable()

        def read(address, size):
            buffer = ctypes.create_string_buffer(size)
            received = ctypes.c_size_t()
            if not kernel.ReadProcessMemory(handle, address, buffer, size, ctypes.byref(received)):
                raise StickerKeyUnavailable()
            return buffer.raw[:received.value]

        yield read, matches[0]
    finally:
        kernel.CloseHandle(handle)


def read_sticker_key(account: str, bridge_url: str) -> bytes:
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise StickerKeyUnavailable()
    try:
        before = bridge_process(bridge_url)
        with process_memory(before[0]) as (read, base):
            key = account_key(read, base, account)
            if bridge_process(bridge_url) != before:
                raise StickerKeyUnavailable()
            return key
    except (OSError, ValueError, psutil.Error):
        raise StickerKeyUnavailable() from None
