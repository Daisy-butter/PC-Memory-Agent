from __future__ import annotations

import gzip
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .media import observe_audio, observe_image, observe_video
from .policy import (
    AUDIO_EXTENSIONS,
    DESIGN_EXTENSIONS,
    DRAWING_EXTENSIONS,
    NOTEBOOK_EXTENSIONS,
    OCR_EXTENSIONS,
    SENSITIVE_EXTENSIONS,
    TEXT_EXTENSIONS,
    VECTOR_EXTENSIONS,
    VIDEO_EXTENSIONS,
    modality_of,
)

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


class Extracted:
    def __init__(self, text: str | None, modality: str):
        self.text = text
        self.modality = modality


def _readable_text(raw: bytes) -> str | None:
    """Decode UTF-8 or GB18030 text. NUL bytes and control-heavy binaries stay out of the graph."""
    if not raw or b"\x00" in raw[:8192]:
        return None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw[:MAX_TEXT_CHARS].decode(encoding)
        except UnicodeDecodeError:
            pass
    try:
        text = raw[:MAX_TEXT_CHARS].decode("gb18030")
    except UnicodeDecodeError:
        return None
    sample = text[:2000]
    controls = sum(ord(char) < 32 and char not in "\n\r\t" for char in sample)
    printable = sum(char.isprintable() or char in "\n\r\t" for char in sample)
    if controls > 4 or not sample or printable / len(sample) < 0.85:
        return None
    return text


def _svg_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")[:500_000]
    pieces = [item.strip() for item in re.findall(r"<(?:text|tspan|title|desc)[^>]*>([^<]+)", raw, flags=re.I)]
    pieces = [item for item in pieces if item]
    viewbox = re.search(r'viewBox=["\']([^"\']+)["\']', raw)
    size = f" viewBox={viewbox.group(1)}" if viewbox else ""
    body = "\n".join(pieces[:40]) if pieces else "SVG 矢量图，文件内没有可抽取的文字节点。"
    return f"[image] svg name={path.name}{size}\n{body}"


def _notebook_text(path: Path) -> str | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    parts: list[str] = []
    for cell in data.get("cells", []):
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        if str(source).strip():
            parts.append(str(source))
    return "\n\n".join(parts)[:MAX_TEXT_CHARS] or None


def _zip_text(path: Path) -> str:
    names: list[str] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            total += 1
            if len(names) < 60:
                names.append(info.filename)
    extra = f"\n... 另有 {total - len(names)} 个条目未列出" if total > len(names) else ""
    body = "\n".join(names) if names else "压缩包里没有文件条目。"
    return f"[archive] {path.name} entries={total}\n{body}{extra}"


def _graffle_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<data>.*?</data>", "", text, flags=re.S)
    strings = [item.strip() for item in re.findall(r"<string>([^<]{2,180})</string>", text)]
    kept = [item for item in strings if item and not re.fullmatch(r"[A-Za-z0-9+/=\s]+", item)]
    body = "\n".join(dict.fromkeys(kept[:40])) if kept else "OmniGraffle 绘图，已跳过内嵌预览图的二进制。"
    return f"[drawing] graffle name={path.name}\n{body}"


def load_catalog(root: Path) -> dict[str, dict]:
    path = root / "metadata.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    catalog: dict[str, dict] = {}
    for item in data:
        if isinstance(item, dict) and item.get("relative_path"):
            catalog[str(item["relative_path"]).replace("\\", "/")] = item
    return catalog


def catalog_preface(record: dict | None) -> str:
    if not record:
        return ""
    summary = str(record.get("summary") or "").strip()
    modality = str(record.get("modality") or "").strip()
    keywords = record.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = []
    words = "、".join(str(item).strip() for item in keywords if str(item).strip())
    lines = ["[目录摘要]"]
    if modality:
        lines.append(f"标注模态: {modality}")
    if summary:
        lines.append(summary)
    if words:
        lines.append("关键词: " + words)
    return "\n".join(lines) + "\n"


def extract_document(path: Path) -> Extracted:
    suffix = path.suffix.lower()
    modality = modality_of(suffix)
    try:
        if suffix in SENSITIVE_EXTENSIONS:
            return Extracted(f"[secret] {path.name} 是证书或密钥材料，只记录路径，不读取内容。", "secret")
        if suffix == ".svg":
            return Extracted(_svg_text(path), "image")
        if suffix in NOTEBOOK_EXTENSIONS:
            return Extracted(_notebook_text(path), "text")
        if suffix == ".zip":
            return Extracted(_zip_text(path), "archive")
        if suffix in DRAWING_EXTENSIONS:
            return Extracted(_graffle_text(path), "drawing")
        if suffix in DESIGN_EXTENSIONS:
            return Extracted(f"[image] {path.name} 是设计稿二进制，文本模型不能直接读取像素。", "image")
        if suffix == ".eps":
            raw = path.read_bytes()[:8000]
            comments = [line for line in raw.decode("latin-1", errors="replace").splitlines() if line.startswith("%")]
            body = "\n".join(comments[:30]) or "EPS 图片，没有可读注释。"
            return Extracted(f"[image] eps name={path.name}\n{body}", "image")
        if suffix in TEXT_EXTENSIONS or suffix == "":
            text = _readable_text(path.read_bytes()[: MAX_TEXT_CHARS + 1])
            if text is not None:
                return Extracted(text, "text")
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                return Extracted(_xml_text(archive.read("word/document.xml"), NS_MAIN + "t")[:MAX_TEXT_CHARS], modality)
        if suffix == ".pdf":
            return Extracted(_pdf_text(path), modality)
        if suffix == ".xlsx":
            return Extracted(_xlsx_text(path), modality)
        if suffix == ".pptx":
            return Extracted(_pptx_text(path), modality)
        if suffix in OCR_EXTENSIONS:
            return Extracted(observe_image(path), "image")
        if suffix in AUDIO_EXTENSIONS:
            return Extracted(observe_audio(path), "audio")
        if suffix in VIDEO_EXTENSIONS:
            return Extracted(observe_video(path), "video")
        if suffix not in VECTOR_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | OCR_EXTENSIONS:
            text = _readable_text(path.read_bytes()[: MAX_TEXT_CHARS + 1])
            if text is not None:
                return Extracted(text, "text")
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError, json.JSONDecodeError, gzip.BadGzipFile, ValueError):
        return Extracted(None, modality)
    return Extracted(None, modality)


def extract_text(path: Path) -> str | None:
    return extract_document(path).text


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