from __future__ import annotations

from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".sql", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".sh",
    ".ps1", ".bat", ".css", ".tex", ".bib", ".ics", ".sty", ".cls", ".plist", ".cnf",
    ".in", ".typed", ".lock", ".editorconfig", ".coveragerc", ".properties", ".mk",
}
STRUCTURED_EXTENSIONS = {".docx", ".pdf", ".xlsx", ".pptx"}
OCR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
VECTOR_EXTENSIONS = {".svg", ".eps"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv"}
ARCHIVE_EXTENSIONS = {".zip"}
DRAWING_EXTENSIONS = {".graffle"}
NOTEBOOK_EXTENSIONS = {".ipynb"}
DESIGN_EXTENSIONS = {".ai"}
SENSITIVE_EXTENSIONS = {".key", ".pem", ".crt", ".csr", ".srl"}
DEFAULT_IGNORED_NAMES = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", ".data",
    "$recycle.bin", "appdata", "windows", "program files", "program files (x86)",
    "system volume information", "__macosx",
}


def is_supported(path: Path) -> bool:
    suffix = path.suffix.lower()
    known = (
        TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS | OCR_EXTENSIONS | VECTOR_EXTENSIONS
        | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | ARCHIVE_EXTENSIONS | DRAWING_EXTENSIONS
        | NOTEBOOK_EXTENSIONS | DESIGN_EXTENSIONS | SENSITIVE_EXTENSIONS
    )
    return suffix in known or suffix == ""


def modality_of(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in TEXT_EXTENSIONS | NOTEBOOK_EXTENSIONS:
        return "text"
    if suffix in STRUCTURED_EXTENSIONS:
        return "document"
    if suffix in OCR_EXTENSIONS | VECTOR_EXTENSIONS | DESIGN_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in ARCHIVE_EXTENSIONS:
        return "archive"
    if suffix in DRAWING_EXTENSIONS:
        return "drawing"
    if suffix in SENSITIVE_EXTENSIONS:
        return "secret"
    return "other"


def is_hidden_or_ignored(path: Path, root: Path, include_hidden: bool) -> bool:
    """Skip VCS, caches and the index itself. Ordinary dotfiles such as .github stay indexed."""
    del include_hidden
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part.lower() in DEFAULT_IGNORED_NAMES for part in relative.parts)
