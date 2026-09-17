from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .indexer import Indexer, sync_registered
from .store import Store
from .watcher import WindowsChangeWatcher


def default_db() -> Path:
    return Path(__file__).resolve().parents[1] / ".data" / "memory.sqlite3"


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读本机文件记忆 MVP")
    parser.add_argument("--db", type=Path, default=default_db(), help="索引数据库路径（默认项目 .data/）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "sync"):
        command = sub.add_parser(name, help="只读扫描指定目录并登记为查询前自动同步目录")
        command.add_argument("root", type=Path)
        command.add_argument("--max-size-mib", type=int, default=25)
        command.add_argument("--include-hidden", action="store_true")
    watch = sub.add_parser("watch", help="轮询增量同步（只读源目录）")
    watch.add_argument("root", type=Path)
    watch.add_argument("--interval", type=int, default=10)
    watch.add_argument("--reconcile-seconds", type=int, default=900, help="低频全量校验间隔；0 表示关闭")
    watch.add_argument("--max-size-mib", type=int, default=25)
    watch.add_argument("--include-hidden", action="store_true")
    for name, help_text in (("search", "全文和路径检索"), ("ask", "基于索引返回任务证据")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("query" if name == "search" else "task")
        command.add_argument("--limit", type=int, default=10 if name == "search" else 8)
        command.add_argument("--no-sync", action="store_true", help="不在查询前同步已登记目录")
    sub.add_parser("roots", help="查看已登记、会在查询前自动同步的目录")
    sub.add_parser("rebuild-graph", help="从现有索引回填图谱事实，不读取源文件")
    explain = sub.add_parser("explain", help="查看文件的可追溯知识图谱事实")
    explain.add_argument("file_id", type=int)
    show = sub.add_parser("show", help="显示已索引文件的文本块，不读取或修改源文件")
    show.add_argument("file_id", type=int)
    return parser


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
            finally:
                watcher.close()
        elif args.command in {"search", "ask"}:
            if not args.no_sync:
                sync_result = sync_registered(store)
                if sync_result:
                    print("查询前同步完成：" + json.dumps(sync_result, ensure_ascii=False), file=sys.stderr)
            rows = store.search(args.query if args.command == "search" else args.task, args.limit)
            print_results(rows, store)
            if args.command == "ask":
                related = store.related_files([row["id"] for row in rows], args.limit)
                if related:
                    print("图谱关联候选（需按证据复核）：")
                    for row in related:
                        print(f"  {row['relation_type']} [{row['evidence']}] -> {row['path']}")
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