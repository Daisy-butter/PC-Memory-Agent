from __future__ import annotations

from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".sql", ".yaml", ".yml", ".toml", ".ini", ".log", ".sh",
    ".ps1", ".bat", ".css", ".tex",
}
STRUCTURED_EXTENSIONS = {".docx", ".pdf", ".xlsx", ".pptx"}
OCR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
DEFAULT_IGNORED_NAMES = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "$recycle.bin",
    "appdata", "windows", "program files", "program files (x86)", "system volume information",
}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS | OCR_EXTENSIONS


def is_hidden_or_ignored(path: Path, root: Path, include_hidden: bool) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        if part.lower() in DEFAULT_IGNORED_NAMES:
            return True
        if not include_hidden and part.startswith("."):
            return True
    return False
