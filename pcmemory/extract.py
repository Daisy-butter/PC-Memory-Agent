from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .policy import TEXT_EXTENSIONS

MAX_TEXT_CHARS = 2_000_000
STOP_WORDS = {"this", "that", "with", "from", "have", "will", "the", "and", "for", "文件", "项目", "相关", "可以", "进行", "课程", "资料", "作业"}


def extract_text(path: Path) -> str | None:
    try:
        if path.suffix.lower() in TEXT_EXTENSIONS:
            raw = path.read_bytes()
            for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
                try:
                    return raw.decode(encoding)[:MAX_TEXT_CHARS]
                except UnicodeDecodeError:
                    pass
        elif path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            return "\n".join(t.text or "" for t in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))[:MAX_TEXT_CHARS]
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        pass
    return None


def chunk_text(text: str, size: int = 1200, overlap: int = 160) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        boundary = text.rfind("\n", start + size // 2, end)
        if boundary > start:
            end = boundary
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def entities_from_text(text: str, limit: int = 40) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for value in re.findall(r"\b\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b", text):
        found.add(("date", value.replace("/", "-")))
    for value in re.findall(r"\b[A-Z]{2,6}\s?-?\d{3,6}[A-Z]?\b", text):
        found.add(("course_code", re.sub(r"\s|-", "", value)))
    for value in re.findall(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text):
        found.add(("email", value.casefold()))
    counts: dict[str, int] = {}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,12}", text):
        if token.casefold() not in STOP_WORDS:
            counts[token] = counts.get(token, 0) + 1
    found.update(("keyword", token) for token, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])
    return sorted(found)


def entities_from_path(path: Path, root: Path) -> list[tuple[str, str]]:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return []
    return [("folder_topic", part.strip()) for part in parts if len(part.strip()) >= 2 and part.casefold() not in STOP_WORDS]