from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

from .extract import catalog_preface, chunk_text, extract_document, load_catalog
from .ontology import index_entities
from .policy import is_hidden_or_ignored
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
    def __init__(self, store: Store, max_size_mib: int = 1024, include_hidden: bool = False):
        self.store = store
        self.max_size_mib = max_size_mib
        self.max_bytes = max_size_mib * 1024 * 1024
        self.include_hidden = include_hidden
        self.catalog: dict[str, dict] = {}

    def _is_own_database(self, path: Path) -> bool:
        row = self.store.conn.execute("PRAGMA database_list").fetchone()
        if row is None or not row[2]:
            return False
        database = Path(row[2]).resolve()
        candidate = path.resolve()
        sidecars = {database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")}
        return candidate == database or candidate in sidecars

    def _consider(self, path: Path, root: Path, stats: dict[str, int]) -> str | None:
        if path.is_symlink() or is_hidden_or_ignored(path, root, self.include_hidden) or self._is_own_database(path):
            stats["skipped"] += 1
            return None
        info = path.stat()
        if info.st_size > self.max_bytes:
            stats["skipped"] += 1
            return None
        resolved = str(path.resolve())
        stats["seen"] += 1
        previous = self.store.find_by_path(resolved)
        if previous and previous["deleted_at"] is None and previous["modified_ns"] == info.st_mtime_ns and previous["size"] == info.st_size:
            stats["unchanged"] += 1
            return resolved
        relative = path.resolve().relative_to(root).as_posix()
        record = self.catalog.get(relative)
        if relative == "metadata.json" and self.catalog:
            text = f"[catalog] 数据集总目录，共 {len(self.catalog)} 条。每条摘要已经写到对应文件，这里不重复索引全文。"
            modality = "text"
        else:
            extracted = extract_document(path)
            modality = extracted.modality
            body = extracted.text if extracted.text and extracted.text.strip() else ""
            preface = catalog_preface(record)
            text = (preface + body).strip()
            if not text:
                text = f"[文件] {path.name} 大小={info.st_size} 扩展名={path.suffix or '无'}。未能抽出正文。"
                modality = modality if modality != "other" else "other"
        content_hash = sha256_file(path)
        entities = index_entities(path, root, text, modality, content_hash)
        if record:
            for keyword in (record.get("keywords") or [])[:12]:
                word = str(keyword).strip()
                if len(word) >= 2:
                    entities.append(("catalog_keyword", word))
        _, changed = self.store.upsert_file(
            path=resolved, name=path.name, extension=path.suffix.lower(), size=info.st_size,
            modified_ns=info.st_mtime_ns, content_hash=content_hash,
            status="indexed", chunks=chunk_text(text),
            entities=entities, timestamp=now(),
        )
        stats["indexed" if changed else "unchanged"] += 1
        return resolved

    def sync(self, root: Path) -> dict[str, int]:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"索引根目录不存在或不是目录：{root}")
        database_path = Path(self.store.conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if root == database_path.parent and database_path.parent.name == ".data":
            raise ValueError("不能索引项目自己的 .data 目录")
        root_string = str(root)
        self.catalog = load_catalog(root)
        self.store.register_root(root_string, self.max_size_mib, self.include_hidden, now())
        stats = {"seen": 0, "indexed": 0, "unchanged": 0, "skipped": 0, "missing": 0}
        present: set[str] = set()
        try:
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    resolved = self._consider(path, root, stats)
                    if resolved:
                        present.add(resolved)
                    if stats["seen"] and stats["seen"] % 200 == 0:
                        print(f"已扫描 {stats['seen']} 个文件，新写入 {stats['indexed']}", file=sys.stderr)
                except (OSError, PermissionError):
                    stats["skipped"] += 1
            stats["missing"] = self.store.mark_missing_under(root_string, present, now())
            self.store.record_root_sync(root_string, now(), "ok")
            return stats
        except Exception as error:
            self.store.record_root_sync(root_string, now(), "error", str(error))
            raise

    def apply_changes(self, root: Path, relative_paths: list[Path]) -> dict[str, int]:
        """Update only watcher-reported paths; periodic sync remains a safety net."""
        root = root.resolve()
        self.catalog = load_catalog(root)
        stats = {"seen": 0, "indexed": 0, "unchanged": 0, "skipped": 0, "missing": 0}
        deleted: list[str] = []
        for relative in set(relative_paths):
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if not path.exists():
                deleted.append(str(path))
                continue
            candidates = (item for item in path.rglob("*") if item.is_file()) if path.is_dir() else (path,)
            for candidate in candidates:
                try:
                    self._consider(candidate, root, stats)
                except (OSError, PermissionError):
                    stats["skipped"] += 1
        stats["missing"] = self.store.mark_deleted_paths(deleted, now())
        return stats


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
