"""Midterm comparison: knowledge-graph evidence versus retrieved snippets only.

Uses the already built .data/memory.sqlite3 and the local Qwen3-8B server.
Does not read or write the source dataset.
"""

from __future__ import annotations

import time
from pathlib import Path

from pcmemory.agent import answer_task, build_evidence, focus_queries
from pcmemory.indexer import Indexer
from pcmemory.llm import chat
from pcmemory.store import Store

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / ".data" / "memory.sqlite3"
DATASET = Path("/SSD_DISK/users/wuruihan/datasets/FileSystem_wrh")

PLAIN = """你是文件助手。只能根据下面检索到的路径和正文片段回答，没有给出的项目关系、新旧顺序不要编造。
路径里的目录名可以引用。用中文，并写出你依据的路径。"""


def norm(text: str) -> str:
    return text.casefold().replace("_", "").replace("-", "").replace(" ", "")


def has(text: str, token: str) -> bool:
    return norm(token) in norm(text)


def plain_context(store: Store, task: str, limit: int = 6) -> str:
    rows = []
    for query in focus_queries(task):
        rows = list(store.search(query, limit))
        if rows:
            break
    lines = ["[检索片段]"]
    seen: set[int] = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        snippet = " ".join((row["snippet"] or "").split())
        lines.append(f"file:{row['id']} {row['path']}\n  {snippet[:500]}")
    if len(lines) == 1:
        lines.append("无直接命中。")
    return "\n".join(lines)[:8000]


def plain_answer(store: Store, task: str) -> tuple[str, str]:
    context = plain_context(store, task)
    answer = chat([
        {"role": "system", "content": PLAIN},
        {"role": "user", "content": f"任务：{task}\n\n{context}"},
    ])
    return context, answer


def timed(function, *args):
    start = time.perf_counter()
    value = function(*args)
    return value, time.perf_counter() - start


def main() -> None:
    store = Store(DB)
    counts = {
        "files": store.conn.execute("select count(*) from files where deleted_at is null").fetchone()[0],
        "facts": store.conn.execute("select count(*) from facts").fetchone()[0],
        "relations": store.conn.execute("select count(*) from file_relations").fetchone()[0],
    }
    print("COUNTS", counts)
    if DATASET.is_dir():
        _, elapsed = timed(Indexer(store).sync, DATASET)
        print(f"RESYNC_SEC {elapsed:.3f}")
    tasks = [
        "Attention Is All You Need 这篇论文属于哪个项目？Iris 数据集又在哪个项目？",
        "GPT-2 损失曲线这张图最新的一份在哪个项目，更早在哪些项目出现过？",
        "nanogpt_model.py 这套模型代码最新出现在哪里，更早在哪个项目里写过？",
        "example.ogg 转写了什么，它属于哪个项目？花朵视频又在哪个项目、是什么模态？",
    ]
    for index, task in enumerate(tasks, 1):
        print(f"\n===== Q{index} =====")
        graph_evidence, graph_build = timed(build_evidence, store, task)
        plain_evidence, plain_build = timed(plain_context, store, task)
        graph_reply, graph_llm = timed(answer_task, store, task)
        (_plain_evidence, plain_reply), plain_llm = timed(plain_answer, store, task)
        print(f"TIME graph_build={graph_build:.3f}s graph_ask={graph_llm:.3f}s plain_build={plain_build:.3f}s plain_ask={plain_llm:.3f}s")
        print(f"CHARS graph={len(graph_evidence)} plain={len(plain_evidence)}")
        print("GRAPH:", graph_reply.replace("\n", " | ")[:700])
        print("PLAIN:", plain_reply.replace("\n", " | ")[:700])
    store.close()


if __name__ == "__main__":
    main()
