from __future__ import annotations

import ctypes
import struct
from pathlib import Path


class WindowsChangeWatcher:
    """Dependency-free ReadDirectoryChangesW wrapper; it never writes watched files."""
    _FILE_LIST_DIRECTORY = 0x0001
    _SHARE = 0x00000001 | 0x00000002 | 0x00000004
    _OPEN_EXISTING = 3
    _BACKUP_SEMANTICS = 0x02000000
    _FILTER = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000010
    _INVALID = ctypes.c_void_p(-1).value

    def __init__(self, root: Path, buffer_size: int = 65536):
        self.root = root.resolve()
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateFileW.restype = ctypes.c_void_p
        self.kernel32.ReadDirectoryChangesW.restype = ctypes.c_int
        self.kernel32.CloseHandle.restype = ctypes.c_int
        self.handle = self.kernel32.CreateFileW(str(self.root), self._FILE_LIST_DIRECTORY, self._SHARE, None, self._OPEN_EXISTING, self._BACKUP_SEMANTICS, None)
        if self.handle == self._INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        self.buffer = ctypes.create_string_buffer(buffer_size)

    def read_batch(self) -> list[Path]:
        count = ctypes.c_uint32()
        if not self.kernel32.ReadDirectoryChangesW(self.handle, self.buffer, len(self.buffer), True, self._FILTER, ctypes.byref(count), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        paths, offset, raw = [], 0, self.buffer.raw
        while offset < count.value:
            next_offset, _action, name_size = struct.unpack_from("III", raw, offset)
            paths.append(Path(raw[offset + 12:offset + 12 + name_size].decode("utf-16-le")))
            if not next_offset:
                break
            offset += next_offset
        return paths

    def close(self) -> None:
        if getattr(self, "handle", None) not in (None, self._INVALID):
            self.kernel32.CloseHandle(self.handle)
            self.handle = None