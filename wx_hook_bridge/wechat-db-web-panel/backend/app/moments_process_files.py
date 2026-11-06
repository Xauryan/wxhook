"""Read disk-file handles without psutil's per-handle name-query threads."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class ProcessFilesChanged(OSError):
    pass


class WindowsProcessFiles:
    MAX_BUFFER = 16 * 1024 * 1024

    def __init__(self):
        import msvcrt

        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.ntdll = ctypes.WinDLL('ntdll')
        signatures = {
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'GetCurrentProcess': ([], wintypes.HANDLE),
            'DuplicateHandle': ([wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                 ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                 wintypes.BOOL, wintypes.DWORD], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'GetFileType': ([wintypes.HANDLE], wintypes.DWORD),
            'GetFinalPathNameByHandleW': ([wintypes.HANDLE, wintypes.LPWSTR,
                                          wintypes.DWORD, wintypes.DWORD], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.query = self.ntdll.NtQueryInformationProcess
        self.query.argtypes = [wintypes.HANDLE, wintypes.ULONG, wintypes.LPVOID,
                               wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        self.query.restype = ctypes.c_long
        self.current = self.kernel.GetCurrentProcess()
        # Type indexes are assigned by Windows, not stable constants. Identify
        # File from our own regular-file handle; never infer it from a socket.
        with open(__file__, 'rb') as stream:
            handle = msvcrt.get_osfhandle(stream.fileno())
            self.file_type = next(entry.type_index for entry in self._snapshot(self.current)
                                  if entry.handle == handle)

    def _snapshot(self, process):
        class Entry(ctypes.Structure):
            _fields_ = [('handle', ctypes.c_size_t), ('handle_count', ctypes.c_size_t),
                        ('pointer_count', ctypes.c_size_t), ('access', wintypes.ULONG),
                        ('type_index', wintypes.ULONG), ('attributes', wintypes.ULONG),
                        ('reserved', wintypes.ULONG)]

        size = 64 * 1024
        header_size = 2 * ctypes.sizeof(ctypes.c_size_t)
        while size <= self.MAX_BUFFER:
            buffer = ctypes.create_string_buffer(size)
            required = wintypes.ULONG()
            status = self.query(process, 51, buffer, size, ctypes.byref(required))
            if status >= 0:
                count = ctypes.c_size_t.from_buffer(buffer).value
                if header_size + count * ctypes.sizeof(Entry) > size:
                    raise OSError('Invalid process handle snapshot')
                return (Entry * count).from_buffer_copy(buffer, header_size)
            if status & 0xffffffff not in {0xc0000004, 0xc0000023}:
                raise OSError('Process handle snapshot unavailable')
            size = max(size * 2, required.value)
        raise OSError('Process handle snapshot exceeds bound')

    def paths(self, pid):
        process = self.kernel.OpenProcess(0x440, False, pid)
        if not process:
            raise PermissionError('Process handle access unavailable')
        try:
            for attempt in range(3):
                try:
                    return self._paths(process)
                except ProcessFilesChanged:
                    if attempt == 2:
                        raise
        finally:
            self.kernel.CloseHandle(process)

    def _paths(self, process):
        result = set()
        name = ctypes.create_unicode_buffer(32768)
        for entry in self._snapshot(process):
            if entry.type_index != self.file_type:
                continue
            duplicate = wintypes.HANDLE()
            if not self.kernel.DuplicateHandle(process, entry.handle, self.current,
                                                ctypes.byref(duplicate), 0, False, 2):
                raise ProcessFilesChanged('File handles changed during verification')
            try:
                # Named pipes, sockets and device handles are not DB files.
                if self.kernel.GetFileType(duplicate) != 1:
                    continue
                length = self.kernel.GetFinalPathNameByHandleW(duplicate, name, len(name), 8)
                if not length or length >= len(name):
                    raise OSError('Disk-file handle path unavailable')
                value = name.value
                if value.startswith('\\\\?\\UNC\\'):
                    value = '\\\\' + value[8:]
                elif value.startswith('\\\\?\\'):
                    value = value[4:]
                result.add(os.path.normpath(value))
            finally:
                self.kernel.CloseHandle(duplicate)
        return result
