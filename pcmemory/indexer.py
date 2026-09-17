from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from .extract import chunk_text, entities_from_path, entities_from_text, extract_text
from .policy import is_hidden_or_ignored, is_supported
from .store import Store


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Indexer:
    def __init__(self, store: Store, max_size_mib: int = 25, include_hidden: bool = False):
        self.store = store
        self.max_size_mib = max_size_mib
        self.max_bytes = max_size_mib * 1024 * 1024
        self.include_hidden = include_hidden

    def sync(self, root: Path) -> dict[str, int]:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"索引根目录不存在或不是目录：{root}")
        database_path = Path(self.store.conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if root == database_path.parent and database_path.parent.name == ".data":
            raise ValueError("不能索引项目自己的 .data 目录")
        root_string = str(root)
        self.store.register_root(root_string, self.max_size_mib, self.include_hidden, now())
        stats = {"seen": 0, "indexed": 0, "unchanged": 0, "skipped": 0, "missing": 0}
        present: set[str] = set()
        try:
            for path in root.rglob("*"):
                try:
                    if not path.is_file() or path.is_symlink() or is_hidden_or_ignored(path, root, self.include_hidden):
                        continue
                    if not is_supported(path):
                        stats["skipped"] += 1
                        continue
                    info = path.stat()
                    if info.st_size > self.max_bytes:
                        stats["skipped"] += 1
                        continue
                    resolved = str(path.resolve())
                    present.add(resolved)
                    stats["seen"] += 1
                    previous = self.store.find_by_path(resolved)
                    if previous and previous["deleted_at"] is None and previous["modified_ns"] == info.st_mtime_ns and previous["size"] == info.st_size:
                        stats["unchanged"] += 1
                        continue
                    content_hash = sha256_file(path)
                    text = extract_text(path)
                    status = "indexed" if text is not None else "unreadable"
                    _, changed = self.store.upsert_file(path=resolved, name=path.name, extension=path.suffix.lower(), size=info.st_size, modified_ns=info.st_mtime_ns, content_hash=content_hash, status=status, chunks=chunk_text(text or ""), entities=entities_from_text(text or "") + entities_from_path(path, root), timestamp=now())
                    stats["indexed" if changed else "unchanged"] += 1
                except (OSError, PermissionError):
                    stats["skipped"] += 1
            stats["missing"] = self.store.mark_missing_under(root_string, present, now())
            self.store.record_root_sync(root_string, now(), "ok")
            return stats
        except Exception as error:
            self.store.record_root_sync(root_string, now(), "error", str(error))
            raise


def sync_registered(store: Store) -> dict[str, dict[str, int] | str]:
    """Synchronize every registered root before a task query; source files remain read-only."""
    results: dict[str, dict[str, int] | str] = {}
    for root in store.registered_roots():
        try:
            results[root["path"]] = Indexer(store, root["max_size_mib"], bool(root["include_hidden"])).sync(Path(root["path"]))
        except (OSError, ValueError) as error:
            store.record_root_sync(root["path"], now(), "unavailable", str(error))
            results[root["path"]] = f"unavailable: {error}"
    return results