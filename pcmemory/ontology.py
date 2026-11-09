from __future__ import annotations

import re
from pathlib import Path

from .extract import entities_from_path, entities_from_text

_VERSION_TOKEN = re.compile(
    r"(?i)(?:^|[\s._\-()（）\[\]]+)(?:"
    r"v(?:ersion)?\s*\d+|ver\s*\d+|final|最终版?|最新版?|copy|副本|修订版?|draft|草稿"
    r"|\(\d+\)|（\d+）"
    r")(?=$|[\s._\-()（）\[\]]+)"
)


def version_family_key(filename: str) -> str:
    """Collapse v1 / final / copy suffixes so versions of one material share an id."""
    stem = Path(filename).stem
    cleaned = _VERSION_TOKEN.sub(" ", stem)
    cleaned = re.sub(r"[\s._\-]+", " ", cleaned).strip().casefold()
    if len(cleaned) < 2:
        cleaned = re.sub(r"[\s._\-]+", " ", stem).strip().casefold()
    return cleaned


def project_name(path: Path, root: Path) -> str:
    """The first directory under the indexed root is the project; loose files belong to the root."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return ""
    if len(relative.parts) >= 2:
        return relative.parts[0].strip()
    return root.name.strip()


def index_entities(path: Path, root: Path, text: str, modality: str, content_hash: str = "") -> list[tuple[str, str]]:
    items = entities_from_text(text) + entities_from_path(path, root)
    items.append(("modality", modality))
    project = project_name(path, root)
    if len(project) >= 2:
        items.append(("project", project))
    family = version_family_key(path.name)
    if len(family) >= 2:
        items.append(("version_family", family))
    if len(content_hash) >= 16:
        items.append(("content_hash", content_hash[:16]))
    return items
