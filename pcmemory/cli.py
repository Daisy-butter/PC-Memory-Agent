from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .agent import answer_task, build_evidence, model_label
from .indexer import Indexer, sync_registered
from .llm import vllm_base_url, vllm_model
from .serve import DEFAULT_DATASET, DEFAULT_PYTHON, serve_env, vllm_command
from .store import Store
from .watcher import LinuxChangeWatcher, WindowsChangeWatcher


def default_db() -> Path:
    return Path(__file__).resolve().parents[1] / ".data" / "memory.sqlite3"


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读本机文件记忆 MVP")
    parser.add_argument("--db", type=Path, default=default_db(), help="索引数据库路径（默认项目 .data/）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "sync"):
        command = sub.add_parser(name, help="只读扫描指定目录并登记为查询前自动同步目录")
        command.add_argument("root", type=Path)
        command.add_argument("--max-size-mib", type=int, default=1024)
        command.add_argument("--include-hidden", action="store_true")
    watch = sub.add_parser("watch", help="轮询增量同步（只读源目录）")
    watch.add_argument("root", type=Path)
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--reconcile-seconds", type=int, default=900, help="低频全量校验间隔；0 表示关闭")
    watch.add_argument("--max-size-mib", type=int, default=1024)
    watch.add_argument("--include-hidden", action="store_true")
    for name, help_text in (("search", "全文和路径检索"), ("ask", "用知识图谱和本地 Qwen3-8B 回答")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("query" if name == "search" else "task")
        command.add_argument("--limit", type=int, default=10 if name == "search" else 8)
        command.add_argument("--no-sync", action="store_true", help="不在查询前同步已登记目录")
        if name == "ask":
            command.add_argument("--no-llm", action="store_true", help="只返回图谱检索证据，不调用本地模型")
    serve = sub.add_parser("serve", help="查看或启动本地 Qwen3-8B vLLM")
    serve.add_argument("--launch", action="store_true", help="在当前进程启动 vLLM，占用 --gpu 指定的卡")
    serve.add_argument("--gpu", default=os.getenv("PCM_CUDA_DEVICE", "1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PCM_VLLM_PORT", "8001")))
    serve.add_argument("--model", default=os.getenv("PCM_VLLM_MODEL") or vllm_model())
    serve.add_argument("--python", default=os.getenv("PCM_VLLM_PYTHON", DEFAULT_PYTHON))
    sub.add_parser("roots", help="查看已登记、会在查询前自动同步的目录")
    sub.add_parser("rebuild-graph", help="从现有索引回填图谱事实，不读取源文件")
    explain = sub.add_parser("explain", help="查看文件的可追溯知识图谱事实")
    explain.add_argument("file_id", type=int)
    show = sub.add_parser("show", help="显示已索引文件的文本块，不读取或修改源文件")
    show.add_argument("file_id", type=int)
    return parser


def print_keyword_ask(store: Store, task: str, limit: int) -> None:
    print(build_evidence(store, task, limit))


def print_results(rows, store: Store) -> None:
    if not rows:
        print("没有找到结果。请先索引目录，或换用更具体的关键词。")
        return
    for number, row in enumerate(rows, 1):
        print(f"[{number}] file:{row['id']}  {row['path']}")
        if row["snippet"]:
            print(f"    {row['snippet']}")
    entities = store.related_entities([row["id"] for row in rows])
    if entities:
        print("相关实体：" + "、".join(item["value"] for item in entities))


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    store = Store(args.db)
    try:
        if args.command in {"index", "sync"}:
            print(json.dumps(Indexer(store, args.max_size_mib, args.include_hidden).sync(args.root), ensure_ascii=False))
        elif args.command == "watch":
            root = args.root.resolve()
            indexer = Indexer(store, args.max_size_mib, args.include_hidden)
            print(json.dumps(indexer.sync(root), ensure_ascii=False))
            if os.name == "nt":
                watcher, last_reconcile = WindowsChangeWatcher(root), time.monotonic()
                print(f"正在原生只读监听：{root}（Ctrl+C 停止）")
                try:
                    while True:
                        changes = watcher.read_batch()
                        time.sleep(0.75)
                        print(json.dumps(indexer.apply_changes(root, changes), ensure_ascii=False))
                        if args.reconcile_seconds and time.monotonic() - last_reconcile >= args.reconcile_seconds:
                            print(json.dumps(indexer.sync(root), ensure_ascii=False))
                            last_reconcile = time.monotonic()
                except KeyboardInterrupt:
                    print("\n已停止监听。")
                finally:
                    watcher.close()
            else:
                try:
                    watcher = LinuxChangeWatcher(root)
                except OSError as error:
                    print(f"inotify 不可用（{error}），改为每 {args.interval}s 全量校验。")
                    watcher = None
                if watcher is None:
                    try:
                        while True:
                            time.sleep(args.interval)
                            print(json.dumps(indexer.sync(root), ensure_ascii=False))
                    except KeyboardInterrupt:
                        print("\n已停止监听。")
                else:
                    print(f"正在通过 inotify 只读监听：{root}（Ctrl+C 停止）")
                    last_reconcile = time.monotonic()
                    try:
                        while True:
                            changes, overflow = watcher.read_batch(min(args.interval, 2))
                            if changes or overflow:
                                deadline = time.monotonic() + 0.75
                                while time.monotonic() < deadline:
                                    extra, extra_overflow = watcher.read_batch(0.2)
                                    changes.extend(extra)
                                    overflow = overflow or extra_overflow
                            due = args.reconcile_seconds and time.monotonic() - last_reconcile >= args.reconcile_seconds
                            if overflow or due:
                                print(json.dumps(indexer.sync(root), ensure_ascii=False))
                                last_reconcile = time.monotonic()
                            elif changes:
                                print(json.dumps(indexer.apply_changes(root, changes), ensure_ascii=False))
                    except KeyboardInterrupt:
                        print("\n已停止监听。")
                    finally:
                        watcher.close()
        elif args.command == "serve":
            command = vllm_command(args.python, args.model, args.port)
            print("数据集目录：", DEFAULT_DATASET)
            print("Qwen3-8B 只处理文本。图片走 OCR/可选视觉服务，音频走 faster-whisper，视频先用 ffmpeg 抽音轨和关键帧。")
            print("启动命令：")
            print("CUDA_VISIBLE_DEVICES=" + args.gpu + " " + " ".join(command))
            print(f"就绪后：PCM_VLLM_BASE_URL=http://127.0.0.1:{args.port}/v1 python -m pcmemory ask \"某份材料属于哪个项目\"")
            if args.launch:
                if not Path(args.python).exists():
                    print(f"找不到解释器：{args.python}", file=sys.stderr)
                    return 1
                raise SystemExit(subprocess.call(command, env=serve_env(args.gpu)))
        elif args.command in {"search", "ask"}:
            if not args.no_sync:
                sync_result = sync_registered(store)
                if sync_result:
                    print("查询前同步完成：" + json.dumps(sync_result, ensure_ascii=False), file=sys.stderr)
            if args.command == "ask" and not args.no_llm:
                print(f"本地模型 {model_label()} @ {vllm_base_url()}", file=sys.stderr)
                try:
                    print(answer_task(store, args.task))
                except (ConnectionError, OSError, KeyError, TimeoutError) as error:
                    print(f"本地模型不可用（{error}），改为返回图谱检索证据。", file=sys.stderr)
                    print_keyword_ask(store, args.task, args.limit)
            elif args.command == "ask":
                print_keyword_ask(store, args.task, args.limit)
            else:
                print_results(store.search(args.query, args.limit), store)
        elif args.command == "roots":
            for root in store.registered_roots():
                print(json.dumps(dict(root), ensure_ascii=False))
        elif args.command == "rebuild-graph":
            print(json.dumps({"facts_backfilled": store.rebuild_facts()}, ensure_ascii=False))
        elif args.command == "explain":
            for fact in store.facts_for_file(args.file_id):
                print(f"{fact['predicate']} -> {fact['kind']}:{fact['value']} (confidence={fact['confidence']:.2f}; {fact['asserted_by']})")
        elif args.command == "show":
            file = store.get_file(args.file_id)
            if not file:
                print("找不到活动索引文件。")
                return 1
            print(file["path"])
            for chunk in store.get_chunks(args.file_id):
                print(f"\n--- 文本块 {chunk['ordinal']} ---\n{chunk['content']}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())