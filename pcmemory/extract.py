from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .policy import OCR_EXTENSIONS, TEXT_EXTENSIONS

MAX_TEXT_CHARS = 2_000_000
STOP_WORDS = {"this", "that", "with", "from", "have", "will", "the", "and", "for", "文件", "项目", "相关", "可以", "进行", "课程", "资料", "作业"}
NS_MAIN = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS_DRAW = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xml_text(data: bytes, tag: str) -> str:
    return "\n".join(node.text or "" for node in ElementTree.fromstring(data).iter(tag))


def _pdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)[:MAX_TEXT_CHARS]
    except Exception:
        return None


def _xlsx_text(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                shared = ["".join(t.text or "" for t in item.iter(NS_SS + "t")) for item in root]
            values = []
            for name in z.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    root = ElementTree.fromstring(z.read(name))
                    for cell in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                        value = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                        if value is not None and value.text:
                            values.append(shared[int(value.text)] if cell.get("t") == "s" and value.text.isdigit() and int(value.text) < len(shared) else value.text)
            return "\n".join(values)[:MAX_TEXT_CHARS]
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return None


def _pptx_text(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            slides = sorted(name for name in z.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
            return "\n".join(_xml_text(z.read(name), NS_DRAW + "t") for name in slides)[:MAX_TEXT_CHARS]
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return None


def _ocr(path: Path) -> str | None:
    if os.getenv("PCM_ENABLE_OCR") != "1" or not shutil.which("tesseract"):
        return None
    try:
        result = subprocess.run(["tesseract", str(path), "stdout", "-l", "eng+chi_sim"], capture_output=True, text=True, timeout=90, check=False)
        return result.stdout[:MAX_TEXT_CHARS] if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def extract_text(path: Path) -> str | None:
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_EXTENSIONS:
            raw = path.read_bytes()
            for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
                try: return raw.decode(encoding)[:MAX_TEXT_CHARS]
                except UnicodeDecodeError: pass
        if suffix == ".docx":
            with zipfile.ZipFile(path) as z: return _xml_text(z.read("word/document.xml"), NS_MAIN + "t")[:MAX_TEXT_CHARS]
        if suffix == ".pdf": return _pdf_text(path)
        if suffix == ".xlsx": return _xlsx_text(path)
        if suffix == ".pptx": return _pptx_text(path)
        if suffix in OCR_EXTENSIONS: return _ocr(path)
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError): pass
    return None


def chunk_text(text: str, size: int = 1200, overlap: int = 160) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text).strip(); chunks=[]; start=0
    while start < len(text):
        end=min(len(text), start+size); boundary=text.rfind("\n", start+size//2, end)
        if boundary > start: end=boundary
        chunks.append(text[start:end].strip())
        if end == len(text): break
        start=max(start+1,end-overlap)
    return chunks


def entities_from_text(text: str, limit: int = 40) -> list[tuple[str, str]]:
    found=set()
    for value in re.findall(r"\b\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b",text): found.add(("date",value.replace("/","-")))
    for value in re.findall(r"\b[A-Z]{2,6}\s?-?\d{3,6}[A-Z]?\b",text): found.add(("course_code",re.sub(r"\s|-","",value)))
    for value in re.findall(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",text): found.add(("email",value.casefold()))
    counts={}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,12}",text):
        if token.casefold() not in STOP_WORDS: counts[token]=counts.get(token,0)+1
    found.update(("keyword",token) for token,_ in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:limit])
    return sorted(found)


def entities_from_path(path: Path, root: Path) -> list[tuple[str, str]]:
    try: parts=path.relative_to(root).parts[:-1]
    except ValueError: return []
    return [("folder_topic",part.strip()) for part in parts if len(part.strip())>=2 and part.casefold() not in STOP_WORDS]