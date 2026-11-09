from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from .llm import chat, vllm_model
from .store import Store

SYSTEM = """你是本机文件记忆助手。回答必须依据给出的知识图谱证据，不能编造不存在的文件、项目或版本。
图谱里的项目归属、版本族和跨文件关系优先于单纯的文字相似。同一内容跨目录的列表按修改时间从新到旧排列，第一项是最新观察到的一份；它和同一文件夹里的版本族不是一回事。图片、音频、视频的正文是 OCR、语音转写或关键帧工具产生的文本，不是模型直接看到的像素或波形。
引用证据时写出 file id 和路径。证据不够就说明缺什么。用中文回答，不要输出思考过程。
只有在证据不够、需要再查一个具体文件时，才额外输出一个 JSON 对象，且不要夹杂其他文字：
{"tool":"facts","file_id":1}
{"tool":"related","file_id":1}
{"tool":"versions","file_id":1}
{"tool":"project","name":"项目目录名"}
证据已经够用时直接回答。"""


_FILENAME = re.compile(r"[A-Za-z0-9][\w.-]*\.[A-Za-z0-9]{1,5}")
_PEEL = re.compile(r"(里说了什么.*|转写了什么.*|在哪个.*|是什么.*|属于哪个.*|请给出.*)$")


def focus_queries(task: str) -> list[str]:
    """Turn a full question into shorter lookups. A whole sentence rarely appears in a file."""
    found: list[str] = []

    def add(item: str) -> None:
        item = item.strip(" ?？,，。！!；;、")
        if len(item) >= 2 and item not in found:
            found.append(item)

    task = task.strip()
    add(task)
    for name in _FILENAME.findall(task):
        add(name)
    add(_PEEL.sub("", task))
    for part in re.split(r"[\s,，。？?！!、；;]+", task):
        add(part)
        add(_PEEL.sub("", part))
    return found[:8]


