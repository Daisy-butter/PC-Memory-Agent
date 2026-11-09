"""Same Qwen3 agent, with and without the knowledge graph.

Both sides use the same file search. The graph side also receives project facts,
modality, and same-content timelines. The plain side must answer the cross-file
and cross-time question from paths and snippets alone. Scores the agent's
answers, not whether a graph edge exists.
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

PLAIN = """你是本机文件记忆助手。这次没有知识图谱，只能根据检索到的文件路径和正文片段，自己回答跨文件、跨时间的问题。
需要判断的是：文件属于哪个项目、哪一份是最新的、更早还在哪些项目出现过、文件是什么模态。
路径中的目录名和正文里的信息都可以作为依据。检索结果里没有出现的文件不要编造。用中文回答，并写出依据的路径。"""


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


def _latest_span(answer: str) -> str:
    for marker in ("最新一份", "最新出现", "最新的", "最新"):
        position = answer.find(marker)
        if position >= 0:
            return norm(answer[position:position + 220])
    return norm(answer[:220])


def _earlier_text(answer: str) -> str:
    """Text in which the agent is naming older copies, not the copy it calls newest."""
    for marker in ("更早", "较早", "之前", "以前"):
        position = answer.find(marker)
        if position >= 0:
            return norm(answer[position:])
    return ""


def _checks(task_index: int, answer: str) -> list[tuple[str, bool]]:
    """Whether the agent's answer resolved the cross-file or cross-time question."""
    text = norm(answer)
    earlier = _earlier_text(answer)
    if task_index == 1:
        return [
            ("跨文件：Iris 属于 05_Datasets", "05datasets" in text),
            ("跨文件：论文 PDF 属于 03_Research_Papers", "03researchpapers" in text),
        ]
    if task_index == 2:
        span = _latest_span(answer)
        newest = "21realgithubprojects" in span and ("124m" in text or "gpt2124mloss" in text)
        return [
            ("跨时间：损失曲线最新一份在 21 的 gpt2_124M_loss.png", newest),
            ("跨文件：更早出现在 15_Experiments", "15experiments" in earlier),
            ("跨文件：更早出现在 20_Data_Visualisation", "20datavisualisation" in earlier),
            ("跨文件：更早出现在 08_Media", "08media" in earlier),
            ("跨文件：更早出现在 01_AI_Projects", "01aiprojects" in earlier),
        ]
    if task_index == 3:
        span = _latest_span(answer)
        newest = "21realgithubprojects" in span or ("nanogpt" in span and "15experiments" not in span)
        return [
            ("跨时间：代码最新一份在 21 的 model.py", newest),
            ("跨文件：代码更早出现在 15_Experiments", "15experiments" in earlier),
            ("跨文件：代码更早出现在 01_AI_Projects", "01aiprojects" in earlier),
        ]
    transcript = any(token in text for token in ("wikipedia", "encyclopedia", "examplesound", "维基"))
    return [
        ("跨文件：example.ogg 转写出示例语音且属于 07_Media", transcript and "07media" in text),
        ("跨文件：花朵视频属于 08_Media", "08media" in text and ("flower" in text or "花" in answer)),
        ("跨模态：花朵视频是 video", "video" in text),
    ]


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
    graph_hits = plain_hits = graph_total = plain_total = 0
    for index, task in enumerate(tasks, 1):
        print(f"\n===== Q{index} =====")
        graph_evidence, graph_build = timed(build_evidence, store, task)
        plain_evidence, plain_build = timed(plain_context, store, task)
        graph_reply, graph_llm = timed(answer_task, store, task)
        (_plain_evidence, plain_reply), plain_llm = timed(plain_answer, store, task)
        print(f"TIME graph_build={graph_build:.3f}s graph_ask={graph_llm:.3f}s plain_build={plain_build:.3f}s plain_ask={plain_llm:.3f}s")
        print(f"CHARS graph={len(graph_evidence)} plain={len(plain_evidence)}")
        print("GRAPH:", graph_reply.replace("\n", " | "))
        print("PLAIN:", plain_reply.replace("\n", " | "))
        for label, graph_ok, plain_ok in (
            (label, graph_ok, plain_ok)
            for (label, graph_ok), (_, plain_ok) in zip(_checks(index, graph_reply), _checks(index, plain_reply), strict=True)
        ):
            graph_total += 1
            plain_total += 1
            graph_hits += int(graph_ok)
            plain_hits += int(plain_ok)
            print(f"CHECK {label} | with_graph={int(graph_ok)} without_graph={int(plain_ok)}")
    print(f"AGENT_SCORE with_graph={graph_hits}/{graph_total} without_graph={plain_hits}/{plain_total}")
    store.close()


if __name__ == "__main__":
    main()
