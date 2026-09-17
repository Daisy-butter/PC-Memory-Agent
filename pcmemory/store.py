from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS files (
 id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL, extension TEXT NOT NULL,
 size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, content_hash TEXT, indexed_at TEXT NOT NULL,
 text_status TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, UNIQUE(file_id, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(content, file_id UNINDEXED, chunk_id UNINDEXED);
CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(kind, value));
CREATE TABLE IF NOT EXISTS file_entities (
 file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 occurrences INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(file_id, entity_id)
);
CREATE TABLE IF NOT EXISTS file_history (
 id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
 previous_path TEXT NOT NULL, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indexed_roots (
 path TEXT PRIMARY KEY, max_size_mib INTEGER NOT NULL, include_hidden INTEGER NOT NULL,
 registered_at TEXT NOT NULL, last_sync_at TEXT, last_sync_status TEXT, last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(deleted_at);
CREATE TABLE IF NOT EXISTS file_relations (
 source_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
 target_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
 relation_type TEXT NOT NULL, evidence_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 score REAL NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(source_file_id,target_file_id,relation_type,evidence_entity_id)
);
CREATE INDEX IF NOT EXISTS idx_file_relations_source ON file_relations(source_file_id);
CREATE INDEX IF NOT EXISTS idx_file_relations_target ON file_relations(target_file_id);
CREATE TABLE IF NOT EXISTS entity_aliases (kind TEXT NOT NULL, alias TEXT NOT NULL, entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE, PRIMARY KEY(kind,alias));
CREATE TABLE IF NOT EXISTS entity_mentions (file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE, extractor TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(file_id,entity_id,extractor));
CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, subject_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, predicate TEXT NOT NULL, object_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE, confidence REAL NOT NULL, asserted_by TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(subject_file_id,predicate,object_entity_id,asserted_by));
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_file_id);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def register_root(self, path: str, max_size_mib: int, include_hidden: bool, timestamp: str) -> None:
        self.conn.execute("""INSERT INTO indexed_roots(path,max_size_mib,include_hidden,registered_at)
          VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET max_size_mib=excluded.max_size_mib,
          include_hidden=excluded.include_hidden""", (path, max_size_mib, int(include_hidden), timestamp))
        self.conn.commit()

    def registered_roots(self):
        return self.conn.execute("SELECT * FROM indexed_roots ORDER BY path").fetchall()

    def record_root_sync(self, path: str, timestamp: str, status: str, error: str | None = None) -> None:
        self.conn.execute("UPDATE indexed_roots SET last_sync_at=?,last_sync_status=?,last_error=? WHERE path=?", (timestamp, status, error, path))
        self.conn.commit()

    def find_by_path(self, path: str):
        return self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def find_deleted_by_hash(self, content_hash: str):
        return self.conn.execute("SELECT * FROM files WHERE content_hash=? AND deleted_at IS NOT NULL ORDER BY id DESC LIMIT 1", (content_hash,)).fetchone()

    def find_active_by_hash(self, content_hash: str):
        return self.conn.execute("SELECT * FROM files WHERE content_hash=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (content_hash,)).fetchone()

    @staticmethod
    def _normalize_entity(kind: str, value: str) -> str:
        value = " ".join(value.strip().split())
        if kind == "course_code":
            return value.upper()
        if kind in {"email", "keyword"}:
            return value.casefold()
        return value

    def _resolve_entity(self, kind: str, value: str) -> int:
        alias = self._normalize_entity(kind, value)
        row = self.conn.execute("SELECT entity_id FROM entity_aliases WHERE kind=? AND alias=?", (kind, alias)).fetchone()
        if row:
            return row["entity_id"]
        self.conn.execute("INSERT OR IGNORE INTO entities(kind,value) VALUES (?,?)", (kind, alias))
        entity_id = self.conn.execute("SELECT id FROM entities WHERE kind=? AND value=?", (kind, alias)).fetchone()[0]
        self.conn.execute("INSERT OR IGNORE INTO entity_aliases(kind,alias,entity_id) VALUES (?,?,?)", (kind, alias, entity_id))
        return entity_id
    def mark_missing_under(self, root: str, present_paths: set[str], timestamp: str) -> int:
        prefix = root.rstrip("\\/") + "\\%"
        rows = self.conn.execute("SELECT id,path FROM files WHERE deleted_at IS NULL AND path LIKE ?", (prefix,)).fetchall()
        missing = [row["id"] for row in rows if row["path"] not in present_paths]
        if missing:
            self.conn.executemany("UPDATE files SET deleted_at=? WHERE id=?", [(timestamp, item) for item in missing])
            self.conn.commit()
        return len(missing)

    def upsert_file(self, *, path: str, name: str, extension: str, size: int, modified_ns: int, content_hash: str, status: str, chunks: list[str], entities: list[tuple[str, str]], timestamp: str) -> tuple[int, bool]:
        existing = self.find_by_path(path)
        if existing and existing["content_hash"] == content_hash and existing["modified_ns"] == modified_ns and existing["size"] == size:
            if existing["deleted_at"] is not None:
                self.conn.execute("UPDATE files SET deleted_at=NULL,indexed_at=? WHERE id=?", (timestamp, existing["id"]))
                self.conn.commit()
                return existing["id"], True
            return existing["id"], False
        moved = None if existing else (self.find_deleted_by_hash(content_hash) or self.find_active_by_hash(content_hash))
        if moved:
            self.conn.execute("INSERT INTO file_history(file_id,previous_path,observed_at) VALUES (?,?,?)", (moved["id"], moved["path"], timestamp))
            self.conn.execute("UPDATE files SET path=?,name=?,extension=?,size=?,modified_ns=?,content_hash=?,indexed_at=?,text_status=?,deleted_at=NULL WHERE id=?", (path, name, extension, size, modified_ns, content_hash, timestamp, status, moved["id"]))
            file_id = moved["id"]
        elif existing:
            self.conn.execute("UPDATE files SET name=?,extension=?,size=?,modified_ns=?,content_hash=?,indexed_at=?,text_status=?,deleted_at=NULL WHERE id=?", (name, extension, size, modified_ns, content_hash, timestamp, status, existing["id"]))
            file_id = existing["id"]
        else:
            cursor = self.conn.execute("INSERT INTO files(path,name,extension,size,modified_ns,content_hash,indexed_at,text_status) VALUES (?,?,?,?,?,?,?,?)", (path, name, extension, size, modified_ns, content_hash, timestamp, status))
            file_id = cursor.lastrowid
        self.conn.execute("DELETE FROM chunk_fts WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM file_entities WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM entity_mentions WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM facts WHERE subject_file_id=?", (file_id,))
        for ordinal, content in enumerate(chunks):
            cursor = self.conn.execute("INSERT INTO chunks(file_id,ordinal,content) VALUES (?,?,?)", (file_id, ordinal, content))
            self.conn.execute("INSERT INTO chunk_fts(content,file_id,chunk_id) VALUES (?,?,?)", (content, file_id, cursor.lastrowid))
        confidence = {"course_code": .98, "email": .98, "date": .85, "folder_topic": .80, "keyword": .35}
        predicate = {"course_code": "HAS_COURSE", "email": "MENTIONS_CONTACT", "date": "OBSERVED_ON", "folder_topic": "IN_FOLDER_TOPIC", "keyword": "MENTIONS"}
        for kind, value in set(entities):
            entity_id = self._resolve_entity(kind, value)
            self.conn.execute("INSERT OR REPLACE INTO file_entities(file_id,entity_id,occurrences) VALUES (?,?,1)", (file_id, entity_id))
            score = confidence.get(kind, .30)
            self.conn.execute("INSERT OR REPLACE INTO entity_mentions VALUES (?,?,?,?,?)", (file_id, entity_id, "rules_v1", score, value[:240]))
            self.conn.execute("INSERT OR REPLACE INTO facts(subject_file_id,predicate,object_entity_id,confidence,asserted_by,observed_at) VALUES (?,?,?,?,?,?)", (file_id, predicate.get(kind, "MENTIONS"), entity_id, score, "rules_v1", timestamp))
        self.conn.commit()
        self._rebuild_file_relations(file_id, timestamp)
        return file_id, True

    def _rebuild_file_relations(self, file_id: int, timestamp: str) -> None:
        self.conn.execute("DELETE FROM file_relations WHERE source_file_id=? OR target_file_id=?", (file_id, file_id))
        weights = {"course_code": ("SAME_COURSE", 0.95), "folder_topic": ("SHARED_FOLDER_TOPIC", 0.75), "date": ("SAME_DATE", 0.45), "email": ("SAME_CONTACT", 0.8), "keyword": ("SHARED_KEYWORD", 0.25)}
        rows = self.conn.execute("""SELECT other.file_id AS target_id,e.id AS entity_id,e.kind
          FROM file_entities mine JOIN file_entities other ON mine.entity_id=other.entity_id
          JOIN entities e ON e.id=mine.entity_id JOIN files target ON target.id=other.file_id
          WHERE mine.file_id=? AND other.file_id<>? AND target.deleted_at IS NULL""", (file_id, file_id)).fetchall()
        for row in rows:
            relation_type, score = weights.get(row["kind"], ("SHARED_ENTITY", 0.2))
            self.conn.execute("INSERT OR REPLACE INTO file_relations VALUES (?,?,?,?,?,?)", (file_id, row["target_id"], relation_type, row["entity_id"], score, timestamp))
        self.conn.commit()

    def related_files(self, file_ids: list[int], limit: int = 10):
        if not file_ids:
            return []
        marks = ",".join("?" for _ in file_ids)
        return self.conn.execute(f"""SELECT f.id,f.path,f.name,r.relation_type,e.value AS evidence,MAX(r.score) AS score
          FROM file_relations r JOIN files f ON f.id=CASE WHEN r.source_file_id IN ({marks}) THEN r.target_file_id ELSE r.source_file_id END
          JOIN entities e ON e.id=r.evidence_entity_id
          WHERE (r.source_file_id IN ({marks}) OR r.target_file_id IN ({marks})) AND f.deleted_at IS NULL AND f.id NOT IN ({marks})
          GROUP BY f.id,r.relation_type,e.value ORDER BY score DESC,f.modified_ns DESC LIMIT ?""", (*file_ids,*file_ids,*file_ids,*file_ids,limit)).fetchall()
    def rebuild_facts(self) -> int:
        """Backfill asserted facts from existing file/entity links without reading source files."""
        predicates = {"course_code": ("HAS_COURSE", .98), "email": ("MENTIONS_CONTACT", .98), "date": ("OBSERVED_ON", .85), "folder_topic": ("IN_FOLDER_TOPIC", .80), "keyword": ("MENTIONS", .35)}
        rows = self.conn.execute("""SELECT fe.file_id,e.id AS entity_id,e.kind,f.indexed_at
          FROM file_entities fe JOIN entities e ON e.id=fe.entity_id JOIN files f ON f.id=fe.file_id
          WHERE f.deleted_at IS NULL""").fetchall()
        self.conn.execute("DELETE FROM facts")
        for row in rows:
            predicate, confidence = predicates.get(row["kind"], ("MENTIONS", .30))
            self.conn.execute("INSERT OR REPLACE INTO facts(subject_file_id,predicate,object_entity_id,confidence,asserted_by,observed_at) VALUES (?,?,?,?,?,?)", (row["file_id"], predicate, row["entity_id"], confidence, "migration_v1", row["indexed_at"]))
        self.conn.commit()
        return len(rows)
    def facts_for_file(self, file_id: int):
        return self.conn.execute("""SELECT fa.predicate,e.kind,e.value,fa.confidence,fa.asserted_by,fa.observed_at
          FROM facts fa JOIN entities e ON e.id=fa.object_entity_id WHERE fa.subject_file_id=?
          ORDER BY fa.confidence DESC,e.kind,e.value""", (file_id,)).fetchall()
    def search(self, query: str, limit: int = 10):
        terms = [part for part in query.replace('"', ' ').split() if part]
        if not terms:
            return []
        fts_query = " AND ".join(f'"{term}"' for term in terms)
        canonical_terms = [self._normalize_entity("course_code", term) for term in terms]
        marks = ",".join("?" for _ in canonical_terms)
        entity_rows = self.conn.execute(f"""SELECT DISTINCT f.id,f.path,f.name,f.extension,f.modified_ns,NULL AS chunk_id,
          ('[entity] ' || e.kind || ':' || e.value) AS snippet,-100.0 AS rank
          FROM entities e JOIN file_entities fe ON fe.entity_id=e.id JOIN files f ON f.id=fe.file_id
          WHERE e.value IN ({marks}) AND f.deleted_at IS NULL ORDER BY f.modified_ns DESC LIMIT ?""", (*canonical_terms, limit)).fetchall()
        if entity_rows:
            return entity_rows
        try:
            rows = self.conn.execute("""SELECT f.id,f.path,f.name,f.extension,f.modified_ns,c.id AS chunk_id,
              snippet(chunk_fts,0,'[',']','...',18) AS snippet,bm25(chunk_fts) AS rank
              FROM chunk_fts JOIN files f ON f.id=chunk_fts.file_id JOIN chunks c ON c.id=chunk_fts.chunk_id
              WHERE chunk_fts MATCH ? AND f.deleted_at IS NULL ORDER BY rank LIMIT ?""", (fts_query, limit)).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
        clause = " AND ".join("(f.name LIKE ? OR f.path LIKE ? OR c.content LIKE ?)" for _ in terms)
        params = [value for term in terms for value in (f"%{term}%", f"%{term}%", f"%{term}%")]
        return self.conn.execute(f"""SELECT DISTINCT f.id,f.path,f.name,f.extension,f.modified_ns,c.id AS chunk_id,
          substr(c.content,1,240) AS snippet,0 AS rank FROM files f LEFT JOIN chunks c ON c.file_id=f.id
          WHERE f.deleted_at IS NULL AND {clause} ORDER BY f.modified_ns DESC LIMIT ?""", (*params, limit)).fetchall()

    def related_entities(self, file_ids: list[int]):
        if not file_ids:
            return []
        marks = ",".join("?" for _ in file_ids)
        return self.conn.execute(f"SELECT e.value,COUNT(*) AS files FROM file_entities fe JOIN entities e ON e.id=fe.entity_id WHERE fe.file_id IN ({marks}) GROUP BY e.id ORDER BY files DESC,e.value LIMIT 15", file_ids).fetchall()

    def get_file(self, file_id: int):
        return self.conn.execute("SELECT * FROM files WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()

    def get_chunks(self, file_id: int):
        return self.conn.execute("SELECT ordinal,content FROM chunks WHERE file_id=? ORDER BY ordinal", (file_id,)).fetchall()