def _clip(text: str, limit: int = 500) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def _parse_tool(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "tool" not in payload:
        return None
    return payload


def _evidence_snippet(store: Store, file_id: int, snippet: str) -> str:
    chunks = store.get_chunks(file_id)
    text = "\n".join(chunk["content"] for chunk in chunks[:2]) or snippet
    for marker in ("[转写]", "[OCR]", "[关键帧]", "[画面描述]", "[目录摘要]"):
        position = text.find(marker)
        if position > 0:
            text = text[max(0, position - 40):]
            break
    return _clip(text, 700)


def _file_block(store: Store, file_id: int, snippet: str = "") -> str:
    record = store.get_file(file_id)
    if not record:
        return ""
    kept = {"BELONGS_TO_PROJECT", "MEMBER_OF_VERSION_FAMILY", "HAS_MODALITY", "IN_FOLDER_TOPIC", "HAS_COURSE", "MENTIONS_CONTACT", "OBSERVED_ON", "TAGGED"}
    facts = [item for item in store.facts_for_file(file_id) if item["predicate"] in kept or (item["predicate"] == "MENTIONS" and item["confidence"] >= 0.35)]
    fact_text = "；".join(f"{item['predicate']}={item['value']}" for item in facts[:12]) or "无"
    relations = store.related_files([file_id], 5)
    relation_text = "；".join(f"{item['relation_type']}[{item['evidence']}]->{item['path']}" for item in relations) or "无"
    versions = store.version_group(file_id)
    if len(versions) > 1:
        version_text = "，".join(f"{item['name']}({'最新' if index == 0 else '较旧'})" for index, item in enumerate(versions))
    else:
        version_text = "同一文件夹内没有其他版本"
    return "\n".join((
        f"file:{file_id} {record['path']}",
        f"  事实: {fact_text}",
        f"  关系: {relation_text}",
        f"  版本(新到旧): {version_text}",
        f"  片段: {_evidence_snippet(store, file_id, snippet)}",
    ))


def _project_of(path: str) -> str:
    parts = Path(path).parts
    if "FileSystem_wrh" in parts:
        index = parts.index("FileSystem_wrh")
        if index + 1 < len(parts):
            return parts[index + 1]
    return Path(path).parent.name


def _relevant_versions(groups: list[dict], rows) -> list[dict]:
    """Keep revision groups that touch the retrieved files. A bare version question still sees the newest groups."""
    if not rows:
        return groups[:4]
    parents = {str(Path(row["path"]).parent) for row in rows}
    stems = {Path(row["name"]).stem.casefold() for row in rows}
    chosen = []
    for group in groups:
        family = str(group["family"])
        if group["parent"] in parents or any(family in stem or stem in family for stem in stems):
            chosen.append(group)
    return chosen[:4]


def build_evidence(store: Store, task: str, limit: int = 6) -> str:
    lines = ["[图谱结构]"]
    projects = store.project_counts()
    lines.append("项目: " + ("；".join(f"{name}({count})" for name, count in projects) if projects else "无"))
    modalities = store.modality_counts()
    lines.append("模态: " + ("；".join(f"{name}({count})" for name, count in modalities) if modalities else "无"))
    rows = []
    for query in focus_queries(task):
        rows = list(store.search(query, limit))
        if rows:
            break
    if not rows and any(word in task for word in ("版本", "项目", "最新", "类似")):
        rows = list(store.recent_files(limit))
    groups = _relevant_versions(store.version_groups(limit=24), rows)
    if groups:
        lines.append("多版本材料:")
        for group in groups:
            ordered = "，".join(f"{item['name']}(file:{item['id']})" for item in group["files"])
            lines.append(f"  {group['family']} @ {Path(group['parent']).name}（新到旧）: {ordered}")
    seen: set[int] = set()
    for row in rows:
        if row["id"] not in seen:
            seen.add(row["id"])
    timelines = store.content_timelines(list(seen))
    if timelines:
        lines.append("[同一内容跨目录，新到旧。回答“最新”时必须采用每行的第一项]")
        for files in timelines:
            newest = files[0]
            lines.append(f"  最新一份: {newest['name']} file:{newest['id']} 项目={_project_of(newest['path'])} 路径={newest['path']}")
            older = "，".join(f"{item['name']}(file:{item['id']},项目={_project_of(item['path'])})" for item in files[1:6])
            if older:
                lines.append("  更早的副本: " + older)
    lines.append("[检索命中]")
    if not rows:
        lines.append("无直接命中。")
    for row in rows:
        if row["id"] not in seen:
            continue
        lines.append(_file_block(store, row["id"], row["snippet"] or ""))
        seen.remove(row["id"])
    text = "\n".join(lines)
    return text[:8000]


def _run_tool(store: Store, payload: dict) -> str:
    tool = payload.get("tool")
    if tool == "facts":
        return _file_block(store, int(payload["file_id"]))
    if tool == "related":
        rows = store.related_files([int(payload["file_id"])], 8)
        return "\n".join(f"{row['relation_type']} [{row['evidence']}] -> file:{row['id']} {row['path']}" for row in rows) or "无关联"
    if tool == "versions":
        rows = store.version_group(int(payload["file_id"]))
        return "\n".join(f"file:{row['id']} {row['path']}" for row in rows) or "无版本族"
    if tool == "project":
        rows = store.project_files(str(payload.get("name", "")), 12)
        return "\n".join(f"file:{row['id']} {row['path']}" for row in rows) or "无此项目"
    return "不支持的工具"


def answer_task(store: Store, task: str, complete: Callable[[list[dict[str, str]]], str] | None = None) -> str:
    complete = complete or chat
    evidence = build_evidence(store, task)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"任务：{task}\n\n{evidence}"},
    ]
    first = complete(messages)
    payload = _parse_tool(first)
    if not payload or payload.get("tool") == "final":
        if payload and payload.get("answer"):
            return str(payload["answer"]).strip()
        return first.strip()
    messages.append({"role": "assistant", "content": first})
    messages.append({"role": "user", "content": "工具结果如下，请直接给出最终回答。\n" + _run_tool(store, payload)})
    return complete(messages).strip()


def model_label() -> str:
    return vllm_model()
