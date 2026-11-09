import os
import tempfile
import unittest
from pathlib import Path

from pcmemory.indexer import Indexer, sync_registered
from pcmemory.store import Store


class MemoryTests(unittest.TestCase):
    def test_index_search_and_rename_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "客户A合同.txt"
            document.write_text("客户A 2026 年合同。最终报价为 100 万元。", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                self.assertEqual(Indexer(store).sync(root)["indexed"], 1)
                results = store.search("客户A 合同")
                self.assertEqual(len(results), 1)
                document.rename(root / "客户A最终合同.txt")
                Indexer(store).sync(root)
                self.assertEqual(store.get_file(results[0]["id"])["name"], "客户A最终合同.txt")
                self.assertIn("客户A合同.txt", store.conn.execute("SELECT previous_path FROM file_history").fetchone()[0])
            finally:
                store.close()

    def test_registered_roots_refresh_before_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "notes.txt"
            document.write_text("旧内容", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                document.write_text("最新项目计划", encoding="utf-8")
                report = sync_registered(store)
                self.assertIn(str(root.resolve()), report)
                self.assertEqual(len(store.search("最新项目")), 1)
                document.unlink()
                sync_registered(store)
                self.assertEqual(store.search("最新项目"), [])
            finally:
                store.close()


    def test_linux_watch_tracks_create_rename_and_delete(self):
        if os.name == "nt":
            self.skipTest("inotify only exists on Linux")
        from pcmemory.watcher import LinuxChangeWatcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "笔记.txt"
            original.write_text("旧会议纪要", encoding="utf-8")
            store = Store(root / "index.sqlite")
            indexer = Indexer(store)
            watcher = LinuxChangeWatcher(root)
            try:
                indexer.sync(root)
                created = root / "补充.txt"
                created.write_text("新会议纪要", encoding="utf-8")
                paths, overflow = watcher.read_batch(2)
                self.assertFalse(overflow)
                self.assertTrue(any(path.name == "补充.txt" for path in paths))
                indexer.apply_changes(root, paths)
                self.assertEqual(len(store.search("新会议纪要")), 1)
                renamed = root / "补充_final.txt"
                created.rename(renamed)
                indexer.apply_changes(root, watcher.read_batch(2)[0])
                self.assertEqual(store.search("新会议纪要")[0]["name"], "补充_final.txt")
                renamed.unlink()
                indexer.apply_changes(root, watcher.read_batch(2)[0])
                self.assertEqual(store.search("新会议纪要"), [])
                self.assertEqual(store.search("旧会议纪要")[0]["name"], "笔记.txt")
            finally:
                watcher.close()
                store.close()

    def test_graph_facts_have_typed_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "course.txt").write_text("CS 101 starts 2026-09-01. Contact tutor@example.com", encoding="utf-8")
            store = Store(root / "index.sqlite")
            try:
                Indexer(store).sync(root)
                file_id = store.search("CS101")[0]["id"]
                facts = {(item["predicate"], item["value"], item["asserted_by"]) for item in store.facts_for_file(file_id)}
                self.assertIn(("HAS_COURSE", "CS101", "rules_v1"), facts)
                self.assertIn(("MENTIONS_CONTACT", "tutor@example.com", "rules_v1"), facts)
            finally:
                store.close()
if __name__ == "__main__":
    unittest.main()