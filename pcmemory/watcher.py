from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import struct
from pathlib import Path

from .policy import DEFAULT_IGNORED_NAMES


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


class LinuxChangeWatcher:
    """Recursive inotify watcher. It only reads directory events and never writes source files."""

    _IN_MODIFY = 0x00000002
    _IN_CLOSE_WRITE = 0x00000008
    _IN_MOVED_FROM = 0x00000040
    _IN_MOVED_TO = 0x00000080
    _IN_CREATE = 0x00000100
    _IN_DELETE = 0x00000200
    _IN_DELETE_SELF = 0x00000400
    _IN_MOVE_SELF = 0x00000800
    _IN_Q_OVERFLOW = 0x00004000
    _IN_IGNORED = 0x00008000
    _IN_ONLYDIR = 0x01000000
    _IN_DONT_FOLLOW = 0x02000000
    _IN_ISDIR = 0x40000000
    _IN_NONBLOCK_CLOEXEC = 0x00080800
    _MASK = (
        _IN_MODIFY | _IN_CLOSE_WRITE | _IN_MOVED_FROM | _IN_MOVED_TO | _IN_CREATE
        | _IN_DELETE | _IN_DELETE_SELF | _IN_MOVE_SELF | _IN_ONLYDIR | _IN_DONT_FOLLOW
    )

    def __init__(self, root: Path):
        self.root = root.resolve()
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        self.libc = ctypes.CDLL(libc_name, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_init1.restype = ctypes.c_int
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_add_watch.restype = ctypes.c_int
        self.fd = self.libc.inotify_init1(self._IN_NONBLOCK_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 失败")
        self.wd_to_path: dict[int, Path] = {}
        self._add_tree(self.root)
        if not self.wd_to_path:
            self.close()
            raise OSError("没有能监听的目录")

    def _add_watch(self, directory: Path) -> None:
        watch = self.libc.inotify_add_watch(self.fd, os.fsencode(directory), self._MASK)
        if watch >= 0:
            self.wd_to_path[watch] = directory

    def _add_tree(self, directory: Path) -> None:
        if directory.name.lower() in DEFAULT_IGNORED_NAMES:
            return
        self._add_watch(directory)
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and not child.is_symlink() and child.name.lower() not in DEFAULT_IGNORED_NAMES:
                self._add_tree(child)

    def read_batch(self, timeout: float = 1.0) -> tuple[list[Path], bool]:
        """Return changed paths relative to the root, and whether the event queue overflowed."""
        poller = select.poll()
        poller.register(self.fd, select.POLLIN)
        if not poller.poll(max(0, int(timeout * 1000))):
            return [], False
        try:
            raw = os.read(self.fd, 65536)
        except BlockingIOError:
            return [], False
        paths: list[Path] = []
        overflow = False
        offset = 0
        while offset + 16 <= len(raw):
            watch, mask, _cookie, name_length = struct.unpack_from("iIII", raw, offset)
            name = raw[offset + 16:offset + 16 + name_length].split(b"\x00", 1)[0]
            offset += 16 + name_length
            if mask & self._IN_Q_OVERFLOW:
                overflow = True
                continue
            directory = self.wd_to_path.get(watch)
            if directory is None:
                continue
            if mask & self._IN_IGNORED:
                self.wd_to_path.pop(watch, None)
            target = directory / os.fsdecode(name) if name else directory
            try:
                relative = target.relative_to(self.root)
            except ValueError:
                continue
            if mask & self._IN_ISDIR and mask & (self._IN_CREATE | self._IN_MOVED_TO) and target.is_dir():
                self._add_tree(target)
            if relative.parts:
                paths.append(relative)
        return paths, overflow

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1
            self.wd_to_path.clear()